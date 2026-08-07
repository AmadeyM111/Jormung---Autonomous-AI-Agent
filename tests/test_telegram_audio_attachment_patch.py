from __future__ import annotations

import pathlib

from ouroboros.telegram_audio_attachment_patch import MARKER, _OLD_BLOCK, patch_plugin


def _plugin_fixture() -> str:
    return '''from __future__ import annotations
import os
import pathlib
from typing import Any, Dict
import httpx

def _load_settings(api):
    return {}

def _data_dir(api):
    return pathlib.Path("/tmp/data")

def _make_poller(api):
    async def poller():
        while True:
            if True:
                if True:
                    safe_text = ""
                    message = {}
                    client = None
                    chat_id = 1
                    lang = "ru"
''' + _OLD_BLOCK + '''        return None
    return poller
'''


def test_patch_adds_streaming_audio_ingestion_and_is_idempotent(tmp_path):
    plugin = tmp_path / "plugin.py"
    plugin.write_text(_plugin_fixture(), encoding="utf-8")

    first = patch_plugin(plugin)
    second = patch_plugin(plugin)

    assert first["ok"] is True and first["changed"] is True
    assert second["ok"] is True and second["changed"] is False
    text = plugin.read_text(encoding="utf-8")
    assert MARKER in text
    assert "downloader.stream" in text
    assert "validate_audio_file" in text
    assert "[Attached file:" in text
    assert "Audio received" in text
    compile(text, str(plugin), "exec")


def test_colab_bootstrap_wrapper_targets_official_bridge(tmp_path):
    from ouroboros.colab_bootstrap import patch_telegram_bridge_audio_attachments

    plugin = tmp_path / "skills" / "ouroboroshub" / "telegram-bridge" / "plugin.py"
    plugin.parent.mkdir(parents=True)
    plugin.write_text(_plugin_fixture(), encoding="utf-8")

    result = patch_telegram_bridge_audio_attachments(tmp_path)

    assert result["ok"] is True
    assert MARKER in plugin.read_text(encoding="utf-8")
