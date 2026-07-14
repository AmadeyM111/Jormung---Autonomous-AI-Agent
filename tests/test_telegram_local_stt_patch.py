from __future__ import annotations

from ouroboros.telegram_local_stt_patch import MARKER, patch_plugin


def test_telegram_local_stt_patch_replaces_openai_whisper(tmp_path):
    plugin = tmp_path / "plugin.py"
    plugin.write_text(
        '''
async def _transcribe_voice(api, ogg_bytes: bytes) -> str:
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

async def poll():
                                api.log("info", "Transcribing audio via OpenAI Whisper API...")
                                voice_text = await _transcribe_voice(api, ogg_bytes)
                                api.log("info", f"Whisper transcription success: '{voice_text}'")

def register(api):
    api.register_settings_section(
        "telegram",
        title="Telegram Bridge",
        schema={"components": [{"type": "markdown", "text": "Use `/menu` in Telegram to see available commands as inline buttons.\\n\\n"}]},
    )
''',
        encoding="utf-8",
    )

    result = patch_plugin(plugin)
    patched = plugin.read_text(encoding="utf-8")

    assert result["ok"] is True
    assert MARKER in patched
    assert "OPENAI_API_KEY" not in patched
    assert "api.openai.com/v1/audio/transcriptions" not in patched
    assert "python -m ouroboros.local_stt" in patched
    assert "TELEGRAM_STT_MODEL" in patched
    assert "Transcribing audio via local Whisper backend" in patched
    assert patch_plugin(plugin)["changed"] is False
