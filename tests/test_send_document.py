from __future__ import annotations

import base64
from types import SimpleNamespace

import ouroboros.event_bus as event_bus
import supervisor.message_bus as message_bus
from ouroboros.utils import append_jsonl
from supervisor.events import dispatch_event


def test_message_bus_publishes_document_transport_event(monkeypatch):
    bridge = message_bus.LocalChatBridge({})
    events = []
    monkeypatch.setattr(message_bus, "publish_event", lambda topic, data: events.append((topic, data)))

    ok, _ = bridge.send_document(
        123, b"transcript", filename="recording.txt", caption="done", mime="text/plain",
    )

    assert ok is True
    topic, payload = events[-1]
    assert topic == event_bus.CHAT_DOCUMENT
    assert base64.b64decode(payload["file_base64"]) == b"transcript"
    assert payload["filename"] == "recording.txt"
    assert payload["caption"] == "done"
    assert payload["mime"] == "text/plain"


def test_supervisor_dispatches_document_file(tmp_path):
    document = tmp_path / "recording.txt"
    document.write_bytes(b"transcript")
    sent = []
    ctx = SimpleNamespace(
        DRIVE_ROOT=tmp_path,
        append_jsonl=append_jsonl,
        bridge=SimpleNamespace(
            send_document=lambda chat_id, data, filename, caption="", mime="": (
                sent.append((chat_id, data, filename, caption, mime)) or (True, "ok")
            ),
        ),
    )

    dispatch_event({
        "type": "send_document",
        "chat_id": 42,
        "file_path": str(document),
        "filename": document.name,
        "caption": "ready",
    }, ctx)

    assert sent == [(42, b"transcript", "recording.txt", "ready", "text/plain")]
