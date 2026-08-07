"""Durable Telegram bridge patch for inbound transcription audio files."""

from __future__ import annotations

import pathlib
from typing import Any, Dict


MARKER = "OUROBOROS_TELEGRAM_AUDIO_ATTACHMENTS"


_HELPER = r'''
async def _download_transcription_audio(api, client, file_meta: Dict[str, Any]) -> tuple[pathlib.Path, str]:
    """Stream one supported Telegram audio object into the shared uploads root."""
    import shutil
    import uuid

    supported_ext = {".m4a", ".mp4", ".mp3", ".wav", ".flac", ".ogg", ".opus"}
    supported_mime = {
        "audio/mp4", "audio/x-m4a", "application/mp4", "audio/mpeg", "audio/wav",
        "audio/x-wav", "audio/flac", "audio/x-flac", "audio/ogg", "audio/opus",
    }
    mime_ext = {
        "audio/mp4": ".m4a", "audio/x-m4a": ".m4a", "application/mp4": ".m4a",
        "audio/mpeg": ".mp3", "audio/wav": ".wav", "audio/x-wav": ".wav",
        "audio/flac": ".flac", "audio/x-flac": ".flac", "audio/ogg": ".ogg",
        "audio/opus": ".opus",
    }
    file_id = str(file_meta.get("file_id") or "").strip()
    if not file_id:
        raise ValueError("Telegram audio file_id is missing")
    mime = str(file_meta.get("mime_type") or "").split(";", 1)[0].strip().lower()
    raw_name = pathlib.Path(str(file_meta.get("file_name") or "")).name.strip()
    suffix = pathlib.Path(raw_name).suffix.lower()
    if not suffix and mime in mime_ext:
        suffix = mime_ext[mime]
        raw_name = f"telegram-audio{suffix}"
    if suffix not in supported_ext or (mime and mime not in supported_mime and mime != "application/octet-stream"):
        raise ValueError("Unsupported Telegram audio format")
    safe_name = (raw_name or f"telegram-audio{suffix}").replace(" ", "_")[:180]
    settings = _load_settings(api)
    try:
        max_bytes = int(
            settings.get("OUROBOROS_AUDIO_UPLOAD_MAX_BYTES")
            or os.environ.get("OUROBOROS_AUDIO_UPLOAD_MAX_BYTES")
            or 1024 * 1024 * 1024
        )
    except (TypeError, ValueError):
        max_bytes = 1024 * 1024 * 1024
    declared_size = int(file_meta.get("file_size") or 0)
    if declared_size > max_bytes:
        raise ValueError(f"Telegram audio exceeds the {max_bytes}-byte limit")

    upload_dir = _data_dir(api) / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    expected = declared_size or min(max_bytes, 64 * 1024 * 1024)
    if shutil.disk_usage(upload_dir).free < expected + 64 * 1024 * 1024:
        raise ValueError("Insufficient disk space for Telegram audio")
    destination = upload_dir / f"{uuid.uuid4().hex}_{safe_name}"
    temporary = upload_dir / f".{uuid.uuid4().hex}.uploading"
    written = 0
    try:
        payload = await client.call("getFile", data={"file_id": file_id}, timeout=20)
        remote_path = str((payload.get("result") or {}).get("file_path") or "").strip()
        if not remote_path:
            raise RuntimeError("Telegram file path is missing")
        async with httpx.AsyncClient(timeout=None) as downloader:
            async with downloader.stream("GET", f"{client.file_base}/{remote_path}") as response:
                if response.status_code >= 400:
                    raise RuntimeError(f"Telegram file download returned HTTP {response.status_code}")
                with temporary.open("wb") as handle:
                    async for chunk in response.aiter_bytes(1024 * 1024):
                        written += len(chunk)
                        if written > max_bytes:
                            raise ValueError(f"Telegram audio exceeds the {max_bytes}-byte limit")
                        handle.write(chunk)
        temporary.replace(destination)
        from ouroboros.transcription import validate_audio_file

        validate_audio_file(destination, max_bytes=max_bytes)
        return destination, safe_name
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    finally:
        temporary.unlink(missing_ok=True)
'''


_OLD_BLOCK = '''                    photos = message.get("photo") or []
                    image_base64 = ""
                    image_mime = ""
                    if photos:
                        file_id = str((photos[-1] or {}).get("file_id") or "").strip()
                        if file_id:
                            image_base64, image_mime = await client.download_photo(file_id)
                    if not safe_text and not image_base64:
                        # Acknowledge unsupported inbound attachments instead of
                        # silently swallowing them. Inbound file INGESTION is not
                        # yet wired on the host side (only text/voice/photo), so
                        # tell the user rather than leaving them wondering.
                        if message.get("document") or message.get("video") or message.get("audio") or message.get("sticker"):
                            await client.send_message(
                                chat_id,
                                ("Пока я не умею принимать файлы/видео/аудио — поддерживаются текст, голос и фото."
                                 if lang == "ru" else
                                 "I can't accept files/video/audio yet — supported input: text, voice, and photos."),
                            )
                        continue
'''


_NEW_BLOCK = f'''                    # {MARKER}: supported document/audio inputs become path attachments.
                    audio_meta = message.get("audio") or message.get("document") or {{}}
                    if audio_meta:
                        try:
                            await client.send_chat_action(chat_id, "typing")
                            audio_path, audio_name = await _download_transcription_audio(api, client, audio_meta)
                            request_text = safe_text or (
                                "Подготовь стенограмму прикреплённой аудиозаписи."
                                if lang == "ru" else
                                "Prepare a transcript of the attached audio recording."
                            )
                            safe_text = (
                                f"{{request_text}}\\n\\n"
                                f"[Attached file: {{audio_name}} saved to {{audio_path}}]"
                            )
                            await client.send_message(
                                chat_id,
                                ("🎧 Аудиофайл принят. Проверяю и запускаю транскрибацию…"
                                 if lang == "ru" else
                                 "🎧 Audio received. Validating and starting transcription…"),
                            )
                        except ValueError as exc:
                            await client.send_message(chat_id, str(exc), parse_mode="")
                            continue
                        except Exception as exc:
                            api.log("error", f"Telegram audio ingestion failed: {{type(exc).__name__}}")
                            await client.send_message(
                                chat_id,
                                (("Не удалось принять аудиофайл: " if lang == "ru" else "Could not ingest audio: ")
                                 + f"{{type(exc).__name__}}"),
                                parse_mode="",
                            )
                            continue

                    photos = message.get("photo") or []
                    image_base64 = ""
                    image_mime = ""
                    if photos:
                        file_id = str((photos[-1] or {{}}).get("file_id") or "").strip()
                        if file_id:
                            image_base64, image_mime = await client.download_photo(file_id)
                    if not safe_text and not image_base64:
                        if message.get("video") or message.get("sticker") or message.get("document") or message.get("audio"):
                            await client.send_message(
                                chat_id,
                                ("Этот тип вложения пока не поддерживается. Для транскрибации отправьте M4A, MP3, WAV, FLAC или OGG."
                                 if lang == "ru" else
                                 "This attachment type is not supported. Send M4A, MP3, WAV, FLAC, or OGG for transcription."),
                            )
                        continue
'''


def patch_plugin(path: pathlib.Path | str) -> Dict[str, Any]:
    plugin = pathlib.Path(path)
    if not plugin.is_file():
        return {"ok": False, "error": f"telegram-bridge plugin.py not found at {plugin}"}
    text = plugin.read_text(encoding="utf-8")
    if MARKER in text:
        return {"ok": True, "changed": False, "path": str(plugin)}
    helper_anchor = "def _make_poller(api):"
    if helper_anchor not in text or _OLD_BLOCK not in text:
        return {"ok": False, "changed": False, "error": "telegram audio attachment patch did not match expected snippets"}
    text = text.replace(helper_anchor, _HELPER + "\n\n" + helper_anchor, 1)
    text = text.replace(_OLD_BLOCK, _NEW_BLOCK, 1)
    if MARKER not in text or "_download_transcription_audio" not in text:
        return {"ok": False, "changed": False, "error": "telegram audio attachment patch verification failed"}
    compile(text, str(plugin), "exec")
    plugin.write_text(text, encoding="utf-8")
    return {"ok": True, "changed": True, "path": str(plugin)}


__all__ = ["MARKER", "patch_plugin"]
