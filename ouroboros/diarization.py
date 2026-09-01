"""Optional speaker diarization and transcript alignment helpers.

``pyannote.audio`` is intentionally imported only when diarization is run so
the base Ouroboros installation does not need the heavyweight ML dependency.
The alignment helper is pure Python and can be used with any diarization
provider that returns timestamped speaker turns.
"""

from __future__ import annotations

import math
import os
import pathlib
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Callable

DEFAULT_DIARIZATION_MODEL = "pyannote/speaker-diarization-community-1"
DEFAULT_WORD_TOLERANCE_SEC = 0.35


class DiarizationError(RuntimeError):
    """Speaker diarization could not be configured or completed."""


def _is_local_model(model: str | pathlib.Path) -> bool:
    raw = os.path.expanduser(os.fspath(model))
    path = pathlib.Path(raw)
    return path.exists() or path.is_absolute() or raw.startswith(("./", "../", "~"))


def _positive_speaker_hint(value: int | None, name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise DiarizationError(f"{name} must be a positive integer")
    return value


def _load_pipeline_factory() -> Callable[..., Any]:
    # pyannote telemetry is opt-in for this integration. ``setdefault`` still
    # lets an operator explicitly enable it before the process starts.
    os.environ.setdefault("PYANNOTE_METRICS_ENABLED", "0")
    try:
        from pyannote.audio import Pipeline
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise DiarizationError(
            "pyannote.audio is not installed; install the optional diarization dependencies"
        ) from exc

    if os.environ.get("PYANNOTE_METRICS_ENABLED", "0").strip().lower() in {"0", "false", "no", "off"}:
        try:
            from pyannote.audio.telemetry import set_telemetry_metrics

            set_telemetry_metrics(False)
        except (ImportError, AttributeError):
            # Older pyannote versions only honor the environment variable.
            pass
    return Pipeline.from_pretrained


def _resolve_device(requested: str) -> str:
    value = str(requested or "auto").strip().lower()
    if value not in {"auto", "cpu", "cuda", "mps"}:
        raise DiarizationError(f"Unsupported diarization device: {value}")
    if value != "auto":
        return value
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda"
        mps = getattr(getattr(torch, "backends", None), "mps", None)
        if mps is not None and mps.is_available():
            return "mps"
    except ImportError:  # pragma: no cover - pyannote itself installs torch
        pass
    return "cpu"


def _move_pipeline(pipeline: Any, device: str) -> None:
    move = getattr(pipeline, "to", None)
    if not callable(move):
        return
    try:
        import torch

        move(torch.device(device))
    except ImportError:  # useful for lightweight injected test pipelines
        move(device)


def _selected_annotation(result: Any) -> Any:
    if isinstance(result, Mapping):
        exclusive = result.get("exclusive_speaker_diarization")
        if exclusive is not None:
            return exclusive
        regular = result.get("speaker_diarization")
        return regular if regular is not None else result
    exclusive = getattr(result, "exclusive_speaker_diarization", None)
    if exclusive is not None:
        return exclusive
    regular = getattr(result, "speaker_diarization", None)
    return regular if regular is not None else result


def _raw_turns(annotation: Any) -> Iterable[Any]:
    itertracks = getattr(annotation, "itertracks", None)
    if callable(itertracks):
        return itertracks(yield_label=True)
    if isinstance(annotation, Mapping) and "turns" in annotation:
        turns = annotation["turns"]
        return turns if isinstance(turns, Iterable) else ()
    return annotation if isinstance(annotation, Iterable) and not isinstance(annotation, (str, bytes)) else ()


def _coerce_turn(item: Any) -> tuple[float, float, str] | None:
    start: Any = None
    end: Any = None
    speaker: Any = None
    if isinstance(item, Mapping):
        start, end = item.get("start"), item.get("end")
        speaker = item.get("speaker", item.get("label"))
    elif isinstance(item, (tuple, list)):
        if len(item) >= 3 and hasattr(item[0], "start"):
            start, end, speaker = item[0].start, item[0].end, item[2]
        elif len(item) >= 3:
            start, end, speaker = item[0], item[1], item[2]
        elif len(item) == 2 and hasattr(item[0], "start"):
            start, end, speaker = item[0].start, item[0].end, item[1]
    else:
        start, end = getattr(item, "start", None), getattr(item, "end", None)
        speaker = getattr(item, "speaker", getattr(item, "label", None))
    try:
        clean_start, clean_end = float(start), float(end)
    except (TypeError, ValueError):
        return None
    clean_start = max(0.0, clean_start)
    if not math.isfinite(clean_start) or not math.isfinite(clean_end) or clean_end <= clean_start:
        return None
    clean_speaker = str(speaker or "unknown").strip() or "unknown"
    return clean_start, clean_end, clean_speaker


def normalize_turns(turns: Iterable[Any]) -> list[dict[str, Any]]:
    """Normalize turns and assign deterministic labels in first-appearance order."""
    parsed = [turn for item in turns if (turn := _coerce_turn(item)) is not None]
    parsed.sort(key=lambda turn: (turn[0], turn[1], turn[2]))
    labels: dict[str, str] = {}
    normalized: list[dict[str, Any]] = []
    for start, end, source_label in parsed:
        label = labels.setdefault(source_label, f"SPEAKER_{len(labels):02d}")
        normalized.append({"start": start, "end": end, "speaker": label})
    return normalized


def run_pyannote_diarization(
    path: pathlib.Path | str,
    model: pathlib.Path | str = DEFAULT_DIARIZATION_MODEL,
    token: str | None = None,
    device: str = "auto",
    min_speakers: int | None = None,
    max_speakers: int | None = None,
    pipeline_factory: Callable[..., Any] | None = None,
) -> list[dict[str, Any]]:
    """Run pyannote and return normalized, exclusive speaker turns when available."""
    source = pathlib.Path(path).expanduser().resolve(strict=False)
    if not source.is_file():
        raise FileNotFoundError(f"Audio file not found: {source.name}")
    minimum = _positive_speaker_hint(min_speakers, "min_speakers")
    maximum = _positive_speaker_hint(max_speakers, "max_speakers")
    if minimum is not None and maximum is not None and minimum > maximum:
        raise DiarizationError("min_speakers cannot exceed max_speakers")

    model_ref = os.path.expanduser(os.fspath(model))
    access_token = str(token or os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_TOKEN") or "").strip()
    local_model = _is_local_model(model_ref)
    if not local_model and not access_token:
        raise DiarizationError("A Hugging Face token is required for a non-local pyannote model")

    os.environ.setdefault("PYANNOTE_METRICS_ENABLED", "0")
    factory = pipeline_factory or _load_pipeline_factory()
    factory_kwargs = {} if local_model else {"token": access_token}
    try:
        pipeline = factory(model_ref, **factory_kwargs)
        _move_pipeline(pipeline, _resolve_device(device))
        call_kwargs: dict[str, int] = {}
        if minimum is not None:
            call_kwargs["min_speakers"] = minimum
        if maximum is not None:
            call_kwargs["max_speakers"] = maximum
        result = pipeline(str(source), **call_kwargs)
    except DiarizationError:
        raise
    except Exception as exc:
        # Provider/model loader errors may contain signed URLs or token-shaped
        # query parameters. Keep the user-facing error actionable but sanitized.
        raise DiarizationError(f"Speaker diarization failed ({type(exc).__name__})") from exc
    return normalize_turns(_raw_turns(_selected_annotation(result)))


def _overlap(start: float, end: float, turn: Mapping[str, Any]) -> float:
    return max(0.0, min(end, float(turn["end"])) - max(start, float(turn["start"])))


def _speaker_for_interval(
    start: float,
    end: float,
    turns: Sequence[Mapping[str, Any]],
    *,
    tolerance_sec: float,
) -> str | None:
    overlaps = [(_overlap(start, end, turn), index, turn) for index, turn in enumerate(turns)]
    best_overlap, _, best_turn = max(overlaps, default=(0.0, 0, None), key=lambda item: (item[0], -item[1]))
    if best_turn is not None and best_overlap > 0:
        return str(best_turn["speaker"])

    nearby: list[tuple[float, int, Mapping[str, Any]]] = []
    for index, turn in enumerate(turns):
        turn_start, turn_end = float(turn["start"]), float(turn["end"])
        if end < turn_start:
            distance = turn_start - end
        elif start > turn_end:
            distance = start - turn_end
        else:
            distance = 0.0
        if distance <= tolerance_sec:
            nearby.append((max(0.0, distance), index, turn))
    if not nearby:
        return None
    return str(min(nearby, key=lambda item: (item[0], item[1]))[2]["speaker"])


def _number(value: Any, default: float) -> float:
    try:
        converted = float(value)
    except (TypeError, ValueError):
        return default
    return converted if math.isfinite(converted) else default


def _join_word_text(words: Sequence[Mapping[str, Any]]) -> str:
    result = ""
    for word in words:
        raw = str(word.get("word", ""))
        token = raw.strip()
        if not token:
            continue
        if not result:
            result = token
        elif raw[:1].isspace() or token[:1] in ".,!?;:%)]}" or result[-1:].isspace():
            result += raw if raw[:1].isspace() else token
        else:
            result += " " + token
    return result.strip()


def align_speakers(
    segments: Sequence[Mapping[str, Any]],
    turns: Iterable[Any],
    *,
    tolerance_sec: float = DEFAULT_WORD_TOLERANCE_SEC,
) -> list[dict[str, Any]]:
    """Add speakers to transcript segments and split on word-level changes.

    Words are matched by maximum temporal overlap, then by the nearest turn
    within ``tolerance_sec``. When word timestamps are absent (or cannot be
    matched), the enclosing transcript segment is matched instead.
    """
    tolerance = _number(tolerance_sec, -1.0)
    if tolerance < 0:
        raise ValueError("tolerance_sec must be a non-negative finite number")
    normalized_turns = normalize_turns(turns)
    if not normalized_turns:
        return [dict(segment) for segment in segments]

    aligned: list[dict[str, Any]] = []
    for original in segments:
        segment = dict(original)
        segment_start = _number(segment.get("start"), 0.0)
        segment_end = max(segment_start, _number(segment.get("end"), segment_start))
        fallback_speaker = _speaker_for_interval(
            segment_start, segment_end, normalized_turns, tolerance_sec=tolerance,
        )
        raw_words = segment.get("words")
        if not isinstance(raw_words, Sequence) or isinstance(raw_words, (str, bytes)) or not raw_words:
            if fallback_speaker is not None:
                segment["speaker"] = fallback_speaker
            aligned.append(segment)
            continue

        assigned_words: list[dict[str, Any]] = []
        for raw_word in raw_words:
            if not isinstance(raw_word, Mapping):
                continue
            word = dict(raw_word)
            word_start = _number(word.get("start"), segment_start)
            word_end = max(word_start, _number(word.get("end"), word_start))
            speaker = _speaker_for_interval(
                word_start, word_end, normalized_turns, tolerance_sec=tolerance,
            ) or fallback_speaker
            if speaker is not None:
                word["speaker"] = speaker
            assigned_words.append(word)

        if not assigned_words or not any(word.get("speaker") for word in assigned_words):
            if fallback_speaker is not None:
                segment["speaker"] = fallback_speaker
            aligned.append(segment)
            continue

        groups: list[list[dict[str, Any]]] = []
        for word in assigned_words:
            if not groups or groups[-1][-1].get("speaker") != word.get("speaker"):
                groups.append([word])
            else:
                groups[-1].append(word)
        for group in groups:
            split = {key: value for key, value in segment.items() if key not in {"start", "end", "text", "words", "speaker"}}
            split["start"] = _number(group[0].get("start"), segment_start)
            split["end"] = _number(group[-1].get("end"), split["start"])
            split["text"] = _join_word_text(group)
            split["speaker"] = group[0].get("speaker") or fallback_speaker
            split["words"] = group
            aligned.append(split)
    return aligned


__all__ = [
    "DEFAULT_DIARIZATION_MODEL",
    "DEFAULT_WORD_TOLERANCE_SEC",
    "DiarizationError",
    "align_speakers",
    "normalize_turns",
    "run_pyannote_diarization",
]
