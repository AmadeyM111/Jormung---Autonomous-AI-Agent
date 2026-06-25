"""Website image-post collection and Telegram broadcast extension."""

from __future__ import annotations

import datetime as _dt
import hashlib
import html
import ipaddress
import json
import mimetypes
import os
import pathlib
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Iterable, List, Tuple

try:
    from starlette.requests import Request
    from starlette.responses import JSONResponse
except ModuleNotFoundError:  # pragma: no cover - lets offline parsers import the skill without web deps.
    Request = Any  # type: ignore[assignment]

    class JSONResponse:  # type: ignore[no-redef]
        def __init__(self, payload: Any, status_code: int = 200):
            self.payload = payload
            self.status_code = status_code


_CONFIG_FILE = "config.json"
_RECORDS_FILE = "records.json"
_PREPARED_FILE = "prepared.json"
_IMAGES_DIR = "images"
_TIMEOUT_SEC = 20
_MAX_HTML_BYTES = 4 * 1024 * 1024
_MAX_IMAGE_BYTES = 8 * 1024 * 1024
_USER_AGENT = "Ouroboros-PostBroadcast/0.1"
_TELEGRAM_PHOTO_CAPTION_LIMIT = 1024

_DEFAULT_CONFIG: Dict[str, Any] = {
    "schema_version": 1,
    "sources": [
        {
            "id": "pinterest_cat_memes",
            "kind": "pinterest_board",
            "url": "https://ru.pinterest.com/odinokayabulka/%D0%BC%D0%B5%D0%BC%D1%8B-%D1%81-%D0%BA%D0%BE%D1%82%D0%B0%D0%BC%D0%B8/",
            "title": "Cat memes",
            "image_required": True,
        }
    ],
    "telegram": {
        "bot_id": "8693178834",
        "chat_ids": ["7568942324"],
        "max_posts_per_run": 1,
        "append_source_link": True,
    },
    "rewrite": {
        "style": "intellectually funny Russian",
        "language": "ru",
        "max_caption_chars": 700,
    },
    "moderation": {
        "mode": "automatic",
        "require_image": True,
        "skip_duplicates": True,
        "skip_broken_images": True,
        "min_image_width": 400,
        "min_image_height": 400,
    },
    "schedule": {
        "cron": "0 10-19/3 * * *",
        "timezone": "Europe/Moscow",
    },
}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        raise urllib.error.HTTPError(req.full_url, code, "redirect refused", headers, fp)


_OPENER = urllib.request.build_opener(_NoRedirect)


def _moscow_now() -> str:
    moscow_tz = _dt.timezone(_dt.timedelta(hours=3))
    now = _dt.datetime.now(moscow_tz)
    return now.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + now.strftime('%:z')


def _utc_now() -> str:
    return _moscow_now()


def _state_path(state_dir: pathlib.Path, name: str) -> pathlib.Path:
    state_dir.mkdir(parents=True, exist_ok=True)
    return state_dir / name


def _read_json(path: pathlib.Path, default: Any) -> Any:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if data is not None else default
    except FileNotFoundError:
        return default
    except Exception:
        return default


def _atomic_write_json(path: pathlib.Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _load_config(state_dir: pathlib.Path) -> Dict[str, Any]:
    config = json.loads(json.dumps(_DEFAULT_CONFIG, ensure_ascii=False))
    saved = _read_json(_state_path(state_dir, _CONFIG_FILE), {})
    if isinstance(saved, dict):
        for key in ("sources", "telegram", "rewrite", "moderation", "schedule"):
            value = saved.get(key)
            if isinstance(value, dict) and isinstance(config.get(key), dict):
                config[key].update(value)
            elif isinstance(value, list) and key == "sources":
                config[key] = value
    return config


def _save_config(state_dir: pathlib.Path, config: Dict[str, Any]) -> None:
    _atomic_write_json(_state_path(state_dir, _CONFIG_FILE), config)


def _load_records(state_dir: pathlib.Path) -> Dict[str, Any]:
    records = _read_json(_state_path(state_dir, _RECORDS_FILE), {"schema_version": 1, "items": []})
    if not isinstance(records, dict):
        records = {"schema_version": 1, "items": []}
    if not isinstance(records.get("items"), list):
        records["items"] = []
    return records


def _save_records(state_dir: pathlib.Path, records: Dict[str, Any]) -> None:
    items = [item for item in records.get("items", []) if isinstance(item, dict)]
    items.sort(key=lambda item: str(item.get("fetched_at") or item.get("published_at") or ""), reverse=True)
    records["schema_version"] = 1
    records["items"] = items[:1000]
    _atomic_write_json(_state_path(state_dir, _RECORDS_FILE), records)


def _is_blocked_host(host: str) -> bool:
    clean = str(host or "").strip().strip("[]").lower()
    if not clean:
        return True
    if clean in {"localhost", "localhost.localdomain"} or clean.endswith(".localhost"):
        return True
    try:
        ip = ipaddress.ip_address(clean)
    except ValueError:
        return False
    return bool(ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved)


def _validate_url(url: str) -> str:
    parsed = urllib.parse.urlparse(str(url or "").strip())
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("URL must use http or https")
    if _is_blocked_host(parsed.hostname or ""):
        raise ValueError("URL host is blocked")
    return urllib.parse.urlunparse(parsed)


def _fetch_bytes(url: str, *, max_bytes: int, accept: str) -> Tuple[bytes, str]:
    safe_url = _validate_url(url)
    request = urllib.request.Request(
        safe_url,
        headers={"User-Agent": _USER_AGENT, "Accept": accept},
    )
    with _OPENER.open(request, timeout=_TIMEOUT_SEC) as response:
        raw = response.read(max_bytes + 1)
        content_type = str(response.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
    if len(raw) > max_bytes:
        raise ValueError("upstream response is too large")
    return raw, content_type


def _fetch_text(url: str) -> str:
    raw, content_type = _fetch_bytes(url, max_bytes=_MAX_HTML_BYTES, accept="text/html,application/xhtml+xml,*/*")
    encoding = "utf-8"
    if "charset=" in content_type:
        encoding = content_type.rsplit("charset=", 1)[-1].strip() or "utf-8"
    return raw.decode(encoding, errors="replace")


def _decode_web_escapes(text: str) -> str:
    out = html.unescape(str(text or ""))
    replacements = {
        "\\/": "/",
        "\\u002F": "/",
        "\\u002f": "/",
        "\\u003A": ":",
        "\\u003a": ":",
        "\\u0026": "&",
        "\\u003D": "=",
        "\\u003d": "=",
        "\\u002D": "-",
        "\\u002d": "-",
    }
    for old, new in replacements.items():
        out = out.replace(old, new)
    return out


def _clean_url(url: str) -> str:
    text = str(url or "").strip()
    text = text.rstrip(".,;)")
    text = text.replace("&amp;", "&")
    return text


def _strip_text(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:500]


def _fingerprint(source_id: str, image_url: str, post_url: str, title: str = "") -> str:
    key = (post_url or image_url or f"{source_id}|{title}").strip().lower()
    return hashlib.sha256(key.encode("utf-8", errors="replace")).hexdigest()[:24]


def _nearest_match(pattern: str, text: str, center: int, *, radius: int = 1600) -> str:
    start = max(0, center - radius)
    end = min(len(text), center + radius)
    window = text[start:end]
    matches = list(re.finditer(pattern, window, flags=re.IGNORECASE))
    if not matches:
        return ""
    best = min(matches, key=lambda match: abs((start + match.start()) - center))
    return _clean_url(best.group(1) if best.groups() else best.group(0))


def _extract_title_near(text: str, center: int) -> str:
    start = max(0, center - 1800)
    end = min(len(text), center + 1800)
    window = text[start:end]
    patterns = [
        r'"(?:title|name|description|alt_text|grid_title)"\s*:\s*"([^"]{3,220})"',
        r"'(?:title|name|description|alt_text|grid_title)'\s*:\s*'([^']{3,220})'",
        r'alt="([^"]{3,220})"',
        r'aria-label="([^"]{3,220})"',
    ]
    for pattern in patterns:
        match = re.search(pattern, window, flags=re.IGNORECASE)
        if match:
            return _strip_text(match.group(1))
    return ""


def _parse_pinterest_board(raw: str, source: Dict[str, Any], fetched_at: str) -> List[Dict[str, Any]]:
    text = _decode_web_escapes(raw)
    image_pattern = r"https?://i\.pinimg\.com/[^\"'<>\s\\]+"
    pin_pattern = r"(https?://(?:www\.|ru\.)?pinterest\.[^\"'<>\s\\]+/pin/\d+/?[^\"'<>\s\\]*)"
    images: List[Tuple[int, str]] = []
    seen_images: set[str] = set()
    for match in re.finditer(image_pattern, text, flags=re.IGNORECASE):
        url = _clean_url(match.group(0))
        if not url or url in seen_images:
            continue
        if "/236x/" in url or "/75x75" in url:
            continue
        seen_images.add(url)
        images.append((match.start(), url))

    out: List[Dict[str, Any]] = []
    source_id = str(source.get("id") or source.get("url") or "pinterest_board")
    source_title = str(source.get("title") or source_id)
    for pos, image_url in images[: max(1, int(source.get("max_items") or 40))]:
        post_url = _nearest_match(pin_pattern, text, pos)
        title = _extract_title_near(text, pos) or source_title
        item_id = _fingerprint(source_id, image_url, post_url, title)
        out.append({
            "id": item_id,
            "source_id": source_id,
            "source_title": source_title,
            "kind": "pinterest_board",
            "title": title,
            "text": title,
            "url": post_url or str(source.get("url") or ""),
            "image_url": image_url,
            "published_at": "",
            "fetched_at": fetched_at,
            "status": "new",
        })
    return out


def _collect_source(source: Dict[str, Any]) -> List[Dict[str, Any]]:
    kind = str(source.get("kind") or "website").strip().lower()
    fetched_at = _utc_now()
    if kind != "pinterest_board":
        raise ValueError("only kind=pinterest_board is implemented in this version")
    raw = _fetch_text(str(source.get("url") or ""))
    return _parse_pinterest_board(raw, source, fetched_at)


def _refresh(state_dir: pathlib.Path, *, source_id: str = "", limit_per_source: int = 40) -> Dict[str, Any]:
    config = _load_config(state_dir)
    sources = [s for s in config.get("sources", []) if isinstance(s, dict)]
    if source_id:
        sources = [s for s in sources if str(s.get("id") or "") == source_id]
    records = _load_records(state_dir)
    by_id = {str(item.get("id") or ""): dict(item) for item in records.get("items", []) if isinstance(item, dict)}
    errors: List[Dict[str, str]] = []
    new_count = 0
    updated_count = 0
    fetched_sources: List[str] = []
    for source in sources:
        sid = str(source.get("id") or source.get("url") or "source")
        try:
            source = dict(source)
            source["max_items"] = int(limit_per_source or source.get("max_items") or 40)
            items = _collect_source(source)
        except Exception as exc:
            errors.append({"source_id": sid, "error": f"{type(exc).__name__}: {exc}"})
            continue
        fetched_sources.append(sid)
        for item in items:
            item_id = str(item.get("id") or "")
            if not item_id:
                continue
            if item_id in by_id:
                previous_status = str(by_id[item_id].get("status") or "new")
                by_id[item_id].update({k: v for k, v in item.items() if v not in ("", None, {}, [])})
                by_id[item_id]["status"] = previous_status
                updated_count += 1
            else:
                by_id[item_id] = item
                new_count += 1
    records["items"] = list(by_id.values())
    _save_records(state_dir, records)
    return {
        "ok": True,
        "sources_requested": len(sources),
        "fetched_source_ids": fetched_sources,
        "new_items": new_count,
        "updated_items": updated_count,
        "errors": errors,
    }


def _image_ext(content_type: str, url: str) -> str:
    if content_type in {"image/jpeg", "image/jpg"}:
        return ".jpg"
    if content_type == "image/png":
        return ".png"
    if content_type == "image/webp":
        return ".webp"
    guessed = pathlib.Path(urllib.parse.urlparse(url).path).suffix.lower()
    if guessed in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        return ".jpg" if guessed == ".jpeg" else guessed
    return ".jpg"


def _detect_image_size(path: pathlib.Path) -> Tuple[int, int]:
    try:
        from PIL import Image

        with Image.open(path) as img:
            return int(img.width), int(img.height)
    except Exception:
        return 0, 0


def _download_image(state_dir: pathlib.Path, item: Dict[str, Any], moderation: Dict[str, Any]) -> Tuple[bool, str, Dict[str, Any]]:
    image_url = str(item.get("image_url") or "").strip()
    if not image_url:
        return False, "image_url is missing", {}
    try:
        raw, content_type = _fetch_bytes(image_url, max_bytes=_MAX_IMAGE_BYTES, accept="image/*,*/*")
    except Exception as exc:
        return False, f"image download failed: {type(exc).__name__}: {exc}", {}
    if not (content_type.startswith("image/") or raw[:3] == b"\xff\xd8" or raw[:8].startswith(b"\x89PNG")):
        return False, f"unexpected image content type: {content_type or 'unknown'}", {}
    image_dir = state_dir / _IMAGES_DIR
    image_dir.mkdir(parents=True, exist_ok=True)
    ext = _image_ext(content_type, image_url)
    image_path = image_dir / f"{item.get('id')}{ext}"
    image_path.write_bytes(raw)
    width, height = _detect_image_size(image_path)
    min_w = int(moderation.get("min_image_width") or 0)
    min_h = int(moderation.get("min_image_height") or 0)
    if width and height and (width < min_w or height < min_h):
        return False, f"image too small: {width}x{height}", {"image_path": str(image_path), "width": width, "height": height}
    return True, "", {
        "image_path": str(image_path),
        "image_mime": content_type or mimetypes.guess_type(str(image_path))[0] or "image/jpeg",
        "image_bytes": len(raw),
        "width": width,
        "height": height,
    }


def _candidate_items(records: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    for item in records.get("items", []):
        if not isinstance(item, dict):
            continue
        if str(item.get("status") or "new") in {"sent", "skipped"}:
            continue
        if not str(item.get("image_url") or ""):
            continue
        yield item


def _prepare_next(state_dir: pathlib.Path, *, refresh: bool = True) -> Dict[str, Any]:
    config = _load_config(state_dir)
    refresh_status = _refresh(state_dir) if refresh else {"enabled": False}
    records = _load_records(state_dir)
    moderation = config.get("moderation") if isinstance(config.get("moderation"), dict) else {}
    prepared = _read_json(_state_path(state_dir, _PREPARED_FILE), {})
    if isinstance(prepared, dict) and prepared.get("prepared_id"):
        return {"ok": True, "has_prepared": True, "prepared": prepared, "refresh": refresh_status}

    failures: List[Dict[str, str]] = []
    for item in _candidate_items(records):
        ok, error, image_meta = _download_image(state_dir, item, moderation)
        if not ok:
            item["status"] = "skipped" if moderation.get("skip_broken_images", True) else "new"
            item["last_error"] = error
            failures.append({"id": str(item.get("id") or ""), "error": error})
            continue
        item.update(image_meta)
        item["status"] = "prepared"
        item["prepared_at"] = _utc_now()
        prepared_payload = {
            "prepared_id": str(item.get("id") or ""),
            "source_id": str(item.get("source_id") or ""),
            "source_title": str(item.get("source_title") or ""),
            "title": str(item.get("title") or ""),
            "source_text": str(item.get("text") or item.get("title") or ""),
            "source_url": str(item.get("url") or ""),
            "image_url": str(item.get("image_url") or ""),
            "image_path": str(item.get("image_path") or ""),
            "rewrite_style": config.get("rewrite", {}),
            "telegram": {
                "chat_ids": list((config.get("telegram") or {}).get("chat_ids") or []),
                "append_source_link": bool((config.get("telegram") or {}).get("append_source_link", True)),
            },
        }
        _atomic_write_json(_state_path(state_dir, _PREPARED_FILE), prepared_payload)
        _save_records(state_dir, records)
        return {"ok": True, "has_prepared": True, "prepared": prepared_payload, "refresh": refresh_status}
    _save_records(state_dir, records)
    return {"ok": True, "has_prepared": False, "reason": "no unsent image posts available", "failures": failures[:10], "refresh": refresh_status}


def _clamp_caption(caption: str, source_url: str, append_source_link: bool) -> str:
    text = str(caption or "").strip()
    if append_source_link and source_url and source_url not in text:
        text = (text + "\n\n" if text else "") + f"Source: {source_url}"
    if len(text) > _TELEGRAM_PHOTO_CAPTION_LIMIT:
        text = text[: _TELEGRAM_PHOTO_CAPTION_LIMIT - 1].rstrip() + "..."
    return text


def _telegram_send_photo(token: str, chat_id: str, image_path: pathlib.Path, caption: str) -> Dict[str, Any]:
    boundary = f"----ouroboros-post-broadcast-{hashlib.sha256(os.urandom(16)).hexdigest()[:16]}"
    data = image_path.read_bytes()
    filename = image_path.name
    mime = mimetypes.guess_type(filename)[0] or "image/jpeg"

    def part(name: str, value: str) -> bytes:
        return (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n"
        ).encode("utf-8")

    body = bytearray()
    body.extend(part("chat_id", str(chat_id)))
    body.extend(part("caption", caption))
    body.extend(
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="photo"; filename="{filename}"\r\n'
            f"Content-Type: {mime}\r\n\r\n"
        ).encode("utf-8")
    )
    body.extend(data)
    body.extend(f"\r\n--{boundary}--\r\n".encode("utf-8"))
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendPhoto",
        data=bytes(body),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=_TIMEOUT_SEC) as response:
        raw = response.read(512 * 1024)
    try:
        payload = json.loads(raw.decode("utf-8", errors="replace"))
    except Exception:
        payload = {"ok": False, "raw": raw.decode("utf-8", errors="replace")[:500]}
    return payload if isinstance(payload, dict) else {"ok": False, "payload": payload}


def _send_prepared(
    state_dir: pathlib.Path,
    *,
    prepared_id: str = "",
    caption: str = "",
    telegram_token: str = "",
) -> Dict[str, Any]:
    config = _load_config(state_dir)
    telegram = config.get("telegram") if isinstance(config.get("telegram"), dict) else {}
    chat_ids = [str(chat).strip() for chat in telegram.get("chat_ids", []) if str(chat).strip()]
    if not chat_ids:
        return {"ok": False, "error": "telegram.chat_ids is empty"}
    token = str(telegram_token or "").strip()
    if not token:
        return {"ok": False, "error": "TELEGRAM_BOT_TOKEN is not configured"}
    prepared = _read_json(_state_path(state_dir, _PREPARED_FILE), {})
    if not isinstance(prepared, dict) or not prepared.get("prepared_id"):
        return {"ok": False, "error": "no prepared post"}
    if prepared_id and prepared_id != str(prepared.get("prepared_id") or ""):
        return {"ok": False, "error": "prepared_id does not match current prepared post"}
    image_path = pathlib.Path(str(prepared.get("image_path") or ""))
    if not image_path.is_file():
        return {"ok": False, "error": f"prepared image is missing: {image_path}"}
    final_caption = _clamp_caption(
        caption or str(prepared.get("source_text") or prepared.get("title") or ""),
        str(prepared.get("source_url") or ""),
        bool(telegram.get("append_source_link", True)),
    )
    results = []
    ok_count = 0
    for chat_id in chat_ids:
        try:
            result = _telegram_send_photo(token, chat_id, image_path, final_caption)
        except Exception as exc:
            result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        if result.get("ok"):
            ok_count += 1
        results.append({"chat_id": chat_id, "ok": bool(result.get("ok")), "result": result})
    records = _load_records(state_dir)
    for item in records.get("items", []):
        if isinstance(item, dict) and str(item.get("id") or "") == str(prepared.get("prepared_id") or ""):
            item["status"] = "sent" if ok_count == len(chat_ids) else "failed"
            item["sent_at"] = _utc_now() if ok_count else ""
            item["caption"] = final_caption
            item["send_results"] = results
            break
    _save_records(state_dir, records)
    if ok_count == len(chat_ids):
        _atomic_write_json(_state_path(state_dir, _PREPARED_FILE), {})
    return {"ok": ok_count == len(chat_ids), "sent_chats": ok_count, "requested_chats": len(chat_ids), "results": results}


def _list_status(state_dir: pathlib.Path) -> Dict[str, Any]:
    records = _load_records(state_dir)
    counts: Dict[str, int] = {}
    for item in records.get("items", []):
        if isinstance(item, dict):
            status = str(item.get("status") or "new")
            counts[status] = counts.get(status, 0) + 1
    return {
        "ok": True,
        "counts": counts,
        "prepared": _read_json(_state_path(state_dir, _PREPARED_FILE), {}),
        "config": _load_config(state_dir),
    }


def _bool_arg(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value if value is not None else "").strip().lower()
    if not text:
        return default
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


async def _json_body(request: Request) -> Dict[str, Any]:
    try:
        data = await request.json()
        return data if isinstance(data, dict) else {}
    except Exception:
        try:
            form = await request.form()
            return {str(k): v for k, v in form.items()}
        except Exception:
            return {}


def register(api: Any) -> None:
    state_dir = pathlib.Path(api.get_state_dir())
    _save_config(state_dir, _load_config(state_dir))

    def _settings_token() -> str:
        try:
            settings = api.get_settings(["TELEGRAM_BOT_TOKEN"])
        except Exception:
            settings = {}
        return str((settings or {}).get("TELEGRAM_BOT_TOKEN") or "").strip()

    async def route_prepare(request: Request) -> JSONResponse:
        body = await _json_body(request)
        refresh = _bool_arg(body.get("refresh") or request.query_params.get("refresh"), default=True)
        payload = _prepare_next(state_dir, refresh=refresh)
        return JSONResponse(payload, status_code=200 if payload.get("ok") else 400)

    async def route_status(request: Request) -> JSONResponse:
        return JSONResponse(_list_status(state_dir))

    api.register_tool(
        "refresh",
        lambda limit_per_source=40, source_id="": json.dumps(
            _refresh(
                state_dir,
                source_id=str(source_id or ""),
                limit_per_source=int(limit_per_source or 40),
            ),
            ensure_ascii=False,
        ),
        description="Fetch configured website image-post sources and deduplicate discovered posts.",
        schema={
            "type": "object",
            "properties": {
                "limit_per_source": {"type": "integer"},
                "source_id": {"type": "string"},
            },
        },
        timeout_sec=90,
    )
    api.register_tool(
        "prepare_next",
        lambda refresh=True: json.dumps(
            _prepare_next(state_dir, refresh=_bool_arg(refresh, default=True)),
            ensure_ascii=False,
        ),
        description=(
            "Prepare one unsent image-required post for LLM caption rewrite. Use this first in the scheduled "
            "post_broadcast workflow; then call send_prepared with the rewritten caption."
        ),
        schema={"type": "object", "properties": {"refresh": {"type": "boolean"}}},
        timeout_sec=120,
    )
    api.register_tool(
        "send_prepared",
        lambda prepared_id="", caption="": json.dumps(
            _send_prepared(
                state_dir,
                prepared_id=str(prepared_id or ""),
                caption=str(caption or ""),
                telegram_token=_settings_token(),
            ),
            ensure_ascii=False,
        ),
        description="Send the currently prepared image post to configured Telegram chats using TELEGRAM_BOT_TOKEN.",
        schema={
            "type": "object",
            "properties": {
                "prepared_id": {"type": "string"},
                "caption": {"type": "string"},
            },
        },
        timeout_sec=60,
    )
    api.register_tool(
        "status",
        lambda: json.dumps(_list_status(state_dir), ensure_ascii=False),
        description="Return post_broadcast config, prepared item, and record status counts.",
        schema={"type": "object", "properties": {}},
        timeout_sec=10,
    )
    api.register_route("prepare", route_prepare, methods=("POST", "GET"))
    api.register_route("status", route_status, methods=("GET",))
    api.register_ui_tab(
        "post_broadcast",
        "Post broadcast",
        icon="send",
        render={
            "kind": "declarative",
            "schema_version": 1,
            "components": [
                {
                    "type": "form",
                    "route": "prepare",
                    "method": "POST",
                    "target": "prepare",
                    "submit_label": "Prepare next",
                    "fields": [{"name": "refresh", "label": "Refresh", "type": "checkbox", "default": "true"}],
                },
                {"type": "json", "target": "prepare"},
                {"type": "json", "target": "status"},
            ],
        },
    )
    api.log("info", "post_broadcast: extension registered")


__all__ = [
    "register",
    "_parse_pinterest_board",
    "_prepare_next",
    "_send_prepared",
    "_refresh",
]
