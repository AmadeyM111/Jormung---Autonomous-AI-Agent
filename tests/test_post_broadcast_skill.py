import json

from skills.post_broadcast import plugin


_PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00"
    b"\x00\x00\x0cIDATx\x9cc```\x00\x00\x00\x04\x00\x01"
    b"\xf6\x178U\x00\x00\x00\x00IEND\xaeB`\x82"
)


def test_post_broadcast_parses_pinterest_board_html():
    raw = """
    <html>
      <script>
        {"title":"A clever cat meme","url":"https:\\u002F\\u002Fru.pinterest.com\\u002Fpin\\u002F123456789\\u002F",
         "image":"https:\\u002F\\u002Fi.pinimg.com\\u002Foriginals\\u002Faa\\u002Fbb\\u002Fcat.jpg"}
      </script>
    </html>
    """

    items = plugin._parse_pinterest_board(
        raw,
        {"id": "cats", "kind": "pinterest_board", "title": "Cats"},
        "2026-06-24T10:00:00Z",
    )

    assert len(items) == 1
    assert items[0]["image_url"] == "https://i.pinimg.com/originals/aa/bb/cat.jpg"
    assert items[0]["url"] == "https://ru.pinterest.com/pin/123456789/"
    assert items[0]["title"] == "A clever cat meme"
    assert items[0]["status"] == "new"


def test_post_broadcast_prepare_next_downloads_required_image(tmp_path, monkeypatch):
    (tmp_path / "config.json").write_text(
        json.dumps({"moderation": {"min_image_width": 1, "min_image_height": 1}}),
        encoding="utf-8",
    )
    raw = """
    {"title":"Cat studies gravity",
     "url":"https:\\u002F\\u002Fru.pinterest.com\\u002Fpin\\u002F42\\u002F",
     "image":"https:\\u002F\\u002Fi.pinimg.com\\u002Foriginals\\u002Fcat.png"}
    """

    def fake_fetch(url, *, max_bytes, accept):
        if "pinimg.com" in url:
            return _PNG_BYTES, "image/png"
        return raw.encode("utf-8"), "text/html"

    monkeypatch.setattr(plugin, "_fetch_bytes", fake_fetch)

    prepared = plugin._prepare_next(tmp_path, refresh=True)

    assert prepared["ok"] is True
    assert prepared["has_prepared"] is True
    assert prepared["prepared"]["prepared_id"]
    assert prepared["prepared"]["source_url"] == "https://ru.pinterest.com/pin/42/"
    assert prepared["prepared"]["image_path"].endswith(".png")

    records = json.loads((tmp_path / "records.json").read_text(encoding="utf-8"))
    assert records["items"][0]["status"] == "prepared"


def test_post_broadcast_send_prepared_appends_source_and_marks_sent(tmp_path, monkeypatch):
    image_path = tmp_path / "images" / "post.png"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(_PNG_BYTES)
    (tmp_path / "config.json").write_text(
        json.dumps(
            {
                "telegram": {
                    "chat_ids": ["7568942324"],
                    "append_source_link": True,
                }
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "prepared.json").write_text(
        json.dumps(
            {
                "prepared_id": "post-1",
                "source_url": "https://ru.pinterest.com/pin/42/",
                "image_path": str(image_path),
                "source_text": "Cat studies gravity",
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "records.json").write_text(
        json.dumps({"schema_version": 1, "items": [{"id": "post-1", "status": "prepared"}]}),
        encoding="utf-8",
    )
    sent = []

    monkeypatch.setattr(
        plugin,
        "_telegram_send_photo",
        lambda token, chat_id, image_path, caption: sent.append((token, chat_id, str(image_path), caption)) or {"ok": True},
    )

    result = plugin._send_prepared(
        tmp_path,
        prepared_id="post-1",
        caption="Intellectual cat joke",
        telegram_token="1234567890:test_token",
    )

    assert result["ok"] is True
    assert sent[0][1] == "7568942324"
    assert "Source: https://ru.pinterest.com/pin/42/" in sent[0][3]
    records = json.loads((tmp_path / "records.json").read_text(encoding="utf-8"))
    assert records["items"][0]["status"] == "sent"
    assert json.loads((tmp_path / "prepared.json").read_text(encoding="utf-8")) == {}
