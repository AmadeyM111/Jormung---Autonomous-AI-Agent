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
    assert items[0]["image_key"] == "pinimg:aa/bb/cat.jpg"


def test_post_broadcast_deduplicates_pinterest_image_sizes():
    first = plugin._pinimg_image_key("https://i.pinimg.com/474x/8e/63/3e/cat.jpg")
    second = plugin._pinimg_image_key("https://i.pinimg.com/736x/8e/63/3e/cat.jpg")
    original = plugin._pinimg_image_key("https://i.pinimg.com/originals/8e/63/3e/cat.jpg")

    assert first == second == original
    assert plugin._fingerprint("cats", "https://i.pinimg.com/474x/8e/63/3e/cat.jpg", "", "") == plugin._fingerprint(
        "cats",
        "https://i.pinimg.com/originals/8e/63/3e/cat.jpg",
        "",
        "",
    )


def test_post_broadcast_candidates_skip_failed_and_prepared():
    records = {
        "items": [
            {"id": "failed", "status": "failed", "image_url": "https://i.pinimg.com/736x/aa/bb/failed.jpg"},
            {"id": "prepared", "status": "prepared", "image_url": "https://i.pinimg.com/736x/aa/bb/prepared.jpg"},
            {"id": "new", "status": "new", "image_url": "https://i.pinimg.com/736x/aa/bb/new.jpg"},
        ]
    }

    assert [item["id"] for item in plugin._candidate_items(records)] == ["new"]


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
    assert prepared["prepared"]["caption_generation"]["vision_tool"] == "vlm_query"
    assert prepared["prepared"]["caption_generation"]["required"] is True
    assert prepared["prepared"]["caption_generation"]["required_subject"] == "cat"
    assert prepared["prepared"]["caption_generation"]["vision_model"].startswith("groq::")

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
        caption=(
            "Серый кот внимательно изучает край стола, будто проверяет действие гравитации "
            "на важных документах. Эксперимент считается успешным, когда всё уже лежит на полу."
        ),
        telegram_token="1234567890:test_token",
    )

    assert result["ok"] is True
    assert sent[0][1] == "7568942324"
    assert "Source: https://ru.pinterest.com/pin/42/" in sent[0][3]
    records = json.loads((tmp_path / "records.json").read_text(encoding="utf-8"))
    assert records["items"][0]["status"] == "sent"
    assert json.loads((tmp_path / "prepared.json").read_text(encoding="utf-8")) == {}


def test_post_broadcast_subscription_state_merges_configured_and_manual_ids(tmp_path):
    (tmp_path / "config.json").write_text(
        json.dumps({"telegram": {"chat_ids": ["7568942324", "394721762"]}}),
        encoding="utf-8",
    )

    subscribed = plugin._set_subscription(tmp_path, "361255098", subscribed=True)
    unsubscribed = plugin._set_subscription(tmp_path, "394721762", subscribed=False)

    assert subscribed["ok"] is True
    assert unsubscribed["ok"] is True
    status = plugin._subscription_status(tmp_path)
    assert status["configured_chat_ids"] == ["7568942324", "394721762"]
    assert status["subscribed_chat_ids"] == ["361255098"]
    assert status["unsubscribed_chat_ids"] == ["394721762"]
    assert status["active_chat_ids"] == ["7568942324", "361255098"]


def test_post_broadcast_send_prepared_skips_unsubscribed_configured_chat(tmp_path, monkeypatch):
    image_path = tmp_path / "images" / "post.png"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(_PNG_BYTES)
    (tmp_path / "config.json").write_text(
        json.dumps(
            {
                "telegram": {
                    "chat_ids": ["7568942324", "394721762"],
                    "append_source_link": False,
                }
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "prepared.json").write_text(
        json.dumps({"prepared_id": "post-1", "image_path": str(image_path), "source_text": "Cat studies gravity"}),
        encoding="utf-8",
    )
    (tmp_path / "records.json").write_text(
        json.dumps({"schema_version": 1, "items": [{"id": "post-1", "status": "prepared"}]}),
        encoding="utf-8",
    )
    plugin._set_subscription(tmp_path, "394721762", subscribed=False)
    sent_chat_ids = []
    monkeypatch.setattr(
        plugin,
        "_telegram_send_photo",
        lambda _token, chat_id, _image_path, _caption: sent_chat_ids.append(chat_id) or {"ok": True},
    )

    result = plugin._send_prepared(
        tmp_path,
        prepared_id="post-1",
        caption=(
            "Белый кот устроился рядом с чашкой и смотрит прямо в камеру. "
            "Так выглядит руководитель встречи, который уже понял: повестка снова могла быть письмом."
        ),
        telegram_token="1234567890:test_token",
    )

    assert result["ok"] is True
    assert result["requested_chats"] == 1
    assert sent_chat_ids == ["7568942324"]


def test_post_broadcast_send_prepared_tolerates_stale_prepared_id(tmp_path, monkeypatch):
    image_path = tmp_path / "images" / "post.png"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(_PNG_BYTES)
    (tmp_path / "config.json").write_text(
        json.dumps({"telegram": {"chat_ids": ["7568942324"], "append_source_link": False}}),
        encoding="utf-8",
    )
    (tmp_path / "prepared.json").write_text(
        json.dumps({"prepared_id": "current", "image_path": str(image_path), "source_text": "Cat meme"}),
        encoding="utf-8",
    )
    (tmp_path / "records.json").write_text(
        json.dumps({"schema_version": 1, "items": [{"id": "current", "status": "prepared"}]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(plugin, "_telegram_send_photo", lambda *_args: {"ok": True})

    result = plugin._send_prepared(
        tmp_path,
        prepared_id="stale",
        caption=(
            "Полосатый кот замер перед открытой коробкой и оценивает её вместимость. "
            "Техническое задание принято: сначала поместиться, потом разобраться с требованиями."
        ),
        telegram_token="1234567890:test_token",
    )

    assert result["ok"] is True
    assert result["warning"] == "requested prepared_id was stale; sent current prepared post"
    assert result["requested_prepared_id"] == "stale"
    assert result["current_prepared_id"] == "current"


def test_post_broadcast_send_prepared_rejects_empty_caption_and_keeps_prepared(tmp_path, monkeypatch):
    image_path = tmp_path / "images" / "post.png"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(_PNG_BYTES)
    (tmp_path / "config.json").write_text(
        json.dumps({"telegram": {"chat_ids": ["7568942324"], "append_source_link": False}}),
        encoding="utf-8",
    )
    (tmp_path / "prepared.json").write_text(
        json.dumps({"prepared_id": "current", "image_path": str(image_path), "title": "Cat memes"}),
        encoding="utf-8",
    )
    (tmp_path / "records.json").write_text(
        json.dumps({"schema_version": 1, "items": [{"id": "current", "status": "prepared"}]}),
        encoding="utf-8",
    )
    sent = []
    monkeypatch.setattr(
        plugin,
        "_telegram_send_photo",
        lambda _token, _chat_id, _image_path, caption: sent.append(caption) or {"ok": True},
    )

    result = plugin._send_prepared(tmp_path, prepared_id="current", caption="", telegram_token="1234567890:test_token")

    assert result["ok"] is False
    assert result["retryable"] is True
    assert "caption is required" in result["error"]
    assert sent == []
    assert json.loads((tmp_path / "prepared.json").read_text(encoding="utf-8"))["prepared_id"] == "current"


def test_post_broadcast_send_prepared_rejects_retired_static_caption(tmp_path, monkeypatch):
    image_path = tmp_path / "images" / "post.png"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(_PNG_BYTES)
    (tmp_path / "config.json").write_text(
        json.dumps({"telegram": {"chat_ids": ["7568942324"], "append_source_link": False}}),
        encoding="utf-8",
    )
    (tmp_path / "prepared.json").write_text(
        json.dumps({"prepared_id": "current", "image_path": str(image_path)}),
        encoding="utf-8",
    )
    (tmp_path / "records.json").write_text(
        json.dumps({"schema_version": 1, "items": [{"id": "current", "status": "prepared"}]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(plugin, "_telegram_send_photo", lambda *_args: {"ok": True})

    result = plugin._send_prepared(
        tmp_path,
        prepared_id="current",
        caption=(
            "Hello Memes\n\nКот в кадре демонстрирует уверенность старшего инженера: лапа уже "
            "на клавиатуре, мышь под контролем, задача почти решена. Осталось понять, кто открыл 47 вкладок."
        ),
        telegram_token="1234567890:test_token",
    )

    assert result["ok"] is False
    assert "retired static template" in result["error"]


def test_post_broadcast_send_prepared_rejects_recent_near_duplicate(tmp_path, monkeypatch):
    image_path = tmp_path / "images" / "post.png"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(_PNG_BYTES)
    previous = (
        "Рыжий кот внимательно смотрит на монитор и держит лапу на клавиатуре. "
        "Похоже, код-ревью закончится одобрением, если в миске уже появился корм."
    )
    (tmp_path / "config.json").write_text(
        json.dumps({"telegram": {"chat_ids": ["7568942324"], "append_source_link": False}}),
        encoding="utf-8",
    )
    (tmp_path / "prepared.json").write_text(
        json.dumps({"prepared_id": "current", "image_path": str(image_path)}),
        encoding="utf-8",
    )
    (tmp_path / "records.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "items": [
                    {"id": "current", "status": "prepared"},
                    {"id": "old", "status": "sent", "caption": previous + "\n\nSource: https://example.test"},
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(plugin, "_telegram_send_photo", lambda *_args: {"ok": True})

    result = plugin._send_prepared(
        tmp_path,
        prepared_id="current",
        caption="Новый заголовок\n\n" + previous,
        telegram_token="1234567890:test_token",
    )

    assert result["ok"] is False
    assert "too similar to a recent broadcast" in result["error"]


def test_post_broadcast_skip_prepared_marks_record_and_clears_state(tmp_path):
    (tmp_path / "prepared.json").write_text(json.dumps({"prepared_id": "dog-post"}), encoding="utf-8")
    (tmp_path / "records.json").write_text(
        json.dumps({"schema_version": 1, "items": [{"id": "dog-post", "status": "prepared"}]}),
        encoding="utf-8",
    )

    result = plugin._skip_prepared(tmp_path, prepared_id="dog-post", reason="SUBJECT: NOT_CAT; dog in image")

    assert result["ok"] is True
    assert json.loads((tmp_path / "prepared.json").read_text(encoding="utf-8")) == {}
    record = json.loads((tmp_path / "records.json").read_text(encoding="utf-8"))["items"][0]
    assert record["status"] == "skipped"
    assert "NOT_CAT" in record["last_error"]
