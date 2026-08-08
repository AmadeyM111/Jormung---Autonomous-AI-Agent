"""Agent tool for long-running local audio transcription."""

from __future__ import annotations

import json
import pathlib
import threading
from typing import Any, Sequence

from ouroboros.artifacts import copy_file_to_task_artifacts, task_artifact_dir_path, task_id_for_artifacts
from ouroboros.tool_access import resource_root_path, user_files_path_block_reason
from ouroboros.tools.registry import ToolContext, ToolEntry
from ouroboros.transcription import TranscriptionError, transcription_setting, transcribe_audio as run_transcription

_TRANSCRIPTION_LOCK = threading.Lock()


def _path_is_within(path: pathlib.Path, root: pathlib.Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _queue_transcript_document(ctx: ToolContext, record: dict[str, Any]) -> None:
    event = {
        "type": "send_document",
        "chat_id": int(ctx.current_chat_id),
        "file_path": str(record["path"]),
        "filename": str(record["name"]),
        "caption": "Стенограмма аудиозаписи",
    }
    event_queue = getattr(ctx, "event_queue", None)
    if event_queue is not None:
        try:
            event_queue.put_nowait(event)
            return
        except Exception:
            pass
    pending_events = getattr(ctx, "pending_events", None)
    if isinstance(pending_events, list):
        pending_events.append(event)


def resolve_audio_path(ctx: ToolContext, path: str) -> pathlib.Path:
    """Resolve an audio input only inside user-controlled, task-scoped roots."""
    raw_text = str(path or "").strip()
    if not raw_text:
        raise TranscriptionError("Audio path is required")
    raw = pathlib.Path(raw_text).expanduser()
    active_root = resource_root_path(ctx, "active_workspace")
    drive_root = pathlib.Path(ctx.drive_root).resolve(strict=False)
    roots: list[tuple[str, pathlib.Path]] = [
        ("uploads", (drive_root / "uploads").resolve(strict=False)),
        ("task_drive", resource_root_path(ctx, "task_drive")),
        ("artifact_store", resource_root_path(ctx, "artifact_store")),
        ("active_workspace", active_root),
        ("user_files", resource_root_path(ctx, "user_files")),
    ]
    candidates: list[pathlib.Path] = []
    if raw.is_absolute() or raw_text.startswith("~"):
        candidates.append(raw.resolve(strict=False))
    else:
        candidates.append((active_root / raw).resolve(strict=False))
        candidates.extend((root / raw).resolve(strict=False) for _, root in roots[:3])
    for candidate in dict.fromkeys(candidates):
        if not candidate.is_file():
            continue
        resolved = candidate.resolve(strict=True)
        for label, root in roots:
            if not _path_is_within(resolved, root.resolve(strict=False)):
                continue
            if label == "user_files" and user_files_path_block_reason(ctx, resolved):
                continue
            return resolved
    if any(candidate.exists() for candidate in candidates):
        raise TranscriptionError(
            "Audio path is outside attachment uploads, user_files, task_drive, artifact_store, or the active workspace"
        )
    raise FileNotFoundError(f"Audio file not found: {raw.name}")


def _transcribe_audio_tool(
    ctx: ToolContext,
    path: str,
    model: str = "auto",
    language: str = "auto",
    chunk_duration_sec: int | None = None,
    overlap_sec: int | None = None,
    output_formats: Sequence[str] | None = None,
) -> str:
    if not _TRANSCRIPTION_LOCK.acquire(blocking=False):
        return "⚠️ TOOL_ERROR (transcribe_audio): another transcription is already running on this worker"
    try:
        source = resolve_audio_path(ctx, path)
        drive_root = pathlib.Path(ctx.drive_root).resolve(strict=False)
        task_id = task_id_for_artifacts(ctx)
        output_dir = task_artifact_dir_path(drive_root, task_id, create=True)
        state_dir = drive_root / "state" / "transcriptions"
        configured_model_dir = str(transcription_setting("TRANSCRIPTION_MODEL_DIR", "") or "").strip()
        model_dir = pathlib.Path(configured_model_dir).expanduser() if configured_model_dir else drive_root / "models" / "faster-whisper"
        result = run_transcription(
            source,
            state_dir=state_dir,
            output_dir=output_dir,
            model=model,
            language=language,
            chunk_duration_sec=int(chunk_duration_sec if chunk_duration_sec is not None else transcription_setting("TRANSCRIPTION_CHUNK_SEC", 900)),
            overlap_sec=int(overlap_sec if overlap_sec is not None else transcription_setting("TRANSCRIPTION_OVERLAP_SEC", 2)),
            output_formats=tuple(output_formats or ("md", "txt", "json")),
            device=str(transcription_setting("TRANSCRIPTION_DEVICE", "auto") or "auto"),
            compute_type=str(transcription_setting("TRANSCRIPTION_COMPUTE_TYPE", "auto") or "auto"),
            model_dir=model_dir,
            progress=ctx.emit_progress_fn,
        )
        artifact_records = []
        for artifact_path in result.pop("artifacts", []):
            record = copy_file_to_task_artifacts(ctx, artifact_path, kind="transcript")
            if record:
                artifact_records.append({"name": record["name"]})
                if getattr(ctx, "current_chat_id", None):
                    _queue_transcript_document(ctx, record)
        result["artifacts"] = artifact_records
        # The transcript text exists only in checkpoint/artifacts, never here.
        return json.dumps(result, ensure_ascii=False)
    except (FileNotFoundError, TranscriptionError) as exc:
        return f"⚠️ TOOL_ERROR (transcribe_audio): {exc}"
    except Exception as exc:
        return f"⚠️ TOOL_ERROR (transcribe_audio): {type(exc).__name__}: {exc}"
    finally:
        _TRANSCRIPTION_LOCK.release()


def get_tools() -> list[ToolEntry]:
    return [ToolEntry(
        "transcribe_audio",
        {
            "name": "transcribe_audio",
            "description": (
                "Locally transcribe one supported audio attachment into downloadable Markdown, TXT, and JSON artifacts. "
                "Use model=large-v3 when the user explicitly requests maximum quality."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to an audio attachment or user/workspace file."},
                    "model": {"type": "string", "enum": ["auto", "large-v3", "turbo", "medium", "small"], "default": "auto"},
                    "language": {"type": "string", "default": "auto"},
                    "chunk_duration_sec": {"type": "integer", "minimum": 60, "maximum": 3600, "default": 900},
                    "overlap_sec": {"type": "integer", "minimum": 0, "maximum": 10, "default": 2},
                    "output_formats": {
                        "type": "array",
                        "items": {"type": "string", "enum": ["md", "txt", "json"]},
                        "default": ["md", "txt", "json"],
                    },
                },
                "required": ["path"],
            },
        },
        _transcribe_audio_tool,
        timeout_sec=12 * 60 * 60,
    )]


__all__ = ["get_tools", "resolve_audio_path"]
