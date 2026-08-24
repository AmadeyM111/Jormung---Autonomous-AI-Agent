from __future__ import annotations

import pathlib
from typing import Any, Dict

from ouroboros.telegram_audio_attachment_patch import (
    MARKER,
    MAX_REVIEW_FILE_BYTES,
    SUPPORT_MODULE,
    _AUDIO_META_HELPER,
    _DETAILED_INGESTION_ERROR,
    _FILTERED_AUDIO_META,
    _OLD_BLOCK,
    _OPAQUE_INGESTION_ERROR,
    _PASSIVE_AGENT_REQUEST,
    _REQUIRED_TOOL_REQUEST,
    _RETRYING_DOWNLOAD,
    _SINGLE_ATTEMPT_DOWNLOAD,
    _UNFILTERED_AUDIO_META,
    _XLSX_HELPER,
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


def test_audio_metadata_filter_rejects_spreadsheets_and_accepts_supported_audio():
    namespace = {"pathlib": pathlib, "Any": Any, "Dict": Dict}
    exec(_AUDIO_META_HELPER, namespace)  # pylint: disable=exec-used
    is_audio = namespace["_is_transcription_audio"]

    assert is_audio({"file_name": "meeting.m4a", "mime_type": "audio/mp4"}) is True
    assert is_audio({"file_name": "voice", "mime_type": "audio/ogg"}) is True
    assert is_audio({"file_name": "report.xlsx", "mime_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}) is False
    assert is_audio({"file_name": "report.xlsx", "mime_type": "application/octet-stream"}) is False


def test_xlsx_metadata_filter_requires_xlsx_extension_and_safe_mime():
    namespace = {"pathlib": pathlib, "Any": Any, "Dict": Dict}
    exec(_XLSX_HELPER, namespace)  # pylint: disable=exec-used
    is_xlsx = namespace["_is_xlsx_document"]

    assert is_xlsx({
        "file_name": "отчёт.xlsx",
        "mime_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }) is True
    assert is_xlsx({"file_name": "report.xlsx", "mime_type": "application/octet-stream"}) is True
    assert is_xlsx({"file_name": "report.xls", "mime_type": "application/octet-stream"}) is False
    assert is_xlsx({"file_name": "report.xlsx", "mime_type": "text/plain"}) is False


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
    assert "for attempt in range(3)" in text
    assert "detail = str(exc).strip() or repr(exc)" in text
    assert "Telegram audio ingestion failed: {type(exc).__name__}: {detail[:500]}" in text
    assert "document_meta if _is_transcription_audio(document_meta) else {}" in text
    assert "spreadsheet_meta = document_meta if _is_xlsx_document(document_meta) else {}" in text
    assert "from ouroboros" not in text
    assert "Call the transcribe_audio tool immediately" in text
    assert "Call the read_spreadsheet tool immediately" in text
    assert "async def _download_xlsx_document" in text
    assert "Do not inspect source code" in text
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
    old = old.replace(_RETRYING_DOWNLOAD, _SINGLE_ATTEMPT_DOWNLOAD, 1)
    old = old.replace(_AUDIO_META_HELPER + "\n\n", "", 1)
    support.write_text(old, encoding="utf-8")
    entry = plugin.read_text(encoding="utf-8").replace(
        _REQUIRED_TOOL_REQUEST,
        _PASSIVE_AGENT_REQUEST,
        1,
    )
    entry = entry.replace(_DETAILED_INGESTION_ERROR, _OPAQUE_INGESTION_ERROR, 1)
    entry = entry.replace(_FILTERED_AUDIO_META, _UNFILTERED_AUDIO_META, 1)
    entry = entry.replace("    _is_transcription_audio,\n", "", 1)
    plugin.write_text(entry, encoding="utf-8")

    result = patch_plugin(plugin)

    assert result["ok"] is True and result["changed"] is True
    upgraded = support.read_text(encoding="utf-8")
    assert "from ouroboros" not in upgraded
    assert "Telegram audio download is incomplete" in upgraded
    assert "for attempt in range(3)" in upgraded
    assert "def _is_transcription_audio(" in upgraded
    assert "def _is_xlsx_document(" in upgraded
    assert "Call the transcribe_audio tool immediately" in plugin.read_text(encoding="utf-8")
    assert "Call the read_spreadsheet tool immediately" in plugin.read_text(encoding="utf-8")
    assert "detail = str(exc).strip() or repr(exc)" in plugin.read_text(encoding="utf-8")
    assert "document_meta if _is_transcription_audio(document_meta) else {}" in plugin.read_text(encoding="utf-8")
    assert patch_plugin(plugin)["changed"] is False


def test_colab_bootstrap_wrapper_targets_official_bridge(tmp_path):
    from ouroboros.colab_bootstrap import patch_telegram_bridge_audio_attachments

    plugin = tmp_path / "skills" / "ouroboroshub" / "telegram-bridge" / "plugin.py"
    plugin.parent.mkdir(parents=True)
    plugin.write_text(_plugin_fixture(), encoding="utf-8")

    result = patch_telegram_bridge_audio_attachments(tmp_path)

    assert result["ok"] is True
    assert MARKER in plugin.read_text(encoding="utf-8")
