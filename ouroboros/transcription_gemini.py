"""Small HTTP adapter for Gemini's prerecorded-audio transcription API.

The adapter intentionally does not depend on Google's SDK.  Audio chunks are
uploaded with the resumable Files API, passed to the Interactions API, and
deleted immediately after the interaction finishes.
"""

from __future__ import annotations

import pathlib
import re
import time
from collections.abc import Iterable, Iterator, Sequence
from typing import Any
from urllib.parse import urlsplit

import httpx


_API_ROOT = "https://generativelanguage.googleapis.com"
_UPLOAD_START_URL = f"{_API_ROOT}/upload/v1beta/files"
_INTERACTIONS_URL = f"{_API_ROOT}/v1beta/interactions"
_UPLOAD_CHUNK_BYTES = 1024 * 1024
_REMOTE_FILE_NAME = re.compile(r"^files/[A-Za-z0-9_-]+$")

# The local pipeline historically accepts Whisper's short language codes.  The
# Transcribe API expects BCP-47, so cover the languages most likely to reach the
# bot while still accepting an explicit BCP-47 code unchanged.
_SHORT_LANGUAGE_CODES = {
    "de": "de-DE",
    "en": "en-US",
    "es": "es-419",
    "fr": "fr-FR",
    "it": "it-IT",
    "kk": "kk-KZ",
    "pt": "pt-BR",
    "ru": "ru-RU",
    "tr": "tr-TR",
    "uk": "uk-UA",
}


class GeminiTranscriptionError(RuntimeError):
    """A safe, user-presentable failure from the Gemini STT adapter."""


class GeminiTranscriptionClient:
    """Transcribe temporary WAV chunks with Gemini 3.5 Transcribe.

    ``http_client`` is injectable for deterministic tests.  When omitted, a
    short-lived :class:`httpx.Client` is created and closed for each call.
    """

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-3.5-transcribe",
        http_client: Any | None = None,
        *,
        timeout_sec: float = 300.0,
    ) -> None:
        clean_key = str(api_key or "").strip()
        if not clean_key:
            raise ValueError("A Gemini API key is required")
        clean_model = str(model or "").strip()
        if not clean_model:
            raise ValueError("A Gemini transcription model is required")
        self._api_key = clean_key
        self.model = clean_model
        self._http_client = http_client
        self._timeout = httpx.Timeout(
            connect=30.0,
            read=float(timeout_sec),
            write=float(timeout_sec),
            pool=30.0,
        )

    def transcribe_file(
        self,
        path: pathlib.Path | str,
        language: str = "auto",
        diarization: bool = False,
        custom_vocabulary: Sequence[str] = (),
    ) -> dict[str, Any]:
        """Upload and transcribe one WAV chunk, returning local time offsets."""
        source = pathlib.Path(path).expanduser().resolve(strict=False)
        if not source.is_file():
            raise FileNotFoundError(f"Audio chunk not found: {source.name}")
        if source.suffix.lower() != ".wav":
            raise ValueError("Gemini transcription chunks must be WAV files")
        size = int(source.stat().st_size)
        if size <= 0:
            raise ValueError("Gemini transcription chunk is empty")

        vocabulary = _clean_vocabulary(custom_vocabulary)
        language_code = _normalize_language(language)
        if self._http_client is not None:
            return self._transcribe_with_client(
                self._http_client,
                source=source,
                size=size,
                language_code=language_code,
                diarization=bool(diarization),
                vocabulary=vocabulary,
            )
        with httpx.Client(timeout=self._timeout) as client:
            return self._transcribe_with_client(
                client,
                source=source,
                size=size,
                language_code=language_code,
                diarization=bool(diarization),
                vocabulary=vocabulary,
            )

    def _transcribe_with_client(
        self,
        client: Any,
        *,
        source: pathlib.Path,
        size: int,
        language_code: str | None,
        diarization: bool,
        vocabulary: tuple[str, ...],
    ) -> dict[str, Any]:
        remote_name: str | None = None
        result: dict[str, Any] | None = None
        try:
            uploaded = self._upload_file(client, source=source, size=size)
            remote_name = uploaded["name"]
            payload = self._create_interaction_payload(
                uri=uploaded["uri"],
                mime_type=uploaded["mime_type"],
                language_code=language_code,
                diarization=diarization,
                vocabulary=vocabulary,
            )
            response = self._request(
                client,
                "post",
                _INTERACTIONS_URL,
                stage="transcription",
                headers=self._api_headers(json_body=True),
                json=payload,
            )
            interaction = _json_object(response, stage="transcription")
            result = _parse_interaction(
                interaction,
                requested_language=language_code,
                model=self.model,
            )
        finally:
            if remote_name is not None:
                try:
                    self._delete_file(client, remote_name)
                except GeminiTranscriptionError:
                    # Cleanup must never replace the useful transcription (or
                    # hide the primary failure), but callers should know that
                    # Google may retain the upload until its automatic expiry.
                    if result is not None:
                        result["warnings"].append(
                            "Gemini could not delete the uploaded audio immediately; "
                            "the Files API will expire it automatically."
                        )
        if result is None:  # pragma: no cover - every failure above raises
            raise GeminiTranscriptionError("Gemini transcription failed")
        return result

    def _upload_file(self, client: Any, *, source: pathlib.Path, size: int) -> dict[str, str]:
        response = self._request(
            client,
            "post",
            _UPLOAD_START_URL,
            stage="upload initialization",
            headers={
                **self._api_headers(json_body=True),
                "X-Goog-Upload-Protocol": "resumable",
                "X-Goog-Upload-Command": "start",
                "X-Goog-Upload-Header-Content-Length": str(size),
                "X-Goog-Upload-Header-Content-Type": "audio/wav",
            },
            json={"file": {"display_name": source.name[:512]}},
            retryable=False,
        )
        upload_url = str(response.headers.get("x-goog-upload-url") or "").strip()
        if not _is_google_upload_url(upload_url):
            raise GeminiTranscriptionError("Gemini upload initialization returned an invalid upload URL")

        response = self._request(
            client,
            "post",
            upload_url,
            stage="audio upload",
            headers={
                "Content-Length": str(size),
                "Content-Type": "audio/wav",
                "X-Goog-Upload-Offset": "0",
                "X-Goog-Upload-Command": "upload, finalize",
            },
            content=_stream_file(source),
            retryable=False,
        )
        payload = _json_object(response, stage="audio upload")
        file_info = payload.get("file")
        if not isinstance(file_info, dict):
            raise GeminiTranscriptionError("Gemini audio upload returned invalid file metadata")
        name = str(file_info.get("name") or "").strip()
        uri = str(file_info.get("uri") or "").strip()
        mime_type = str(file_info.get("mimeType") or file_info.get("mime_type") or "audio/wav").strip()
        if not _REMOTE_FILE_NAME.fullmatch(name) or not uri:
            raise GeminiTranscriptionError("Gemini audio upload returned invalid file metadata")
        return {"name": name, "uri": uri, "mime_type": mime_type or "audio/wav"}

    def _create_interaction_payload(
        self,
        *,
        uri: str,
        mime_type: str,
        language_code: str | None,
        diarization: bool,
        vocabulary: tuple[str, ...],
    ) -> dict[str, Any]:
        mode: dict[str, Any] = {
            "type": "verbatim",
            "timestamp_granularities": ["word"],
        }
        if diarization:
            mode["diarization_mode"] = "speaker"
        config: dict[str, Any] = {"mode": mode}
        if language_code:
            config["language_codes"] = [language_code]
        if vocabulary:
            config["custom_vocabulary"] = list(vocabulary)
        return {
            "model": self.model,
            "input": [{"type": "audio", "uri": uri, "mime_type": mime_type}],
            "generation_config": {"transcription_config": config},
        }

    def _delete_file(self, client: Any, name: str) -> None:
        if not _REMOTE_FILE_NAME.fullmatch(name):
            raise GeminiTranscriptionError("Gemini returned an invalid remote file name")
        self._request(
            client,
            "delete",
            f"{_API_ROOT}/v1beta/{name}",
            stage="remote file cleanup",
            headers=self._api_headers(json_body=False),
        )

    def _api_headers(self, *, json_body: bool) -> dict[str, str]:
        headers = {"x-goog-api-key": self._api_key}
        if json_body:
            headers["Content-Type"] = "application/json"
        return headers

    @staticmethod
    def _request(
        client: Any,
        method: str,
        url: str,
        *,
        stage: str,
        retryable: bool = True,
        **kwargs: Any,
    ) -> Any:
        for attempt in range(3):
            response = None
            try:
                response = getattr(client, method)(url, **kwargs)
                response.raise_for_status()
                return response
            except Exception as exc:
                status = getattr(getattr(exc, "response", None), "status_code", None)
                if status is None:
                    status = getattr(response, "status_code", None)
                should_retry = retryable and isinstance(status, int) and (status == 429 or status >= 500)
                if should_retry and attempt < 2:
                    retry_after = str(getattr(response, "headers", {}).get("retry-after", "") or "").strip()
                    try:
                        delay = min(5.0, max(0.0, float(retry_after)))
                    except ValueError:
                        delay = min(5.0, float(2 ** attempt))
                    time.sleep(delay)
                    continue
                suffix = f" (HTTP {int(status)})" if isinstance(status, int) else ""
                # Never include response bodies, request URLs, exception messages,
                # or authentication material in this user-facing exception.
                raise GeminiTranscriptionError(f"Gemini request failed during {stage}{suffix}") from exc
        raise GeminiTranscriptionError(f"Gemini request failed during {stage}")  # pragma: no cover


def _stream_file(path: pathlib.Path) -> Iterator[bytes]:
    with path.open("rb") as handle:
        while True:
            block = handle.read(_UPLOAD_CHUNK_BYTES)
            if not block:
                return
            yield block


def _is_google_upload_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and parsed.hostname == "generativelanguage.googleapis.com"
        and parsed.port in {None, 443}
        and parsed.path == "/upload/v1beta/files"
    )


def _clean_vocabulary(values: Sequence[str]) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise TypeError("custom_vocabulary must be a sequence of phrases")
    clean = tuple(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))
    if len(clean) > 1000:
        raise ValueError("Gemini custom vocabulary supports at most 1000 phrases")
    if any(len(item) > 200 for item in clean):
        raise ValueError("Gemini custom vocabulary phrases support at most 200 characters")
    if sum(len(item.encode("utf-8")) for item in clean) > 32 * 1024:
        raise ValueError("Gemini custom vocabulary supports at most 32768 UTF-8 bytes")
    return clean


def _normalize_language(language: str) -> str | None:
    clean = str(language or "auto").strip()
    if not clean or clean.lower() == "auto":
        return None
    return _SHORT_LANGUAGE_CODES.get(clean.lower(), clean)


def _json_object(response: Any, *, stage: str) -> dict[str, Any]:
    try:
        payload = response.json()
    except Exception as exc:
        raise GeminiTranscriptionError(f"Gemini returned invalid JSON during {stage}") from exc
    if not isinstance(payload, dict):
        raise GeminiTranscriptionError(f"Gemini returned invalid JSON during {stage}")
    return payload


def _parse_offset(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return max(0.0, float(value))
    clean = str(value or "").strip().lower()
    if clean.endswith("s"):
        clean = clean[:-1]
    try:
        return max(0.0, float(clean))
    except (TypeError, ValueError):
        return None


def _word_annotations(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
    words: list[dict[str, Any]] = []
    invalid = 0
    for step in payload.get("steps") or []:
        if not isinstance(step, dict):
            continue
        for content in step.get("content") or []:
            if not isinstance(content, dict):
                continue
            for annotation in content.get("annotations") or []:
                if not isinstance(annotation, dict) or annotation.get("type") != "word_info":
                    continue
                text = str(annotation.get("text") or "")
                start = _parse_offset(annotation.get("start_offset"))
                end = _parse_offset(annotation.get("end_offset"))
                if not text.strip() or start is None or end is None or end < start:
                    invalid += 1
                    continue
                word: dict[str, Any] = {"start": start, "end": end, "word": text}
                speaker = str(annotation.get("speaker") or "").strip()
                if speaker:
                    word["speaker"] = speaker
                words.append(word)
    words.sort(key=lambda item: (item["start"], item["end"]))
    return words, invalid


def _content_text(payload: dict[str, Any]) -> str:
    direct = str(payload.get("output_text") or payload.get("outputText") or "").strip()
    if direct:
        return direct
    parts: list[str] = []
    for step in payload.get("steps") or []:
        if not isinstance(step, dict):
            continue
        for content in step.get("content") or []:
            if isinstance(content, dict) and content.get("type") == "text":
                text = str(content.get("text") or "").strip()
                if text:
                    parts.append(text)
    return "\n".join(parts)


def _join_word_text(words: Iterable[dict[str, Any]]) -> str:
    values = [str(word.get("word") or "") for word in words]
    if any(value[:1].isspace() for value in values):
        return "".join(values).strip()
    text = " ".join(value.strip() for value in values if value.strip())
    return re.sub(r"\s+([,.;:!?%)\]}])", r"\1", text).strip()


def _segments_from_words(words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not words:
        return []
    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for word in words:
        speaker = word.get("speaker")
        previous = current[-1] if current else None
        changed_speaker = previous is not None and speaker != previous.get("speaker")
        long_pause = previous is not None and word["start"] - previous["end"] > 1.5
        if current and (changed_speaker or long_pause):
            groups.append(current)
            current = []
        current.append(word)
    if current:
        groups.append(current)

    segments: list[dict[str, Any]] = []
    for group in groups:
        segment: dict[str, Any] = {
            "start": group[0]["start"],
            "end": group[-1]["end"],
            "text": _join_word_text(group),
            "words": group,
        }
        speaker = group[0].get("speaker")
        if speaker:
            segment["speaker"] = speaker
        segments.append(segment)
    return segments


def _parse_interaction(
    payload: dict[str, Any],
    *,
    requested_language: str | None,
    model: str,
) -> dict[str, Any]:
    words, invalid_words = _word_annotations(payload)
    text = _content_text(payload)
    segments = _segments_from_words(words)
    warnings: list[str] = []
    if invalid_words:
        warnings.append(f"Gemini omitted {invalid_words} invalid word annotation(s).")
    if not segments and text:
        segments = [{"start": 0.0, "end": 0.0, "text": text}]
        warnings.append("Gemini returned text without usable word timestamps.")
    if not segments:
        raise GeminiTranscriptionError("Gemini returned an empty transcription")

    detected_language = str(
        payload.get("language_code")
        or payload.get("languageCode")
        or payload.get("language")
        or requested_language
        or "unknown"
    ).strip()
    return {
        "segments": segments,
        "language": detected_language,
        "model": model,
        "warnings": warnings,
    }


__all__ = ["GeminiTranscriptionClient", "GeminiTranscriptionError"]
