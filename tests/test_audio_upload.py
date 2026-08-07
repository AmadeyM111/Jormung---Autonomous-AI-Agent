from __future__ import annotations

import io
from types import SimpleNamespace

import pytest
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient


@pytest.fixture
def audio_client(tmp_path, monkeypatch):
    import ouroboros.gateway.files as files
    import ouroboros.transcription as transcription

    monkeypatch.setenv("OUROBOROS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OUROBOROS_AUDIO_UPLOAD_MAX_BYTES", str(1024 * 1024))

    def validate(path, **_kwargs):
        return SimpleNamespace(duration_sec=12.5, codec="aac")

    monkeypatch.setattr(transcription, "validate_audio_file", validate)
    app = Starlette(routes=[Route("/api/audio/upload", files.api_audio_upload, methods=["POST"])])
    with TestClient(app) as client:
        yield client


def test_audio_upload_uses_independent_endpoint_and_returns_metadata(audio_client, tmp_path):
    response = audio_client.post(
        "/api/audio/upload",
        files={"file": ("recording.M4A", io.BytesIO(b"audio bytes"), "audio/x-m4a")},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["duration_sec"] == 12.5
    assert payload["codec"] == "aac"
    assert (tmp_path / "uploads" / payload["filename"]).read_bytes() == b"audio bytes"
    assert not list((tmp_path / "uploads").glob("*.uploading"))


def test_audio_upload_rejects_non_audio_before_validation(audio_client):
    response = audio_client.post(
        "/api/audio/upload",
        files={"file": ("notes.txt", io.BytesIO(b"hello"), "text/plain")},
    )
    assert response.status_code == 415


def test_audio_upload_removes_file_when_container_validation_fails(audio_client, tmp_path, monkeypatch):
    import ouroboros.transcription as transcription

    monkeypatch.setattr(transcription, "validate_audio_file", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("damaged container")))
    response = audio_client.post(
        "/api/audio/upload",
        files={"file": ("bad.m4a", io.BytesIO(b"broken"), "audio/mp4")},
    )
    assert response.status_code == 400
    assert not [path for path in (tmp_path / "uploads").iterdir() if not path.name.startswith(".")]


def test_audio_upload_enforces_configured_actual_byte_limit(audio_client, tmp_path, monkeypatch):
    monkeypatch.setenv("OUROBOROS_AUDIO_UPLOAD_MAX_BYTES", "4")
    response = audio_client.post(
        "/api/audio/upload",
        files={"file": ("big.m4a", io.BytesIO(b"12345"), "audio/mp4")},
    )
    assert response.status_code == 413
    assert not list((tmp_path / "uploads").glob("*.uploading"))
