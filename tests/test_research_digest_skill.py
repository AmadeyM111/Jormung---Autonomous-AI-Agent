import json
import pathlib
import datetime as dt

from skills.research_digest import plugin


def test_research_digest_manifest_declares_subprocess_permission():
    manifest_path = pathlib.Path(plugin.__file__).with_name("skill.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert "subprocess" in manifest["permissions"]


def test_research_digest_parses_rss_and_scores_topics(tmp_path):
    raw = """<?xml version="1.0"?>
    <rss version="2.0"><channel>
      <item>
        <title>Agentic RAG in production ML systems</title>
        <link>https://example.com/a</link>
        <description>Engineering case study for enterprise AI adoption.</description>
        <pubDate>Sun, 14 Jun 2026 09:00:00 GMT</pubDate>
      </item>
    </channel></rss>
    """
    source = {"id": "demo", "kind": "rss", "title": "Demo"}
    items = plugin._parse_rss_atom(raw, source, "2026-06-14T10:00:00Z")

    assert len(items) == 1
    scored = plugin._score_item(items[0], plugin._DEFAULT_CONFIG["topics"])
    assert scored["id"] == ""
    assert scored["score"] > 0
    assert {"ai", "agentic_systems", "ml_business", "engineering"} <= set(scored["topic_matches"])


def test_research_digest_upserts_telegram_public_source(tmp_path):
    result = plugin._upsert_source(
        pathlib.Path(tmp_path),
        {"id": "tg_ai", "kind": "telegram_public", "channel": "some_public_channel", "title": "TG AI"},
    )

    assert result["ok"] is True
    assert result["source"]["url"] == "https://t.me/s/some_public_channel"
    saved = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert saved["sources"][-1]["id"] == "tg_ai"


def test_research_digest_digest_markdown_orders_by_score(tmp_path):
    now = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    records = {
        "schema_version": 1,
        "items": [
            {
                "id": "low",
                "title": "General update",
                "url": "https://example.com/low",
                "source_title": "Low",
                "summary": "",
                "published_at": now,
                "fetched_at": now,
                "score": 1,
            },
            {
                "id": "high",
                "title": "AI agents for production ML",
                "url": "https://example.com/high",
                "source_title": "High",
                "summary": "Business implementation notes.",
                "published_at": now,
                "fetched_at": now,
                "score": 9,
                "topic_matches": {"ai": ["ai"], "ml_business": ["business"]},
            },
        ],
    }
    (tmp_path / "records.json").write_text(json.dumps(records), encoding="utf-8")

    digest = plugin._digest_items(pathlib.Path(tmp_path), hours=48, limit=2, min_score=1)

    assert digest["items"][0]["id"] == "high"
    assert "1. AI agents for production ML" in digest["markdown"]
    assert "Link: https://example.com/high" in digest["markdown"]
    assert "TL;DR" in digest["markdown"]


def test_research_digest_prepare_digest_returns_direct_compact_response(tmp_path):
    now = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    long_summary = "Business implementation notes. " * 80
    records = {
        "schema_version": 1,
        "items": [
            {
                "id": "high",
                "title": "AI agents for production ML",
                "url": "https://example.com/high",
                "source_title": "High",
                "summary": long_summary,
                "published_at": now,
                "fetched_at": now,
                "score": 9,
                "topic_matches": {"ai": ["ai"], "ml_business": ["business"]},
            },
        ],
    }
    (tmp_path / "records.json").write_text(json.dumps(records), encoding="utf-8")

    prepared = plugin._prepare_digest(pathlib.Path(tmp_path), refresh=False, hours=48, limit=1)

    assert prepared["final_response_mode"] == "direct"
    assert "1. AI agents for production ML" in prepared["final_response"]
    assert "Link: https://example.com/high" in prepared["final_response"]
    assert prepared["refresh"]["enabled"] is False
    assert len(prepared["digest"]["items"][0]["summary"]) < len(long_summary)


def test_research_digest_prepare_digest_diversifies_sources(tmp_path):
    now = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    items = []
    for idx in range(4):
        items.append({
            "id": f"arxiv-{idx}",
            "title": f"arXiv AI agents paper {idx}",
            "url": f"https://example.com/arxiv/{idx}",
            "source_id": "arxiv_cs_ai",
            "source_title": "arXiv cs.AI",
            "summary": "AI agents benchmark research.",
            "published_at": now,
            "fetched_at": now,
            "score": 20 - idx,
            "topic_matches": {"ai": ["ai"], "agentic_systems": ["agents"]},
        })
    for source_id, title, score in (("aws_ml_blog", "AWS ML", 12), ("google_research", "Google Research", 11)):
        items.append({
            "id": source_id,
            "title": f"{title} production AI case",
            "url": f"https://example.com/{source_id}",
            "source_id": source_id,
            "source_title": title,
            "summary": "Enterprise AI deployment notes.",
            "published_at": now,
            "fetched_at": now,
            "score": score,
            "topic_matches": {"ai": ["ai"], "ml_business": ["enterprise"]},
        })
    (tmp_path / "records.json").write_text(json.dumps({"schema_version": 1, "items": items}), encoding="utf-8")

    prepared = plugin._prepare_digest(pathlib.Path(tmp_path), refresh=False, hours=48, limit=4, max_per_source=2)
    selected_sources = [item["source"] for item in prepared["digest"]["items"]]

    assert selected_sources.count("arXiv cs.AI") == 2
    assert "AWS ML" in selected_sources
    assert "Google Research" in selected_sources


def test_research_digest_markdown_cleans_arxiv_summary_noise(tmp_path):
    now = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    records = {
        "schema_version": 1,
        "items": [
            {
                "id": "paper",
                "title": "Predictive Validity for LLM Agents",
                "url": "https://example.com/paper",
                "source_title": "arXiv cs.AI",
                "summary": (
                    "arXiv:2606.19704v1 Announce Type: new Abstract: "
                    "Agent benchmarks are growing fast, but no single benchmark "
                    "captures deployment risk."
                ),
                "published_at": now,
                "fetched_at": now,
                "score": 21,
                "topic_matches": {"ai": ["ai"], "agentic_systems": ["agents"]},
            },
        ],
    }
    (tmp_path / "records.json").write_text(json.dumps(records), encoding="utf-8")

    digest = plugin._digest_items(pathlib.Path(tmp_path), hours=48, limit=1, min_score=1)

    assert "arXiv:2606" not in digest["markdown"]
    assert "Announce Type" not in digest["markdown"]
    assert "Why it matters: Agent benchmarks are growing fast" in digest["markdown"]


def test_research_digest_parses_last30days_compact_output():
    raw = """last30days v3.8.0 · synced 2026-06-21

What I learned:
**AI agents moved from demos to production.** Teams are discussing evals, tool use, and workflow reliability across [HN](https://news.ycombinator.com/item?id=1).

KEY PATTERNS from the research:
1. Production ML agents

---
✅ All agents reported back!
"""
    source = {"id": "l30_ai", "kind": "last30days", "topic": "AI agents", "title": "Last30Days AI"}

    items = plugin._parse_last30days_output(raw, source, "2026-06-21T10:00:00Z")
    scored = plugin._score_item(items[0], plugin._DEFAULT_CONFIG["topics"])

    assert len(items) == 1
    assert scored["kind"] == "last30days"
    assert scored["source_title"] == "Last30Days AI"
    assert scored["url"] == "https://news.ycombinator.com/item?id=1"
    assert "AI agents moved from demos" in scored["summary"]
    assert scored["score"] > 0


def test_research_digest_collects_last30days_source_with_local_engine(tmp_path):
    engine = tmp_path / "last30days" / "scripts" / "last30days.py"
    engine.parent.mkdir(parents=True)
    engine.write_text(
        "import sys\n"
        "print('last30days v3.8.0 · synced 2026-06-21')\n"
        "print('')\n"
        "print('What I learned:')\n"
        "print('AI agents and production ML workflows are the discussion this month.')\n",
        encoding="utf-8",
    )
    source = {
        "id": "l30_ai",
        "kind": "last30days",
        "topic": "AI agents",
        "title": "Last30Days AI",
        "engine_path": str(engine),
        "timeout_sec": 10,
    }

    result = plugin._collect_source(source, plugin._DEFAULT_CONFIG["topics"], 5, pathlib.Path(tmp_path))

    assert result["source_id"] == "l30_ai"
    assert len(result["items"]) == 1
    assert result["items"][0]["kind"] == "last30days"
    assert result["items"][0]["score"] > 0


def test_direct_final_response_is_extension_only():
    from ouroboros.loop_tool_execution import _direct_final_response_from_tool

    payload = json.dumps(
        {
            "ok": True,
            "final_response_mode": "direct",
            "final_response": "ready markdown",
        }
    )

    assert _direct_final_response_from_tool("ext_17_r_research_digest_prepare_digest", payload) == "ready markdown"
    assert _direct_final_response_from_tool("read_file", payload) == ""


def test_tool_prepare_digest_parses_string_false_refresh(tmp_path):
    payload = json.loads(plugin._tool_prepare_digest(state_dir=pathlib.Path(tmp_path), refresh="false"))

    assert payload["ok"] is True
    assert payload["refresh"]["enabled"] is False


def test_research_digest_subscription_state_is_explicit_opt_in(tmp_path):
    state_dir = pathlib.Path(tmp_path)

    empty = plugin._subscription_status(state_dir)
    subscribed = plugin._set_subscription(state_dir, "4242", subscribed=True)
    unsubscribed = plugin._set_subscription(state_dir, "4242", subscribed=False)

    assert empty["active_chat_ids"] == []
    assert subscribed["ok"] is True
    assert subscribed["active_chat_ids"] == ["4242"]
    assert unsubscribed["ok"] is True
    assert unsubscribed["active_chat_ids"] == []
    assert unsubscribed["unsubscribed_chat_ids"] == ["4242"]


def test_research_digest_send_digest_targets_only_active_subscribers(tmp_path, monkeypatch):
    state_dir = pathlib.Path(tmp_path)
    plugin._set_subscription(state_dir, "111", subscribed=True)
    plugin._set_subscription(state_dir, "222", subscribed=True)
    plugin._set_subscription(state_dir, "222", subscribed=False)
    now = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    (state_dir / "records.json").write_text(
        json.dumps({
            "schema_version": 1,
            "items": [{
                "id": "high",
                "title": "AI agents for production ML",
                "url": "https://example.com/high",
                "source_title": "High",
                "summary": "Business implementation notes.",
                "published_at": now,
                "fetched_at": now,
                "score": 9,
            }],
        }),
        encoding="utf-8",
    )
    sent = []
    monkeypatch.setattr(
        plugin,
        "_telegram_send_message",
        lambda _token, chat_id, text: sent.append((chat_id, text)) or {"ok": True},
    )

    result = plugin._send_digest(
        state_dir,
        telegram_token="token",
        refresh=False,
        hours=48,
        limit=1,
    )

    assert result["ok"] is True
    assert result["sent_count"] == 1
    assert sent[0][0] == "111"
    assert "AI agents for production ML" in sent[0][1]
