"""Website image-post collection and Telegram broadcast extension."""

from __future__ import annotations

import datetime as _dt
import difflib
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
_SUBSCRIPTIONS_FILE = "subscribers.json"
_IMAGES_DIR = "images"
_TIMEOUT_SEC = 20
_MAX_HTML_BYTES = 4 * 1024 * 1024
_MAX_IMAGE_BYTES = 8 * 1024 * 1024
_USER_AGENT = "Ouroboros-PostBroadcast/0.1"
_TELEGRAM_PHOTO_CAPTION_LIMIT = 1024
_MIN_GENERATED_CAPTION_CHARS = 80
_RECENT_CAPTION_LIMIT = 12
_CAPTION_SIMILARITY_LIMIT = 0.86
_DEFAULT_VISION_MODEL = "google/gemma-4-26b-a4b-it:free"
_REJECTED_CAPTION_PHRASES = (
    "кот в кадре демонстрирует уверенность старшего инженера",
    "кот занял рабочее место и выглядит так, будто сейчас закроет спринт",
)

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
        "chat_ids": [],
        "max_posts_per_run": 1,
        "append_source_link": True,
    },
    "rewrite": {
        "style": "intellectually funny Russian",
        "language": "ru",
        "max_caption_chars": 700,
        "vision_model": _DEFAULT_VISION_MODEL,
    },
    "moderation": {
        "mode": "automatic",
        "require_image": True,
        "skip_duplicates": True,
        "skip_broken_images": True,
        "require_topic_match": True,
        "topic_keywords": [
            "cat",
            "cats",
            "kitten",
            "kittens",
            "meow",
            "meme",
            "memes",
            "кот",
            "коты",
            "кошка",
            "кошки",
            "котик",
            "котики",
            "мем",
            "мемы",
        ],
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


def _normalize_chat_id(chat_id: Any) -> str:
    return str(chat_id or "").strip()


def _unique_chat_ids(values: Iterable[Any]) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for value in values:
        chat_id = _normalize_chat_id(value)
        if not chat_id or chat_id in seen:
            continue
        seen.add(chat_id)
        out.append(chat_id)
    return out


def _load_subscriptions(state_dir: pathlib.Path) -> Dict[str, Any]:
    data = _read_json(
        _state_path(state_dir, _SUBSCRIPTIONS_FILE),
        {"schema_version": 1, "subscribed_chat_ids": [], "unsubscribed_chat_ids": []},
    )
    if not isinstance(data, dict):
        data = {}
    data["schema_version"] = 1
    data["subscribed_chat_ids"] = _unique_chat_ids(data.get("subscribed_chat_ids") or [])
    data["unsubscribed_chat_ids"] = _unique_chat_ids(data.get("unsubscribed_chat_ids") or [])
    return data


def _save_subscriptions(state_dir: pathlib.Path, subscriptions: Dict[str, Any]) -> None:
    payload = {
        "schema_version": 1,
        "subscribed_chat_ids": _unique_chat_ids(subscriptions.get("subscribed_chat_ids") or []),
        "unsubscribed_chat_ids": _unique_chat_ids(subscriptions.get("unsubscribed_chat_ids") or []),
        "updated_at": _utc_now(),
    }
    _atomic_write_json(_state_path(state_dir, _SUBSCRIPTIONS_FILE), payload)


def _configured_chat_ids(config: Dict[str, Any]) -> List[str]:
    telegram = config.get("telegram") if isinstance(config.get("telegram"), dict) else {}
    return _unique_chat_ids(telegram.get("chat_ids") or [])


def _effective_chat_ids(state_dir: pathlib.Path, config: Dict[str, Any]) -> List[str]:
    subscriptions = _load_subscriptions(state_dir)
    subscribed = _unique_chat_ids(subscriptions.get("subscribed_chat_ids") or [])
    unsubscribed = set(_unique_chat_ids(subscriptions.get("unsubscribed_chat_ids") or []))
    return [chat_id for chat_id in subscribed if chat_id not in unsubscribed]


def _subscription_status(state_dir: pathlib.Path) -> Dict[str, Any]:
    config = _load_config(state_dir)
    subscriptions = _load_subscriptions(state_dir)
    return {
        "ok": True,
        "configured_chat_ids": _configured_chat_ids(config),
        "subscribed_chat_ids": list(subscriptions.get("subscribed_chat_ids") or []),
        "unsubscribed_chat_ids": list(subscriptions.get("unsubscribed_chat_ids") or []),
        "active_chat_ids": _effective_chat_ids(state_dir, config),
    }


def _set_subscription(state_dir: pathlib.Path, chat_id: str, *, subscribed: bool) -> Dict[str, Any]:
    normalized = _normalize_chat_id(chat_id)
    if not normalized:
        return {"ok": False, "error": "chat_id is required"}
    subscriptions = _load_subscriptions(state_dir)
    subscribed_ids = _unique_chat_ids(subscriptions.get("subscribed_chat_ids") or [])
    unsubscribed_ids = _unique_chat_ids(subscriptions.get("unsubscribed_chat_ids") or [])
    if subscribed:
        subscribed_ids = _unique_chat_ids([*subscribed_ids, normalized])
        unsubscribed_ids = [item for item in unsubscribed_ids if item != normalized]
    else:
        subscribed_ids = [item for item in subscribed_ids if item != normalized]
        unsubscribed_ids = _unique_chat_ids([*unsubscribed_ids, normalized])
    subscriptions["subscribed_chat_ids"] = subscribed_ids
    subscriptions["unsubscribed_chat_ids"] = unsubscribed_ids
    _save_subscriptions(state_dir, subscriptions)
    payload = _subscription_status(state_dir)
    payload["chat_id"] = normalized
    payload["subscribed"] = subscribed
    return payload


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


def _pinimg_image_key(url: str) -> str:
    parsed = urllib.parse.urlparse(str(url or "").strip())
    host = (parsed.hostname or "").lower()
    path = urllib.parse.unquote(parsed.path or "").strip("/")
    if "pinimg.com" not in host or not path:
        return str(url or "").strip().split("?", 1)[0].lower()
    parts = [part for part in path.split("/") if part]
    if parts and re.fullmatch(r"(?:\d+x|originals)", parts[0], flags=re.IGNORECASE):
        parts = parts[1:]
    return "pinimg:" + "/".join(parts).lower()


def _text_has_topic(text: str, keywords: List[str]) -> bool:
    haystack = urllib.parse.unquote(str(text or "")).lower()
    return any(str(keyword or "").lower() in haystack for keyword in keywords)


def _is_technical_noise(text: str) -> bool:
    value = str(text or "").strip()
    if not value:
        return True
    return bool(re.fullmatch(r"(?:get|build|set|create|init)[A-Z][A-Za-z0-9_]{2,}", value))


def _matches_topic(item: Dict[str, Any], source: Dict[str, Any]) -> bool:
    moderation = source.get("moderation") if isinstance(source.get("moderation"), dict) else {}
    require_topic = bool(moderation.get("require_topic_match", _DEFAULT_CONFIG["moderation"]["require_topic_match"]))
    if not require_topic:
        return True
    keywords = moderation.get("topic_keywords") or _DEFAULT_CONFIG["moderation"]["topic_keywords"]
    if not isinstance(keywords, list):
        keywords = _DEFAULT_CONFIG["moderation"]["topic_keywords"]
    fields = [
        item.get("title", ""),
        item.get("text", ""),
        item.get("url", ""),
        source.get("title", ""),
        source.get("url", ""),
        source.get("id", ""),
    ]
    return any(_text_has_topic(str(field), keywords) for field in fields)


def _strip_text(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:500]


def _fingerprint(source_id: str, image_url: str, post_url: str, title: str = "") -> str:
    image_key = _pinimg_image_key(image_url)
    key = (image_key or post_url or image_url or f"{source_id}|{title}").strip().lower()
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
        if _is_technical_noise(title):
            title = source_title
        item_id = _fingerprint(source_id, image_url, post_url, title)
        item = {
            "id": item_id,
            "source_id": source_id,
            "source_title": source_title,
            "kind": "pinterest_board",
            "title": title,
            "text": title,
            "url": post_url or str(source.get("url") or ""),
            "image_url": image_url,
            "image_key": _pinimg_image_key(image_url),
            "published_at": "",
            "fetched_at": fetched_at,
            "status": "new",
        }
        if _matches_topic(item, source):
            out.append(item)
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
            image_key = str(item.get("image_key") or _pinimg_image_key(str(item.get("image_url") or "")))
            item["image_key"] = image_key
            duplicate_id = ""
            for existing_id, existing_item in by_id.items():
                if image_key and str(existing_item.get("image_key") or _pinimg_image_key(str(existing_item.get("image_url") or ""))) == image_key:
                    duplicate_id = existing_id
                    break
            target_id = duplicate_id or item_id
            if target_id in by_id:
                item_id = target_id
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
    seen_image_keys: set[str] = set()
    for item in records.get("items", []):
        if not isinstance(item, dict):
            continue
        if str(item.get("status") or "new") not in {"new"}:
            continue
        if not str(item.get("image_url") or ""):
            continue
        image_key = str(item.get("image_key") or _pinimg_image_key(str(item.get("image_url") or "")))
        if image_key in seen_image_keys:
            item["status"] = "skipped"
            item["last_error"] = "duplicate image variant"
            continue
        seen_image_keys.add(image_key)
        yield item


def _caption_generation_config(config: Dict[str, Any], records: Dict[str, Any]) -> Dict[str, Any]:
    rewrite = config.get("rewrite") if isinstance(config.get("rewrite"), dict) else {}
    recent_captions = [
        str(record.get("caption") or "").strip()[:500]
        for record in records.get("items", [])
        if isinstance(record, dict) and str(record.get("caption") or "").strip()
    ][:_RECENT_CAPTION_LIMIT]
    return {
        "required": True,
        "required_subject": "cat",
        "vision_tool": "vlm_query",
        "vision_model": str(rewrite.get("vision_model") or _DEFAULT_VISION_MODEL),
        "vision_prompt": (
            "Сначала напиши ровно SUBJECT: CAT, если на изображении виден кот или кошка, либо "
            "SUBJECT: NOT_CAT, если кошки нет. Затем опиши только то, что действительно видно: "
            "животное, его позу, выражение, предметы, обстановку и читаемый текст. Для CAT предложи "
            "оригинальную информативную подпись на русском с одной лёгкой шуткой. Не повторяй "
            "прежние подписи и не используй заголовок источника как заголовок поста."
        ),
        "min_chars": _MIN_GENERATED_CAPTION_CHARS,
        "max_chars": int(rewrite.get("max_caption_chars") or 700),
        "recent_captions_to_avoid": recent_captions,
    }


def _prepare_next(state_dir: pathlib.Path, *, refresh: bool = True) -> Dict[str, Any]:
    config = _load_config(state_dir)
    refresh_status = _refresh(state_dir) if refresh else {"enabled": False}
    records = _load_records(state_dir)
    moderation = config.get("moderation") if isinstance(config.get("moderation"), dict) else {}
    prepared = _read_json(_state_path(state_dir, _PREPARED_FILE), {})
    if isinstance(prepared, dict) and prepared.get("prepared_id"):
        prepared["caption_generation"] = _caption_generation_config(config, records)
        _atomic_write_json(_state_path(state_dir, _PREPARED_FILE), prepared)
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
            "caption_generation": _caption_generation_config(config, records),
            "rewrite_style": config.get("rewrite", {}),
            "telegram": {
                "chat_ids": _effective_chat_ids(state_dir, config),
                "append_source_link": bool((config.get("telegram") or {}).get("append_source_link", True)),
            },
        }
        _atomic_write_json(_state_path(state_dir, _PREPARED_FILE), prepared_payload)
        _save_records(state_dir, records)
        return {"ok": True, "has_prepared": True, "prepared": prepared_payload, "refresh": refresh_status}
    _save_records(state_dir, records)
    return {"ok": True, "has_prepared": False, "reason": "no unsent image posts available", "failures": failures[:10], "refresh": refresh_status}


def _clamp_caption(caption: str, source_url: str, append_source_link: bool, max_caption_chars: int = 700) -> str:
    limit = min(_TELEGRAM_PHOTO_CAPTION_LIMIT, max(120, int(max_caption_chars or 700)))
    text = str(caption or "").strip()
    source_suffix = f"\n\nSource: {source_url}" if append_source_link and source_url and source_url not in text else ""
    body_limit = limit - len(source_suffix)
    if len(text) > body_limit:
        text = text[: body_limit - 3].rstrip() + "..."
    text += source_suffix
    return text


def _caption_comparison_text(caption: str) -> str:
    lines = []
    for line in str(caption or "").splitlines():
        clean = line.strip()
        if re.match(r"^(?:source|источник)\s*:", clean, flags=re.IGNORECASE):
            continue
        lines.append(clean)
    text = " ".join(lines).lower()
    text = re.sub(r"https?://\S+", " ", text)
    return re.sub(r"[^a-zа-яё0-9]+", " ", text, flags=re.IGNORECASE).strip()


def _validate_generated_caption(caption: str, records: Dict[str, Any], prepared: Dict[str, Any]) -> str:
    normalized = _caption_comparison_text(caption)
    if not normalized:
        return "caption is required; generate it from the prepared image with vlm_query"
    if len(normalized) < _MIN_GENERATED_CAPTION_CHARS:
        return f"caption is too short; provide at least {_MIN_GENERATED_CAPTION_CHARS} informative characters"
    if any(phrase in normalized for phrase in _REJECTED_CAPTION_PHRASES):
        return "caption repeats a retired static template; analyze the current image and generate a new caption"
    first_line = next((line.strip().lower() for line in str(caption).splitlines() if line.strip()), "")
    source_titles = {
        str(prepared.get("title") or "").strip().lower(),
        str(prepared.get("source_title") or "").strip().lower(),
        "cat memes",
        "hello memes",
    }
    if first_line and first_line in source_titles:
        return "caption starts with a generic source title; begin with a concrete description of the current image"

    recent = [
        _caption_comparison_text(str(item.get("caption") or ""))
        for item in records.get("items", [])
        if isinstance(item, dict) and str(item.get("caption") or "").strip()
    ][:_RECENT_CAPTION_LIMIT]
    for previous in recent:
        if not previous:
            continue
        similarity = difflib.SequenceMatcher(None, normalized, previous).ratio()
        if similarity >= _CAPTION_SIMILARITY_LIMIT:
            return (
                f"caption is too similar to a recent broadcast ({similarity:.0%}); "
                "analyze the current image and generate substantially different wording"
            )
    return ""


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
    chat_ids = _effective_chat_ids(state_dir, config)
    if not chat_ids:
        return {"ok": False, "error": "no active Telegram broadcast subscribers"}
    token = str(telegram_token or "").strip()
    if not token:
        return {"ok": False, "error": "TELEGRAM_BOT_TOKEN is not configured"}
    prepared = _read_json(_state_path(state_dir, _PREPARED_FILE), {})
    if not isinstance(prepared, dict) or not prepared.get("prepared_id"):
        return {"ok": False, "error": "no prepared post"}
    records = _load_records(state_dir)
    caption_error = _validate_generated_caption(caption, records, prepared)
    if caption_error:
        return {
            "ok": False,
            "error": caption_error,
            "prepared_id": str(prepared.get("prepared_id") or ""),
            "retryable": True,
        }
    requested_prepared_id = str(prepared_id or "").strip()
    current_prepared_id = str(prepared.get("prepared_id") or "")
    stale_prepared_id = bool(requested_prepared_id and requested_prepared_id != current_prepared_id)
    image_path = pathlib.Path(str(prepared.get("image_path") or ""))
    if not image_path.is_file():
        return {"ok": False, "error": f"prepared image is missing: {image_path}"}
    final_caption = _clamp_caption(
        caption,
        str(prepared.get("source_url") or ""),
        bool(telegram.get("append_source_link", True)),
        int((config.get("rewrite") or {}).get("max_caption_chars") or 700),
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
    payload = {"ok": ok_count == len(chat_ids), "sent_chats": ok_count, "requested_chats": len(chat_ids), "results": results}
    if stale_prepared_id:
        payload["warning"] = "requested prepared_id was stale; sent current prepared post"
        payload["requested_prepared_id"] = requested_prepared_id
        payload["current_prepared_id"] = current_prepared_id
    return payload


def _skip_prepared(state_dir: pathlib.Path, *, prepared_id: str = "", reason: str = "") -> Dict[str, Any]:
    prepared = _read_json(_state_path(state_dir, _PREPARED_FILE), {})
    current_id = str(prepared.get("prepared_id") or "") if isinstance(prepared, dict) else ""
    if not current_id:
        return {"ok": False, "error": "no prepared post"}
    requested_id = str(prepared_id or "").strip()
    if requested_id and requested_id != current_id:
        return {"ok": False, "error": "prepared_id does not match current prepared post"}
    records = _load_records(state_dir)
    skip_reason = str(reason or "image does not match required subject").strip()[:500]
    for item in records.get("items", []):
        if isinstance(item, dict) and str(item.get("id") or "") == current_id:
            item["status"] = "skipped"
            item["last_error"] = skip_reason
            item["skipped_at"] = _utc_now()
            break
    _save_records(state_dir, records)
    _atomic_write_json(_state_path(state_dir, _PREPARED_FILE), {})
    return {"ok": True, "skipped_id": current_id, "reason": skip_reason}


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
        "subscriptions": _subscription_status(state_dir),
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

    async def route_subscribe(request: Request) -> JSONResponse:
        body = await _json_body(request)
        chat_id = body.get("chat_id") or request.query_params.get("chat_id")
        payload = _set_subscription(state_dir, str(chat_id or ""), subscribed=True)
        return JSONResponse(payload, status_code=200 if payload.get("ok") else 400)

    async def route_unsubscribe(request: Request) -> JSONResponse:
        body = await _json_body(request)
        chat_id = body.get("chat_id") or request.query_params.get("chat_id")
        payload = _set_subscription(state_dir, str(chat_id or ""), subscribed=False)
        return JSONResponse(payload, status_code=200 if payload.get("ok") else 400)

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
            "Prepare one unsent image-required post for LLM caption rewrite. After this call, you MUST call "
            "the core vlm_query tool with prepared.image_url and prepared.caption_generation.vision_prompt. "
            "Use that visual analysis to write a new Russian caption, then call send_prepared. Avoid every "
            "caption in prepared.caption_generation.recent_captions_to_avoid."
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
        description=(
            "Send the currently prepared image post to configured Telegram chats using TELEGRAM_BOT_TOKEN. "
            "Pass a non-empty Russian caption generated after vlm_query analyzed prepared.image_url. Empty, "
            "retired-template, and recently repeated captions are rejected while the post remains prepared."
        ),
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
        "skip_prepared",
        lambda prepared_id="", reason="": json.dumps(
            _skip_prepared(state_dir, prepared_id=str(prepared_id or ""), reason=str(reason or "")),
            ensure_ascii=False,
        ),
        description=(
            "Skip and clear the current prepared post when vlm_query reports SUBJECT: NOT_CAT or the image "
            "otherwise violates the configured topic. Pass the prepared_id and the visual-analysis reason."
        ),
        schema={
            "type": "object",
            "properties": {
                "prepared_id": {"type": "string"},
                "reason": {"type": "string"},
            },
        },
    )
    api.register_tool(
        "subscribe",
        lambda chat_id="": json.dumps(
            _set_subscription(state_dir, str(chat_id or ""), subscribed=True),
            ensure_ascii=False,
        ),
        description=(
            "Subscribe a Telegram chat_id to post_broadcast. The chat_id is stored only in the skill state "
            "directory and will be included in future broadcasts unless it unsubscribes."
        ),
        schema={"type": "object", "properties": {"chat_id": {"type": "string"}}},
        timeout_sec=10,
    )
    api.register_tool(
        "unsubscribe",
        lambda chat_id="": json.dumps(
            _set_subscription(state_dir, str(chat_id or ""), subscribed=False),
            ensure_ascii=False,
        ),
        description=(
            "Opt a Telegram chat_id out of post_broadcast. This suppresses delivery even if the chat_id is "
            "still present in the configured telegram.chat_ids list."
        ),
        schema={"type": "object", "properties": {"chat_id": {"type": "string"}}},
        timeout_sec=10,
    )
    api.register_tool(
        "list_subscribers",
        lambda: json.dumps(_subscription_status(state_dir), ensure_ascii=False),
        description="Return configured, subscribed, unsubscribed, and active post_broadcast Telegram chat_ids.",
        schema={"type": "object", "properties": {}},
        timeout_sec=10,
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
    api.register_route("subscribe", route_subscribe, methods=("POST", "GET"))
    api.register_route("unsubscribe", route_unsubscribe, methods=("POST", "GET"))
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
