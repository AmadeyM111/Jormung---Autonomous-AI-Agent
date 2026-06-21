"""Research/news collection extension for RSS, Atom, and public Telegram pages."""

from __future__ import annotations

import datetime as _dt
import email.utils
import hashlib
import html
import ipaddress
import json
import os
import pathlib
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any, Dict, Iterable, List

try:
    from starlette.requests import Request
    from starlette.responses import JSONResponse
except ModuleNotFoundError:  # pragma: no cover - lets offline parsers import the skill without web deps.
    Request = Any  # type: ignore[assignment]

    class JSONResponse:  # type: ignore[no-redef]
        def __init__(self, payload: Any, status_code: int = 200):
            self.payload = payload
            self.status_code = status_code


_TIMEOUT_SEC = 15
_MAX_RESPONSE_BYTES = 3 * 1024 * 1024
_MAX_LAST30DAYS_OUTPUT_BYTES = 2 * 1024 * 1024
_USER_AGENT = "Ouroboros-ResearchDigest/0.1"
_CONFIG_FILE = "config.json"
_RECORDS_FILE = "records.json"
_MAX_STORED_ITEMS = 600
_DEFAULT_MAX_PER_SOURCE = 2

_DEFAULT_CONFIG: Dict[str, Any] = {
    "schema_version": 1,
    "topics": {
        "ai": [
            "ai", "artificial intelligence", "generative", "llm", "gpt", "claude",
            "gemini", "openai", "anthropic", "inference", "fine-tuning", "rag",
            "eval", "benchmark", "ии", "нейросеть", "нейросети",
        ],
        "agentic_systems": [
            "agent", "agents", "agentic", "multi-agent", "tool use", "workflow",
            "autonomous", "planning", "агент", "агентск",
        ],
        "ml_business": [
            "mlops", "production ml", "enterprise", "case study", "deployment",
            "implementation", "adoption", "roi", "business", "platform",
            "внедрение", "бизнес", "продакшен",
        ],
        "engineering": [
            "engineering", "architecture", "distributed", "systems", "infra",
            "latency", "reliability", "observability", "инженер", "архитектур",
        ],
        "research": [
            "paper", "research", "arxiv", "preprint", "dataset", "method",
            "study", "survey", "исследован", "статья",
        ],
    },
    "sources": [
        {"id": "arxiv_cs_ai", "kind": "rss", "url": "https://export.arxiv.org/rss/cs.AI", "title": "arXiv cs.AI"},
        {"id": "arxiv_cs_lg", "kind": "rss", "url": "https://export.arxiv.org/rss/cs.LG", "title": "arXiv cs.LG"},
        {
            "id": "aws_ml_blog",
            "kind": "rss",
            "url": "https://aws.amazon.com/blogs/machine-learning/feed/",
            "title": "AWS ML Blog",
        },
        {"id": "google_research", "kind": "rss", "url": "https://research.google/blog/rss/", "title": "Google Research"},
        {
            "id": "mit_ai",
            "kind": "rss",
            "url": "https://news.mit.edu/rss/topic/artificial-intelligence2",
            "title": "MIT AI News",
        },
    ],
}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        raise urllib.error.HTTPError(req.full_url, code, "redirect refused", headers, fp)


_OPENER = urllib.request.build_opener(_NoRedirect)


def _utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_dt(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parsed = email.utils.parsedate_to_datetime(text)
    except Exception:
        try:
            parsed = _dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
        except Exception:
            return text[:80]
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_dt.timezone.utc)
    return parsed.astimezone(_dt.timezone.utc).isoformat().replace("+00:00", "Z")


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


def _dt_value(value: Any) -> _dt.datetime:
    text = str(value or "").strip()
    if not text:
        return _dt.datetime.fromtimestamp(0, tz=_dt.timezone.utc)
    try:
        parsed = _dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=_dt.timezone.utc)
        return parsed.astimezone(_dt.timezone.utc)
    except Exception:
        return _dt.datetime.fromtimestamp(0, tz=_dt.timezone.utc)


def _strip_html(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", text)
    text = re.sub(r"(?s)<br\s*/?>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s+", "\n", text)
    return text.strip()


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
        if isinstance(saved.get("topics"), dict):
            config["topics"].update(saved["topics"])
        if isinstance(saved.get("sources"), list):
            config["sources"] = saved["sources"]
    return config


def _save_config(state_dir: pathlib.Path, config: Dict[str, Any]) -> None:
    clean = {
        "schema_version": 1,
        "topics": config.get("topics") if isinstance(config.get("topics"), dict) else _DEFAULT_CONFIG["topics"],
        "sources": [s for s in config.get("sources", []) if isinstance(s, dict)],
    }
    _atomic_write_json(_state_path(state_dir, _CONFIG_FILE), clean)


def _load_records(state_dir: pathlib.Path) -> Dict[str, Any]:
    data = _read_json(_state_path(state_dir, _RECORDS_FILE), {"schema_version": 1, "items": []})
    if not isinstance(data, dict):
        data = {"schema_version": 1, "items": []}
    if not isinstance(data.get("items"), list):
        data["items"] = []
    return data


def _save_records(state_dir: pathlib.Path, records: Dict[str, Any]) -> None:
    items = [item for item in records.get("items", []) if isinstance(item, dict)]
    items.sort(key=lambda x: (_dt_value(x.get("published_at") or x.get("fetched_at")), int(x.get("score") or 0)), reverse=True)
    records["schema_version"] = 1
    records["items"] = items[:_MAX_STORED_ITEMS]
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
        raise ValueError("source URL must use http or https")
    if _is_blocked_host(parsed.hostname or ""):
        raise ValueError("source URL host is blocked")
    return urllib.parse.urlunparse(parsed)


def _fetch_text(url: str) -> str:
    safe_url = _validate_url(url)
    request = urllib.request.Request(
        safe_url,
        headers={"User-Agent": _USER_AGENT, "Accept": "application/rss+xml, application/atom+xml, text/html, */*"},
    )
    with _OPENER.open(request, timeout=_TIMEOUT_SEC) as response:
        raw = response.read(_MAX_RESPONSE_BYTES + 1)
    if len(raw) > _MAX_RESPONSE_BYTES:
        raise ValueError("upstream response is too large")
    encoding = response.headers.get_content_charset() or "utf-8"
    return raw.decode(encoding, errors="replace")


def _first_text(parent: ET.Element, names: Iterable[str]) -> str:
    wanted = set(names)
    for child in parent.iter():
        name = child.tag.rsplit("}", 1)[-1].lower()
        if name in wanted and child.text:
            return _strip_html(child.text)
    return ""


def _entry_link(entry: ET.Element) -> str:
    for child in entry:
        name = child.tag.rsplit("}", 1)[-1].lower()
        if name == "link":
            href = child.attrib.get("href")
            if href:
                return str(href).strip()
            if child.text:
                return str(child.text).strip()
    return ""


def _parse_rss_atom(raw: str, source: Dict[str, Any], fetched_at: str) -> List[Dict[str, Any]]:
    root = ET.fromstring(raw)
    root_name = root.tag.rsplit("}", 1)[-1].lower()
    entries: list[ET.Element] = []
    if root_name == "rss":
        channel = root.find("channel")
        entries = list(channel.findall("item")) if channel is not None else []
    elif root_name in {"feed", "rdf"}:
        entries = [el for el in root.iter() if el.tag.rsplit("}", 1)[-1].lower() in {"entry", "item"}]
    else:
        entries = [el for el in root.iter() if el.tag.rsplit("}", 1)[-1].lower() in {"entry", "item"}]

    out: List[Dict[str, Any]] = []
    for entry in entries:
        title = _first_text(entry, ("title",))
        link = _entry_link(entry)
        summary = _first_text(entry, ("description", "summary", "content"))
        published = _parse_dt(_first_text(entry, ("pubdate", "published", "updated", "date")))
        if not title and summary:
            title = summary[:100]
        if not title and not link:
            continue
        out.append(_make_item(source, title, link, summary, published, fetched_at))
    return out


def _parse_telegram_public(raw: str, source: Dict[str, Any], fetched_at: str) -> List[Dict[str, Any]]:
    blocks = re.findall(r'(?is)<div class="tgme_widget_message[^"]*".*?</div>\s*</div>', raw)
    if not blocks:
        blocks = re.findall(r'(?is)<div class="tgme_widget_message[^"]*".*?(?=<div class="tgme_widget_message|\Z)', raw)
    out: List[Dict[str, Any]] = []
    for block in blocks[-40:]:
        text_match = re.search(r'(?is)<div class="tgme_widget_message_text[^"]*".*?>(.*?)</div>', block)
        if not text_match:
            continue
        text = _strip_html(text_match.group(1))
        if not text:
            continue
        date_match = re.search(r'<time[^>]+datetime="([^"]+)"', block)
        post_match = re.search(r'data-post="([^"]+)"', block)
        post = html.unescape(post_match.group(1)) if post_match else ""
        link = f"https://t.me/{post}" if post else str(source.get("url") or "")
        title = text.splitlines()[0][:140]
        out.append(_make_item(source, title, link, text[:800], _parse_dt(date_match.group(1) if date_match else ""), fetched_at))
    return out


def _strip_ansi(value: Any) -> str:
    return re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", str(value or ""))


def _first_markdown_or_raw_url(text: str) -> str:
    md = re.search(r"\[[^\]]+\]\((https?://[^)\s]+)\)", text)
    if md:
        return md.group(1).strip()
    raw = re.search(r"https?://[^\s)>\]]+", text)
    return raw.group(0).strip().rstrip(".,;") if raw else ""


def _last30days_summary(raw: str) -> str:
    text = _strip_ansi(raw)
    text = re.sub(r"(?s)<!--.*?-->", " ", text)
    lines = [line.strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    if lines and lines[0].lower().startswith("last30days "):
        lines = lines[1:]
    joined = "\n".join(lines)
    joined = re.split(r"(?im)^key patterns from the research:\s*$", joined, maxsplit=1)[0]
    joined = re.split(r"(?m)^---\s*$", joined, maxsplit=1)[0]
    joined = re.sub(r"(?im)^what i learned:\s*", "", joined).strip()
    joined = re.sub(r"\s+", " ", joined).strip()
    return joined[:1200]


def _parse_last30days_output(raw: str, source: Dict[str, Any], fetched_at: str) -> List[Dict[str, Any]]:
    summary = _last30days_summary(raw)
    if not summary:
        return []
    topic = str(source.get("topic") or source.get("query") or "").strip()
    title = str(source.get("title") or "").strip()
    if not title:
        title = f"Last30Days: {topic}" if topic else "Last30Days report"
    link = str(source.get("url") or "").strip() or _first_markdown_or_raw_url(raw)
    item = _make_item(source, title, link, summary, fetched_at, fetched_at)
    item["kind"] = "last30days"
    return [item]


def _last30days_engine_candidates(source: Dict[str, Any]) -> List[pathlib.Path]:
    candidates: List[pathlib.Path] = []
    for raw in (
        source.get("engine_path"),
        os.environ.get("LAST30DAYS_ENGINE_PATH"),
    ):
        text = str(raw or "").strip()
        if text:
            candidates.append(pathlib.Path(text).expanduser())
    for raw in (
        source.get("skill_dir"),
        os.environ.get("LAST30DAYS_SKILL_DIR"),
    ):
        text = str(raw or "").strip()
        if text:
            candidates.append(pathlib.Path(text).expanduser() / "scripts" / "last30days.py")
    home = pathlib.Path.home()
    candidates.extend([
        home / ".codex" / "skills" / "last30days" / "scripts" / "last30days.py",
        home / ".agents" / "skills" / "last30days" / "scripts" / "last30days.py",
        home / ".openclaw" / "skills" / "last30days" / "scripts" / "last30days.py",
    ])
    candidates.extend(home.glob(".claude/plugins/cache/last30days-skill/last30days/*/skills/last30days/scripts/last30days.py"))
    candidates.extend(home.glob(".claude/plugins/cache/last30days-skill/last30days/*/scripts/last30days.py"))
    out: List[pathlib.Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        out.append(candidate)
    return out


def _resolve_last30days_engine(source: Dict[str, Any]) -> pathlib.Path:
    for candidate in _last30days_engine_candidates(source):
        if candidate.name != "last30days.py":
            continue
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        "last30days engine not found; set LAST30DAYS_SKILL_DIR, LAST30DAYS_ENGINE_PATH, "
        "or source skill_dir/engine_path"
    )


def _collect_last30days(source: Dict[str, Any], fetched_at: str, state_dir: pathlib.Path) -> List[Dict[str, Any]]:
    topic = str(source.get("topic") or source.get("query") or "").strip()
    if not topic:
        raise ValueError("last30days source requires topic")
    engine = _resolve_last30days_engine(source)
    try:
        timeout_sec = int(source.get("timeout_sec") or 180)
    except (TypeError, ValueError):
        timeout_sec = 180
    timeout_sec = max(10, min(timeout_sec, 600))
    env = dict(os.environ)
    env.setdefault("LAST30DAYS_MEMORY_DIR", str(state_dir / "last30days"))
    cmd = [sys.executable, str(engine), topic, "--emit=compact"]
    completed = subprocess.run(
        cmd,
        cwd=str(engine.parent.parent if engine.parent.name == "scripts" else engine.parent),
        env=env,
        text=True,
        capture_output=True,
        timeout=timeout_sec,
        check=False,
    )
    stdout = str(completed.stdout or "")
    stderr = str(completed.stderr or "")
    if len(stdout.encode("utf-8", errors="replace")) > _MAX_LAST30DAYS_OUTPUT_BYTES:
        raise ValueError("last30days output is too large")
    if completed.returncode != 0:
        detail = (stderr or stdout or f"exit {completed.returncode}").strip()
        raise RuntimeError(f"last30days failed: {detail[:500]}")
    return _parse_last30days_output(stdout, source, fetched_at)


def _make_item(source: Dict[str, Any], title: str, link: str, summary: str, published: str, fetched_at: str) -> Dict[str, Any]:
    source_id = str(source.get("id") or source.get("title") or source.get("url") or "source").strip()
    return {
        "id": "",
        "source_id": source_id,
        "source_title": str(source.get("title") or source_id).strip(),
        "kind": str(source.get("kind") or "rss").strip(),
        "title": _strip_html(title)[:300],
        "url": str(link or "").strip(),
        "summary": _strip_html(summary)[:1200],
        "published_at": published,
        "fetched_at": fetched_at,
    }


def _fingerprint(item: Dict[str, Any]) -> str:
    key = str(item.get("url") or "").strip().lower()
    if not key:
        key = "|".join(str(item.get(k) or "").strip().lower() for k in ("source_id", "title", "published_at"))
    return hashlib.sha256(key.encode("utf-8", errors="replace")).hexdigest()[:24]


def _score_item(item: Dict[str, Any], topics: Dict[str, Any]) -> Dict[str, Any]:
    text = f"{item.get('title', '')}\n{item.get('summary', '')}".lower()
    matches: Dict[str, List[str]] = {}
    score = 0
    for topic, words in topics.items():
        if not isinstance(words, list):
            continue
        hits = []
        for word in words:
            token = str(word or "").strip().lower()
            if token and token in text:
                hits.append(token)
        if hits:
            unique = sorted(set(hits))[:8]
            matches[str(topic)] = unique
            score += 2 + min(len(unique), 5)
    item["topic_matches"] = matches
    item["score"] = score
    return item


def _collect_source(source: Dict[str, Any], topics: Dict[str, Any], limit: int, state_dir: pathlib.Path | None = None) -> Dict[str, Any]:
    fetched_at = _utc_now()
    kind = str(source.get("kind") or "rss").strip().lower()
    if kind == "last30days":
        parsed = _collect_last30days(source, fetched_at, state_dir or pathlib.Path.cwd())
        items = [_score_item(item, topics) for item in parsed]
        return {"source_id": source.get("id"), "items": items[: max(1, min(limit, 100))], "fetched_at": fetched_at}
    url = str(source.get("url") or "").strip()
    if kind == "telegram_public" and not url:
        channel = str(source.get("channel") or "").strip().lstrip("@")
        url = f"https://t.me/s/{urllib.parse.quote(channel)}"
    raw = _fetch_text(url)
    if kind == "telegram_public":
        parsed = _parse_telegram_public(raw, source, fetched_at)
    else:
        parsed = _parse_rss_atom(raw, source, fetched_at)
    items = [_score_item(item, topics) for item in parsed]
    return {"source_id": source.get("id"), "items": items[: max(1, min(limit, 100))], "fetched_at": fetched_at}


def _compact_item(item: Dict[str, Any]) -> Dict[str, Any]:
    """Small LLM-facing projection; full records stay in records.json."""
    topics = sorted((item.get("topic_matches") or {}).keys())
    return {
        "title": str(item.get("title") or "")[:220],
        "url": str(item.get("url") or "")[:500],
        "source": str(item.get("source_title") or item.get("source_id") or "")[:120],
        "published_at": str(item.get("published_at") or item.get("fetched_at") or "")[:80],
        "score": int(item.get("score") or 0),
        "topics": topics[:8],
        "summary": _clean_summary(item.get("summary"))[:260],
    }


def _source_key(item: Dict[str, Any]) -> str:
    return str(item.get("source_id") or item.get("source_title") or "source").strip() or "source"


def _select_diverse_items(items: List[Dict[str, Any]], *, limit: int, max_per_source: int) -> List[Dict[str, Any]]:
    target = max(1, min(int(limit or 12), 50))
    cap = max(1, int(max_per_source or _DEFAULT_MAX_PER_SOURCE))
    selected: List[Dict[str, Any]] = []
    selected_ids: set[str] = set()
    counts: Dict[str, int] = {}

    # First pass: one best item per source, preserving score/date ordering.
    for item in items:
        if len(selected) >= target:
            break
        src = _source_key(item)
        if counts.get(src, 0) > 0:
            continue
        item_id = str(item.get("id") or _fingerprint(item))
        selected.append(item)
        selected_ids.add(item_id)
        counts[src] = 1

    # Second pass: fill remaining slots, capped per source where possible.
    for item in items:
        if len(selected) >= target:
            break
        item_id = str(item.get("id") or _fingerprint(item))
        if item_id in selected_ids:
            continue
        src = _source_key(item)
        if counts.get(src, 0) >= cap:
            continue
        selected.append(item)
        selected_ids.add(item_id)
        counts[src] = counts.get(src, 0) + 1

    # Last resort: if only one/few sources exist, fill the requested limit.
    for item in items:
        if len(selected) >= target:
            break
        item_id = str(item.get("id") or _fingerprint(item))
        if item_id in selected_ids:
            continue
        selected.append(item)
        selected_ids.add(item_id)

    return selected


def _refresh(
    state_dir: pathlib.Path,
    *,
    limit_per_source: int = 20,
    source_id: str = "",
    include_top: bool = True,
) -> Dict[str, Any]:
    config = _load_config(state_dir)
    topics = config.get("topics") if isinstance(config.get("topics"), dict) else {}
    sources = [s for s in config.get("sources", []) if isinstance(s, dict)]
    if source_id:
        sources = [s for s in sources if str(s.get("id") or "") == source_id]
    records = _load_records(state_dir)
    by_id = {str(item.get("id") or _fingerprint(item)): dict(item) for item in records.get("items", []) if isinstance(item, dict)}
    errors: List[Dict[str, str]] = []
    fetched_sources: List[str] = []
    fetched = 0
    new_count = 0
    updated_count = 0
    for source in sources:
        sid = str(source.get("id") or source.get("url") or "source")
        try:
            result = _collect_source(source, topics, int(limit_per_source or 20), state_dir)
        except Exception as exc:
            errors.append({"source_id": sid, "error": f"{type(exc).__name__}: {exc}"})
            continue
        fetched += 1
        fetched_sources.append(sid)
        for item in result["items"]:
            item_id = _fingerprint(item)
            item["id"] = item_id
            if item_id in by_id:
                by_id[item_id].update({k: v for k, v in item.items() if v not in ("", None, {}, [])})
                updated_count += 1
            else:
                by_id[item_id] = item
                new_count += 1
    records["items"] = list(by_id.values())
    _save_records(state_dir, records)
    status = {
        "ok": True,
        "sources_requested": len(sources),
        "sources_fetched": fetched,
        "fetched_source_ids": fetched_sources,
        "new_items": new_count,
        "updated_items": updated_count,
        "errors": errors,
    }
    if include_top:
        status["top"] = _digest_items(state_dir, hours=72, limit=8, min_score=1, compact=True)["items"]
    return status


def _digest_items(
    state_dir: pathlib.Path,
    *,
    hours: int = 48,
    limit: int = 12,
    min_score: int = 1,
    compact: bool = False,
    max_per_source: int = 0,
) -> Dict[str, Any]:
    records = _load_records(state_dir)
    cutoff = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(hours=max(1, int(hours or 48)))
    items = []
    for item in records.get("items", []):
        if not isinstance(item, dict):
            continue
        when = _dt_value(item.get("published_at") or item.get("fetched_at"))
        if when < cutoff:
            continue
        if int(item.get("score") or 0) < int(min_score or 0):
            continue
        items.append(item)
    items.sort(key=lambda x: (int(x.get("score") or 0), _dt_value(x.get("published_at") or x.get("fetched_at"))), reverse=True)
    if int(max_per_source or 0) > 0:
        selected = _select_diverse_items(items, limit=int(limit or 12), max_per_source=int(max_per_source))
    else:
        selected = items[: max(1, min(int(limit or 12), 50))]
    return {
        "ok": True,
        "hours": hours,
        "limit": limit,
        "items": [_compact_item(item) for item in selected] if compact else selected,
        "markdown": _to_markdown(selected),
    }


def _prepare_digest(
    state_dir: pathlib.Path,
    *,
    hours: int = 168,
    limit: int = 6,
    min_score: int = 1,
    refresh: bool = True,
    limit_per_source: int = 30,
    max_per_source: int = _DEFAULT_MAX_PER_SOURCE,
) -> Dict[str, Any]:
    """Refresh if requested and return a Telegram-ready direct final response."""
    refresh_status: Dict[str, Any] | None = None
    if refresh:
        refresh_status = _refresh(
            state_dir,
            limit_per_source=limit_per_source,
            include_top=False,
        )
    digest = _digest_items(
        state_dir,
        hours=hours,
        limit=limit,
        min_score=min_score,
        compact=True,
        max_per_source=max_per_source,
    )
    lines = [str(digest.get("markdown") or "").strip()]
    errors = (refresh_status or {}).get("errors") or []
    fetched_source_ids = (refresh_status or {}).get("fetched_source_ids") or []
    if refresh_status is not None:
        lines.append("")
        lines.append(
            "Updated sources: "
            + (", ".join(str(src) for src in fetched_source_ids[:8]) if fetched_source_ids else "none")
        )
    if errors:
        lines.append("")
        lines.append("Source errors:")
        for err in errors[:8]:
            sid = str(err.get("source_id") or "source")
            detail = str(err.get("error") or "unknown error")
            lines.append(f"- {sid}: {detail[:220]}")
        if len(errors) > 8:
            lines.append(f"- ... and {len(errors) - 8} more")
    final_response = "\n".join(part for part in lines if part is not None).strip()
    return {
        "ok": True,
        "final_response_mode": "direct",
        "final_response": final_response,
        "refresh": {
            "enabled": bool(refresh),
            "sources_requested": (refresh_status or {}).get("sources_requested", 0),
            "sources_fetched": (refresh_status or {}).get("sources_fetched", 0),
            "fetched_source_ids": fetched_source_ids[:20],
            "new_items": (refresh_status or {}).get("new_items", 0),
            "updated_items": (refresh_status or {}).get("updated_items", 0),
            "errors": errors[:8],
            "errors_omitted": max(0, len(errors) - 8),
        },
        "digest": {
            "hours": digest.get("hours"),
            "limit": digest.get("limit"),
            "max_per_source": max_per_source,
            "items": digest.get("items", []),
        },
    }


def _clean_summary(value: Any, *, limit: int = 360) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        return ""
    text = re.sub(r"(?i)\barxiv:\d{4}\.\d+(?:v\d+)?\s*", "", text).strip()
    text = re.sub(r"(?i)\bannounce type:\s*\w+\s*", "", text).strip()
    text = re.sub(r"(?i)^abstract:\s*", "", text).strip()
    text = re.sub(r"(?i)\s+abstract:\s*", " ", text).strip()
    if len(text) <= limit:
        return text
    cut = text[:limit].rstrip()
    sentence_end = max(cut.rfind("."), cut.rfind("!"), cut.rfind("?"))
    if sentence_end >= 120:
        return cut[: sentence_end + 1]
    return cut.rstrip(" ,;:") + "..."


def _format_date(value: Any) -> str:
    dt_value = _dt_value(value)
    if dt_value.timestamp() <= 0:
        return ""
    return dt_value.strftime("%Y-%m-%d UTC")


def _topic_label(topic: str) -> str:
    labels = {
        "agentic_systems": "agents",
        "ml_business": "ML business",
        "engineering": "engineering",
        "research": "research",
        "ai": "AI",
    }
    return labels.get(topic, topic.replace("_", " "))


def _digest_tldr(items: List[Dict[str, Any]]) -> List[str]:
    if not items:
        return []
    topic_counts: Dict[str, int] = {}
    source_counts: Dict[str, int] = {}
    for item in items:
        for topic in (item.get("topic_matches") or {}).keys():
            topic_counts[str(topic)] = topic_counts.get(str(topic), 0) + 1
        source = str(item.get("source_title") or item.get("source_id") or "").strip()
        if source:
            source_counts[source] = source_counts.get(source, 0) + 1
    top_topics = sorted(topic_counts.items(), key=lambda pair: (-pair[1], pair[0]))[:3]
    top_sources = sorted(source_counts.items(), key=lambda pair: (-pair[1], pair[0]))[:3]
    out = [
        f"{len(items)} selected items from {len(source_counts) or 1} source(s).",
    ]
    if top_topics:
        out.append("Main themes: " + ", ".join(f"{_topic_label(k)} x{v}" for k, v in top_topics) + ".")
    if top_sources:
        out.append("Source mix: " + ", ".join(f"{name} x{count}" for name, count in top_sources) + ".")
    return out


def _to_markdown(items: List[Dict[str, Any]]) -> str:
    if not items:
        return "No matching items yet. Run refresh or add more sources."
    lines = ["AI/ML research digest", ""]
    lines.append("TL;DR")
    for bullet in _digest_tldr(items):
        lines.append(f"- {bullet}")
    lines.append("")
    lines.append("Items")
    for idx, item in enumerate(items, 1):
        title = str(item.get("title") or "Untitled").strip()
        url = str(item.get("url") or "").strip()
        source = str(item.get("source_title") or item.get("source_id") or "").strip()
        topics = ", ".join(_topic_label(topic) for topic in sorted((item.get("topic_matches") or {}).keys()))
        published = _format_date(item.get("published_at") or item.get("fetched_at"))
        score = int(item.get("score") or 0)
        lines.append("")
        lines.append(f"{idx}. {title}")
        meta = " | ".join(part for part in (source, published, f"score {score}") if part)
        if meta:
            lines.append(f"Source: {meta}")
        summary = _clean_summary(item.get("summary"))
        if summary:
            lines.append(f"Why it matters: {summary}")
        if topics:
            lines.append(f"Topics: {topics}")
        if url:
            lines.append(f"Link: {url}")
    return "\n".join(lines).strip()


def _list_sources(state_dir: pathlib.Path) -> Dict[str, Any]:
    config = _load_config(state_dir)
    return {"ok": True, "sources": config.get("sources", []), "topics": config.get("topics", {})}


def _upsert_source(state_dir: pathlib.Path, payload: Dict[str, Any]) -> Dict[str, Any]:
    config = _load_config(state_dir)
    sources = [dict(s) for s in config.get("sources", []) if isinstance(s, dict)]
    action = str(payload.get("action") or "upsert").strip().lower()
    source_id = str(payload.get("id") or "").strip()
    if not source_id:
        return {"ok": False, "error": "id is required"}
    if action == "remove":
        config["sources"] = [s for s in sources if str(s.get("id") or "") != source_id]
        _save_config(state_dir, config)
        return {"ok": True, "removed": source_id, "sources": config["sources"]}
    kind = str(payload.get("kind") or "rss").strip().lower()
    if kind not in {"rss", "atom", "telegram_public", "last30days"}:
        return {"ok": False, "error": "kind must be rss, atom, telegram_public, or last30days"}
    source = {
        "id": source_id,
        "kind": kind,
        "title": str(payload.get("title") or source_id).strip(),
    }
    if kind == "last30days":
        topic = str(payload.get("topic") or payload.get("query") or "").strip()
        if not topic:
            return {"ok": False, "error": "last30days source requires topic"}
        source["topic"] = topic
        for key in ("engine_path", "skill_dir", "timeout_sec"):
            value = payload.get(key)
            if value not in ("", None):
                source[key] = str(value).strip()
    elif kind == "telegram_public":
        channel = str(payload.get("channel") or "").strip().lstrip("@")
        url = str(payload.get("url") or "").strip()
        if not channel and not url:
            return {"ok": False, "error": "telegram_public source requires channel or url"}
        if channel:
            source["channel"] = channel
            source["url"] = f"https://t.me/s/{urllib.parse.quote(channel)}"
        else:
            source["url"] = _validate_url(url)
    else:
        source["url"] = _validate_url(str(payload.get("url") or ""))
    sources = [s for s in sources if str(s.get("id") or "") != source_id]
    sources.append(source)
    config["sources"] = sources
    _save_config(state_dir, config)
    return {"ok": True, "source": source, "sources": sources}


def _tool_refresh(*, state_dir: pathlib.Path, limit_per_source: int = 20, source_id: str = "") -> str:
    return json.dumps(
        _refresh(state_dir, limit_per_source=limit_per_source, source_id=source_id),
        ensure_ascii=False,
        indent=2,
    )


def _tool_digest(*, state_dir: pathlib.Path, hours: int = 48, limit: int = 12, min_score: int = 1) -> str:
    return json.dumps(
        _digest_items(state_dir, hours=hours, limit=limit, min_score=min_score, compact=True),
        ensure_ascii=False,
        indent=2,
    )


def _tool_prepare_digest(
    *,
    state_dir: pathlib.Path,
    hours: int = 168,
    limit: int = 6,
    min_score: int = 1,
    refresh: bool = True,
    limit_per_source: int = 30,
    max_per_source: int = _DEFAULT_MAX_PER_SOURCE,
) -> str:
    return json.dumps(
        _prepare_digest(
            state_dir,
            hours=hours,
            limit=limit,
            min_score=min_score,
            refresh=_bool_arg(refresh, default=True),
            limit_per_source=limit_per_source,
            max_per_source=max_per_source,
        ),
        ensure_ascii=False,
        indent=2,
    )


def _tool_sources(*, state_dir: pathlib.Path) -> str:
    return json.dumps(_list_sources(state_dir), ensure_ascii=False, indent=2)


def _tool_source_upsert(
    *,
    state_dir: pathlib.Path,
    id: str = "",
    kind: str = "rss",
    url: str = "",
    title: str = "",
    channel: str = "",
    topic: str = "",
    engine_path: str = "",
    skill_dir: str = "",
    action: str = "upsert",
) -> str:
    return json.dumps(
        _upsert_source(
            state_dir,
            {
                "id": id,
                "kind": kind,
                "url": url,
                "title": title,
                "channel": channel,
                "topic": topic,
                "engine_path": engine_path,
                "skill_dir": skill_dir,
                "action": action,
            },
        ),
        ensure_ascii=False,
        indent=2,
    )


async def _json_body(request: Request) -> Dict[str, Any]:
    try:
        data = await request.json()
        return data if isinstance(data, dict) else {}
    except Exception:
        form = await request.form()
        return {str(k): v for k, v in form.items()}


def register(api: Any) -> None:
    state_dir = pathlib.Path(api.get_state_dir())

    async def route_refresh(request: Request) -> JSONResponse:
        body = await _json_body(request) if request.method != "GET" else {}
        limit = int(body.get("limit_per_source") or request.query_params.get("limit_per_source") or 20)
        source_id = str(body.get("source_id") or request.query_params.get("source_id") or "")
        return JSONResponse(_refresh(state_dir, limit_per_source=limit, source_id=source_id))

    async def route_digest(request: Request) -> JSONResponse:
        hours = int(request.query_params.get("hours") or 48)
        limit = int(request.query_params.get("limit") or 12)
        min_score = int(request.query_params.get("min_score") or 1)
        return JSONResponse(_digest_items(state_dir, hours=hours, limit=limit, min_score=min_score))

    async def route_sources(request: Request) -> JSONResponse:
        if request.method == "GET":
            return JSONResponse(_list_sources(state_dir))
        body = await _json_body(request)
        status = _upsert_source(state_dir, body)
        return JSONResponse(status, status_code=200 if status.get("ok") else 400)

    api.register_tool(
        "refresh",
        lambda limit_per_source=20, source_id="": _tool_refresh(
            state_dir=state_dir,
            limit_per_source=int(limit_per_source or 20),
            source_id=str(source_id or ""),
        ),
        description=(
            "Fetch configured RSS/Atom, public Telegram, and optional Last30Days sources, deduplicate them, and update the "
            "research digest store. Do not use this for a user-facing Telegram digest request; use "
            "prepare_digest instead."
        ),
        schema={
            "type": "object",
            "properties": {
                "limit_per_source": {"type": "integer", "description": "Maximum items to read from each source."},
                "source_id": {"type": "string", "description": "Optional single configured source id to refresh."},
            },
        },
        timeout_sec=120,
    )
    api.register_tool(
        "prepare_digest",
        lambda hours=168, limit=6, min_score=1, refresh=True, limit_per_source=30, max_per_source=_DEFAULT_MAX_PER_SOURCE: _tool_prepare_digest(
            state_dir=state_dir,
            hours=int(hours or 168),
            limit=int(limit or 6),
            min_score=int(min_score or 1),
            refresh=_bool_arg(refresh, default=True),
            limit_per_source=int(limit_per_source or 30),
            max_per_source=int(max_per_source or _DEFAULT_MAX_PER_SOURCE),
        ),
        description=(
            "Use this single tool for user requests to prepare/build/send a digest for Telegram. "
            "It refreshes sources, builds a compact source-diverse AI/ML/business digest, and returns "
            "a Telegram-ready final_response that can be sent directly without another LLM rewrite."
        ),
        schema={
            "type": "object",
            "properties": {
                "hours": {"type": "integer", "description": "Lookback window in hours."},
                "limit": {"type": "integer", "description": "Maximum digest items."},
                "min_score": {"type": "integer", "description": "Minimum topic score."},
                "refresh": {"type": "boolean", "description": "Refresh sources before building the digest."},
                "limit_per_source": {"type": "integer", "description": "Maximum items to read from each source during refresh."},
                "max_per_source": {"type": "integer", "description": "Preferred maximum selected digest items per source."},
            },
        },
        timeout_sec=150,
    )
    api.register_tool(
        "digest",
        lambda hours=48, limit=12, min_score=1: _tool_digest(
            state_dir=state_dir,
            hours=int(hours or 48),
            limit=int(limit or 12),
            min_score=int(min_score or 1),
        ),
        description="Return a scored AI/ML/engineering/business research digest as JSON plus Telegram-ready markdown.",
        schema={
            "type": "object",
            "properties": {
                "hours": {"type": "integer", "description": "Lookback window in hours."},
                "limit": {"type": "integer", "description": "Maximum digest items."},
                "min_score": {"type": "integer", "description": "Minimum topic score."},
            },
        },
        timeout_sec=20,
    )
    api.register_tool(
        "sources",
        lambda: _tool_sources(state_dir=state_dir),
        description="List configured research digest sources and topic keywords.",
        schema={"type": "object", "properties": {}},
        timeout_sec=10,
    )
    api.register_tool(
        "source_upsert",
        lambda id="", kind="rss", url="", title="", channel="", topic="", engine_path="", skill_dir="", action="upsert": _tool_source_upsert(
            state_dir=state_dir,
            id=str(id or ""),
            kind=str(kind or "rss"),
            url=str(url or ""),
            title=str(title or ""),
            channel=str(channel or ""),
            topic=str(topic or ""),
            engine_path=str(engine_path or ""),
            skill_dir=str(skill_dir or ""),
            action=str(action or "upsert"),
        ),
        description="Add, update, or remove a research digest source. Use kind=telegram_public with channel for public Telegram pages.",
        schema={
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "kind": {"type": "string", "enum": ["rss", "atom", "telegram_public", "last30days"]},
                "url": {"type": "string"},
                "title": {"type": "string"},
                "channel": {"type": "string"},
                "topic": {"type": "string", "description": "Topic/query for kind=last30days."},
                "engine_path": {"type": "string", "description": "Optional path to scripts/last30days.py."},
                "skill_dir": {"type": "string", "description": "Optional path to the last30days skill directory."},
                "action": {"type": "string", "enum": ["upsert", "remove"]},
            },
            "required": ["id"],
        },
        timeout_sec=20,
    )
    api.register_route("refresh", route_refresh, methods=("POST", "GET"))
    api.register_route("digest", route_digest, methods=("GET",))
    api.register_route("sources", route_sources, methods=("GET", "POST"))
    api.register_ui_tab(
        "digest",
        "Research digest",
        icon="newspaper",
        render={
            "kind": "declarative",
            "schema_version": 1,
            "components": [
                {
                    "type": "form",
                    "route": "refresh",
                    "method": "POST",
                    "target": "refresh",
                    "submit_label": "Refresh",
                    "fields": [{"name": "limit_per_source", "label": "Per source", "type": "number", "default": "20"}],
                },
                {
                    "type": "status",
                    "target": "refresh",
                    "idle": "Refresh configured RSS/Atom, public Telegram, and Last30Days sources.",
                    "loading": "Fetching sources...",
                    "error": "Refresh failed.",
                    "success": "Refresh complete",
                },
                {
                    "type": "form",
                    "route": "digest",
                    "method": "GET",
                    "target": "digest",
                    "submit_label": "Build digest",
                    "fields": [
                        {"name": "hours", "label": "Hours", "type": "number", "default": "48"},
                        {"name": "limit", "label": "Items", "type": "number", "default": "12"},
                    ],
                },
                {"type": "json", "target": "digest"},
            ],
        },
    )
    api.log("info", "research_digest: extension registered")


__all__ = [
    "register",
    "_parse_rss_atom",
    "_parse_telegram_public",
    "_parse_last30days_output",
    "_digest_items",
    "_prepare_digest",
    "_refresh",
    "_upsert_source",
]
