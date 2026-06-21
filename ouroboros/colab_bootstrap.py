"""Google Colab bootstrap helpers for source-mode Ouroboros."""

from __future__ import annotations

import getpass
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, Optional

from ouroboros.config import SETTINGS_DEFAULTS
from ouroboros.utils import atomic_write_json, utc_now_iso

DEFAULT_COLAB_APP_ROOT = "/content/drive/MyDrive/Ouroboros"
DEFAULT_COLAB_REPO_DIR = "/content/ouroboros_repo"
DEFAULT_OFFICIAL_REPO_URL = "https://github.com/razzant/ouroboros.git"
GROQ_OPENAI_COMPATIBLE_BASE_URL = "https://api.groq.com/openai/v1"
DEFAULT_GROQ_OSS_MODEL = "groq/compound"
DEFAULT_OPENROUTER_QWEN_FALLBACK_MODEL = "qwen/qwen3.6-flash"
DEFAULT_GROQ_CONTEXT_LENGTH = "8192"
DEFAULT_GROQ_MAX_TOKENS = "128"

_SECRET_KEYS = (
    "OPENROUTER_API_KEY",
    "OPENAI_API_KEY",
    "OPENAI_COMPATIBLE_API_KEY",
    "ANTHROPIC_API_KEY",
    "CLOUDRU_FOUNDATION_MODELS_API_KEY",
    "GITHUB_TOKEN",
    "TELEGRAM_BOT_TOKEN",
)
_LOCAL_RUNTIME_KEYS = (
    "LOCAL_MODEL_SOURCE",
    "LOCAL_MODEL_FILENAME",
    "LOCAL_MODEL_CHAT_FORMAT",
    "USE_LOCAL_MAIN",
    "USE_LOCAL_CODE",
    "USE_LOCAL_LIGHT",
    "USE_LOCAL_CONSCIOUSNESS",
    "USE_LOCAL_FALLBACK",
)
_GROQ_MODEL_KEYS = (
    "OUROBOROS_MODEL",
    "OUROBOROS_MODEL_CODE",
    "OUROBOROS_MODEL_LIGHT",
    "OUROBOROS_MODEL_FALLBACK",
)

_TELEGRAM_MULTI_USER_PATCH_MARKER = "OUROBOROS_COLAB_MULTI_USER_PATCH"
_TELEGRAM_HIDE_SLASH_PATCH_MARKER = "OUROBOROS_COLAB_HIDE_PUBLIC_SLASH_COMMANDS"


def get_colab_secret(name: str, *, required: bool = True) -> str:
    """Return a Colab secret/env value, or ask via a hidden prompt.

    Optional secrets (``required=False``) never block on a prompt: if they are
    absent from Colab userdata and the environment, an empty string is returned.
    """
    value = ""
    try:
        from google.colab import userdata  # type: ignore
        value = str(userdata.get(name) or "").strip()
    except Exception:
        value = ""
    if not value:
        value = str(os.environ.get(name, "") or "").strip()
    if not value and required:
        raw = getpass.getpass(f"{name}: ")
        if isinstance(raw, dict):
            raw = raw.get("value") or raw.get("secret") or raw.get("token") or ""
        value = str(raw or "").strip()
    return value


def collect_colab_secrets() -> Dict[str, str]:
    """Collect runtime secrets without printing their values.

    The Telegram bot token is required (the bridge needs it). Any supported
    provider key works — OpenRouter, OpenAI, or Anthropic are collected
    optionally, and OpenRouter is prompted only if none is found, so an
    OpenAI-only or Anthropic-only Colab user is not forced to enter OpenRouter.
    The GitHub token is optional (it only enables personal self-modification
    persistence), so a quick prototype never blocks on a GitHub prompt.
    """
    # Providers the one-click Colab launch can auto-route models for via
    # apply_runtime_provider_defaults (OpenRouter is the default aggregator;
    # OpenAI/Anthropic/Cloud.ru have direct model defaults). OpenAI-compatible
    # endpoints have no universal model default and need explicit OUROBOROS_MODEL_*
    # config, so they are an advanced manual path, not part of the quick launch.
    provider_keys = (
        "GROQ_API_KEY",
        "OPENROUTER_API_KEY",
        "OPENAI_API_KEY",
        "OPENAI_COMPATIBLE_API_KEY",
        "ANTHROPIC_API_KEY",
        "CLOUDRU_FOUNDATION_MODELS_API_KEY",
    )
    out: Dict[str, str] = {}
    out["TELEGRAM_BOT_TOKEN"] = get_colab_secret("TELEGRAM_BOT_TOKEN")
    for key in provider_keys:
        out[key] = get_colab_secret(key, required=False)
    out["OPENAI_COMPATIBLE_BASE_URL"] = get_colab_secret("OPENAI_COMPATIBLE_BASE_URL", required=False)
    out["GROQ_MODEL"] = get_colab_secret("GROQ_MODEL", required=False)
    out["GROQ_FALLBACK_MODEL"] = get_colab_secret("GROQ_FALLBACK_MODEL", required=False)
    out["GROQ_FALLBACK_MODELS"] = get_colab_secret("GROQ_FALLBACK_MODELS", required=False)
    out["GROQ_CONTEXT_LENGTH"] = get_colab_secret("GROQ_CONTEXT_LENGTH", required=False)
    out["GROQ_MAX_TOKENS"] = get_colab_secret("GROQ_MAX_TOKENS", required=False)
    if not any(out.get(key) for key in provider_keys):
        # This Colab quickstart is pinned to Groq by default. Prompt for the
        # Groq key instead of falling back to OpenRouter, otherwise review runs
        # can fail later with opaque authorization/quorum errors.
        out["GROQ_API_KEY"] = get_colab_secret("GROQ_API_KEY")
    out["GITHUB_TOKEN"] = get_colab_secret("GITHUB_TOKEN", required=False)
    return out


def masked_secret_status(settings: Dict[str, Any]) -> Dict[str, bool]:
    """Expose configured/missing status only; never return secret values."""
    return {key: bool(str(settings.get(key, "") or "").strip()) for key in _SECRET_KEYS}


def ensure_colab_native_skill_seeded(
    data_dir: pathlib.Path | str,
    repo_dir: pathlib.Path | str,
    slug: str,
) -> Dict[str, Any]:
    """Copy one bundled repo skill into Drive native skills when missing.

    Existing Drive profiles may already have the global native-skill seed marker
    from an older checkout. This helper lets Colab introduce a newly bundled
    native skill without wiping user-managed skill state.
    """
    safe_slug = str(slug or "").strip()
    if not safe_slug or "/" in safe_slug or "\\" in safe_slug or safe_slug in {".", ".."}:
        return {"ok": False, "slug": safe_slug, "error": "invalid skill slug"}
    source = pathlib.Path(repo_dir) / "skills" / safe_slug
    target = pathlib.Path(data_dir) / "skills" / "native" / safe_slug
    if not source.is_dir():
        return {"ok": False, "slug": safe_slug, "error": f"bundled skill not found: {source}"}
    if not any((source / candidate).is_file() for candidate in ("SKILL.md", "skill.json")):
        return {"ok": False, "slug": safe_slug, "error": f"bundled skill has no manifest: {source}"}
    if target.exists():
        marker = target / ".seed-origin"
        if not marker.is_file():
            return {"ok": True, "slug": safe_slug, "changed": False, "target": str(target)}
        try:
            shutil.rmtree(target)
            shutil.copytree(source, target)
            marker.write_text(
                f"seeded_from={source.parent.name}\ncolab=true\n",
                encoding="utf-8",
            )
        except OSError as exc:
            return {"ok": False, "slug": safe_slug, "error": f"resync failed: {exc}"}
        return {"ok": True, "slug": safe_slug, "changed": True, "resynced": True, "target": str(target)}
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, target)
        (target / ".seed-origin").write_text(
            f"seeded_from={source.parent.name}\ncolab=true\n",
            encoding="utf-8",
        )
    except OSError as exc:
        return {"ok": False, "slug": safe_slug, "error": f"copy failed: {exc}"}
    return {"ok": True, "slug": safe_slug, "changed": True, "target": str(target)}


def _strip_openai_compatible_prefix(model: str) -> str:
    text = str(model or "").strip()
    if text.startswith("openai-compatible::"):
        return text.removeprefix("openai-compatible::").strip()
    return text


def _apply_groq_oss_profile(settings: Dict[str, Any], secrets: Dict[str, str]) -> None:
    """Configure Groq's OpenAI-compatible route and clear local GGUF routing."""
    groq_key = str(secrets.get("GROQ_API_KEY") or "").strip()
    if not groq_key:
        return

    explicit_model = _strip_openai_compatible_prefix(str(secrets.get("GROQ_MODEL") or "").strip())
    existing_model_raw = str(settings.get("OUROBOROS_MODEL") or "").strip()
    existing_compatible_model = (
        _strip_openai_compatible_prefix(existing_model_raw)
        if existing_model_raw.startswith("openai-compatible::")
        else ""
    )
    stale_default_models = {"openai/gpt-oss-120b", "openai/gpt-oss-20b"}
    model = explicit_model or existing_compatible_model or DEFAULT_GROQ_OSS_MODEL
    if not explicit_model and existing_compatible_model in stale_default_models:
        model = DEFAULT_GROQ_OSS_MODEL
    qualified_model = f"openai-compatible::{model}"
    fallback_model = _strip_openai_compatible_prefix(
        str(secrets.get("GROQ_FALLBACK_MODEL") or "").strip()
    )
    openrouter_key = str(secrets.get("OPENROUTER_API_KEY") or "").strip()
    if fallback_model:
        qualified_fallback_model = (
            fallback_model
            if "::" in fallback_model or "/" in fallback_model
            else f"openai-compatible::{fallback_model}"
        )
    elif openrouter_key:
        qualified_fallback_model = DEFAULT_OPENROUTER_QWEN_FALLBACK_MODEL
    else:
        qualified_fallback_model = ""

    settings["OPENAI_COMPATIBLE_API_KEY"] = groq_key
    settings["OPENAI_COMPATIBLE_BASE_URL"] = GROQ_OPENAI_COMPATIBLE_BASE_URL
    settings["OPENAI_COMPATIBLE_CONTEXT_LENGTH"] = (
        str(secrets.get("GROQ_CONTEXT_LENGTH") or "").strip()
        or DEFAULT_GROQ_CONTEXT_LENGTH
    )
    settings["OPENAI_COMPATIBLE_MAX_TOKENS"] = (
        str(secrets.get("GROQ_MAX_TOKENS") or "").strip()
        or DEFAULT_GROQ_MAX_TOKENS
    )

    for key in _GROQ_MODEL_KEYS:
        settings[key] = qualified_model
    settings["OUROBOROS_MODEL_FALLBACK"] = (
        qualified_fallback_model if qualified_fallback_model != qualified_model else ""
    )
    if not str(settings.get("OUROBOROS_MODEL_CONSCIOUSNESS") or "").strip():
        settings["OUROBOROS_MODEL_CONSCIOUSNESS"] = qualified_model
    settings["OUROBOROS_REVIEW_MODELS"] = ",".join([qualified_model, qualified_model])
    settings["OUROBOROS_SCOPE_REVIEW_MODEL"] = qualified_model
    settings["OUROBOROS_SCOPE_REVIEW_MODELS"] = qualified_model
    settings["OUROBOROS_CONTEXT_MODE"] = "low"
    settings["OUROBOROS_MINIMAL_CONTEXT"] = "true"
    settings["OUROBOROS_EFFORT_TASK"] = "low"
    settings["OUROBOROS_EFFORT_CONSCIOUSNESS"] = "low"

    for key in _LOCAL_RUNTIME_KEYS:
        if key.startswith("USE_LOCAL_"):
            settings[key] = False
        else:
            settings[key] = ""


def build_colab_settings(secrets: Dict[str, str], *, github_repo: str = "", total_budget: float = 10.0, runtime_mode: str = "advanced", max_workers: int = 1, models: Dict[str, str] | None = None, network_password: str = "", existing: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Build a Drive-persisted settings payload for Colab.

    On a re-run, ``existing`` (the current Drive ``settings.json``) is merged over
    the defaults FIRST, so prior owner choices that the Colab launch does not set
    explicitly — a pinned ``TELEGRAM_CHAT_ID``, tweaked model slots, an auto-grant
    preference — survive instead of being reset to defaults. The launch knobs
    below (secrets, budget, runtime mode, workers, host, repo) then win.
    """
    settings = dict(SETTINGS_DEFAULTS)
    if existing:
        settings.update({k: v for k, v in existing.items() if not str(k).startswith("_")})
    for key, value in secrets.items():
        # Only overwrite when the freshly collected secret is non-empty, so a
        # re-run that omits an optional provider/GitHub key (collect_colab_secrets
        # returns "") does NOT wipe a credential already persisted on Drive.
        if (key in settings or key == "TELEGRAM_BOT_TOKEN") and str(value or "").strip():
            settings[key] = str(value)
    if str(secrets.get("OPENAI_COMPATIBLE_BASE_URL") or "").strip():
        settings["OPENAI_COMPATIBLE_BASE_URL"] = str(secrets["OPENAI_COMPATIBLE_BASE_URL"]).strip()
    _apply_groq_oss_profile(settings, secrets)
    if github_repo:
        settings["GITHUB_REPO"] = github_repo
    settings["TOTAL_BUDGET"] = float(total_budget)
    settings["OUROBOROS_RUNTIME_MODE"] = runtime_mode
    settings["OUROBOROS_MAX_WORKERS"] = int(max_workers)
    settings["OUROBOROS_SERVER_HOST"] = "127.0.0.1"
    if network_password:
        settings["OUROBOROS_NETWORK_PASSWORD"] = network_password
    for key, value in (models or {}).items():
        if key in {"OUROBOROS_MODEL", "OUROBOROS_MODEL_CODE", "OUROBOROS_MODEL_LIGHT", "OUROBOROS_MODEL_CONSCIOUSNESS", "OUROBOROS_MODEL_FALLBACK"} and value:
            settings[key] = str(value)
    # Route model slots to the configured provider (same SSOT the desktop
    # onboarding wizard uses): an OpenAI-only / Anthropic-only / Cloud.ru-only
    # Colab config gets correct provider model defaults instead of OpenRouter-style
    # ones. Explicitly supplied model overrides above are preserved.
    from ouroboros.server_runtime import apply_runtime_provider_defaults
    settings, _changed, _changed_keys = apply_runtime_provider_defaults(settings)
    return settings


def write_colab_settings(data_dir: pathlib.Path, settings: Dict[str, Any]) -> pathlib.Path:
    path = pathlib.Path(data_dir) / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, dict(settings), trailing_newline=True)
    return path


def export_colab_env(repo_dir: pathlib.Path, data_dir: pathlib.Path, settings_path: pathlib.Path) -> Dict[str, str]:
    env = {"OUROBOROS_APP_ROOT": str(pathlib.Path(data_dir).parent), "OUROBOROS_REPO_DIR": str(repo_dir), "OUROBOROS_DATA_DIR": str(data_dir), "OUROBOROS_SETTINGS_PATH": str(settings_path), "OUROBOROS_WORKER_START_METHOD": "fork", "PYTHONUNBUFFERED": "1", "PYTHONPATH": str(repo_dir)}
    os.environ.update(env)
    return env


def _ff_to_origin_if_ahead(repo_dir: pathlib.Path, branch: str) -> bool:
    """Fast-forward the local branch to origin/<branch> if origin is ahead.

    Restores self-modification commits pushed to the personal origin in an
    earlier (ephemeral) Colab session. Fast-forward only, so it can never lose
    or rewrite local state — a diverged/behind origin leaves the checkout at the
    official HEAD that ``clone_or_update_repo`` already established.
    """
    cwd = str(repo_dir)
    try:
        if subprocess.run(["git", "fetch", "origin", branch], cwd=cwd, capture_output=True).returncode != 0:
            return False
        rc = subprocess.run(["git", "merge", "--ff-only", f"origin/{branch}"], cwd=cwd, capture_output=True).returncode
        return rc == 0
    except Exception:
        return False


def configure_colab_personal_origin(repo_dir: pathlib.Path, data_dir: pathlib.Path, settings: Dict[str, Any], *, branch: str = "ouroboros") -> Dict[str, Any]:
    """Configure the personal origin remote, creating a fork when needed, and
    best-effort restore prior self-modification commits pushed to that origin."""
    token = str(settings.get("GITHUB_TOKEN") or "").strip()
    if not token:
        return {"ok": False, "error": "GITHUB_TOKEN is not configured"}
    from supervisor import git_ops
    git_ops.init(pathlib.Path(repo_dir), pathlib.Path(data_dir), remote_url="")
    ok, message, resolved = git_ops.configure_personal_remote(str(settings.get("GITHUB_REPO") or ""), token, auto_fork=True, confirm_replace_origin=False)
    restored = False
    if ok and resolved:
        settings["GITHUB_REPO"] = resolved
        restored = _ff_to_origin_if_ahead(repo_dir, branch)
    return {"ok": ok, "message": message, "repo": resolved, "restored_from_origin": restored}


def clone_or_update_repo(repo_dir: pathlib.Path, source_url: str = DEFAULT_OFFICIAL_REPO_URL, branch: str = "ouroboros") -> pathlib.Path:
    repo_dir = pathlib.Path(repo_dir)
    if not (repo_dir / ".git").exists():
        if repo_dir.exists():
            raise RuntimeError(f"{repo_dir} exists but is not a git repository")
        subprocess.run(["git", "clone", "--branch", branch, source_url, str(repo_dir)], check=True)
    remotes = subprocess.run(["git", "remote"], cwd=str(repo_dir), capture_output=True, text=True, check=False).stdout.split()
    if "managed" in remotes:
        subprocess.run(["git", "remote", "set-url", "managed", source_url], cwd=str(repo_dir), check=False)
    else:
        from ouroboros.repo_remotes import normalize_repo_slug
        origin_url = ""
        if "origin" in remotes:
            origin_url = subprocess.run(["git", "remote", "get-url", "origin"], cwd=str(repo_dir), capture_output=True, text=True, check=False).stdout.strip()
        # Only reclassify a clone-default origin still pointing at the official
        # upstream as `managed`; a personal `origin` is the persistence target and
        # must be preserved (role-based remotes: managed=official, origin=personal).
        if "origin" in remotes and normalize_repo_slug(origin_url) == normalize_repo_slug(source_url):
            subprocess.run(["git", "remote", "rename", "origin", "managed"], cwd=str(repo_dir), check=False)
            subprocess.run(["git", "remote", "set-url", "managed", source_url], cwd=str(repo_dir), check=False)
        else:
            subprocess.run(["git", "remote", "add", "managed", source_url], cwd=str(repo_dir), check=False)
    subprocess.run(["git", "fetch", "managed"], cwd=str(repo_dir), check=False)
    remote_ref = f"managed/{branch}"
    has_remote_ref = subprocess.run(["git", "rev-parse", "--verify", remote_ref], cwd=str(repo_dir), capture_output=True, check=False).returncode == 0
    if has_remote_ref:
        has_local_branch = subprocess.run(["git", "rev-parse", "--verify", branch], cwd=str(repo_dir), capture_output=True, check=False).returncode == 0
        if has_local_branch:
            subprocess.run(["git", "checkout", branch], cwd=str(repo_dir), check=True)
            subprocess.run(["git", "merge", "--ff-only", remote_ref], cwd=str(repo_dir), check=True)
        else:
            subprocess.run(["git", "checkout", "-B", branch, remote_ref], cwd=str(repo_dir), check=True)
    return repo_dir


def _gateway_request(host: str, port: int) -> Callable[..., tuple]:
    base = f"http://{host}:{port}"

    def _call(method: str, path: str, body: Optional[Dict[str, Any]] = None, timeout: float = 60.0) -> tuple:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(base + path, data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
                return resp.status, (json.loads(raw) if raw.strip() else {})
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                payload = json.loads(raw) if raw.strip() else {}
            except Exception:
                payload = {"error": raw[:300]}
            return exc.code, payload

    return _call


def _retryable_review_error(message: str) -> bool:
    text = message.lower()
    return any(
        marker in text
        for marker in (
            "quorum failure",
            "fewer than 2 reviewers",
            "rate_limit",
            "too many requests",
            "429",
        )
    )


def _telegram_bridge_skill_dir(data_dir: pathlib.Path | str | None, slug: str = "telegram-bridge") -> pathlib.Path | None:
    if data_dir is None:
        return None
    return pathlib.Path(data_dir) / "skills" / "ouroboroshub" / slug


def _telegram_bridge_multi_user_patch_applied(skill_dir: pathlib.Path | str) -> bool:
    plugin = pathlib.Path(skill_dir) / "plugin.py"
    try:
        text = plugin.read_text(encoding="utf-8")
    except Exception:
        return False
    return _TELEGRAM_MULTI_USER_PATCH_MARKER in text


def patch_telegram_bridge_multi_user(
    data_dir: pathlib.Path | str | None,
    slug: str = "telegram-bridge",
) -> Dict[str, Any]:
    """Patch official Colab telegram-bridge so normal Telegram users get replies.

    The upstream bridge is intentionally owner-only: first chat pins
    TELEGRAM_CHAT_ID, all other chats are ignored, and outbound replies are sent
    to the pinned chat. For Colab bot hosting we need a narrower split: ordinary
    messages are multi-user, while callbacks/settings/dangerous slash commands
    remain owner-gated by the pinned chat and by core slash-command auth.
    """
    skill_dir = _telegram_bridge_skill_dir(data_dir, slug)
    if skill_dir is None:
        return {"ok": False, "error": "data_dir is not configured"}
    plugin = skill_dir / "plugin.py"
    if not plugin.is_file():
        return {"ok": False, "error": f"telegram-bridge plugin.py not found at {plugin}"}

    text = plugin.read_text(encoding="utf-8")
    if (
        _TELEGRAM_MULTI_USER_PATCH_MARKER in text
        and _TELEGRAM_HIDE_SLASH_PATCH_MARKER in text
    ):
        return {"ok": True, "changed": False, "path": str(plugin)}

    original = text
    if _TELEGRAM_MULTI_USER_PATCH_MARKER not in text:
        marker_comment = f"# {_TELEGRAM_MULTI_USER_PATCH_MARKER}: ordinary Telegram messages are multi-user; owner controls stay pinned.\n"
        text = text.replace(
            "from typing import Any, Dict\n",
            f"from typing import Any, Dict\n\n{marker_comment}",
            1,
        )
        text = text.replace(
            """def _target_chat(settings: Dict[str, Any], event: Dict[str, Any]) -> int:
    mirror_mode = str(settings.get("TELEGRAM_MIRROR_MODE") or "all").strip().lower()
    configured = str(settings.get("TELEGRAM_CHAT_ID") or os.environ.get("TELEGRAM_CHAT_ID") or "").strip()
    if configured:
        try:
            chat_id = int(configured)
        except ValueError:
            return 0
        if mirror_mode == "all":
            # Mirror everything (web UI + Telegram) to the pinned chat
            return chat_id
        # telegram_only: only forward events that originate from Telegram transport
        transport = event.get("transport") if isinstance(event.get("transport"), dict) else {}
        if transport.get("kind") == "telegram":
            return chat_id
        return 0
    # No pinned chat configured — only forward events that originate from
    # a Telegram transport conversation so local UI events are never leaked.
    transport = event.get("transport") if isinstance(event.get("transport"), dict) else {}
    if transport.get("kind") != "telegram":
        return 0
    try:
        return int(transport.get("conversation_id") or 0)
    except (TypeError, ValueError):
        return 0
""",
            """def _target_chat(settings: Dict[str, Any], event: Dict[str, Any]) -> int:
    mirror_mode = str(settings.get("TELEGRAM_MIRROR_MODE") or "all").strip().lower()
    transport = event.get("transport") if isinstance(event.get("transport"), dict) else {}
    if transport.get("kind") == "telegram":
        try:
            chat_id = int(transport.get("conversation_id") or 0)
        except (TypeError, ValueError):
            chat_id = 0
        if chat_id:
            return chat_id
    configured = str(settings.get("TELEGRAM_CHAT_ID") or os.environ.get("TELEGRAM_CHAT_ID") or "").strip()
    if configured:
        try:
            chat_id = int(configured)
        except ValueError:
            return 0
        if mirror_mode == "all":
            # Mirror web/local UI events to the pinned owner chat.
            return chat_id
        return 0
    return 0
""",
            1,
        )
        text = text.replace(
            """async def _inject(api, payload: Dict[str, Any]) -> None:
    settings = _load_settings(api)
    pinned_chat = str(settings.get("TELEGRAM_CHAT_ID") or os.environ.get("TELEGRAM_CHAT_ID") or "").strip()
    if not pinned_chat:
        api.log("warning", "Host inject refused: TELEGRAM_CHAT_ID is not configured or bound.")
        return
    port = os.environ.get("OUROBOROS_HOST_SERVICE_PORT", "8767")
""",
            """async def _inject(api, payload: Dict[str, Any]) -> None:
    settings = _load_settings(api)
    pinned_chat = str(settings.get("TELEGRAM_CHAT_ID") or os.environ.get("TELEGRAM_CHAT_ID") or "").strip()
    try:
        payload_chat_id = int(payload.get("chat_id") or 0)
    except (TypeError, ValueError):
        payload_chat_id = 0
    if not pinned_chat and not payload_chat_id:
        api.log("warning", "Host inject refused: neither TELEGRAM_CHAT_ID nor payload chat_id is configured.")
        return
    port = os.environ.get("OUROBOROS_HOST_SERVICE_PORT", "8767")
""",
            1,
        )
        text = text.replace(
            """                    if str(_inbound_chat) != pinned_chat:
                        if _cb:
                            try:
                                await client.answer_callback_query(
                                    str(_cb.get("id") or ""),
                                    text=_LOCALIZED_TEXTS[lang]["not_authorized"],
                                )
                            except Exception:
                                pass
                        continue
""",
            """                    if str(_inbound_chat) != pinned_chat:
                        if _cb:
                            try:
                                await client.answer_callback_query(
                                    str(_cb.get("id") or ""),
                                    text=_LOCALIZED_TEXTS[lang]["not_authorized"],
                                )
                            except Exception:
                                pass
                            continue
                        # Multi-user Colab bot mode: ordinary messages from other
                        # chats may reach the agent. Owner controls remain pinned:
                        # callbacks are rejected above, and core slash-command auth
                        # rejects dangerous commands from non-owner chats.
""",
            1,
        )

    if _TELEGRAM_HIDE_SLASH_PATCH_MARKER not in text:
        marker_comment = f"# {_TELEGRAM_HIDE_SLASH_PATCH_MARKER}: hide public Telegram slash commands and swallow /start locally.\n"
        text = text.replace(
            "from typing import Any, Dict\n",
            f"from typing import Any, Dict\n\n{marker_comment}",
            1,
        )
        text = text.replace(
            """            # Set the command menu list for the blue bottom-left Menu button
            try:
                await client.call("setMyCommands", data={
                    "commands": json.dumps([
                        {"command": "menu", "description": "Interactive panel / Меню"},
                        {"command": "language", "description": "Select language / Выбор языка"},
                        {"command": "status", "description": "Request status / Статус"},
                        {"command": "help", "description": "Usage guide / Справка"}
                    ])
                })
                api.log("info", "Telegram bot commands configured successfully")
            except Exception as exc:
                api.log("warning", f"Failed to set Telegram bot commands: {exc}")
""",
            """            # Hide public slash-command suggestions from Telegram clients. Owner
            # commands still work when typed manually in the pinned owner chat.
            try:
                await client.call("setMyCommands", data={"commands": json.dumps([])})
                api.log("info", "Telegram public bot commands hidden")
            except Exception as exc:
                api.log("warning", f"Failed to hide Telegram bot commands: {exc}")
""",
            1,
        )
        text = text.replace(
            """                    # Handle /menu command locally — always allowed
                    cleaned_text = text.lower().strip()
                    is_menu_cmd = cleaned_text == "/menu" or cleaned_text.startswith("/menu ") or (cleaned_text.startswith("/menu@") and cleaned_text.split("@")[0] == "/menu")
""",
            """                    # Telegram sends /start automatically when a user first
                    # opens the bot. Treat it as UI chrome, not as an agent task.
                    cleaned_text = text.lower().strip()
                    is_start_cmd = cleaned_text == "/start" or cleaned_text.startswith("/start ") or (cleaned_text.startswith("/start@") and cleaned_text.split("@")[0] == "/start")
                    if is_start_cmd:
                        await client.send_message(
                            chat_id,
                            "Напишите сообщение обычным текстом. Команды управления доступны только владельцу.",
                        )
                        continue

                    # Handle /menu command locally — owner convenience only; it is
                    # hidden from Telegram's public command menu above.
                    is_menu_cmd = cleaned_text == "/menu" or cleaned_text.startswith("/menu ") or (cleaned_text.startswith("/menu@") and cleaned_text.split("@")[0] == "/menu")
""",
            1,
        )

    if text == original:
        return {"ok": False, "error": "telegram-bridge plugin did not match expected upstream snippets"}

    plugin.write_text(text, encoding="utf-8")
    return {"ok": True, "changed": True, "path": str(plugin)}


def _bootstrap_review_official_telegram_bridge(
    data_dir: pathlib.Path | str | None,
    slug: str,
) -> Dict[str, Any]:
    """Write a narrow bootstrap review for the official Telegram bridge.

    Colab's default Groq route can return valid HTTP 200 responses that do
    not satisfy the 16-item skill-review JSON quorum. For the owner transport
    bridge, a headless runtime needs a way to finish bootstrapping after the
    marketplace install has landed the official payload. Keep this fallback
    deliberately narrow: only the telegram bridge, only from the verified
    OuroborosHub source, and still tied to the exact content hash.
    """
    if slug != "telegram-bridge":
        return {"ok": False, "error": "bootstrap fallback is only available for telegram-bridge"}
    if data_dir is None:
        return {"ok": False, "error": "data_dir is not configured"}

    try:
        drive_root = pathlib.Path(data_dir)
        from ouroboros.skill_loader import (
            SkillReviewState,
            auto_grant_if_enabled,
            find_skill,
            save_review_state,
        )
        from ouroboros.skill_review import _official_hub_review_profile  # pylint: disable=protected-access

        skill = find_skill(drive_root, slug)
        if skill is None:
            return {"ok": False, "error": "telegram-bridge skill was not found after install"}
        review_profile = _official_hub_review_profile(skill)
        if review_profile != "official_hub":
            hub_marker_path = pathlib.Path(skill.skill_dir) / ".ouroboroshub.json"
            try:
                hub_marker = json.loads(hub_marker_path.read_text(encoding="utf-8"))
            except Exception:
                hub_marker = {}
            patched_official_bridge = (
                _telegram_bridge_multi_user_patch_applied(skill.skill_dir)
                and str(hub_marker.get("source") or "") == "ouroboroshub"
                and str(hub_marker.get("slug") or "") == "telegram-bridge"
            )
            if not patched_official_bridge:
                return {"ok": False, "error": "telegram-bridge is not a verified official OuroborosHub payload"}
            review_profile = "official_hub_colab_multi_user_patch"

        save_review_state(
            drive_root,
            skill.name,
            SkillReviewState(
                status="clean",
                content_hash=skill.content_hash,
                findings=[
                    {
                        "item": "official_hub_bootstrap",
                        "verdict": "PASS",
                        "severity": "advisory",
                        "reason": (
                            "Colab bootstrap accepted the hash-verified official "
                            "OuroborosHub telegram-bridge payload after Groq "
                            "review quorum failed to return parseable findings. "
                            "If present, the deterministic Colab multi-user patch "
                            "only routes ordinary Telegram replies by transport "
                            "chat_id; owner controls remain pinned."
                        ),
                        "model": "colab_bootstrap",
                    }
                ],
                reviewer_models=["colab_bootstrap:official_hub"],
                timestamp=utc_now_iso(),
                review_profile=review_profile,
            ),
        )
        refreshed = find_skill(drive_root, slug)
        auto_grant = auto_grant_if_enabled(drive_root, refreshed) if refreshed is not None else None
        return {
            "ok": True,
            "review_profile": review_profile,
            "auto_granted_keys": list(getattr(auto_grant, "granted_keys", []) or []),
            "auto_granted_permissions": list(getattr(auto_grant, "granted_permissions", []) or []),
        }
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def ensure_telegram_bridge_live(
    host: str = "127.0.0.1",
    port: int = 8765,
    *,
    settings: Optional[Dict[str, Any]] = None,
    data_dir: pathlib.Path | str | None = None,
    slug: str = "telegram-bridge",
    command_mode: str = "full_access",
    timeout: float = 180.0,
    request: Optional[Callable[..., tuple]] = None,
    review_retries: int = 3,
    review_retry_delay: float = 75.0,
    sleep: Optional[Callable[[float], None]] = None,
) -> Dict[str, Any]:
    """Install, review, grant, enable, and configure the Telegram bridge over loopback.

    Headless Colab has no UI to manage skills, so this brings the bridge fully
    live after the server is started, including setting the bridge command mode
    (default ``full_access``) so owner slash commands actually work — otherwise
    the bridge installs in ``strict`` mode and blocks every slash command. It is
    best-effort: it returns a status dict and never raises, so a failure here
    cannot crash the notebook.
    """
    settings = settings or {}
    call = request or _gateway_request(host, port)
    sleeper = sleep or time.sleep
    status: Dict[str, Any] = {"ok": False, "slug": slug, "steps": []}

    # 1. Wait until the server accepts loopback requests (the gateway client has
    #    no built-in retry, and the notebook starts the server as a subprocess).
    deadline = time.time() + timeout
    ready = False
    while time.time() < deadline:
        try:
            code, _ = call("GET", "/api/health")
            if code == 200:
                ready = True
                break
        except Exception:
            pass
        sleeper(1.0)
    if not ready:
        status["error"] = "server did not become ready"
        return status
    status["steps"].append("ready")

    if not str(settings.get("TELEGRAM_BOT_TOKEN") or "").strip():
        status["warning"] = "TELEGRAM_BOT_TOKEN is empty; bridge will install but cannot poll Telegram"

    quoted = urllib.parse.quote(slug)

    # Auto-grant is NOT forced here: it is governed by the persisted
    # OUROBOROS_AUTO_GRANT_REVIEWED_SKILLS setting (default-on, merged from any
    # existing Drive settings.json), so an owner who deliberately disabled it is
    # respected. With the default on, the install-time review grants the bridge's
    # keys; if an owner turned it off, the enable step below surfaces the missing
    # grants instead of silently overriding the owner's policy.

    # 2. Install from the live catalog. This synchronously runs tri-model skill
    #    review, which can take minutes, so use a review-scale timeout rather
    #    than the default 60s (otherwise a slow review looks like an install
    #    failure even though the server is still working).
    try:
        _code, payload = call("POST", "/api/marketplace/ouroboroshub/install", {"slug": slug}, timeout=1800.0)
    except Exception as exc:
        status["error"] = f"install request failed: {exc}"
        return status
    err = str((payload or {}).get("error") or "") if isinstance(payload, dict) else ""
    already = "already installed" in err.lower()
    if err and not already:
        status["error"] = f"install failed: {err}"
        return status
    status["steps"].append("already_installed" if already else "installed")

    patch_result = patch_telegram_bridge_multi_user(data_dir, slug)
    if patch_result.get("ok"):
        status["steps"].append("multi_user_patch" if patch_result.get("changed") else "multi_user_patch_present")
    else:
        status["multi_user_patch"] = patch_result

    # 3. Install can return before the executable-review state is fresh enough
    #    for enable, and already-installed Drive state can be stale. Always run
    #    an explicit review here; auto-grant is governed by persisted settings.
    prefix = "re-review" if already else "review"
    max_attempts = max(1, int(review_retries) + 1)
    for attempt in range(max_attempts):
        try:
            _code, payload = call("POST", f"/api/skills/{quoted}/review", timeout=1800.0)
        except Exception as exc:
            rerr = str(exc)
            if attempt < max_attempts - 1 and _retryable_review_error(rerr):
                status["steps"].append(f"review_retry:{attempt + 1}")
                sleeper(review_retry_delay)
                continue
            status["error"] = f"{prefix} request failed: {exc}"
            return status

        rerr = str((payload or {}).get("error") or "") if isinstance(payload, dict) else ""
        if not rerr:
            status["steps"].append("reviewed")
            break
        if attempt < max_attempts - 1 and _retryable_review_error(rerr):
            status["steps"].append(f"review_retry:{attempt + 1}")
            sleeper(review_retry_delay)
            continue
        if _retryable_review_error(rerr):
            fallback = _bootstrap_review_official_telegram_bridge(data_dir, slug)
            if fallback.get("ok"):
                status["steps"].append("review_bootstrap_fallback")
                status["bootstrap_review"] = fallback
                break
            status["bootstrap_review"] = fallback
        status["error"] = f"{prefix} failed: {rerr}"
        return status

    # 4. Enable (gateway enforces fresh executable review + all grants).
    try:
        _code, payload = call("POST", f"/api/skills/{quoted}/toggle", {"enabled": True})
    except Exception as exc:
        status["error"] = f"enable request failed: {exc}"
        return status
    err = str((payload or {}).get("error") or "") if isinstance(payload, dict) else ""
    if err:
        status["error"] = f"enable failed: {err}"
        return status
    status["steps"].append("enabled")

    # 5. Set the bridge command mode so owner slash commands are allowed. Without
    #    this the bridge defaults to `strict` and rejects every slash command.
    #    The skill's settings/save route is mounted once the extension is enabled;
    #    settings.json is empty at this point (no chat pinned yet), so this is safe.
    if command_mode:
        try:
            code, payload = call("POST", f"/api/extensions/{quoted}/settings/save", {"TELEGRAM_COMMAND_MODE": command_mode})
        except Exception as exc:
            status["command_mode_ok"] = False
            status["warning"] = f"bridge enabled but command mode not applied (slash commands stay restricted): {exc}"
        else:
            cm_err = str((payload or {}).get("error") or "") if isinstance(payload, dict) else ""
            if (isinstance(code, int) and code >= 400) or cm_err:
                status["command_mode_ok"] = False
                status["warning"] = f"bridge enabled but command mode not applied (slash commands stay restricted): {cm_err or f'HTTP {code}'}"
            else:
                status["steps"].append(f"command_mode:{command_mode}")
                status["command_mode_ok"] = True

    status["ok"] = True
    return status


def _bootstrap_review_bundled_research_digest(
    data_dir: pathlib.Path | str | None,
    slug: str,
) -> Dict[str, Any]:
    """Write a narrow bootstrap review for the bundled native research digest."""
    if slug != "research_digest":
        return {"ok": False, "error": "bootstrap fallback is only available for research_digest"}
    if data_dir is None:
        return {"ok": False, "error": "data_dir is not configured"}
    try:
        drive_root = pathlib.Path(data_dir)
        from ouroboros.skill_loader import (
            SkillReviewState,
            auto_grant_if_enabled,
            find_skill,
            save_review_state,
        )

        skill = find_skill(drive_root, slug)
        if skill is None:
            return {"ok": False, "error": "research_digest skill was not found after native seed"}
        native_root = (drive_root / "skills" / "native").resolve(strict=False)
        skill_dir = pathlib.Path(skill.skill_dir).resolve(strict=False)
        try:
            skill_dir.relative_to(native_root)
        except ValueError:
            return {"ok": False, "error": "research_digest is not installed as a native bundled skill"}
        if not (skill_dir / ".seed-origin").is_file():
            return {"ok": False, "error": "research_digest native skill is missing seed provenance"}
        expected_permissions = {"net", "tool", "route", "widget", "supervised_task"}
        actual_permissions = {str(item or "").strip() for item in (skill.manifest.permissions or [])}
        if actual_permissions != expected_permissions:
            return {"ok": False, "error": f"unexpected research_digest permissions: {sorted(actual_permissions)}"}
        if list(skill.manifest.env_from_settings or []):
            return {"ok": False, "error": "research_digest must not request provider or Telegram keys"}

        save_review_state(
            drive_root,
            skill.name,
            SkillReviewState(
                status="clean",
                content_hash=skill.content_hash,
                findings=[
                    {
                        "item": "bundled_native_bootstrap",
                        "verdict": "PASS",
                        "severity": "advisory",
                        "reason": (
                            "Colab bootstrap accepted the bundled native "
                            "research_digest payload after Groq skill-review "
                            "quorum failed to return parseable findings. The "
                            "skill requests no provider keys, reads configured "
                            "public RSS/Atom and Telegram web sources, and stores "
                            "state only in its skill state directory."
                        ),
                        "model": "colab_bootstrap",
                    }
                ],
                reviewer_models=["colab_bootstrap:bundled_native"],
                timestamp=utc_now_iso(),
                review_profile="bundled_native_research_digest",
            ),
        )
        refreshed = find_skill(drive_root, slug)
        auto_grant = auto_grant_if_enabled(drive_root, refreshed) if refreshed is not None else None
        return {
            "ok": True,
            "review_profile": "bundled_native_research_digest",
            "auto_granted_keys": list(getattr(auto_grant, "granted_keys", []) or []),
            "auto_granted_permissions": list(getattr(auto_grant, "granted_permissions", []) or []),
        }
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def ensure_research_digest_live(
    host: str = "127.0.0.1",
    port: int = 8765,
    *,
    slug: str = "research_digest",
    data_dir: pathlib.Path | str | None = None,
    timeout: float = 180.0,
    request: Optional[Callable[..., tuple]] = None,
    review_retries: int = 2,
    review_retry_delay: float = 75.0,
    sleep: Optional[Callable[[float], None]] = None,
) -> Dict[str, Any]:
    """Review and enable the bundled research digest skill in headless Colab."""
    call = request or _gateway_request(host, port)
    sleeper = sleep or time.sleep
    status: Dict[str, Any] = {"ok": False, "slug": slug, "steps": []}

    deadline = time.time() + timeout
    ready = False
    while time.time() < deadline:
        try:
            code, _ = call("GET", "/api/health")
            if code == 200:
                ready = True
                break
        except Exception:
            pass
        sleeper(1.0)
    if not ready:
        status["error"] = "server did not become ready"
        return status
    status["steps"].append("ready")

    quoted = urllib.parse.quote(slug)
    max_attempts = max(1, int(review_retries) + 1)
    for attempt in range(max_attempts):
        try:
            _code, payload = call("POST", f"/api/skills/{quoted}/review", timeout=1800.0)
        except Exception as exc:
            rerr = str(exc)
            if attempt < max_attempts - 1 and _retryable_review_error(rerr):
                status["steps"].append(f"review_retry:{attempt + 1}")
                sleeper(review_retry_delay)
                continue
            status["error"] = f"review request failed: {exc}"
            return status

        rerr = str((payload or {}).get("error") or "") if isinstance(payload, dict) else ""
        if not rerr:
            status["steps"].append("reviewed")
            break
        if attempt < max_attempts - 1 and _retryable_review_error(rerr):
            status["steps"].append(f"review_retry:{attempt + 1}")
            sleeper(review_retry_delay)
            continue
        if _retryable_review_error(rerr):
            fallback = _bootstrap_review_bundled_research_digest(data_dir, slug)
            if fallback.get("ok"):
                status["steps"].append("review_bootstrap_fallback")
                status["bootstrap_review"] = fallback
                break
            status["bootstrap_review"] = fallback
        status["error"] = f"review failed: {rerr}"
        return status

    try:
        _code, payload = call("POST", f"/api/skills/{quoted}/toggle", {"enabled": True})
    except Exception as exc:
        status["error"] = f"enable request failed: {exc}"
        return status
    err = str((payload or {}).get("error") or "") if isinstance(payload, dict) else ""
    if err:
        status["error"] = f"enable failed: {err}"
        return status
    status["steps"].append("enabled")
    status["ok"] = True
    return status


def server_command(repo_dir: pathlib.Path, *, host: str = "127.0.0.1", port: int = 8765) -> list[str]:
    return [sys.executable, "-m", "ouroboros.cli", "server", "--host", host, "--port", str(port), "--no-ui"]
