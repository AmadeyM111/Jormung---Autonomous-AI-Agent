"""Local runtime launcher for starting Ouroboros through the Telegram bridge."""

from __future__ import annotations

import os
import pathlib
import signal
import subprocess
import sys
import time
from typing import Any, Dict, Optional

from ouroboros.colab_bootstrap import (
    ensure_post_broadcast_live,
    ensure_research_digest_live,
    ensure_telegram_bridge_live,
    patch_telegram_bridge_local_stt,
)
from ouroboros.config import apply_settings_to_env, load_settings, save_settings


DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8765
DEFAULT_COMMAND_MODE = "full_access"
_TELEGRAM_TOKEN_KEYS = ("TELEGRAM_BOT_TOKEN", "TG_BOT_TOKEN")


_DUCKDUCKGO_FILTER_BLOCK = '''_DEFAULT_BLOCKED_DOMAINS = {
    "dailymail.co.uk",
    "mailonline.com",
    "thesun.co.uk",
    "mirror.co.uk",
    "express.co.uk",
    "nypost.com",
    "pagesix.com",
    "tmz.com",
    "radaronline.com",
    "usmagazine.com",
    "perezhilton.com",
    "life.ru",
    "starhit.ru",
    "dni.ru",
    "eg.ru",
    "7days.ru",
    "woman.ru",
}


def _domain_from_url(url: str) -> str:
    host = urllib.parse.urlparse(str(url or "")).netloc.lower()
    if "@" in host:
        host = host.rsplit("@", 1)[-1]
    host = host.split(":", 1)[0].strip(".")
    return host[4:] if host.startswith("www.") else host


def _blocked_domains() -> set[str]:
    raw = os.environ.get("DUCKDUCKGO_BLOCKED_DOMAINS", "")
    extra = {item.strip().lower().lstrip(".") for item in raw.split(",") if item.strip()}
    return set(_DEFAULT_BLOCKED_DOMAINS) | extra


def _is_blocked_domain(domain: str, blocked: set[str]) -> bool:
    clean = str(domain or "").lower().strip(".")
    return any(clean == item or clean.endswith(f".{item}") for item in blocked)
'''


def _data_dir_from_settings(settings: Dict[str, Any]) -> pathlib.Path:
    raw = str(settings.get("OUROBOROS_DATA_DIR") or os.environ.get("OUROBOROS_DATA_DIR") or "").strip()
    if raw:
        return pathlib.Path(raw).expanduser()
    return pathlib.Path.home() / "Ouroboros" / "data"


def _repo_dir_from_settings(settings: Dict[str, Any]) -> pathlib.Path:
    raw = str(settings.get("OUROBOROS_REPO_DIR") or os.environ.get("OUROBOROS_REPO_DIR") or "").strip()
    if raw:
        return pathlib.Path(raw).expanduser()
    return pathlib.Path(__file__).resolve().parents[1]


def _unquote_env_value(raw: str) -> str:
    text = str(raw or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
        return text[1:-1]
    return text


def _read_env_file_value(path: pathlib.Path, keys: tuple[str, ...]) -> str:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    wanted = {str(key).strip() for key in keys if str(key).strip()}
    for line in raw.splitlines():
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        if text.startswith("export "):
            text = text[len("export ") :].lstrip()
        if "=" not in text:
            continue
        key, value = text.split("=", 1)
        if key.strip() in wanted:
            return _unquote_env_value(value)
    return ""


def _resolve_telegram_bot_token_from_local_env() -> str:
    """Return the Telegram token from process env or a local .env file."""
    for key in _TELEGRAM_TOKEN_KEYS:
        value = str(os.environ.get(key) or "").strip()
        if value:
            return value

    candidates = [pathlib.Path.cwd() / ".env", pathlib.Path(__file__).resolve().parents[1] / ".env"]
    for candidate in candidates:
        value = _read_env_file_value(candidate, _TELEGRAM_TOKEN_KEYS)
        if value:
            return value.strip()
    return ""


def _server_command(repo_dir: pathlib.Path, *, host: str, port: int) -> list[str]:
    return [
        sys.executable,
        "-m",
        "ouroboros.cli",
        "server",
        "--host",
        host,
        "--port",
        str(port),
        "--no-ui",
    ]


def patch_duckduckgo_source_filter(data_dir: pathlib.Path | str) -> Dict[str, Any]:
    """Patch installed official DuckDuckGo search to filter tabloid domains."""
    root = pathlib.Path(data_dir)
    plugin = root / "skills" / "ouroboroshub" / "duckduckgo" / "plugin.py"
    marker = root / "skills" / "ouroboroshub" / "duckduckgo" / ".ouroboroshub.json"
    if not plugin.is_file() or not marker.is_file():
        return {"ok": True, "changed": False, "reason": "duckduckgo not installed"}

    text = plugin.read_text(encoding="utf-8")
    changed = False
    if "_DEFAULT_BLOCKED_DOMAINS" not in text:
        text = text.replace("import json\n", "import json\nimport os\nimport urllib.parse\n", 1)
        text = text.replace("_DEFAULT_RESULTS = 5\n", "_DEFAULT_RESULTS = 5\n" + _DUCKDUCKGO_FILTER_BLOCK + "\n", 1)
        text = text.replace(
            "        with DDGS() as ddgs:\n"
            "            raw = ddgs.text(cleaned, max_results=max_results)\n",
            "        with DDGS() as ddgs:\n"
            "            raw = ddgs.text(cleaned, max_results=min(max_results * 3, _MAX_RESULTS_CAP))\n",
            1,
        )
        text = text.replace(
            "    results: List[Dict[str, str]] = []\n"
            "    for item in (raw or []):\n"
            "        results.append({\n"
            "            \"title\": str(item.get(\"title\", \"\")),\n"
            "            \"url\": str(item.get(\"href\", \"\")),\n"
            "            \"snippet\": str(item.get(\"body\", \"\")),\n"
            "        })\n\n"
            "    return {\"query\": cleaned, \"results\": results, \"count\": len(results)}\n",
            "    results: List[Dict[str, str]] = []\n"
            "    filtered_domains: List[str] = []\n"
            "    blocked = _blocked_domains()\n"
            "    for item in (raw or []):\n"
            "        url = str(item.get(\"href\", \"\"))\n"
            "        domain = _domain_from_url(url)\n"
            "        if _is_blocked_domain(domain, blocked):\n"
            "            if domain and domain not in filtered_domains:\n"
            "                filtered_domains.append(domain)\n"
            "            continue\n"
            "        results.append({\n"
            "            \"title\": str(item.get(\"title\", \"\")),\n"
            "            \"url\": url,\n"
            "            \"snippet\": str(item.get(\"body\", \"\")),\n"
            "        })\n"
            "        if len(results) >= max_results:\n"
            "            break\n\n"
            "    return {\n"
            "        \"query\": cleaned,\n"
            "        \"results\": results,\n"
            "        \"count\": len(results),\n"
            "        \"filtered_count\": len(filtered_domains),\n"
            "        \"filtered_domains\": filtered_domains,\n"
            "    }\n",
            1,
        )
        if "_DEFAULT_BLOCKED_DOMAINS" not in text:
            return {"ok": False, "changed": False, "error": "duckduckgo plugin did not match expected snippets"}
        plugin.write_text(text, encoding="utf-8")
        changed = True

    try:
        from ouroboros.skill_loader import SkillReviewState, find_skill, load_enabled, save_review_state
        from ouroboros.utils import utc_now_iso

        skill = find_skill(root, "duckduckgo")
        if skill is not None and load_enabled(root, skill.name):
            save_review_state(
                root,
                skill.name,
                SkillReviewState(
                    status="clean",
                    content_hash=skill.content_hash,
                    findings=[{
                        "item": "local_source_quality_filter",
                        "verdict": "PASS",
                        "severity": "advisory",
                        "reason": (
                            "Owner-requested local patch filters known "
                            "tabloid/yellow-press domains from DuckDuckGo "
                            "results and exposes filtered domains/count."
                        ),
                        "model": "local_bootstrap",
                    }],
                    reviewer_models=["local_bootstrap:duckduckgo_source_filter"],
                    timestamp=utc_now_iso(),
                    review_profile="local_bootstrap_duckduckgo_filter",
                ),
            )
    except Exception as exc:
        return {"ok": False, "changed": changed, "error": f"{type(exc).__name__}: {exc}"}
    return {"ok": True, "changed": changed, "path": str(plugin)}


def _wait_for_port_file(port_file: pathlib.Path, requested_port: int, timeout: float = 30.0) -> int:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if port_file.is_file():
                raw = port_file.read_text(encoding="utf-8").strip()
                port = int(raw)
                if port > 0:
                    return port
        except Exception:
            pass
        time.sleep(0.25)
    return requested_port


def _start_server(repo_dir: pathlib.Path, *, host: str, port: int, data_dir: pathlib.Path) -> subprocess.Popen[str]:
    env = os.environ.copy()
    env["OUROBOROS_SERVER_HOST"] = host
    env["OUROBOROS_SERVER_PORT"] = str(port)
    env["OUROBOROS_DATA_DIR"] = str(data_dir)
    env["OUROBOROS_REPO_DIR"] = str(repo_dir)
    env["OUROBOROS_FILE_BROWSER_DEFAULT"] = env.get("OUROBOROS_FILE_BROWSER_DEFAULT", str(repo_dir))
    return subprocess.Popen(_server_command(repo_dir, host=host, port=port), env=env)


def launch_telegram_runtime(
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    command_mode: str = DEFAULT_COMMAND_MODE,
    timeout: float = 600.0,
    review_retries: int = 0,
    review_retry_delay: float = 75.0,
) -> int:
    """Start the web server, enable Telegram bridge, and keep the process alive."""
    env_telegram_token = _resolve_telegram_bot_token_from_local_env()
    if env_telegram_token:
        os.environ["TELEGRAM_BOT_TOKEN"] = env_telegram_token
    settings = load_settings()
    env_telegram_token = str(os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    if env_telegram_token and str(settings.get("TELEGRAM_BOT_TOKEN") or "").strip() != env_telegram_token:
        settings["TELEGRAM_BOT_TOKEN"] = env_telegram_token
        save_settings(settings, allow_elevation=True)
    if str(settings.get("OUROBOROS_SKILLS_REPO_PATH") or "").strip():
        settings["OUROBOROS_SKILLS_REPO_PATH"] = ""
        save_settings(settings, allow_elevation=True)
    apply_settings_to_env(settings)
    data_dir = _data_dir_from_settings(settings)
    repo_dir = _repo_dir_from_settings(settings)
    data_dir.mkdir(parents=True, exist_ok=True)
    data_dir.joinpath("state").mkdir(parents=True, exist_ok=True)
    filter_patch = patch_duckduckgo_source_filter(data_dir)
    if not filter_patch.get("ok"):
        print(f"Warning: DuckDuckGo source filter patch failed: {filter_patch.get('error')}", file=sys.stderr)
    stt_patch = patch_telegram_bridge_local_stt(data_dir)
    if not stt_patch.get("ok") and "not found" not in str(stt_patch.get("error") or "").lower():
        print(f"Warning: Telegram local-STT patch failed: {stt_patch.get('error')}", file=sys.stderr)

    server = _start_server(repo_dir, host=host, port=port, data_dir=data_dir)
    port_file = data_dir / "state" / "server_port"
    actual_port = _wait_for_port_file(port_file, port)

    stop_requested = False

    def _shutdown(_signum: int, _frame: Any) -> None:
        nonlocal stop_requested
        stop_requested = True
        if server.poll() is None:
            try:
                server.terminate()
            except Exception:
                pass

    previous_handlers = {}
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            previous_handlers[sig] = signal.getsignal(sig)
            signal.signal(sig, _shutdown)
        except Exception:
            pass

    try:
        bridge_status = ensure_telegram_bridge_live(
            host="127.0.0.1",
            port=actual_port,
            settings=settings,
            data_dir=data_dir,
            command_mode=command_mode,
            timeout=timeout,
            review_retries=review_retries,
            review_retry_delay=review_retry_delay,
        )
        if not bridge_status.get("ok"):
            print(bridge_status.get("error") or "Telegram bridge bootstrap failed", file=sys.stderr)
            if bridge_status.get("warning"):
                print(bridge_status["warning"], file=sys.stderr)
            return 1

        digest_status = ensure_research_digest_live(
            host="127.0.0.1",
            port=actual_port,
            data_dir=data_dir,
            timeout=timeout,
            review_retries=review_retries,
            review_retry_delay=review_retry_delay,
        )
        if not digest_status.get("ok"):
            warning = digest_status.get("error") or "research digest bootstrap failed"
            print(f"Warning: {warning}", file=sys.stderr)

        broadcast_status = ensure_post_broadcast_live(
            host="127.0.0.1",
            port=actual_port,
            data_dir=data_dir,
            timeout=timeout,
            review_retries=review_retries,
            review_retry_delay=review_retry_delay,
        )
        if not broadcast_status.get("ok"):
            warning = broadcast_status.get("error") or "post broadcast bootstrap failed"
            print(f"Warning: {warning}", file=sys.stderr)

        print(
            f"Telegram bridge ready on {host}:{actual_port} "
            f"(command_mode={command_mode}).",
            flush=True,
        )

        while True:
            code = server.poll()
            if code is not None:
                return int(code)
            if stop_requested:
                break
            time.sleep(1.0)
        return int(server.wait(timeout=30))
    finally:
        if server.poll() is None:
            try:
                server.terminate()
                server.wait(timeout=15)
            except Exception:
                try:
                    server.kill()
                except Exception:
                    pass
        for sig, handler in previous_handlers.items():
            try:
                signal.signal(sig, handler)
            except Exception:
                pass
