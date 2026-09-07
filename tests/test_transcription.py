from __future__ import annotations

import json
import pathlib
import queue
from types import SimpleNamespace

import pytest

from ouroboros.transcription import (
    AudioMetadata,
    build_job_id,
    calculate_chunk_windows,
    load_checkpoint,
    load_whisper_model,
    merge_segments,
    parse_model_routes,
    route_model,
    resolve_diarization,
    resolve_transcription_provider,
    transcribe_audio,
    write_transcript_artifacts,
)


@pytest.mark.parametrize(
    ("duration", "device", "expected", "rule"),
    [
        (900, "cpu", "medium", "900:medium"),
        (900.001, "cpu", "small", "*:small"),
        (1800, "cuda", "large-v3", "1800:large-v3"),
        (1800.001, "cuda", "turbo", "*:turbo"),
        (3600, "unknown", "turbo", "*:turbo"),
    ],
)
def test_duration_model_routing_boundaries(monkeypatch, duration, device, expected, rule):
    monkeypatch.delenv("TRANSCRIPTION_MODEL_ROUTES", raising=False)
    monkeypatch.delenv("TRANSCRIPTION_CPU_MODEL_ROUTES", raising=False)
    assert route_model(duration, device=device) == (expected, rule)


def test_manual_model_override_wins(monkeypatch):
    monkeypatch.setenv("TRANSCRIPTION_MODEL_ROUTES", "1:small,*:small")
    assert route_model(50_000, device="cuda", model="large-v3") == ("large-v3", "manual")


def test_provider_and_diarization_defaults_are_local_and_off(monkeypatch):
    monkeypatch.delenv("TRANSCRIPTION_PROVIDER", raising=False)
    monkeypatch.delenv("TRANSCRIPTION_DIARIZATION", raising=False)
    assert resolve_transcription_provider() == "local"
    assert resolve_transcription_provider("google") == "gemini"
    assert resolve_diarization() == "off"


def test_provider_specific_limits_fail_before_hashing(tmp_path, monkeypatch):
    import ouroboros.transcription as module

    audio = tmp_path / "meeting.wav"
    audio.write_bytes(b"audio")
    metadata = AudioMetadata(audio, 10, 7201, "pcm", 16_000, 1)
    monkeypatch.setattr(module, "validate_audio_file", lambda _path: metadata)
    monkeypatch.setattr(module, "sha256_file", lambda _path: pytest.fail("limit must fail before hashing"))
    with pytest.raises(Exception, match="cloud limit"):
        transcribe_audio(
            audio,
            state_dir=tmp_path / "state",
            output_dir=tmp_path / "out",
            provider="gemini",
        )

    metadata = AudioMetadata(audio, 10, 14401, "pcm", 16_000, 1)
    with pytest.raises(Exception, match="diarization limit"):
        transcribe_audio(
            audio,
            state_dir=tmp_path / "state",
            output_dir=tmp_path / "out",
            diarization="pyannote",
        )


def test_custom_vocabulary_limits_fail_before_file_validation(tmp_path, monkeypatch):
    import ouroboros.transcription as module

    monkeypatch.setattr(module, "validate_audio_file", lambda _path: pytest.fail("vocabulary must validate first"))
    with pytest.raises(Exception, match="200 characters"):
        transcribe_audio(
            tmp_path / "missing.wav",
            state_dir=tmp_path / "state",
            output_dir=tmp_path / "out",
            provider="gemini",
            custom_vocabulary=("x" * 201,),
        )


def test_invalid_route_config_falls_back_to_builtin(caplog):
    routes = parse_model_routes("broken,10:not-a-model", default="900:medium,*:small")
    assert [(item.max_duration_sec, item.model) for item in routes] == [(900, "medium"), (None, "small")]
    assert "built-in routes" in caplog.text


def test_chunk_windows_include_last_partial_chunk():
    assert calculate_chunk_windows(121, 60, 2) == [(0.0, 60.0), (58.0, 118.0), (116.0, 121.0)]


def test_overlap_merge_deduplicates_and_makes_timestamps_monotonic():
    merged = merge_segments(
        [{"start": 0, "end": 5, "text": "Hello world"}],
        [
            {"start": 4.8, "end": 5.2, "text": "hello, world!"},
            {"start": 4.9, "end": 8, "text": "Next sentence"},
        ],
    )
    assert [item["text"] for item in merged] == ["Hello world", "Next sentence"]
    assert merged[1]["start"] == 5.0


def test_job_id_changes_with_significant_parameters():
    base = dict(
        source_sha256="a" * 64,
        source_size=12,
        requested_model="auto",
        selected_model="small",
        language="auto",
        chunk_duration_sec=900,
        overlap_sec=2,
    )
    first = build_job_id(**base)
    assert build_job_id(**{**base, "overlap_sec": 3}) != first
    assert build_job_id(**{**base, "language": "ru"}) != first
    assert build_job_id(**base, provider="gemini", provider_model="gemini-3.5-transcribe") != first
    assert build_job_id(**base, diarization="pyannote", diarization_model="community-1") != first


def test_corrupt_checkpoint_is_backed_up(tmp_path):
    checkpoint = tmp_path / "job" / "checkpoint.json"
    checkpoint.parent.mkdir()
    checkpoint.write_text("{broken", encoding="utf-8")
    loaded, warning = load_checkpoint(checkpoint, job_id="job")
    assert loaded is None
    assert "restarted" in warning
    assert list(checkpoint.parent.glob("checkpoint.corrupt.*.json"))


def test_model_load_fallback_chain():
    calls = []

    def factory(model, **kwargs):
        calls.append(model)
        if model != "small":
            raise RuntimeError("out of memory")
        return object()

    _, actual, fallback_from, _ = load_whisper_model(
        "large-v3", device="cpu", model_factory=factory, allow_fallback=True,
    )
    assert calls == ["large-v3", "turbo", "small"]
    assert actual == "small"
    assert fallback_from == "large-v3"


def test_transcript_formats(tmp_path):
    source_path = tmp_path / "recording.m4a"
    source_path.write_bytes(b"audio")
    source = AudioMetadata(source_path, 5, 65, "aac", 44_100, 2)
    paths = write_transcript_artifacts(
        tmp_path / "out",
        source=source,
        source_sha256="f" * 64,
        language="ru",
        requested_model="auto",
        selected_model="small",
        routing_rule="*:small",
        segments=[{"start": 3.12, "end": 11.48, "text": "Добрый день."}],
        output_formats=("md", "txt", "json"),
    )
    assert [path.name for path in paths] == [
        "recording.transcript.md", "recording.transcript.txt", "recording.transcript.json",
    ]
    assert "[00:00:03 — 00:00:11]" in paths[0].read_text(encoding="utf-8")
    payload = json.loads(paths[2].read_text(encoding="utf-8"))
    assert payload["segments"][0]["text"] == "Добрый день."


def test_transcript_formats_render_speaker_labels(tmp_path):
    source_path = tmp_path / "meeting.wav"
    source_path.write_bytes(b"audio")
    source = AudioMetadata(source_path, 5, 5, "pcm", 16_000, 1)
    paths = write_transcript_artifacts(
        tmp_path / "out",
        source=source,
        source_sha256="e" * 64,
        language="ru",
        requested_model="auto",
        selected_model="gemini-3.5-transcribe",
        routing_rule="provider:gemini",
        segments=[{"start": 0, "end": 2, "text": "Здравствуйте", "speaker": "SPEAKER_00"}],
        output_formats=("md", "json"),
        provider="gemini",
        diarization="provider",
        diarization_status="completed",
        speaker_scope="recording",
    )
    assert "SPEAKER_00: Здравствуйте" in paths[0].read_text(encoding="utf-8")
    payload = json.loads(paths[1].read_text(encoding="utf-8"))
    assert payload["transcription"]["provider"] == "gemini"
    assert payload["segments"][0]["speaker"] == "SPEAKER_00"


def test_gemini_pipeline_chunks_checkpoints_and_marks_native_speakers_chunk_scoped(tmp_path, monkeypatch):
    import ouroboros.transcription as module

    audio = tmp_path / "meeting.wav"
    audio.write_bytes(b"unchanged")
    metadata = AudioMetadata(audio, audio.stat().st_size, 1801, "pcm_s16le", 16_000, 1)
    monkeypatch.setattr(module, "validate_audio_file", lambda _path: metadata)
    monkeypatch.setattr(module, "sha256_file", lambda _path: "d" * 64)
    monkeypatch.setattr(module, "_iter_pcm_chunks", lambda *_args: iter([object(), object()]))
    monkeypatch.setattr(module, "_write_waveform_wav", lambda path, _waveform: path.write_bytes(b"wav"))

    class FakeGemini:
        def __init__(self):
            self.calls = []

        def transcribe_file(self, path, **kwargs):
            self.calls.append((path.name, kwargs))
            if len(self.calls) == 2:
                return {
                    "language": "ru",
                    "segments": [{"start": 0.0, "end": 0.0, "text": "без таймкодов"}],
                    "warnings": ["Gemini returned text without usable word timestamps."],
                }
            return {
                "language": "ru",
                "segments": [{
                    "start": 1.0,
                    "end": 2.0,
                    "text": "реплика",
                    "speaker": "spk_1",
                    "words": [{"start": 1.0, "end": 2.0, "word": "реплика", "speaker": "spk_1"}],
                }],
                "warnings": [],
            }

    client = FakeGemini()
    result = transcribe_audio(
        audio,
        state_dir=tmp_path / "state",
        output_dir=tmp_path / "artifacts",
        provider="gemini",
        diarization="provider",
        chunk_duration_sec=3600,
        overlap_sec=2,
        gemini_client=client,
    )
    assert len(client.calls) == 2
    assert result["provider"] == "gemini"
    assert result["diarization"]["speaker_scope"] == "chunk"
    transcript = json.loads(next(path for path in result["artifacts"] if path.suffix == ".json").read_text(encoding="utf-8"))
    assert transcript["segments"][0]["speaker"] == "CHUNK_001_spk_1"
    assert transcript["segments"][-1]["text"] == "без таймкодов"
    assert transcript["segments"][-1]["end"] == 1801.0

    class MustNotRun:
        def transcribe_file(self, *_args, **_kwargs):
            pytest.fail("completed Gemini chunks must be resumed from checkpoint")

    resumed = transcribe_audio(
        audio,
        state_dir=tmp_path / "state",
        output_dir=tmp_path / "artifacts",
        provider="gemini",
        diarization="provider",
        chunk_duration_sec=3600,
        overlap_sec=2,
        gemini_client=MustNotRun(),
    )
    assert resumed["resumed"] is True


def test_pyannote_postprocessing_reuses_completed_local_stt(tmp_path, monkeypatch):
    import ouroboros.transcription as module

    audio = tmp_path / "meeting.wav"
    audio.write_bytes(b"unchanged")
    metadata = AudioMetadata(audio, audio.stat().st_size, 30, "pcm_s16le", 16_000, 1)
    monkeypatch.setattr(module, "validate_audio_file", lambda _path: metadata)
    monkeypatch.setattr(module, "sha256_file", lambda _path: "c" * 64)
    monkeypatch.setattr(module, "detect_device", lambda _device: "cpu")
    monkeypatch.setattr(module, "_iter_pcm_chunks", lambda *_args: iter([object()]))
    monkeypatch.setenv("HF_TOKEN", "hf-test")

    class FakeModel:
        def transcribe(self, _waveform, **_kwargs):
            word = SimpleNamespace(start=0.1, end=0.8, word=" привет")
            segment = SimpleNamespace(start=0.1, end=0.8, text="привет", words=[word])
            return [segment], SimpleNamespace(language="ru", language_probability=1.0)

    first = transcribe_audio(
        audio,
        state_dir=tmp_path / "state",
        output_dir=tmp_path / "artifacts",
        chunk_duration_sec=60,
        model_factory=lambda *_args, **_kwargs: FakeModel(),
        diarization="off",
    )

    class Annotation:
        def itertracks(self, *, yield_label=False):
            assert yield_label
            yield SimpleNamespace(start=0.0, end=1.0), "track", "speaker-a"

    class Pipeline:
        def to(self, _device):
            pass

        def __call__(self, _path, **_kwargs):
            return SimpleNamespace(exclusive_speaker_diarization=Annotation())

    second = transcribe_audio(
        audio,
        state_dir=tmp_path / "state",
        output_dir=tmp_path / "artifacts",
        chunk_duration_sec=60,
        model_factory=lambda *_args, **_kwargs: pytest.fail("pyannote must reuse completed STT"),
        diarization="pyannote",
        diarization_factory=lambda *_args, **_kwargs: Pipeline(),
    )
    assert second["job_id"] == first["job_id"]
    assert second["resumed"] is True
    assert second["diarization"]["status"] == "completed"
    assert second["diarization"]["speakers_count"] == 1


def test_pipeline_checkpoints_and_resumes_without_retranscribing(tmp_path, monkeypatch):
    import ouroboros.transcription as module

    audio = tmp_path / "recording.wav"
    audio.write_bytes(b"unchanged")
    metadata = AudioMetadata(audio, audio.stat().st_size, 121, "pcm_s16le", 16_000, 1)
    monkeypatch.setattr(module, "validate_audio_file", lambda _path: metadata)
    monkeypatch.setattr(module, "sha256_file", lambda _path: "a" * 64)
    monkeypatch.setattr(module, "detect_device", lambda _device: "cpu")
    monkeypatch.setattr(module, "_iter_pcm_chunks", lambda *_args: iter([object(), object(), object()]))

    calls = []

    class FakeModel:
        def transcribe(self, _waveform, **_kwargs):
            index = len(calls)
            calls.append(index)
            segment = SimpleNamespace(start=2.0, end=3.0, text=f"chunk {index}", words=[])
            info = SimpleNamespace(language="ru", language_probability=0.99)
            return [segment], info

    result = transcribe_audio(
        audio,
        state_dir=tmp_path / "state",
        output_dir=tmp_path / "artifacts",
        chunk_duration_sec=60,
        overlap_sec=2,
        model_factory=lambda *_args, **_kwargs: FakeModel(),
    )
    assert result["segments_count"] == 3
    assert len(calls) == 3
    assert result["resumed"] is False
    assert all("text" not in item for item in result["artifacts"] if isinstance(item, dict))

    resumed = transcribe_audio(
        audio,
        state_dir=tmp_path / "state",
        output_dir=tmp_path / "artifacts",
        chunk_duration_sec=60,
        overlap_sec=2,
        model_factory=lambda *_args, **_kwargs: pytest.fail("completed chunks must not be retranscribed"),
    )
    assert resumed["resumed"] is True
    assert len(calls) == 3


def test_auto_routing_reselects_cpu_model_when_cuda_init_fails(tmp_path, monkeypatch):
    import ouroboros.transcription as module

    audio = tmp_path / "recording.wav"
    audio.write_bytes(b"unchanged")
    metadata = AudioMetadata(audio, audio.stat().st_size, 61, "pcm_s16le", 16_000, 1)
    monkeypatch.setattr(module, "validate_audio_file", lambda _path: metadata)
    monkeypatch.setattr(module, "sha256_file", lambda _path: "b" * 64)
    monkeypatch.setattr(module, "detect_device", lambda _device: "unknown")
    monkeypatch.setattr(module, "_iter_pcm_chunks", lambda *_args: iter([object(), object()]))

    class FakeModel:
        def transcribe(self, _waveform, **_kwargs):
            return [], SimpleNamespace(language="", language_probability=0)

    calls = []

    def factory(model, **kwargs):
        calls.append((model, kwargs["device"]))
        if kwargs["device"] != "cpu":
            raise RuntimeError("CUDA initialization failed")
        return FakeModel()

    result = transcribe_audio(
        audio,
        state_dir=tmp_path / "state",
        output_dir=tmp_path / "artifacts",
        chunk_duration_sec=60,
        overlap_sec=2,
        model_factory=factory,
    )
    assert calls[:3] == [("large-v3", "auto"), ("turbo", "auto"), ("small", "auto")]
    assert ("medium", "cpu") in calls
    assert result["selected_model"] == "medium"
    assert result["routing"]["matched_rule"] == "900:medium"


def test_tool_path_confinement(tmp_path):
    from ouroboros.tools.registry import ToolContext
    from ouroboros.tools.transcription import resolve_audio_path

    repo = tmp_path / "workspace"
    drive = tmp_path / "data"
    repo.mkdir()
    (drive / "uploads").mkdir(parents=True)
    audio = drive / "uploads" / "voice.ogg"
    audio.write_bytes(b"ogg")
    ctx = ToolContext(repo_dir=repo, drive_root=drive)
    assert resolve_audio_path(ctx, str(audio)) == audio.resolve()
    outside = tmp_path / "outside.ogg"
    outside.write_bytes(b"ogg")
    with pytest.raises(Exception, match="outside"):
        resolve_audio_path(ctx, str(outside))


@pytest.mark.parametrize("codec", ["aac", "alac"])
def test_real_m4a_container_validation_and_streaming_decode(tmp_path, codec):
    av = pytest.importorskip("av")
    np = pytest.importorskip("numpy")
    from ouroboros.transcription import _iter_pcm_chunks, validate_audio_file

    path = tmp_path / f"fixture-{codec}.m4a"
    with av.open(str(path), "w", format="ipod") as container:
        stream = container.add_stream(codec, rate=16_000)
        stream.layout = "mono"
        samples = (np.sin(2 * np.pi * 440 * np.arange(16_000) / 16_000) * 10_000).astype(np.int16)
        for start in range(0, len(samples), 1024):
            frame = av.AudioFrame.from_ndarray(samples[start:start + 1024].reshape(1, -1), format="s16", layout="mono")
            frame.sample_rate = 16_000
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)

    metadata = validate_audio_file(path)
    chunks = list(_iter_pcm_chunks(path, 60, 2))
    assert metadata.codec == codec
    assert 0.9 <= metadata.duration_sec <= 1.2
    assert len(chunks) == 1
    assert chunks[0].dtype == np.float32
    assert chunks[0].size >= 16_000


def test_agent_tool_registers_artifacts_without_returning_transcript_text(tmp_path, monkeypatch):
    import ouroboros.tools.transcription as tool_module
    from ouroboros.tools.registry import ToolContext

    repo = tmp_path / "repo"
    drive = tmp_path / "data"
    repo.mkdir()
    (drive / "uploads").mkdir(parents=True)
    source = drive / "uploads" / "recording.m4a"
    source.write_bytes(b"audio")
    ctx = ToolContext(repo_dir=repo, drive_root=drive, task_id="task-1", current_chat_id=42)
    monkeypatch.setenv("TRANSCRIPTION_PROVIDER", "gemini")

    def fake_pipeline(_source, **kwargs):
        assert kwargs["provider"] == "local"
        artifact = kwargs["output_dir"] / "recording.transcript.txt"
        artifact.write_text("secret transcript body", encoding="utf-8")
        return {
            "ok": True,
            "job_id": "job",
            "duration_sec": 1.0,
            "language": "ru",
            "requested_model": "auto",
            "selected_model": "small",
            "routing": {"strategy": "duration", "matched_rule": "*:small"},
            "resumed": False,
            "segments_count": 1,
            "artifacts": [artifact],
            "warnings": [],
        }

    monkeypatch.setattr(tool_module, "run_transcription", fake_pipeline)
    payload = json.loads(tool_module._transcribe_audio_tool(ctx, str(source)))
    assert payload["artifacts"] == [{"name": "recording.transcript.txt"}]
    assert "secret transcript body" not in json.dumps(payload)
    assert ctx.pending_events == [{
        "type": "send_document",
        "chat_id": 42,
        "file_path": str(drive / "task_results" / "artifacts" / "task-1" / "recording.transcript.txt"),
        "filename": "recording.transcript.txt",
        "caption": "Стенограмма аудиозаписи",
    }]
    manifest = drive / "task_results" / "artifacts" / "task-1" / ".artifact_manifest.json"
    assert manifest.exists()


def test_agent_tool_is_registered_with_twelve_hour_timeout(tmp_path):
    from ouroboros.tools.registry import ToolRegistry

    registry = ToolRegistry(repo_dir=tmp_path / "repo", drive_root=tmp_path / "data")
    assert registry.get_schema_by_name("transcribe_audio") is not None
    assert registry.get_timeout("transcribe_audio") == 12 * 60 * 60
    properties = registry.get_schema_by_name("transcribe_audio")["function"]["parameters"]["properties"]
    assert properties["provider"]["enum"] == ["local", "gemini", "whisperx"]
    assert properties["provider"]["default"] == "local"
    assert "pyannote" in properties["diarization"]["enum"]


def test_whisperx_provider_is_supported():
    from ouroboros.transcription import resolve_transcription_provider

    assert resolve_transcription_provider("whisperx") == "whisperx"


def test_transcript_document_prefers_live_event_queue(tmp_path):
    from ouroboros.tools.registry import ToolContext
    from ouroboros.tools.transcription import _queue_transcript_document

    event_queue = queue.Queue()
    ctx = ToolContext(
        repo_dir=tmp_path, drive_root=tmp_path, current_chat_id=42,
        event_queue=event_queue,
    )
    _queue_transcript_document(ctx, {"path": "/tmp/recording.txt", "name": "recording.txt"})

    assert ctx.pending_events == []
    assert event_queue.get_nowait()["type"] == "send_document"


def test_direct_chat_transcript_document_uses_live_bridge(tmp_path, monkeypatch):
    from ouroboros.tools.registry import ToolContext
    from ouroboros.tools.transcription import _deliver_transcript_document

    transcript = tmp_path / "recording.txt"
    transcript.write_bytes(b"transcript")
    sent = []
    bridge = SimpleNamespace(
        send_document=lambda chat_id, data, filename, caption="", mime="": (
            sent.append((chat_id, data, filename, caption, mime)) or (True, "ok")
        ),
    )
    monkeypatch.setattr("supervisor.message_bus.try_get_bridge", lambda: bridge)
    ctx = ToolContext(
        repo_dir=tmp_path, drive_root=tmp_path, current_chat_id=42,
        is_direct_chat=True,
    )

    _deliver_transcript_document(ctx, {"path": str(transcript), "name": transcript.name})

    assert sent == [(42, b"transcript", "recording.txt", "Стенограмма аудиозаписи", "text/plain")]
    assert ctx.pending_events == []
