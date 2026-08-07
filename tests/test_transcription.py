from __future__ import annotations

import json
import pathlib
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
    ctx = ToolContext(repo_dir=repo, drive_root=drive, task_id="task-1")

    def fake_pipeline(_source, **kwargs):
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
    manifest = drive / "task_results" / "artifacts" / "task-1" / ".artifact_manifest.json"
    assert manifest.exists()


def test_agent_tool_is_registered_with_twelve_hour_timeout(tmp_path):
    from ouroboros.tools.registry import ToolRegistry

    registry = ToolRegistry(repo_dir=tmp_path / "repo", drive_root=tmp_path / "data")
    assert registry.get_schema_by_name("transcribe_audio") is not None
    assert registry.get_timeout("transcribe_audio") == 12 * 60 * 60
