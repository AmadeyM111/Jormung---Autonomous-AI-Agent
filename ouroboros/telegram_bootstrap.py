"""Local runtime launcher for starting Ouroboros through the Telegram bridge."""

from __future__ import annotations

import os
import pathlib
import signal
import subprocess
import sys
import time
from typing import Any, Dict, Optional

from ouroboros.colab_bootstrap import ensure_research_digest_live, ensure_telegram_bridge_live
from ouroboros.config import apply_settings_to_env, load_settings, save_settings


DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8765
DEFAULT_COMMAND_MODE = "full_access"


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
