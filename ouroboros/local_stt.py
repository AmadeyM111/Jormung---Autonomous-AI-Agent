"""Local speech-to-text CLI for Telegram voice transcription."""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Transcribe an audio file with a local Whisper-compatible model.")
    parser.add_argument("audio_path")
    parser.add_argument("--model", default="small")
    parser.add_argument("--lang", default="ru")
    parser.add_argument("--device", default="auto")
    parser.add_argument("--compute-type", default="auto")
    parser.add_argument("--model-dir", default="")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def _clean_device(value: str) -> str:
    text = str(value or "auto").strip().lower()
    return text if text in {"auto", "cpu", "cuda"} else "auto"


def _clean_compute_type(value: str, device: str) -> str:
    text = str(value or "auto").strip()
    if text and text != "auto":
        return text
    return "float16" if device == "cuda" else "int8"


def transcribe_file(
    audio_path: pathlib.Path,
    *,
    model: str = "small",
    language: str = "ru",
    device: str = "auto",
    compute_type: str = "auto",
    model_dir: pathlib.Path | None = None,
) -> dict[str, Any]:
    from ouroboros.transcription import transcribe_short_audio

    if not audio_path.is_file():
        raise FileNotFoundError(str(audio_path))
    clean_device = _clean_device(device)
    clean_compute = _clean_compute_type(compute_type, clean_device)
    return transcribe_short_audio(
        audio_path,
        model=str(model or "small"),
        language=language,
        device=clean_device,
        compute_type=clean_compute,
        model_dir=model_dir,
    )


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(list(argv if argv is not None else sys.argv[1:]))
    try:
        payload = transcribe_file(
            pathlib.Path(args.audio_path),
            model=args.model,
            language=args.lang,
            device=args.device,
            compute_type=args.compute_type,
            model_dir=pathlib.Path(args.model_dir) if args.model_dir else None,
        )
    except Exception as exc:
        payload = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        print(json.dumps(payload, ensure_ascii=False), file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(str(payload.get("text") or ""))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
