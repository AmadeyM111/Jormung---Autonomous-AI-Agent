from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from ouroboros.diarization import DiarizationError, align_speakers, run_pyannote_diarization


class FakeAnnotation:
    def __init__(self, turns):
        self.turns = turns

    def itertracks(self, *, yield_label=False):
        assert yield_label is True
        for start, end, label in self.turns:
            yield SimpleNamespace(start=start, end=end), "track", label


def test_run_prefers_exclusive_output_and_forwards_hints(tmp_path, monkeypatch):
    audio = tmp_path / "meeting.wav"
    audio.write_bytes(b"audio")
    monkeypatch.delenv("PYANNOTE_METRICS_ENABLED", raising=False)
    calls = {}

    class Pipeline:
        def to(self, device):
            calls["device"] = str(device)

        def __call__(self, path, **kwargs):
            calls["path"] = path
            calls["kwargs"] = kwargs
            return SimpleNamespace(
                exclusive_speaker_diarization=FakeAnnotation([
                    (2.0, 3.0, "guest"),
                    (0.0, 1.5, "host"),
                    (3.0, 4.0, "host"),
                ]),
                speaker_diarization=FakeAnnotation([(0.0, 4.0, "wrong")]),
            )

    def factory(model, **kwargs):
        calls["model"] = model
        calls["factory_kwargs"] = kwargs
        return Pipeline()

    turns = run_pyannote_diarization(
        audio,
        token="secret",
        device="cpu",
        min_speakers=2,
        max_speakers=4,
        pipeline_factory=factory,
    )

    assert calls["model"] == "pyannote/speaker-diarization-community-1"
    assert calls["factory_kwargs"] == {"token": "secret"}
    assert calls["kwargs"] == {"min_speakers": 2, "max_speakers": 4}
    assert calls["path"] == str(audio.resolve())
    assert calls["device"] == "cpu"
    assert os.environ["PYANNOTE_METRICS_ENABLED"] == "0"
    assert turns == [
        {"start": 0.0, "end": 1.5, "speaker": "SPEAKER_00"},
        {"start": 2.0, "end": 3.0, "speaker": "SPEAKER_01"},
        {"start": 3.0, "end": 4.0, "speaker": "SPEAKER_00"},
    ]


def test_local_model_does_not_require_or_forward_token(tmp_path):
    audio = tmp_path / "meeting.wav"
    audio.write_bytes(b"audio")
    model = tmp_path / "community-1"
    model.mkdir()
    seen = {}

    def factory(model_ref, **kwargs):
        seen.update(model=model_ref, kwargs=kwargs)
        return lambda _path, **_hints: []

    assert run_pyannote_diarization(audio, model=model, pipeline_factory=factory) == []
    assert seen == {"model": str(model), "kwargs": {}}


def test_remote_model_requires_hugging_face_token(tmp_path, monkeypatch):
    audio = tmp_path / "meeting.wav"
    audio.write_bytes(b"audio")
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HUGGINGFACE_TOKEN", raising=False)

    with pytest.raises(DiarizationError, match="Hugging Face token"):
        run_pyannote_diarization(audio, pipeline_factory=lambda *_args, **_kwargs: None)


def test_invalid_speaker_hints_fail_before_loading_pipeline(tmp_path):
    audio = tmp_path / "meeting.wav"
    audio.write_bytes(b"audio")

    with pytest.raises(DiarizationError, match="cannot exceed"):
        run_pyannote_diarization(
            audio, token="secret", min_speakers=3, max_speakers=2,
            pipeline_factory=lambda *_args, **_kwargs: pytest.fail("must not load"),
        )


def test_align_splits_segment_when_word_speaker_changes():
    segments = [{
        "start": 0.0,
        "end": 3.0,
        "text": "Hello there friend",
        "language": "en",
        "words": [
            {"start": 0.1, "end": 0.6, "word": " Hello"},
            {"start": 0.7, "end": 1.2, "word": " there"},
            {"start": 1.8, "end": 2.4, "word": " friend"},
        ],
    }]
    turns = [
        {"start": 0.0, "end": 1.5, "speaker": "host"},
        {"start": 1.5, "end": 3.0, "speaker": "guest"},
    ]

    aligned = align_speakers(segments, turns)

    assert [(item["text"], item["speaker"]) for item in aligned] == [
        ("Hello there", "SPEAKER_00"),
        ("friend", "SPEAKER_01"),
    ]
    assert aligned[0]["language"] == "en"
    assert aligned[0]["words"][0]["speaker"] == "SPEAKER_00"
    assert aligned[1]["start"] == 1.8


def test_align_uses_near_tolerance_then_segment_fallback():
    near = align_speakers(
        [{"start": 0.0, "end": 2.0, "text": "near", "words": [{"start": 0.8, "end": 0.9, "word": " near"}]}],
        [{"start": 1.0, "end": 2.0, "speaker": "A"}],
        tolerance_sec=0.2,
    )
    assert near[0]["speaker"] == "SPEAKER_00"

    fallback = align_speakers(
        [{"start": 0.0, "end": 4.0, "text": "no word times", "words": []}],
        [
            {"start": 0.0, "end": 1.0, "speaker": "A"},
            {"start": 1.0, "end": 4.0, "speaker": "B"},
        ],
    )
    assert fallback[0]["speaker"] == "SPEAKER_01"


def test_align_reconstructs_unspaced_words_readably():
    aligned = align_speakers(
        [{
            "start": 0,
            "end": 1,
            "text": "Hello, world!",
            "words": [
                {"start": 0.0, "end": 0.2, "word": "Hello"},
                {"start": 0.2, "end": 0.3, "word": ","},
                {"start": 0.3, "end": 0.7, "word": "world"},
                {"start": 0.7, "end": 0.8, "word": "!"},
            ],
        }],
        [{"start": 0, "end": 1, "speaker": "one"}],
    )
    assert aligned[0]["text"] == "Hello, world!"


def test_align_is_pure_and_leaves_segments_unchanged_without_turns():
    segments = [{"start": 0, "end": 1, "text": "hello", "nested": {"kept": True}}]
    result = align_speakers(segments, [])
    assert result == segments
    assert result is not segments
    assert result[0] is not segments[0]
