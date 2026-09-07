"""Optional WhisperX transcription and forced-alignment adapter.

WhisperX is deliberately lazy-loaded: the base installation keeps using
faster-whisper unless the ``provider=whisperx`` profile is selected.
"""

from __future__ import annotations

import pathlib
from collections.abc import Callable, Mapping
from typing import Any


class WhisperXError(RuntimeError):
    """WhisperX is unavailable or failed to process the audio."""


def _load_whisperx() -> Any:
    try:
        import whisperx
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise WhisperXError(
            "WhisperX is not installed; install the optional whisperx dependencies"
        ) from exc
    return whisperx


def _normalise_segments(segments: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for raw in segments:
        try:
            start = max(0.0, float(raw.get("start", 0.0) or 0.0))
            end = float(raw.get("end", start) or start)
        except (TypeError, ValueError):
            continue
        if end <= start:
            end = start
        text = str(raw.get("text", "") or "").strip()
        item: dict[str, Any] = {"start": start, "end": end, "text": text}
        words: list[dict[str, Any]] = []
        for word in raw.get("words", []) or []:
            if not isinstance(word, Mapping) or not str(word.get("word", "") or "").strip():
                continue
            try:
                word_start = max(0.0, float(word.get("start", start) or start))
                word_end = float(word.get("end", word_start) or word_start)
            except (TypeError, ValueError):
                continue
            words.append({
                "start": word_start,
                "end": max(word_start, word_end),
                "word": str(word.get("word", "") or ""),
            })
        if words:
            item["words"] = words
        if text:
            output.append(item)
    return output


def transcribe_with_whisperx(
    path: pathlib.Path | str,
    *,
    model: str = "large-v3",
    language: str = "auto",
    device: str = "auto",
    compute_type: str = "float16",
    batch_size: int = 8,
    model_dir: pathlib.Path | None = None,
    whisperx_module: Any | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Transcribe and forced-align one complete recording with WhisperX."""
    source = pathlib.Path(path).expanduser().resolve(strict=False)
    if not source.is_file():
        raise FileNotFoundError(source.name)
    emit = progress or (lambda _message: None)
    wx = whisperx_module or _load_whisperx()
    requested_device = str(device or "auto").lower()
    if requested_device == "auto":
        try:
            import torch

            clean_device = "cuda" if torch.cuda.is_available() else "cpu"
        except ImportError:
            clean_device = "cpu"
    else:
        clean_device = "cuda" if requested_device == "cuda" else "cpu"
    clean_language = None if str(language or "auto").lower() == "auto" else str(language).lower()
    try:
        emit("Загружаю модель WhisperX…")
        asr_model = wx.load_model(
            model,
            clean_device,
            compute_type=compute_type,
            download_root=str(model_dir) if model_dir else None,
        )
        audio = wx.load_audio(str(source))
        result = asr_model.transcribe(audio, batch_size=int(batch_size), language=clean_language)
        detected_language = str(result.get("language") or clean_language or "unknown")
        emit("Уточняю таймкоды слов…")
        align_model, metadata = wx.load_align_model(
            language_code=detected_language,
            device=clean_device,
            model_dir=str(model_dir) if model_dir else None,
        )
        aligned = wx.align(
            result.get("segments", []),
            align_model,
            metadata,
            audio,
            clean_device,
            return_char_alignments=False,
        )
        segments = _normalise_segments(list(aligned.get("segments", [])))
    except WhisperXError:
        raise
    except Exception as exc:
        raise WhisperXError(f"WhisperX transcription failed ({type(exc).__name__})") from exc
    if not segments:
        raise WhisperXError("WhisperX returned an empty transcription")
    return {
        "segments": segments,
        "language": detected_language,
        "model": model,
        "warnings": [],
    }


__all__ = ["WhisperXError", "transcribe_with_whisperx"]
