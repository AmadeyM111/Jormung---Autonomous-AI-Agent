from __future__ import annotations

import pathlib

from ouroboros.telegram_audio_attachment_patch import (
    MARKER,
    MAX_REVIEW_FILE_BYTES,
    SUPPORT_MODULE,
    _OLD_BLOCK,
    patch_plugin,
)


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
    assert "Telegram audio download is incomplete" in text
    assert "from ouroboros" not in text
    assert "[Attached file:" in text
    assert "Audio received" in text
    compile(text, str(plugin), "exec")


def test_patch_splits_oversized_plugin_for_review(tmp_path):
    plugin = tmp_path / "plugin.py"
    text = _plugin_fixture().replace(
        "def _make_poller(api):",
        f"# {'p' * 25_000}\ndef _make_poller(api):",
    ).replace(
        "                    photos = message.get(\"photo\") or []",
        f"                    # {'s' * 40_000}\n                    photos = message.get(\"photo\") or []",
    )
    plugin.write_text(text, encoding="utf-8")

    result = patch_plugin(plugin)

    support = plugin.with_name(SUPPORT_MODULE)
    assert result["ok"] is True
    assert support.is_file()
    assert plugin.stat().st_size <= MAX_REVIEW_FILE_BYTES
    assert support.stat().st_size <= MAX_REVIEW_FILE_BYTES
    assert MARKER in plugin.read_text(encoding="utf-8")
    assert "async def _download_transcription_audio" in support.read_text(encoding="utf-8")
    compile(plugin.read_text(encoding="utf-8"), str(plugin), "exec")
    compile(support.read_text(encoding="utf-8"), str(support), "exec")
    assert patch_plugin(plugin)["changed"] is False


def test_patch_upgrades_split_helper_without_core_runtime_import(tmp_path):
    plugin = tmp_path / "plugin.py"
    text = _plugin_fixture().replace(
        "def _make_poller(api):",
        f"# {'p' * 25_000}\ndef _make_poller(api):",
    ).replace(
        "                    photos = message.get(\"photo\") or []",
        f"                    # {'s' * 40_000}\n                    photos = message.get(\"photo\") or []",
    )
    plugin.write_text(text, encoding="utf-8")
    assert patch_plugin(plugin)["ok"] is True
    support = plugin.with_name(SUPPORT_MODULE)
    old = support.read_text(encoding="utf-8").replace(
        '''        actual_size = int(destination.stat().st_size)
        if actual_size <= 0:
            raise ValueError("Telegram audio download is empty")
        if actual_size > max_bytes:
            raise ValueError(f"Telegram audio exceeds the {max_bytes}-byte limit")
        if declared_size and actual_size != declared_size:
            raise ValueError("Telegram audio download is incomplete")
''',
        '''        from ouroboros.transcription import validate_audio_file

        validate_audio_file(destination, max_bytes=max_bytes)
''',
    )
    support.write_text(old, encoding="utf-8")

    result = patch_plugin(plugin)

    assert result["ok"] is True and result["changed"] is True
    upgraded = support.read_text(encoding="utf-8")
    assert "from ouroboros" not in upgraded
    assert "Telegram audio download is incomplete" in upgraded
    assert patch_plugin(plugin)["changed"] is False


def test_colab_bootstrap_wrapper_targets_official_bridge(tmp_path):
    from ouroboros.colab_bootstrap import patch_telegram_bridge_audio_attachments

    plugin = tmp_path / "skills" / "ouroboroshub" / "telegram-bridge" / "plugin.py"
    plugin.parent.mkdir(parents=True)
    plugin.write_text(_plugin_fixture(), encoding="utf-8")

    result = patch_telegram_bridge_audio_attachments(tmp_path)

    assert result["ok"] is True
    assert MARKER in plugin.read_text(encoding="utf-8")
