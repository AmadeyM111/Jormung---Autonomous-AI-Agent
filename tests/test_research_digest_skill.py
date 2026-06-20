import json
import pathlib
import datetime as dt

from skills.research_digest import plugin


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
    assert "[AI agents for production ML](https://example.com/high)" in digest["markdown"]


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
    assert "[AI agents for production ML](https://example.com/high)" in prepared["final_response"]
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
