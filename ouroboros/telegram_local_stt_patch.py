"""Patch the Telegram bridge to transcribe voice locally inside the runtime."""

from __future__ import annotations

import pathlib
from typing import Any, Dict


MARKER = "OUROBOROS_TELEGRAM_LOCAL_STT"


def patch_plugin(path: pathlib.Path | str) -> Dict[str, Any]:
    plugin = pathlib.Path(path)
    try:
        text = plugin.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {"ok": False, "error": f"telegram-bridge plugin.py not found at {plugin}"}
    if MARKER in text:
        return {"ok": True, "changed": False, "path": str(plugin)}

    original = text
    old = '''async def _transcribe_voice(api, ogg_bytes: bytes) -> str:
    """Send voice bytes to OpenAI Whisper API for transcriptions."""
    protected_settings = api.get_settings(["OPENAI_API_KEY"])
    api_key = str(protected_settings.get("OPENAI_API_KEY") or "").strip()
    if not api_key:
        api.log("warning", "Voice message transcription skipped: OPENAI_API_KEY is not configured")
        return ""
    headers = {"Authorization": f"Bearer {api_key}"}
    files = {"file": ("voice.ogg", ogg_bytes, "audio/ogg")}
    data = {"model": "whisper-1"}
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            "https://api.openai.com/v1/audio/transcriptions",
            headers=headers,
            files=files,
            data=data,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"Whisper API transcription returned HTTP {response.status_code}")
        res = response.json()
        return str(res.get("text") or "").strip()
'''
    new = f'''# {MARKER}
async def _transcribe_voice(api, ogg_bytes: bytes) -> str:
    """Transcribe Telegram voice locally with python -m ouroboros.local_stt."""
    import sys
    import uuid

    settings = _load_settings(api)
    backend = str(settings.get("TELEGRAM_STT_BACKEND") or os.environ.get("TELEGRAM_STT_BACKEND") or "local").strip().lower()
    if backend not in {{"local", "off"}}:
        backend = "local"
    if backend == "off":
        api.log("warning", "Voice message transcription skipped: TELEGRAM_STT_BACKEND=off")
        return ""
    state_dir = _data_dir(api) / "state" / "skills" / "telegram-bridge" / "voice"
    state_dir.mkdir(parents=True, exist_ok=True)
    audio_path = state_dir / f"voice-{{uuid.uuid4().hex}}.ogg"
    audio_path.write_bytes(ogg_bytes)
    model_dir = pathlib.Path(
        str(settings.get("TELEGRAM_STT_MODEL_DIR") or os.environ.get("TELEGRAM_STT_MODEL_DIR") or (_data_dir(api) / "models" / "faster-whisper"))
    )
    cmd = [
        sys.executable,
        "-m",
        "ouroboros.local_stt",
        str(audio_path),
        "--json",
        "--model",
        str(settings.get("TELEGRAM_STT_MODEL") or os.environ.get("TELEGRAM_STT_MODEL") or "small"),
        "--lang",
        str(settings.get("TELEGRAM_STT_LANGUAGE") or os.environ.get("TELEGRAM_STT_LANGUAGE") or "ru"),
        "--device",
        str(settings.get("TELEGRAM_STT_DEVICE") or os.environ.get("TELEGRAM_STT_DEVICE") or "auto"),
        "--compute-type",
        str(settings.get("TELEGRAM_STT_COMPUTE_TYPE") or os.environ.get("TELEGRAM_STT_COMPUTE_TYPE") or "auto"),
        "--model-dir",
        str(model_dir),
    ]
    timeout = float(settings.get("TELEGRAM_STT_TIMEOUT_SEC") or os.environ.get("TELEGRAM_STT_TIMEOUT_SEC") or 180)
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        if proc.returncode != 0:
            detail = (stderr or stdout).decode("utf-8", errors="replace")[:1000]
            raise RuntimeError(f"local STT failed with exit {{proc.returncode}}: {{detail}}")
        payload = json.loads(stdout.decode("utf-8", errors="replace") or "{{}}")
        if not payload.get("ok"):
            raise RuntimeError(str(payload.get("error") or "local STT failed"))
        return str(payload.get("text") or "").strip()
    finally:
        try:
            audio_path.unlink()
        except FileNotFoundError:
            pass
'''
    text = text.replace(old, new, 1)
    text = text.replace(
        'api.log("info", "Transcribing audio via OpenAI Whisper API...")',
        'api.log("info", "Transcribing audio via local Whisper backend...")',
        1,
    )
    text = text.replace(
        'api.log("info", f"Whisper transcription success: \'{voice_text}\'")',
        'api.log("info", f"Local STT transcription success: \'{voice_text}\'")',
        1,
    )
    text = text.replace(
        '"Use `/menu` in Telegram to see available commands as inline buttons.\\n\\n"',
        '"Use `/menu` in Telegram to see available commands as inline buttons.\\n\\n"'
        '"**Local STT**: voice messages are transcribed inside Docker with faster-whisper. Configure TELEGRAM_STT_MODEL, TELEGRAM_STT_LANGUAGE, TELEGRAM_STT_DEVICE, TELEGRAM_STT_MODEL_DIR if needed.\\n\\n"',
        1,
    )
    if text == original or MARKER not in text:
        return {"ok": False, "changed": False, "error": "telegram local STT patch did not match expected snippets"}
    plugin.write_text(text, encoding="utf-8")
    return {"ok": True, "changed": True, "path": str(plugin)}
