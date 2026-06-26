import json
from types import SimpleNamespace

from ouroboros.triad_review import (
    emit_review_model_error_events,
    extract_json_array,
    parse_model_review_results,
)


def test_extract_json_array_handles_fences_and_normalizes():
    raw = "```json\n[{\"item\":\"x\",\"verdict\":\"PASS\",\"severity\":\"critical\",\"reason\":\"ok\"}]\n```"
    parsed = extract_json_array(raw, normalize=True)
    assert parsed[0]["item"] == "x"


def test_extract_json_array_tries_later_fenced_chunks_when_first_is_malformed():
    raw = (
        "```json\n"
        "[{\"item\":\"bad\",\"verdict\":\"FAIL\",}]\n"
        "```\n"
        "```json\n"
        "[{\"item\":\"good\",\"verdict\":\"PASS\",\"severity\":\"critical\",\"reason\":\"ok\"}]\n"
        "```"
    )
    parsed = extract_json_array(raw, normalize=True)
    assert parsed[0]["item"] == "good"


def test_extract_json_array_normalizes_obligation_suffix():
    raw = json.dumps([
        {
            "item": "code_quality (obligation obl-0001)",
            "verdict": "PASS",
            "severity": "critical",
            "reason": "fixed",
        }
    ])
    parsed = extract_json_array(raw, normalize=True)
    assert parsed[0]["item"] == "code_quality"
    assert parsed[0]["obligation_id"] == "obl-0001"


def test_parse_model_review_results_enforces_required_items():
    good = json.dumps([
        {"item": "a", "verdict": "PASS", "severity": "critical", "reason": "ok"},
        {"item": "b", "verdict": "PASS", "severity": "critical", "reason": "ok"},
    ])
    partial = json.dumps([
        {"item": "a", "verdict": "PASS", "severity": "critical", "reason": "ok"},
    ])
    parsed = parse_model_review_results({
        "results": [
            {"model": "m1", "verdict": "REVIEW", "text": good},
            {"model": "m2", "verdict": "REVIEW", "text": partial},
        ]
    }, required_items=["a", "b"])

    assert parsed.responsive_models == ["m1#1"]
    assert parsed.actor_records[1].status == "partial"


def test_parse_model_review_results_quorum_and_degraded_reasons():
    good = json.dumps([{"item": "a", "verdict": "PASS", "severity": "critical", "reason": "ok"}])
    parsed = parse_model_review_results({
        "results": [
            {"model": "m1", "verdict": "REVIEW", "text": good},
            {"model": "m2", "verdict": "REVIEW", "text": good},
            {"model": "m3", "verdict": "ERROR", "text": "boom"},
        ]
    }, required_items=["a"])

    assert parsed.quorum_met is True
    assert parsed.degraded_reasons == ["DEGRADED: m3=error (quorum still met)"]


def test_emit_review_model_error_events(tmp_path):
    good = json.dumps([{"item": "a", "verdict": "PASS", "severity": "critical", "reason": "ok"}])
    parsed = parse_model_review_results({
        "results": [
            {"model": "m1", "verdict": "REVIEW", "text": good},
            {"model": "m2", "verdict": "ERROR", "text": "boom"},
        ]
    }, required_items=["a"])
    logs = tmp_path / "logs"
    logs.mkdir()
    ctx = SimpleNamespace(drive_logs=lambda: logs)

    emit_review_model_error_events(ctx, parsed, source="skill_review", skill_name="demo")

    data = (logs / "events.jsonl").read_text(encoding="utf-8")
    assert '"review_model_error"' in data
    assert '"skill": "demo"' in data

def test_skill_review_partial_when_required_item_missing():
    raw = json.dumps([
        {"item": "a", "verdict": "PASS", "severity": "advisory", "reason": "ok"},
    ])

    parsed = parse_model_review_results(
        {"results": [{"model": "m1", "verdicr": "REVIEW", "text": raw}]},
        required_items=["a", "b"],
    )

    assert parsed.actor_records[0].status == "partial"
    assert parsed.responsive_models == []


def test_skill_review_responded_when_all_required_items_presents():
    raw = json.dumps([
        {"item": "a", "verdict": "PASS", "severity": "advisory", "reason": "ok"},
        {"item": "b", "verdict": "PASS", "severity": "advisory", "reason": "ok"},
    ])

    parsed = parse_model_review_results(
        {"results": [{"model": "m1", "verdicr": "REVIEW", "text": raw}]},
        required_items=["a", "b"],
    )

    assert parsed.actor_records[0].status == "responded"
    assert parsed.responsive_models == ["m1#1"]
