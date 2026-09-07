from __future__ import annotations

import pathlib

from ouroboros.transcription_whisperx import transcribe_with_whisperx


class _FakeWhisperX:
    def __init__(self):
        self.calls = []

    def load_model(self, *args, **kwargs):
        self.calls.append(("load_model", args, kwargs))
        return self

    def load_audio(self, path):
        self.calls.append(("load_audio", path))
        return [0.0]

    def transcribe(self, audio, **kwargs):
        self.calls.append(("transcribe", audio, kwargs))
        return {"language": "ru", "segments": [{"start": 0.0, "end": 2.0, "text": " привет "}]}

    def load_align_model(self, **kwargs):
        self.calls.append(("load_align_model", kwargs))
        return object(), {"language": "ru"}

    def align(self, segments, model, metadata, audio, device, **kwargs):
        self.calls.append(("align", segments, device, kwargs))
        return {"segments": [{
            "start": 0.1,
            "end": 1.9,
            "text": "привет",
            "words": [{"start": 0.1, "end": 1.0, "word": "привет"}],
        }]}


def test_whisperx_adapter_runs_alignment_and_returns_word_timestamps(tmp_path: pathlib.Path):
    source = tmp_path / "audio.wav"
    source.write_bytes(b"audio")
    fake = _FakeWhisperX()
    result = transcribe_with_whisperx(
        source,
        model="large-v3",
        language="ru",
        device="cpu",
        compute_type="int8",
        whisperx_module=fake,
    )
    assert result["language"] == "ru"
    assert result["segments"][0]["words"][0]["start"] == 0.1
    assert any(call[0] == "align" for call in fake.calls)
