from __future__ import annotations

import sys
import types


def test_local_stt_transcribe_file_uses_faster_whisper(tmp_path, monkeypatch):
    from ouroboros.local_stt import transcribe_file

    audio = tmp_path / "voice.ogg"
    audio.write_bytes(b"ogg")
    calls = []

    class Segment:
        text = " привет "

    class Info:
        language = "ru"
        duration = 1.5

    class WhisperModel:
        def __init__(self, model, **kwargs):
            calls.append((model, kwargs))

        def transcribe(self, path, **kwargs):
            calls.append((path, kwargs))
            return [Segment()], Info()

    module = types.ModuleType("faster_whisper")
    module.WhisperModel = WhisperModel
    monkeypatch.setitem(sys.modules, "faster_whisper", module)

    result = transcribe_file(
        audio,
        model="small",
        language="ru",
        device="cpu",
        compute_type="auto",
        model_dir=tmp_path / "models",
    )

    assert result["ok"] is True
    assert result["text"] == "привет"
    assert calls[0][0] == "small"
    assert calls[0][1]["device"] == "cpu"
    assert calls[0][1]["compute_type"] == "int8"
    assert calls[0][1]["download_root"] == str(tmp_path / "models")


def test_local_stt_auto_device_defaults_to_cpu_safe_compute_type(tmp_path, monkeypatch):
    from ouroboros.local_stt import transcribe_file

    audio = tmp_path / "voice.ogg"
    audio.write_bytes(b"ogg")
    calls = []

    class Info:
        language = "ru"
        duration = 0.5

    class WhisperModel:
        def __init__(self, model, **kwargs):
            calls.append((model, kwargs))

        def transcribe(self, path, **kwargs):
            return [], Info()

    module = types.ModuleType("faster_whisper")
    module.WhisperModel = WhisperModel
    monkeypatch.setitem(sys.modules, "faster_whisper", module)

    result = transcribe_file(audio, device="auto", compute_type="auto")

    assert result["ok"] is True
    assert calls[0][1]["device"] == "auto"
    assert calls[0][1]["compute_type"] == "int8"
    assert result["compute_type"] == "int8"


def test_local_stt_float16_falls_back_to_int8_when_backend_rejects_it(tmp_path, monkeypatch):
    from ouroboros.local_stt import transcribe_file

    audio = tmp_path / "voice.ogg"
    audio.write_bytes(b"ogg")
    calls = []

    class Info:
        language = "ru"
        duration = 0.5

    class WhisperModel:
        def __init__(self, model, **kwargs):
            calls.append((model, kwargs.copy()))
            if kwargs.get("compute_type") == "float16":
                raise ValueError("Requested float16 compute type is not supported")

        def transcribe(self, path, **kwargs):
            return [], Info()

    module = types.ModuleType("faster_whisper")
    module.WhisperModel = WhisperModel
    monkeypatch.setitem(sys.modules, "faster_whisper", module)

    result = transcribe_file(audio, device="auto", compute_type="float16")

    assert result["ok"] is True
    assert [call[1]["compute_type"] for call in calls] == ["float16", "int8"]
    assert result["compute_type"] == "int8"
