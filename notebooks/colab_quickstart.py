import json
import os
import pathlib
import socket
import subprocess
import sys
import urllib.request

token = os.getenv("TELEGRAM_BOT_TOKEN")
print("TELEGRAM_BOT_TOKEN configured:", bool(token))

try:
    from google.colab import drive  # type: ignore
except Exception as exc:
    raise RuntimeError(
        "notebooks/colab_quickstart.py must be run inside Google Colab. "
        "For local startup, run: python -m ouroboros.cli server --host 127.0.0.1 --port 8765 --no-ui"
    ) from exc

if not pathlib.Path("/content").is_dir() or not os.access("/content", os.W_OK):
    raise RuntimeError(
        "Google Colab writable /content is not available. "
        "Open this script/notebook in Colab instead of running it locally."
    )

DRIVE_MOUNTED = False
try:
    drive.mount("/content/drive")
    DRIVE_MOUNTED = True
except Exception as exc:
    print("Google Drive mount failed; continuing with ephemeral /content storage:", exc)

# Minimal bootstrap clone so `ouroboros.colab_bootstrap` becomes importable.
# Remote roles and fast-forward updates are handled by clone_or_update_repo below.
SOURCE_URL = os.environ.get(
    "OUROBOROS_COLAB_REPO_URL",
    "https://github.com/AmadeyM111/oil-ai-agent.git",
)
REPO_DIR = pathlib.Path("/content/ouroboros_repo")
os.chdir("/content")


def _free_port(preferred: int, used: set[int] | None = None) -> int:
    used = used or set()
    for port in [preferred, *range(preferred + 1, preferred + 50)]:
        if port in used:
            continue
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError(f"No free loopback port found near {preferred}")


def _bootstrap_checkout(repo_dir: pathlib.Path, source_url: str, branch: str = "ouroboros") -> None:
    if not (repo_dir / ".git").exists():
        subprocess.run(
            ["git", "clone", "--branch", branch, source_url, str(repo_dir)],
            cwd="/content",
            check=True,
        )
        return
    remotes = subprocess.run(
        ["git", "remote"],
        cwd=str(repo_dir),
        capture_output=True,
        text=True,
        check=False,
    ).stdout.split()
    if "managed" in remotes:
        subprocess.run(["git", "remote", "set-url", "managed", source_url], cwd=str(repo_dir), check=False)
    else:
        subprocess.run(["git", "remote", "add", "managed", source_url], cwd=str(repo_dir), check=False)
    subprocess.run(["git", "fetch", "managed", branch], cwd=str(repo_dir), check=True)
    subprocess.run(["git", "checkout", branch], cwd=str(repo_dir), check=True)
    subprocess.run(["git", "merge", "--ff-only", f"managed/{branch}"], cwd=str(repo_dir), check=True)


def _strip_openai_compatible_prefix(model: str) -> str:
    text = str(model or "").strip()
    if text.startswith("openai-compatible::"):
        return text.removeprefix("openai-compatible::").strip()
    return text


def _set_openai_compatible_model(settings: dict, model: str) -> None:
    model = _strip_openai_compatible_prefix(model)
    qualified = f"openai-compatible::{model}"
    for key in (
        "OUROBOROS_MODEL",
        "OUROBOROS_MODEL_CODE",
        "OUROBOROS_MODEL_LIGHT",
        "OUROBOROS_MODEL_FALLBACK",
        "OUROBOROS_MODEL_CONSCIOUSNESS",
    ):
        settings[key] = qualified
    settings["OUROBOROS_REVIEW_MODELS"] = ",".join([qualified, qualified])
    settings["OUROBOROS_SCOPE_REVIEW_MODEL"] = qualified
    settings["OUROBOROS_SCOPE_REVIEW_MODELS"] = qualified


def _split_model_list(value: str) -> list[str]:
    return [
        item.strip()
        for chunk in str(value or "").splitlines()
        for item in chunk.split(",")
        if item.strip()
    ]


def _fetch_groq_model_ids(api_key: str) -> set[str]:
    if not api_key:
        return set()
    req = urllib.request.Request(
        "https://api.groq.com/openai/v1/models",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        print("Could not fetch Groq model list; using static fallback candidates:", exc)
        return set()
    models = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(models, list):
        return set()
    return {
        str(item.get("id") or "").strip()
        for item in models
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    }


def _groq_fallback_candidates(settings: dict, secrets: dict) -> list[str]:
    configured = _split_model_list(
        str(secrets.get("GROQ_FALLBACK_MODELS") or os.environ.get("GROQ_FALLBACK_MODELS") or "")
    )
    defaults = [
        "llama-3.3-70b-versatile",
    ]
    available = _fetch_groq_model_ids(str(settings.get("OPENAI_COMPATIBLE_API_KEY") or ""))
    current = _strip_openai_compatible_prefix(str(settings.get("OUROBOROS_MODEL") or ""))
    candidates: list[str] = []
    for model in [*configured, *defaults]:
        model = _strip_openai_compatible_prefix(model)
        if not model or model == current or model in candidates:
            continue
        if available and model not in available:
            continue
        candidates.append(model)
    return candidates


def _run_groq_smoke(settings: dict, model: str | None = None) -> subprocess.CompletedProcess[str]:
    smoke_env = os.environ.copy()
    for key in (
        "OPENAI_COMPATIBLE_API_KEY",
        "OPENAI_COMPATIBLE_BASE_URL",
        "OPENAI_COMPATIBLE_CONTEXT_LENGTH",
        "OPENAI_COMPATIBLE_MAX_TOKENS",
        "OUROBOROS_MODEL",
    ):
        value = settings.get(key)
        if value not in (None, ""):
            smoke_env[key] = str(value)
    command = [sys.executable, "-m", "ouroboros.groq_api_smoke"]
    if model:
        command.extend(["--model", _strip_openai_compatible_prefix(model)])
        smoke_env["OUROBOROS_MODEL"] = f"openai-compatible::{_strip_openai_compatible_prefix(model)}"
    return subprocess.run(
        command,
        env=smoke_env,
        text=True,
        capture_output=True,
    )


_bootstrap_checkout(REPO_DIR, SOURCE_URL)

os.chdir(REPO_DIR)
sys.path.insert(0, str(REPO_DIR))

# %%
from ouroboros.colab_bootstrap import (
    build_colab_settings,
    clone_or_update_repo,
    collect_colab_secrets,
    configure_colab_personal_origin,
    ensure_colab_native_skill_seeded,
    ensure_research_digest_live,
    ensure_telegram_bridge_live,
    export_colab_env,
    masked_secret_status,
    server_command,
    write_colab_settings,
)
from ouroboros.config import apply_settings_to_env

# Canonical update: establish the `managed` remote role and fast-forward.
clone_or_update_repo(REPO_DIR, source_url=SOURCE_URL)
subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-e", "."], check=True)

APP_ROOT = pathlib.Path("/content/drive/MyDrive/Ouroboros") if DRIVE_MOUNTED else pathlib.Path("/content/Ouroboros")
DATA_DIR = APP_ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

secrets = collect_colab_secrets()
# Preserve prior owner choices on Drive across ephemeral Colab sessions (a re-run
# of this cell must not wipe a pinned chat, tweaked models, or other prefs).
_existing_settings = {}
_settings_file = DATA_DIR / "settings.json"
if _settings_file.exists():
    try:
        _existing_settings = json.loads(_settings_file.read_text(encoding="utf-8"))
    except Exception:
        _existing_settings = {}
settings = build_colab_settings(
    secrets,
    total_budget=float(os.environ.get("TOTAL_BUDGET", "10")),
    runtime_mode=os.environ.get("OUROBOROS_RUNTIME_MODE", "advanced"),
    max_workers=int(os.environ.get("OUROBOROS_MAX_WORKERS", "1")),
    existing=_existing_settings,
)
SERVER_PORT = _free_port(8765)
HOST_SERVICE_PORT = _free_port(8767, used={SERVER_PORT})
settings["OUROBOROS_HOST_SERVICE_PORT"] = HOST_SERVICE_PORT
# GitHub persistence is optional: a personal fork is configured only when a token
# is present, otherwise the prototype still runs (without remote self-persistence).
origin_result = configure_colab_personal_origin(REPO_DIR, DATA_DIR, settings)
settings_path = write_colab_settings(DATA_DIR, settings)
export_colab_env(REPO_DIR, DATA_DIR, settings_path)
apply_settings_to_env(settings)

print("Secrets configured:", masked_secret_status(settings))
print("Personal origin:", origin_result)
print("Settings:", settings_path)
research_seed = ensure_colab_native_skill_seeded(DATA_DIR, REPO_DIR, "research_digest")
print("Research digest seed:", research_seed)

# %%
if settings.get("OPENAI_COMPATIBLE_BASE_URL") == "https://api.groq.com/openai/v1":
    print("Running Groq smoke test...")
    smoke = _run_groq_smoke(settings)
    if smoke.stdout:
        print(smoke.stdout)
    if smoke.returncode != 0:
        combined_smoke_output = "\n".join(part for part in (smoke.stdout, smoke.stderr) if part)
        project_blocked = (
            "blocked at the project level" in combined_smoke_output
            or "PermissionDeniedError" in combined_smoke_output
        )
        fallback_ok = False
        if project_blocked:
            print(
                "Groq project blocked the selected model. "
                "Trying fallback chat models; override with GROQ_FALLBACK_MODELS if needed."
            )
            for fallback_model in _groq_fallback_candidates(settings, secrets):
                print(f"Trying Groq fallback model: {fallback_model}")
                fallback_smoke = _run_groq_smoke(settings, fallback_model)
                if fallback_smoke.stdout:
                    print(fallback_smoke.stdout)
                if fallback_smoke.returncode == 0:
                    print(f"Using Groq fallback model: {fallback_model}")
                    _set_openai_compatible_model(settings, fallback_model)
                    settings_path = write_colab_settings(DATA_DIR, settings)
                    export_colab_env(REPO_DIR, DATA_DIR, settings_path)
                    apply_settings_to_env(settings)
                    fallback_ok = True
                    break
                if fallback_smoke.stderr:
                    print(fallback_smoke.stderr)
        if not fallback_ok:
            if smoke.stderr:
                print(smoke.stderr)
            if project_blocked:
                print(
                    "Fix in Groq Console: enable the blocked model in Project -> Limits, "
                    "or set GROQ_MODEL / GROQ_FALLBACK_MODELS to a model enabled for this project."
                )
            raise RuntimeError(f"Groq smoke test failed with exit code {smoke.returncode}")

# %%
server_log_path = DATA_DIR / "logs" / "colab_server.log"
server_log_path.parent.mkdir(parents=True, exist_ok=True)
server_log_handle = server_log_path.open("a", encoding="utf-8")
server = subprocess.Popen(
    server_command(REPO_DIR, port=SERVER_PORT),
    cwd=str(REPO_DIR),
    env=os.environ.copy(),
    stdout=server_log_handle,
    stderr=subprocess.STDOUT,
)
print("Ouroboros server PID:", server.pid)
print("Ouroboros gateway port:", SERVER_PORT)
print("Ouroboros host service port:", HOST_SERVICE_PORT)
print("Ouroboros server log:", server_log_path)

# Install + review + grant + enable the Telegram bridge over the loopback gateway.
bridge_status = ensure_telegram_bridge_live(settings=settings, data_dir=DATA_DIR, port=SERVER_PORT, timeout=600.0)
print("Telegram bridge:", bridge_status)
digest_status = ensure_research_digest_live(data_dir=DATA_DIR, port=SERVER_PORT, timeout=600.0)
print("Research digest:", digest_status)
if bridge_status.get("ok") and bridge_status.get("command_mode_ok"):
    print("Message your Telegram bot now. Your first owner slash command (e.g. /status) registers your chat and asks you to send it once more;")
    print("after that, owner commands like /status and /panic run immediately.")
elif bridge_status.get("ok"):
    print("Bridge installed and enabled, but full_access command mode was not applied:", bridge_status.get("warning"))
    print("Slash commands stay restricted until you set TELEGRAM_COMMAND_MODE=full_access in the bridge settings.")
else:
    server_rc = server.poll()
    print("Ouroboros server return code:", server_rc)
    try:
        server_log_handle.flush()
        tail = server_log_path.read_text(encoding="utf-8", errors="replace")[-4000:]
        if tail:
            print("Ouroboros server log tail:")
            print(tail)
    except Exception as exc:
        print("Could not read Ouroboros server log:", exc)
    print("Bridge not live yet:", bridge_status.get("error") or bridge_status)
