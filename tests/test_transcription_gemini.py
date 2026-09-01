from __future__ import annotations

import httpx
import pytest

from ouroboros.transcription_gemini import (
    GeminiTranscriptionClient,
    GeminiTranscriptionError,
)


class FakeResponse:
    def __init__(self, payload=None, *, headers=None, status_code=200, body=""):
        self._payload = {} if payload is None else payload
        self.headers = httpx.Headers(headers or {})
        self.status_code = status_code
        self.text = body

    def json(self):
        if isinstance(self._payload, BaseException):
            raise self._payload
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("POST", "https://generativelanguage.googleapis.com/redacted")
            response = httpx.Response(self.status_code, request=request, text=self.text)
            raise httpx.HTTPStatusError("provider detail", request=request, response=response)


class FakeClient:
    def __init__(self, *, interaction=None, interaction_status=200, delete_status=200):
        self.calls = []
        self.uploaded = b""
        self.interaction = interaction or {}
        self.interaction_status = interaction_status
        self.delete_status = delete_status

    def post(self, url, **kwargs):
        self.calls.append(("post", url, kwargs))
        if url.endswith("/upload/v1beta/files"):
            return FakeResponse(
                headers={
                    "X-Goog-Upload-URL":
                        "https://generativelanguage.googleapis.com/upload/v1beta/files?upload_id=safe"
                }
            )
        if "/upload/v1beta/files?" in url:
            content = kwargs["content"]
            assert not isinstance(content, (bytes, bytearray))
            self.uploaded = b"".join(content)
            return FakeResponse({
                "file": {
                    "name": "files/audio-123",
                    "uri": "https://generativelanguage.googleapis.com/v1beta/files/audio-123",
                    "mimeType": "audio/wav",
                }
            })
        if url.endswith("/v1beta/interactions"):
            return FakeResponse(
                self.interaction,
                status_code=self.interaction_status,
                body="secret provider body",
            )
        raise AssertionError(f"Unexpected POST {url}")

    def delete(self, url, **kwargs):
        self.calls.append(("delete", url, kwargs))
        return FakeResponse(status_code=self.delete_status, body="secret cleanup body")


def _interaction():
    return {
        "output_text": "Привет, мир. Добрый день.",
        "language_code": "ru-RU",
        "steps": [{
            "content": [{
                "type": "text",
                "text": "Привет, мир. Добрый день.",
                "annotations": [
                    {
                        "type": "word_info", "text": "Привет,", "speaker": "spk_1",
                        "start_offset": "0.100s", "end_offset": "0.450s",
                    },
                    {
                        "type": "word_info", "text": "мир.", "speaker": "spk_1",
                        "start_offset": "0.500s", "end_offset": "0.850s",
                    },
                    {
                        "type": "word_info", "text": "Добрый", "speaker": "spk_2",
                        "start_offset": "1.000s", "end_offset": "1.300s",
                    },
                    {
                        "type": "word_info", "text": "день.", "speaker": "spk_2",
                        "start_offset": "1.310s", "end_offset": "1.600s",
                    },
                ],
            }],
        }],
    }


def test_requires_explicit_api_key():
    with pytest.raises(ValueError, match="API key is required"):
        GeminiTranscriptionClient("  ")


def test_resumable_upload_interaction_parse_and_cleanup(tmp_path):
    audio = tmp_path / "chunk.wav"
    audio.write_bytes(b"RIFF-fake-wave-bytes")
    http = FakeClient(interaction=_interaction())

    result = GeminiTranscriptionClient("top-secret-key", http_client=http).transcribe_file(
        audio,
        language="ru",
        diarization=True,
        custom_vocabulary=("Немотрон", "Немотрон", "Ouroboros"),
    )

    assert http.uploaded == audio.read_bytes()
    assert [call[0] for call in http.calls] == ["post", "post", "post", "delete"]
    start_headers = http.calls[0][2]["headers"]
    assert start_headers["X-Goog-Upload-Protocol"] == "resumable"
    assert start_headers["X-Goog-Upload-Header-Content-Length"] == str(audio.stat().st_size)

    interaction_payload = http.calls[2][2]["json"]
    transcription = interaction_payload["generation_config"]["transcription_config"]
    assert interaction_payload["model"] == "gemini-3.5-transcribe"
    assert transcription == {
        "mode": {
            "type": "verbatim",
            "timestamp_granularities": ["word"],
            "diarization_mode": "speaker",
        },
        "language_codes": ["ru-RU"],
        "custom_vocabulary": ["Немотрон", "Ouroboros"],
    }
    assert result == {
        "segments": [
            {
                "start": 0.1, "end": 0.85, "text": "Привет, мир.", "speaker": "spk_1",
                "words": [
                    {"start": 0.1, "end": 0.45, "word": "Привет,", "speaker": "spk_1"},
                    {"start": 0.5, "end": 0.85, "word": "мир.", "speaker": "spk_1"},
                ],
            },
            {
                "start": 1.0, "end": 1.6, "text": "Добрый день.", "speaker": "spk_2",
                "words": [
                    {"start": 1.0, "end": 1.3, "word": "Добрый", "speaker": "spk_2"},
                    {"start": 1.31, "end": 1.6, "word": "день.", "speaker": "spk_2"},
                ],
            },
        ],
        "language": "ru-RU",
        "model": "gemini-3.5-transcribe",
        "warnings": [],
    }
    assert http.calls[-1][1].endswith("/v1beta/files/audio-123")


def test_auto_language_omits_hint_and_diarization(tmp_path):
    audio = tmp_path / "chunk.wav"
    audio.write_bytes(b"wave")
    http = FakeClient(interaction=_interaction())

    GeminiTranscriptionClient("key", http_client=http).transcribe_file(audio)

    config = http.calls[2][2]["json"]["generation_config"]["transcription_config"]
    assert "language_codes" not in config
    assert "diarization_mode" not in config["mode"]
    assert config["mode"]["timestamp_granularities"] == ["word"]


def test_interaction_failure_is_sanitized_and_remote_file_is_deleted(tmp_path):
    audio = tmp_path / "chunk.wav"
    audio.write_bytes(b"wave")
    http = FakeClient(interaction_status=403)

    with pytest.raises(GeminiTranscriptionError) as caught:
        GeminiTranscriptionClient("top-secret-key", http_client=http).transcribe_file(audio)

    message = str(caught.value)
    assert message == "Gemini request failed during transcription (HTTP 403)"
    assert "top-secret-key" not in message
    assert "secret provider body" not in message
    assert http.calls[-1][0] == "delete"


def test_cleanup_failure_is_warning_not_lost_transcript(tmp_path):
    audio = tmp_path / "chunk.wav"
    audio.write_bytes(b"wave")
    http = FakeClient(interaction=_interaction(), delete_status=500)

    result = GeminiTranscriptionClient("key", http_client=http).transcribe_file(audio)

    assert result["segments"]
    assert result["warnings"] == [
        "Gemini could not delete the uploaded audio immediately; "
        "the Files API will expire it automatically."
    ]


def test_rate_limit_retries_are_bounded(tmp_path, monkeypatch):
    audio = tmp_path / "chunk.wav"
    audio.write_bytes(b"wave")

    class RateLimitedOnce(FakeClient):
        attempts = 0

        def post(self, url, **kwargs):
            if url.endswith("/v1beta/interactions"):
                self.attempts += 1
                if self.attempts == 1:
                    self.calls.append(("post", url, kwargs))
                    return FakeResponse(status_code=429, headers={"Retry-After": "0"})
            return super().post(url, **kwargs)

    http = RateLimitedOnce(interaction=_interaction())
    sleeps = []
    monkeypatch.setattr("ouroboros.transcription_gemini.time.sleep", sleeps.append)
    result = GeminiTranscriptionClient("key", http_client=http).transcribe_file(audio)

    assert result["segments"]
    assert http.attempts == 2
    assert sleeps == [0.0]


def test_text_without_word_annotations_has_safe_fallback_segment(tmp_path):
    audio = tmp_path / "chunk.wav"
    audio.write_bytes(b"wave")
    http = FakeClient(interaction={"output_text": "Только текст"})

    result = GeminiTranscriptionClient("key", http_client=http).transcribe_file(audio)

    assert result["segments"] == [{"start": 0.0, "end": 0.0, "text": "Только текст"}]
    assert result["language"] == "unknown"
    assert result["warnings"] == ["Gemini returned text without usable word timestamps."]


def test_rejects_non_wav_and_oversized_vocabulary(tmp_path):
    audio = tmp_path / "chunk.mp3"
    audio.write_bytes(b"audio")
    client = GeminiTranscriptionClient("key", http_client=FakeClient())
    with pytest.raises(ValueError, match="must be WAV"):
        client.transcribe_file(audio)

    wav = tmp_path / "chunk.wav"
    wav.write_bytes(b"wave")
    with pytest.raises(ValueError, match="at most 1000"):
        client.transcribe_file(wav, custom_vocabulary=tuple(f"term-{i}" for i in range(1001)))
    with pytest.raises(ValueError, match="200 characters"):
        client.transcribe_file(wav, custom_vocabulary=("x" * 201,))


def test_upload_initialization_is_not_retried(tmp_path, monkeypatch):
    audio = tmp_path / "chunk.wav"
    audio.write_bytes(b"wave")

    class FailingInit(FakeClient):
        def post(self, url, **kwargs):
            self.calls.append(("post", url, kwargs))
            return FakeResponse(status_code=503)

    http = FailingInit()
    monkeypatch.setattr(
        "ouroboros.transcription_gemini.time.sleep",
        lambda _delay: pytest.fail("upload init must not retry"),
    )
    with pytest.raises(GeminiTranscriptionError, match="upload initialization"):
        GeminiTranscriptionClient("key", http_client=http).transcribe_file(audio)
    assert len(http.calls) == 1
