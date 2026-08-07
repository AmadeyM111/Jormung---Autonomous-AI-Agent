"""Streaming, resumable local audio transcription pipeline.

The module deliberately keeps transcript text out of return values and logs.
Callers receive only metadata and artifact paths; segment text lives in the
checkpoint and generated task artifacts.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import pathlib
import re
import tempfile
import threading
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Callable, Iterable, Iterator, Sequence

log = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = frozenset({".m4a", ".mp4", ".mp3", ".wav", ".flac", ".ogg", ".opus"})
SUPPORTED_MIME_TYPES = frozenset({
    "audio/mp4", "audio/x-m4a", "application/mp4", "audio/mpeg", "audio/wav",
    "audio/x-wav", "audio/flac", "audio/x-flac", "audio/ogg", "audio/opus",
})
SUPPORTED_MODELS = frozenset({"large-v3", "turbo", "medium", "small"})
DEFAULT_GPU_ROUTES = "1800:large-v3,*:turbo"
DEFAULT_CPU_ROUTES = "900:medium,*:small"
ROUTING_VERSION = 1
SAMPLE_RATE = 16_000
_MODEL_FALLBACKS: dict[str, tuple[str, ...]] = {
    "large-v3": ("turbo", "small"),
    "turbo": ("small",),
    "medium": ("small",),
    "small": (),
}


def transcription_setting(name: str, default: Any = None) -> Any:
    """Read an operator setting with environment taking precedence."""
    if name in os.environ:
        return os.environ[name]
    try:
        from ouroboros.config import load_settings

        value = load_settings().get(name)
        if value is not None and str(value).strip() != "":
            return value
    except Exception:
        pass
    return default


class TranscriptionError(RuntimeError):
    """User-facing transcription failure."""


class InvalidAudioError(TranscriptionError):
    """The uploaded object is not a supported, decodable audio file."""


@dataclass(frozen=True)
class AudioMetadata:
    path: pathlib.Path
    size: int
    duration_sec: float
    codec: str
    sample_rate: int
    channels: int


@dataclass(frozen=True)
class ModelRoute:
    max_duration_sec: float | None
    model: str

    @property
    def rule(self) -> str:
        maximum = "*" if self.max_duration_sec is None else str(int(self.max_duration_sec))
        return f"{maximum}:{self.model}"


def _env_int(name: str, default: int, *, minimum: int = 0) -> int:
    try:
        value = int(str(transcription_setting(name, default)).strip())
    except (TypeError, ValueError):
        return default
    return value if value >= minimum else default


def _env_bool(name: str, default: bool) -> bool:
    raw = str(transcription_setting(name, "true" if default else "false")).strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return default


def parse_model_routes(value: str, *, default: str) -> tuple[ModelRoute, ...]:
    """Parse ``seconds:model,*:model`` or safely fall back to built-ins."""

    def parse(raw: str) -> tuple[ModelRoute, ...]:
        routes: list[ModelRoute] = []
        last_max = -1.0
        for index, item in enumerate(str(raw or "").split(",")):
            pair = item.strip().split(":", 1)
            if len(pair) != 2 or pair[1].strip() not in SUPPORTED_MODELS:
                raise ValueError("invalid model route")
            limit_text, model = pair[0].strip(), pair[1].strip()
            if limit_text == "*":
                if index != len(str(raw).split(",")) - 1:
                    raise ValueError("wildcard route must be last")
                routes.append(ModelRoute(None, model))
                continue
            limit = float(limit_text)
            if not math.isfinite(limit) or limit <= last_max or limit <= 0:
                raise ValueError("route limits must be positive and increasing")
            last_max = limit
            routes.append(ModelRoute(limit, model))
        if not routes or routes[-1].max_duration_sec is not None:
            raise ValueError("model routes require a terminal wildcard")
        return tuple(routes)

    try:
        return parse(value)
    except (TypeError, ValueError):
        if str(value or "").strip() and str(value or "").strip() != default:
            log.warning("Invalid transcription model routing configuration; using built-in routes")
        return parse(default)


def configured_model_routes(*, cpu: bool) -> tuple[ModelRoute, ...]:
    env_name = "TRANSCRIPTION_CPU_MODEL_ROUTES" if cpu else "TRANSCRIPTION_MODEL_ROUTES"
    default = DEFAULT_CPU_ROUTES if cpu else DEFAULT_GPU_ROUTES
    return parse_model_routes(str(transcription_setting(env_name, default)), default=default)


def route_model(duration_sec: float, *, device: str, model: str = "auto") -> tuple[str, str]:
    requested = str(model or "auto").strip().lower()
    if requested != "auto":
        if requested not in SUPPORTED_MODELS:
            raise TranscriptionError(f"Unsupported transcription model: {requested}")
        return requested, "manual"
    strategy = str(transcription_setting("TRANSCRIPTION_MODEL_ROUTING", "duration") or "duration").strip().lower()
    if strategy != "duration":
        log.warning("Unsupported transcription routing strategy %r; using duration", strategy)
    routes = configured_model_routes(cpu=str(device or "unknown").lower() == "cpu")
    for route in routes:
        if route.max_duration_sec is None or duration_sec <= route.max_duration_sec:
            return route.model, route.rule
    raise AssertionError("terminal wildcard route missing")


def detect_device(requested: str = "auto") -> str:
    clean = str(requested or "auto").strip().lower()
    if clean in {"cpu", "cuda"}:
        return clean
    try:
        import ctranslate2

        return "cuda" if int(ctranslate2.get_cuda_device_count()) > 0 else "cpu"
    except Exception:
        # The specification assigns unknown hardware to the GPU/general policy.
        return "unknown"


def calculate_chunk_windows(duration_sec: float, chunk_duration_sec: int = 900, overlap_sec: int = 2) -> list[tuple[float, float]]:
    validate_chunk_parameters(chunk_duration_sec, overlap_sec)
    duration = max(0.0, float(duration_sec))
    if duration == 0:
        return []
    step = chunk_duration_sec - overlap_sec
    windows: list[tuple[float, float]] = []
    start = 0.0
    while start < duration:
        windows.append((start, min(duration, start + chunk_duration_sec)))
        if start + chunk_duration_sec >= duration:
            break
        start += step
    return windows


def validate_chunk_parameters(chunk_duration_sec: int, overlap_sec: int) -> None:
    if not 60 <= int(chunk_duration_sec) <= 3600:
        raise TranscriptionError("Chunk duration must be between 60 and 3600 seconds")
    if not 0 <= int(overlap_sec) <= 10:
        raise TranscriptionError("Chunk overlap must be between 0 and 10 seconds")
    if int(overlap_sec) >= int(chunk_duration_sec):
        raise TranscriptionError("Chunk overlap must be shorter than the chunk")


def validate_audio_file(
    path: pathlib.Path | str,
    *,
    max_bytes: int | None = None,
    max_duration_sec: int | None = None,
) -> AudioMetadata:
    """Open a container with PyAV and return trusted audio stream metadata."""
    source = pathlib.Path(path).expanduser().resolve(strict=False)
    if not source.is_file():
        raise FileNotFoundError(f"Audio file not found: {source.name}")
    if source.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise InvalidAudioError(f"Unsupported audio format: {source.suffix or 'unknown'}")
    size = int(source.stat().st_size)
    size_limit = max_bytes if max_bytes is not None else _env_int(
        "OUROBOROS_AUDIO_UPLOAD_MAX_BYTES", 1024 * 1024 * 1024, minimum=1,
    )
    if size > size_limit:
        raise InvalidAudioError(f"Audio file exceeds the {size_limit}-byte limit")
    try:
        import av
    except Exception as exc:  # pragma: no cover - dependency/build failure
        raise TranscriptionError("PyAV is not installed") from exc
    try:
        with av.open(str(source), mode="r") as container:
            stream = next((item for item in container.streams if item.type == "audio"), None)
            if stream is None:
                raise InvalidAudioError("Container has no audio stream")
            codec = str(getattr(getattr(stream, "codec_context", None), "name", "") or "unknown")
            duration = 0.0
            if getattr(stream, "duration", None) is not None and getattr(stream, "time_base", None) is not None:
                duration = float(stream.duration * stream.time_base)
            elif getattr(container, "duration", None) is not None:
                duration = float(container.duration) / float(getattr(av, "time_base", 1_000_000))
            if not math.isfinite(duration) or duration <= 0:
                # Scan timestamps/samples without retaining PCM when a streaming
                # container omits duration from its header.
                decoded_any = False
                decoded_samples = 0
                timestamp_end = 0.0
                for frame in container.decode(stream):
                    decoded_any = True
                    frame_time = float(getattr(frame, "time", 0.0) or 0.0)
                    frame_samples = int(getattr(frame, "samples", 0) or 0)
                    frame_rate = int(getattr(frame, "sample_rate", 0) or 0)
                    decoded_samples += frame_samples
                    timestamp_end = max(timestamp_end, frame_time + (frame_samples / frame_rate if frame_rate else 0.0))
                if not decoded_any:
                    raise InvalidAudioError("Audio stream contains no decodable frames")
                duration = timestamp_end or (decoded_samples / int(getattr(stream, "sample_rate", 0) or 1))
            codec_context = getattr(stream, "codec_context", None)
            sample_rate = int(getattr(stream, "sample_rate", 0) or getattr(codec_context, "sample_rate", 0) or 0)
            channels = int(getattr(stream, "channels", 0) or getattr(codec_context, "channels", 0) or 0)
    except InvalidAudioError:
        raise
    except Exception as exc:
        raise InvalidAudioError(f"Damaged or unsupported audio container: {type(exc).__name__}") from exc
    duration_limit = max_duration_sec if max_duration_sec is not None else _env_int(
        "TRANSCRIPTION_MAX_DURATION_SEC", 12 * 60 * 60, minimum=1,
    )
    if duration > duration_limit:
        raise InvalidAudioError(f"Audio duration exceeds the {duration_limit}-second limit")
    return AudioMetadata(source, size, duration, codec, sample_rate, channels)


def sha256_file(path: pathlib.Path, block_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with pathlib.Path(path).open("rb") as handle:
        while True:
            block = handle.read(block_size)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def build_job_id(
    *,
    source_sha256: str,
    source_size: int,
    requested_model: str,
    selected_model: str,
    language: str,
    chunk_duration_sec: int,
    overlap_sec: int,
) -> str:
    identity = {
        "schema_version": 1,
        "source_sha256": source_sha256,
        "source_size": int(source_size),
        "requested_model": requested_model,
        "selected_model": selected_model,
        "routing_version": ROUTING_VERSION,
        "language": language,
        "chunk_duration_sec": int(chunk_duration_sec),
        "overlap_sec": int(overlap_sec),
        "whisper": {
            "vad_filter": True,
            "word_timestamps": True,
            "condition_on_previous_text": False,
            "beam_size": 5,
        },
    }
    raw = json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def atomic_write_json(path: pathlib.Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass


def load_checkpoint(path: pathlib.Path, *, job_id: str) -> tuple[dict[str, Any] | None, str | None]:
    if not path.exists():
        return None, None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("schema_version") != 1 or payload.get("job_id") != job_id:
            raise ValueError("checkpoint schema or job id mismatch")
        if not isinstance(payload.get("segments", []), list):
            raise ValueError("checkpoint segments are invalid")
        return payload, None
    except Exception:
        backup = path.with_name(f"checkpoint.corrupt.{os.getpid()}.json")
        try:
            os.replace(path, backup)
        except OSError:
            backup = path
        warning = f"Damaged checkpoint moved to {backup.name}; transcription restarted"
        log.warning("Damaged transcription checkpoint for job %s was backed up", job_id)
        return None, warning


def _normalized_text(text: str) -> str:
    return re.sub(r"[^\w]+", " ", str(text or "").casefold(), flags=re.UNICODE).strip()


def merge_segments(existing: Sequence[dict[str, Any]], incoming: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge chunk output with overlap de-duplication and monotonic timestamps."""
    combined = [dict(item) for item in existing if str(item.get("text") or "").strip()]
    candidates = sorted(
        (dict(item) for item in incoming if str(item.get("text") or "").strip()),
        key=lambda item: (float(item.get("start", 0.0)), float(item.get("end", 0.0))),
    )
    for candidate in candidates:
        start = max(0.0, float(candidate.get("start", 0.0)))
        end = max(start, float(candidate.get("end", start)))
        text = str(candidate.get("text") or "").strip()
        duplicate = False
        norm = _normalized_text(text)
        for previous in reversed(combined[-4:]):
            p_start = float(previous.get("start", 0.0))
            p_end = float(previous.get("end", p_start))
            temporal_overlap = min(end, p_end) - max(start, p_start)
            near = abs(start - p_start) <= 3.0 and abs(end - p_end) <= 3.0
            p_norm = _normalized_text(str(previous.get("text") or ""))
            similar = bool(norm and p_norm) and (norm == p_norm or SequenceMatcher(None, norm, p_norm).ratio() >= 0.88)
            if similar and (temporal_overlap >= 0 or near):
                duplicate = True
                break
        if duplicate:
            continue
        if combined:
            previous_end = float(combined[-1].get("end", 0.0))
            start = max(start, previous_end)
            end = max(start, end)
        candidate.update({"start": round(start, 3), "end": round(end, 3), "text": text})
        combined.append(candidate)
    return combined


def _iter_pcm_chunks(path: pathlib.Path, chunk_duration_sec: int, overlap_sec: int) -> Iterator[Any]:
    """Yield float32 mono 16 kHz numpy arrays while retaining only one chunk."""
    try:
        import av
        import numpy as np
    except Exception as exc:  # pragma: no cover - dependency/build failure
        raise TranscriptionError("PyAV and NumPy are required for transcription") from exc
    chunk_samples = int(chunk_duration_sec * SAMPLE_RATE)
    overlap_samples = int(overlap_sec * SAMPLE_RATE)
    resampler = av.audio.resampler.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
    buffers: list[Any] = []
    buffered = 0
    yielded = False
    fresh_after_yield = 0

    def append_frame(frame: Any) -> None:
        nonlocal buffered, fresh_after_yield
        array = frame.to_ndarray().reshape(-1).astype(np.float32) / 32768.0
        if array.size:
            buffers.append(array)
            buffered += int(array.size)
            if yielded:
                fresh_after_yield += int(array.size)

    with av.open(str(path), mode="r") as container:
        stream = next((item for item in container.streams if item.type == "audio"), None)
        if stream is None:
            raise InvalidAudioError("Container has no audio stream")
        for decoded in container.decode(stream):
            converted = resampler.resample(decoded)
            for frame in converted if isinstance(converted, list) else [converted]:
                if frame is not None:
                    append_frame(frame)
            while buffered >= chunk_samples:
                joined = np.concatenate(buffers) if len(buffers) > 1 else buffers[0]
                current = joined[:chunk_samples]
                remainder = joined[chunk_samples:]
                yield current
                yielded = True
                prefix = current[-overlap_samples:].copy() if overlap_samples else np.empty(0, dtype=np.float32)
                buffers = [part for part in (prefix, remainder) if part.size]
                buffered = int(prefix.size + remainder.size)
                fresh_after_yield = int(remainder.size)
        flushed = resampler.resample(None)
        for frame in flushed if isinstance(flushed, list) else [flushed]:
            if frame is not None:
                append_frame(frame)
        if buffered and (not yielded or fresh_after_yield > 0):
            yield np.concatenate(buffers) if len(buffers) > 1 else buffers[0]


def _model_compute_type(device: str, requested: str) -> str:
    clean = str(requested or "auto").strip()
    if clean and clean != "auto":
        return clean
    return "float16" if device == "cuda" else "int8"


def load_whisper_model(
    selected_model: str,
    *,
    device: str,
    compute_type: str = "auto",
    model_dir: pathlib.Path | None = None,
    allow_fallback: bool | None = None,
    model_factory: Callable[..., Any] | None = None,
) -> tuple[Any, str, str | None, str]:
    """Load one model, falling back only while model initialization fails."""
    if model_factory is None:
        try:
            from faster_whisper import WhisperModel
        except Exception as exc:  # pragma: no cover - dependency/build failure
            raise TranscriptionError("faster-whisper is not installed") from exc
        model_factory = WhisperModel
    allow = _env_bool("TRANSCRIPTION_ALLOW_MODEL_FALLBACK", True) if allow_fallback is None else allow_fallback
    candidates = (selected_model,) + (_MODEL_FALLBACKS.get(selected_model, ()) if allow else ())
    first_error: Exception | None = None
    clean_device = "auto" if device == "unknown" else device
    clean_compute = _model_compute_type(device, compute_type)
    for candidate in candidates:
        kwargs: dict[str, Any] = {"device": clean_device, "compute_type": clean_compute}
        if model_dir is not None:
            model_dir.mkdir(parents=True, exist_ok=True)
            kwargs["download_root"] = str(model_dir)
        try:
            return model_factory(candidate, **kwargs), candidate, (selected_model if candidate != selected_model else None), clean_compute
        except ValueError as exc:
            if clean_compute == "float16" and "float16" in str(exc).lower():
                clean_compute = "int8"
                kwargs["compute_type"] = clean_compute
                try:
                    return model_factory(candidate, **kwargs), candidate, (selected_model if candidate != selected_model else None), clean_compute
                except Exception as retry_exc:
                    exc = retry_exc
            if first_error is None:
                first_error = exc
        except Exception as exc:
            if first_error is None:
                first_error = exc
        if candidate != candidates[-1]:
            log.warning("Transcription model %s failed to load; attempting configured fallback", candidate)
    detail = type(first_error).__name__ if first_error is not None else "unknown error"
    raise TranscriptionError(f"Could not load transcription model ({detail})") from first_error


def transcribe_short_audio(
    path: pathlib.Path | str,
    *,
    model: str = "small",
    language: str = "ru",
    device: str = "auto",
    compute_type: str = "auto",
    model_dir: pathlib.Path | None = None,
) -> dict[str, Any]:
    """Compatibility path for short Telegram voice messages."""
    source = pathlib.Path(path)
    if not source.is_file():
        raise FileNotFoundError(str(source))
    whisper, actual_model, _fallback_from, used_compute = load_whisper_model(
        str(model or "small"), device=device, compute_type=compute_type,
        model_dir=model_dir, allow_fallback=False,
    )
    segments, info = whisper.transcribe(
        str(source),
        language=str(language or "ru").strip() or None,
        vad_filter=True,
        beam_size=5,
    )
    text = " ".join(str(segment.text or "").strip() for segment in segments).strip()
    return {
        "ok": True,
        "text": text,
        "language": getattr(info, "language", "") or language,
        "duration": float(getattr(info, "duration", 0.0) or 0.0),
        "model": actual_model,
        "device": device,
        "compute_type": used_compute,
    }


def _segment_dict(segment: Any, chunk_start: float) -> dict[str, Any]:
    result: dict[str, Any] = {
        "start": chunk_start + float(getattr(segment, "start", 0.0) or 0.0),
        "end": chunk_start + float(getattr(segment, "end", 0.0) or 0.0),
        "text": str(getattr(segment, "text", "") or "").strip(),
    }
    words = []
    for word in getattr(segment, "words", None) or []:
        words.append({
            "start": chunk_start + float(getattr(word, "start", 0.0) or 0.0),
            "end": chunk_start + float(getattr(word, "end", 0.0) or 0.0),
            "word": str(getattr(word, "word", "") or ""),
        })
    if words:
        result["words"] = words
    return result


def _is_memory_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return isinstance(exc, MemoryError) or any(token in text for token in (
        "out of memory", "cuda_error_out_of_memory", "failed to allocate", "cublas_status_alloc_failed",
    ))


def _transcribe_chunk_with_heartbeat(
    whisper: Any,
    waveform: Any,
    *,
    transcribe_kwargs: dict[str, Any],
    heartbeat: Callable[[], None],
    heartbeat_sec: float = 60.0,
) -> tuple[list[Any], Any]:
    stop = threading.Event()

    def run_heartbeat() -> None:
        while not stop.wait(heartbeat_sec):
            heartbeat()

    thread = threading.Thread(target=run_heartbeat, name="transcription-progress", daemon=True)
    thread.start()
    try:
        segments, info = whisper.transcribe(waveform, **transcribe_kwargs)
        return list(segments), info
    finally:
        stop.set()
        thread.join(timeout=1.0)


def _accept_for_chunk(segment: dict[str, Any], *, index: int, total: int, start: float, chunk_sec: int, overlap_sec: int) -> bool:
    midpoint = (float(segment["start"]) + float(segment["end"])) / 2.0
    left = start + (overlap_sec / 2.0 if index > 0 else 0.0)
    right = start + chunk_sec - overlap_sec / 2.0 if index < total - 1 else math.inf
    return left <= midpoint < right


def _format_timestamp(seconds: float) -> str:
    total = max(0, int(float(seconds)))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _display_filename(path: pathlib.Path) -> str:
    return re.sub(r"^[0-9a-f]{32}_", "", path.name, flags=re.IGNORECASE) or path.name


def write_transcript_artifacts(
    output_dir: pathlib.Path,
    *,
    source: AudioMetadata,
    source_sha256: str,
    language: str,
    requested_model: str,
    selected_model: str,
    routing_rule: str,
    segments: Sequence[dict[str, Any]],
    output_formats: Sequence[str],
) -> list[pathlib.Path]:
    formats = tuple(dict.fromkeys(str(item).strip().lower() for item in output_formats))
    if not formats or any(item not in {"md", "txt", "json"} for item in formats):
        raise TranscriptionError("Output formats must contain md, txt, and/or json")
    output_dir.mkdir(parents=True, exist_ok=True)
    display_name = _display_filename(source.path)
    stem = pathlib.Path(display_name).stem or "recording"
    base = output_dir / f"{stem}.transcript"
    lines = [
        f"[{_format_timestamp(float(item.get('start', 0.0)))} — {_format_timestamp(float(item.get('end', 0.0)))}] {str(item.get('text') or '').strip()}"
        for item in segments
    ]
    written: list[pathlib.Path] = []
    for fmt in formats:
        target = base.with_suffix(f".transcript.{fmt}") if base.suffix != ".transcript" else pathlib.Path(f"{base}.{fmt}")
        if fmt == "md":
            body = (
                f"# Стенограмма: {display_name}\n\n"
                f"- Продолжительность: {_format_timestamp(source.duration_sec)}\n"
                f"- Язык: {language}\n"
                f"- Модель: {selected_model}\n\n"
                + "\n\n".join(lines)
                + "\n"
            )
            target.write_text(body, encoding="utf-8")
        elif fmt == "txt":
            target.write_text("\n\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        else:
            atomic_write_json(target, {
                "schema_version": 1,
                "source": {
                    "filename": display_name,
                    "duration_sec": source.duration_sec,
                    "sha256": source_sha256,
                },
                "transcription": {
                    "language": language,
                    "requested_model": requested_model,
                    "selected_model": selected_model,
                    "routing_rule": routing_rule,
                },
                "segments": list(segments),
            })
        written.append(target)
    return written


def transcribe_audio(
    path: pathlib.Path | str,
    *,
    state_dir: pathlib.Path,
    output_dir: pathlib.Path,
    model: str = "auto",
    language: str = "auto",
    chunk_duration_sec: int = 900,
    overlap_sec: int = 2,
    output_formats: Sequence[str] = ("md", "txt", "json"),
    device: str = "auto",
    compute_type: str = "auto",
    model_dir: pathlib.Path | None = None,
    progress: Callable[[str], None] | None = None,
    model_factory: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Run the full pipeline and return metadata without transcript text."""
    emit = progress or (lambda _message: None)
    validate_chunk_parameters(chunk_duration_sec, overlap_sec)
    requested_model = str(model or "auto").strip().lower()
    requested_language = str(language or "auto").strip().lower()
    if requested_language in {"", "auto"}:
        requested_language = "auto"
    emit("Проверяю аудиофайл…")
    source = validate_audio_file(path)
    source_stat = source.path.stat()
    device_class = detect_device(device)
    selected_model, matched_rule = route_model(source.duration_sec, device=device_class, model=requested_model)
    emit(f"Продолжительность: {_format_timestamp(source.duration_sec)}. Выбрана модель {selected_model}.")
    source_hash = sha256_file(source.path)
    job_id = build_job_id(
        source_sha256=source_hash,
        source_size=source.size,
        requested_model=requested_model,
        selected_model=selected_model,
        language=requested_language,
        chunk_duration_sec=chunk_duration_sec,
        overlap_sec=overlap_sec,
    )
    checkpoint_path = pathlib.Path(state_dir) / job_id / "checkpoint.json"
    checkpoint, checkpoint_warning = load_checkpoint(checkpoint_path, job_id=job_id)
    resumed = bool(checkpoint and int(checkpoint.get("completed_chunks", 0)) > 0)
    windows = calculate_chunk_windows(source.duration_sec, chunk_duration_sec, overlap_sec)
    total_chunks = len(windows)
    completed_chunks = min(int((checkpoint or {}).get("completed_chunks", 0) or 0), total_chunks)
    collected: list[dict[str, Any]] = list((checkpoint or {}).get("segments", []))
    fixed_language = str((checkpoint or {}).get("language") or "")
    if fixed_language == "auto":
        fixed_language = ""
    actual_model = str((checkpoint or {}).get("selected_model") or selected_model)
    effective_device = str((checkpoint or {}).get("device") or device_class)
    fallback_from = (checkpoint or {}).get("fallback_from")
    warnings: list[str] = [checkpoint_warning] if checkpoint_warning else []

    if completed_chunks < total_chunks:
        emit("Загружаю модель…")
        try:
            whisper, loaded_model, load_fallback_from, used_compute = load_whisper_model(
                actual_model,
                device=effective_device,
                compute_type=compute_type,
                model_dir=model_dir,
                model_factory=model_factory,
            )
        except TranscriptionError:
            if requested_model != "auto" or str(device or "auto").lower() != "auto" or effective_device == "cpu":
                raise
            cpu_model, cpu_rule = route_model(source.duration_sec, device="cpu", model="auto")
            whisper, loaded_model, load_fallback_from, used_compute = load_whisper_model(
                cpu_model,
                device="cpu",
                compute_type=compute_type,
                model_dir=model_dir,
                model_factory=model_factory,
            )
            effective_device = "cpu"
            matched_rule = cpu_rule
            warning = f"CUDA недоступна; применён CPU routing и выбрана модель {loaded_model}."
            warnings.append(warning)
            emit(warning)
        if load_fallback_from:
            fallback_from = load_fallback_from
            actual_model = loaded_model
            warning = f"Модель {load_fallback_from} недоступна; используется {loaded_model}."
            warnings.append(warning)
            emit(warning)
        else:
            actual_model = loaded_model
        for index, waveform in enumerate(_iter_pcm_chunks(source.path, chunk_duration_sec, overlap_sec)):
            if index >= total_chunks:
                break
            if index < completed_chunks:
                continue
            chunk_start = windows[index][0]
            transcribe_kwargs = {
                "language": (requested_language if requested_language != "auto" else (fixed_language or None)),
                "vad_filter": True,
                "word_timestamps": True,
                "condition_on_previous_text": False,
                "beam_size": 5,
            }
            try:
                transcribed, info = _transcribe_chunk_with_heartbeat(
                    whisper,
                    waveform,
                    transcribe_kwargs=transcribe_kwargs,
                    heartbeat=lambda: emit(f"Обрабатываю чанк {index + 1} из {total_chunks}…"),
                )
            except Exception as exc:
                fallback_candidates = (
                    _MODEL_FALLBACKS.get(actual_model, ())
                    if _env_bool("TRANSCRIPTION_ALLOW_MODEL_FALLBACK", True)
                    else ()
                )
                if index != 0 or completed_chunks != 0 or not fallback_candidates or not _is_memory_error(exc):
                    raise TranscriptionError(f"Audio recognition failed ({type(exc).__name__})") from exc
                previous_model = actual_model
                whisper, actual_model, _load_from, used_compute = load_whisper_model(
                    fallback_candidates[0], device=effective_device, compute_type=compute_type,
                    model_dir=model_dir, model_factory=model_factory,
                )
                fallback_from = fallback_from or previous_model
                warning = f"Недостаточно RAM/VRAM для {previous_model}; используется {actual_model}."
                warnings.append(warning)
                emit(warning)
                transcribed, info = _transcribe_chunk_with_heartbeat(
                    whisper,
                    waveform,
                    transcribe_kwargs=transcribe_kwargs,
                    heartbeat=lambda: emit(f"Обрабатываю чанк {index + 1} из {total_chunks}…"),
                )
            chunk_segments = []
            for segment in transcribed:
                item = _segment_dict(segment, chunk_start)
                if item["text"] and _accept_for_chunk(
                    item, index=index, total=total_chunks, start=chunk_start,
                    chunk_sec=chunk_duration_sec, overlap_sec=overlap_sec,
                ):
                    chunk_segments.append(item)
            if requested_language != "auto":
                fixed_language = requested_language
            elif not fixed_language:
                detected = str(getattr(info, "language", "") or "").strip()
                confidence = float(getattr(info, "language_probability", 1.0) or 0.0)
                if detected and confidence >= 0.5 and chunk_segments:
                    fixed_language = detected
            collected = merge_segments(collected, chunk_segments)
            completed_chunks = index + 1
            checkpoint = {
                "schema_version": 1,
                "job_id": job_id,
                "status": "running",
                "source": {
                    "path": str(source.path), "sha256": source_hash, "size": source.size,
                    "duration_sec": source.duration_sec,
                },
                "requested_model": requested_model,
                "selected_model": actual_model,
                "fallback_from": fallback_from,
                "device": effective_device,
                "language": fixed_language or "auto",
                "completed_chunks": completed_chunks,
                "total_chunks": total_chunks,
                "segments": collected,
            }
            atomic_write_json(checkpoint_path, checkpoint)
            pct = int(round((completed_chunks / max(total_chunks, 1)) * 100))
            emit(f"Обрабатываю чанк {completed_chunks} из {total_chunks} — {pct}%.")
        del whisper
    if completed_chunks < total_chunks:
        raise TranscriptionError("Audio ended before all expected chunks were decoded")
    final_stat = source.path.stat()
    if final_stat.st_size != source_stat.st_size or final_stat.st_mtime_ns != source_stat.st_mtime_ns:
        raise TranscriptionError("Source audio file changed during transcription")
    final_language = fixed_language or (requested_language if requested_language != "auto" else "unknown")
    emit("Формирую стенограмму…")
    artifacts = write_transcript_artifacts(
        pathlib.Path(output_dir), source=source, source_sha256=source_hash,
        language=final_language, requested_model=requested_model, selected_model=actual_model,
        routing_rule=matched_rule, segments=collected, output_formats=output_formats,
    )
    final_checkpoint = dict(checkpoint or {})
    final_checkpoint.update({
        "schema_version": 1, "job_id": job_id, "status": "completed",
        "requested_model": requested_model, "selected_model": actual_model,
        "fallback_from": fallback_from, "language": final_language,
        "device": effective_device,
        "completed_chunks": total_chunks, "total_chunks": total_chunks,
        "segments": collected,
    })
    final_checkpoint.setdefault("source", {
        "path": str(source.path), "sha256": source_hash, "size": source.size,
        "duration_sec": source.duration_sec,
    })
    atomic_write_json(checkpoint_path, final_checkpoint)
    return {
        "ok": True,
        "job_id": job_id,
        "duration_sec": source.duration_sec,
        "language": final_language,
        "requested_model": requested_model,
        "selected_model": actual_model,
        "fallback_from": fallback_from,
        "routing": {"strategy": "duration" if requested_model == "auto" else "manual", "matched_rule": matched_rule},
        "resumed": resumed,
        "segments_count": len(collected),
        "artifacts": artifacts,
        "warnings": warnings,
    }


__all__ = [
    "AudioMetadata", "InvalidAudioError", "ModelRoute", "SUPPORTED_EXTENSIONS",
    "SUPPORTED_MIME_TYPES", "SUPPORTED_MODELS", "TranscriptionError", "atomic_write_json",
    "build_job_id", "calculate_chunk_windows", "configured_model_routes", "detect_device",
    "load_checkpoint", "load_whisper_model", "merge_segments", "parse_model_routes", "route_model",
    "sha256_file", "transcribe_audio", "transcribe_short_audio", "validate_audio_file", "validate_chunk_parameters",
    "transcription_setting", "write_transcript_artifacts",
]
