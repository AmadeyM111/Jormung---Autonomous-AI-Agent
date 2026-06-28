"""Loop miscellaneous regressions.

Consolidated from former ``test_loop_incoming_messages.py`` (image
payload preservation) and ``test_loop_skill_finalization.py``
(self-authored skill finalization gate). Both modules exercise
narrow corners of ``ouroboros.loop`` that did not justify standalone
files after Phase 5.

Kept here as one module so future loop micro-regressions have a
natural home instead of producing yet another single-test file.
"""
from __future__ import annotations

import json
import queue
from types import SimpleNamespace

import ouroboros.loop as loop_mod
from ouroboros.loop import (
    _drain_incoming_messages,
    _maybe_inject_self_check,
    _run_task_acceptance_review_once,
    _skill_finalization_message,
    _skill_names_touched_by_trace,
    _task_acceptance_eligible,
    run_llm_loop,
)
from ouroboros.skill_loader import (
    SkillReviewState,
    compute_content_hash,
    save_enabled,
    save_review_state,
)


# ---------------------------------------------------------------------------
# _drain_incoming_messages — telegram image payload preservation
# ---------------------------------------------------------------------------


def test_drain_incoming_messages_preserves_image_payload():
    messages: list = []
    incoming_messages: queue.Queue = queue.Queue()
    incoming_messages.put({
        "text": "photo from telegram",
        "image_base64": "aW1hZ2U=",
        "image_mime": "image/png",
        "image_caption": "photo from telegram",
    })

    _drain_incoming_messages(
        messages=messages,
        incoming_messages=incoming_messages,
        drive_root=None,
        task_id="",
        event_queue=None,
        _owner_msg_seen=set(),
    )

    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    content = messages[0]["content"]
    assert isinstance(content, list)
    assert content[0]["type"] == "text"
    assert content[0]["text"] == "[Message from my human]: photo from telegram"
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"] == "data:image/png;base64,aW1hZ2U="


def test_fallback_model_switch_is_trace_only_not_chat_progress(tmp_path, monkeypatch):
    from ouroboros.tools.registry import ToolRegistry

    calls = []
    progress = []
    monkeypatch.setenv("OUROBOROS_MODEL_FALLBACK", "openai-compatible::fallback")

    class FakeLLM:
        def default_model(self):
            return "openai-compatible::primary"

    def fake_call_llm_with_retry(_llm, _messages, model, *_args, **_kwargs):
        calls.append(model)
        if len(calls) == 1:
            return None, 0.0
        return {"role": "assistant", "content": "fallback answer"}, 0.0

    monkeypatch.setattr(loop_mod, "call_llm_with_retry", fake_call_llm_with_retry)

    result, _usage, trace = run_llm_loop(
        messages=[{"role": "user", "content": "write a short status report"}],
        tools=ToolRegistry(repo_dir=tmp_path, drive_root=tmp_path),
        llm=FakeLLM(),
        drive_logs=tmp_path,
        emit_progress=progress.append,
        incoming_messages=queue.Queue(),
        task_id="task1",
        drive_root=tmp_path,
    )

    assert result == "fallback answer"
    assert calls == ["openai-compatible::primary", "openai-compatible::fallback"]
    assert not any("Fallback:" in item for item in progress)
    assert any("Fallback:" in item for item in trace["reasoning_notes"])


def test_fallback_model_chain_reaches_reserve(tmp_path, monkeypatch):
    from ouroboros.tools.registry import ToolRegistry

    calls = []
    monkeypatch.setenv("OUROBOROS_MODEL_FALLBACK", "openai-compatible::fallback")
    monkeypatch.setenv("OUROBOROS_MODEL_RESERVE", "openrouter::reserve/model")

    class FakeLLM:
        def default_model(self):
            return "openai-compatible::primary"

    def fake_call_llm_with_retry(_llm, _messages, model, *_args, **_kwargs):
        calls.append(model)
        if model == "openrouter::reserve/model":
            return {"role": "assistant", "content": "reserve answer"}, 0.0
        return None, 0.0

    monkeypatch.setattr(loop_mod, "call_llm_with_retry", fake_call_llm_with_retry)

    result, _usage, trace = run_llm_loop(
        messages=[{"role": "user", "content": "write a short status report"}],
        tools=ToolRegistry(repo_dir=tmp_path, drive_root=tmp_path),
        llm=FakeLLM(),
        drive_logs=tmp_path,
        emit_progress=lambda _text: None,
        incoming_messages=queue.Queue(),
        task_id="task1",
        drive_root=tmp_path,
    )

    assert result == "reserve answer"
    assert calls == [
        "openai-compatible::primary",
        "openai-compatible::fallback",
        "openrouter::reserve/model",
    ]
    assert len([item for item in trace["reasoning_notes"] if item.startswith("Fallback:")]) == 2


def test_minimal_context_answers_model_question_directly(monkeypatch):
    monkeypatch.setenv("OUROBOROS_MINIMAL_CONTEXT", "true")
    monkeypatch.setenv("OUROBOROS_MODEL_FALLBACK", "openai-compatible::groq/compound")
    monkeypatch.setenv("OUROBOROS_MODEL_RESERVE", "openrouter::reserve/model")

    answer = loop_mod._maybe_answer_model_question_direct(
        [{"role": "user", "content": "[Message from my human]: какая ты модель?"}],
        "openai-compatible::gemma4:31b-cloud",
    )

    assert "Я Jormung" in answer
    assert "внутренней конфигурацией runtime" in answer
    assert "openai-compatible::gemma4:31b-cloud" not in answer
    assert "openai-compatible::groq/compound" not in answer
    assert "openrouter::reserve/model" not in answer
    assert "ext_" not in answer


def test_minimal_context_answers_capabilities_question_directly(monkeypatch):
    monkeypatch.setenv("OUROBOROS_MINIMAL_CONTEXT", "true")

    answer = loop_mod._maybe_answer_capabilities_question_direct(
        [{"role": "user", "content": "[Message from my human]: Какие у тебя есть функции?"}],
    )

    assert "искать информацию в интернете" in answer
    assert "исследовательский дайджест" in answer
    assert "подготовь дайджест" in answer
    assert "ext_" not in answer
    assert "AI/ML" not in answer


def test_minimal_context_sanitizes_leaked_extension_tool_names(monkeypatch):
    monkeypatch.setenv("OUROBOROS_MINIMAL_CONTEXT", "true")

    text, _usage, trace = loop_mod._handle_text_response(
        "Используй ext_17_r_research_digest_prepare_digest для дайджеста.",
        {"reasoning_notes": [], "tool_calls": []},
        {},
    )

    assert "ext_" not in text
    assert "внутренний инструмент" in text
    assert "ext_" not in trace["reasoning_notes"][0]


def test_minimal_context_answers_simple_greeting_directly(monkeypatch):
    monkeypatch.setenv("OUROBOROS_MINIMAL_CONTEXT", "true")

    answer = loop_mod._maybe_answer_greeting_direct(
        [{"role": "user", "content": "[Message from my human]: привет"}],
    )

    assert answer == "Привет. Я на связи."
    assert "source tool" not in answer.lower()
    assert "дайджест" not in answer.lower()


def test_simple_greeting_detector_does_not_swallow_digest_request():
    assert loop_mod._looks_like_simple_greeting("[Message from my human]: привет")
    assert not loop_mod._looks_like_simple_greeting(
        "[Message from my human]: привет, подготовь дайджест"
    )


def test_reserve_model_alias_is_env_only_not_setting_key():
    from ouroboros.config import SETTINGS_DEFAULTS

    assert "OUROBOROS_MODEL_RESERVE" in SETTINGS_DEFAULTS
    assert "OUROBOROS_RESERVE_MODEL" not in SETTINGS_DEFAULTS


def test_minimal_context_keeps_research_digest_extension_tools(tmp_path, monkeypatch):
    from ouroboros import extension_loader
    from ouroboros.tools.registry import ToolRegistry

    monkeypatch.setenv("OUROBOROS_MINIMAL_CONTEXT_TOOLS", "research_digest")
    registry = ToolRegistry(repo_dir=tmp_path, drive_root=tmp_path)
    old_tools = dict(extension_loader._tools)
    old_is_live = extension_loader.is_extension_live
    try:
        with extension_loader._lock:
            extension_loader._tools.clear()
            extension_loader._tools["ext_research_refresh"] = {
                "name": "ext_research_refresh",
                "skill": "research_digest",
                "description": "Refresh research sources",
                "schema": {"type": "object", "properties": {}},
            }
            extension_loader._tools["ext_other_refresh"] = {
                "name": "ext_other_refresh",
                "skill": "other_skill",
                "description": "Other tool",
                "schema": {"type": "object", "properties": {}},
            }
        monkeypatch.setattr(extension_loader, "is_extension_live", lambda *_args, **_kwargs: True)

        schemas = loop_mod._minimal_context_tool_schemas(registry)
    finally:
        with extension_loader._lock:
            extension_loader._tools.clear()
            extension_loader._tools.update(old_tools)
        monkeypatch.setattr(extension_loader, "is_extension_live", old_is_live)

    names = [schema["function"]["name"] for schema in schemas or []]
    assert {
        "read_file",
        "list_files",
        "write_file",
        "edit_text",
        "search_code",
        "web_search",
        "browse_page",
        "browser_action",
        "vlm_query",
        "ext_research_refresh",
    } <= set(names)
    assert "ext_other_refresh" not in names


def test_minimal_context_keeps_duckduckgo_extension_tools(tmp_path, monkeypatch):
    from ouroboros import extension_loader
    from ouroboros.tools.registry import ToolRegistry

    monkeypatch.delenv("OUROBOROS_MINIMAL_CONTEXT_TOOLS", raising=False)
    registry = ToolRegistry(repo_dir=tmp_path, drive_root=tmp_path)
    old_tools = dict(extension_loader._tools)
    old_is_live = extension_loader.is_extension_live
    try:
        with extension_loader._lock:
            extension_loader._tools.clear()
            extension_loader._tools["ext_duck_search"] = {
                "name": "ext_duck_search",
                "skill": "duckduckgo",
                "description": "Search the web",
                "schema": {"type": "object", "properties": {}},
            }
        monkeypatch.setattr(extension_loader, "is_extension_live", lambda *_args, **_kwargs: True)

        schemas = loop_mod._minimal_context_tool_schemas(registry)
    finally:
        with extension_loader._lock:
            extension_loader._tools.clear()
            extension_loader._tools.update(old_tools)
        monkeypatch.setattr(extension_loader, "is_extension_live", old_is_live)

    names = [schema["function"]["name"] for schema in schemas or []]
    assert "ext_duck_search" in names


def test_minimal_context_runs_research_digest_direct_route(monkeypatch):
    monkeypatch.setenv("OUROBOROS_MINIMAL_CONTEXT", "true")
    tool_name = "ext_17_r_research_digest_prepare_digest"
    calls = []
    progress = []

    class FakeTools:
        def execute(self, name, args):
            calls.append((name, args))
            return json.dumps({
                "ok": True,
                "final_response_mode": "direct",
                "final_response": "# AI/ML research digest\n\n1. Item",
            })

    def fake_get_tool(name):
        if name == tool_name:
            return {"name": name, "skill": "research_digest"}
        return None

    monkeypatch.setattr("ouroboros.extension_loader.get_tool", fake_get_tool)
    trace = {"reasoning_notes": [], "tool_calls": []}

    result = loop_mod._maybe_run_research_digest_direct(
        messages=[{"role": "user", "content": "[Message from my human]: подготовь дайджест"}],
        tools_registry=FakeTools(),
        tool_schemas=[{"type": "function", "function": {"name": tool_name}}],
        llm_trace=trace,
        emit_progress=progress.append,
    )

    assert result.startswith("# AI/ML research digest")
    assert calls == [(tool_name, {"hours": 168, "limit": 6, "min_score": 1, "refresh": True, "limit_per_source": 30})]
    assert progress == ["Preparing research digest..."]
    assert trace["tool_calls"][0]["tool"] == tool_name


def test_minimal_context_runs_duckduckgo_direct_route(monkeypatch):
    monkeypatch.setenv("OUROBOROS_MINIMAL_CONTEXT", "true")
    tool_name = "ext_12_r_duckduckgo_search"
    calls = []
    progress = []

    class FakeTools:
        _ctx = SimpleNamespace(messages=[])

        def execute(self, name, args):
            calls.append((name, args))
            return json.dumps({
                "query": args["query"],
                "results": [
                    {
                        "title": "Machine learning article",
                        "url": "https://example.com/ml",
                        "snippet": "A useful ML overview.",
                    }
                ],
                "count": 1,
            })

    def fake_get_tool(name):
        if name == tool_name:
            return {"name": name, "skill": "duckduckgo"}
        return None

    monkeypatch.setattr("ouroboros.extension_loader.get_tool", fake_get_tool)
    trace = {"reasoning_notes": [], "tool_calls": []}

    result = loop_mod._maybe_run_duckduckgo_direct(
        messages=[{"role": "user", "content": "[Message from my human]: Пришли топ статью по ML из поисковика"}],
        tools_registry=FakeTools(),
        tool_schemas=[{"type": "function", "function": {"name": tool_name}}],
        llm_trace=trace,
        emit_progress=progress.append,
    )

    assert "Machine learning article" in result
    assert "https://example.com/ml" in result
    assert calls[0][0] == tool_name
    assert "ml" in calls[0][1]["query"]
    assert progress == ["Searching DuckDuckGo..."]
    assert trace["tool_calls"][0]["status"] == "direct_intent_route"


def test_web_search_query_extraction_is_domain_agnostic():
    query = loop_mod._web_search_query_from_request("Пришли топ статью по нефти из поисковика")

    assert "нефти" in query
    assert "machine learning" not in query
    assert "из поисковика" not in query


def test_article_summary_direct_route_combines_recent_telegram_messages():
    calls = []
    progress = []
    messages = [
        {"role": "user", "content": "расскажи суть статьи и ключевые инженерные решения"},
        {
            "role": "user",
            "content": (
                "https://dzen.ru/a/example?share_to=telegram\n"
                "Экзоскелеты, строительный кран на джойстиках и ИИ"
            ),
        },
    ]

    class FakeTools:
        def execute(self, name, args):
            calls.append((name, args))
            return (
                "# Стройка будущего\n\nНа площадке применяют промышленные экзоскелеты, "
                "дистанционное управление краном и компьютерное зрение для контроля безопасности. "
                "Оператор управляет оборудованием из защищённой кабины."
            )

    trace = {"reasoning_notes": [], "tool_calls": []}
    enriched = loop_mod._maybe_enrich_article_summary_request(
        messages=messages,
        tools_registry=FakeTools(),
        llm_trace=trace,
        emit_progress=progress.append,
        attempted_urls=set(),
    )

    assert enriched is True
    assert calls == [(
        "browse_page",
        {"url": "https://dzen.ru/a/example?share_to=telegram", "output": "markdown", "timeout": 45_000},
    )]
    assert progress == ["Extracting article content..."]
    assert "[EXTERNAL_ARTICLE_CONTENT]" in messages[-1]["content"]
    assert "untrusted source material" in messages[-1]["content"]
    assert trace["tool_calls"][0]["status"] == "direct_article_extract"


def test_article_summary_direct_route_records_concrete_browser_failure():
    messages = [
        {"role": "user", "content": "Сделай summary https://example.com/article"},
    ]

    class FakeTools:
        def execute(self, _name, _args):
            return "⚠️ TOOL_ERROR (browse_page): navigation timeout"

    trace = {"reasoning_notes": [], "tool_calls": []}
    enriched = loop_mod._maybe_enrich_article_summary_request(
        messages=messages,
        tools_registry=FakeTools(),
        llm_trace=trace,
        emit_progress=lambda _text: None,
        attempted_urls=set(),
    )

    assert enriched is False
    assert "[ARTICLE_EXTRACTION_FAILED]" in messages[-1]["content"]
    assert "navigation timeout" in messages[-1]["content"]
    assert trace["tool_calls"][0]["is_error"] is True


def test_post_broadcast_scheduled_route_runs_prepare_vision_send(monkeypatch):
    tool_names = {
        "ext_pb_prepare_next": {"skill": "post_broadcast"},
        "ext_pb_send_prepared": {"skill": "post_broadcast"},
        "ext_pb_skip_prepared": {"skill": "post_broadcast"},
    }
    monkeypatch.setattr(
        "ouroboros.extension_loader.get_tool",
        lambda name: tool_names.get(name),
    )
    calls = []

    class FakeTools:
        _ctx = SimpleNamespace()

        def execute(self, name, args):
            calls.append((name, args))
            if name.endswith("_prepare_next"):
                return json.dumps({
                    "ok": True,
                    "has_prepared": True,
                    "prepared": {
                        "prepared_id": "cat-1",
                        "image_url": "https://example.com/cat.jpg",
                        "caption_generation": {
                            "vision_prompt": "Classify and describe",
                            "vision_model": "groq::vision",
                            "min_chars": 80,
                        },
                    },
                })
            if name == "vlm_query":
                return (
                    "SUBJECT: CAT\n"
                    "Подпись: Рыжий кот внимательно смотрит на кружку и явно оценивает, "
                    "достаточно ли серьёзно человек относится к утреннему кофе и кошачьему распорядку."
                )
            if name.endswith("_send_prepared"):
                return json.dumps({"ok": True, "sent_chats": 4})
            raise AssertionError(name)

    schemas = [
        {"type": "function", "function": {"name": name}}
        for name in tool_names
    ]
    trace = {"reasoning_notes": [], "tool_calls": []}
    result = loop_mod._maybe_run_post_broadcast_direct(
        messages=[{
            "role": "user",
            "content": "Run reviewed scheduled skill task `post_broadcast/cat_meme_post_broadcast`.",
        }],
        tools_registry=FakeTools(),
        tool_schemas=schemas,
        llm_trace=trace,
        emit_progress=lambda _text: None,
    )

    assert result == "post_broadcast sent prepared post cat-1 to 4 subscriber(s)."
    assert [name for name, _args in calls] == [
        "ext_pb_prepare_next",
        "vlm_query",
        "ext_pb_send_prepared",
    ]
    assert len(trace["tool_calls"]) == 3


def test_post_broadcast_scheduled_route_skips_non_cat(monkeypatch):
    tool_names = {
        "ext_pb_prepare_next": {"skill": "post_broadcast"},
        "ext_pb_send_prepared": {"skill": "post_broadcast"},
        "ext_pb_skip_prepared": {"skill": "post_broadcast"},
    }
    monkeypatch.setattr("ouroboros.extension_loader.get_tool", lambda name: tool_names.get(name))

    class FakeTools:
        _ctx = SimpleNamespace()

        def execute(self, name, _args):
            if name.endswith("_prepare_next"):
                return json.dumps({
                    "ok": True,
                    "has_prepared": True,
                    "prepared": {
                        "prepared_id": "dog-1",
                        "image_url": "https://example.com/dog.jpg",
                        "caption_generation": {"vision_prompt": "Classify", "vision_model": "groq::vision"},
                    },
                })
            if name == "vlm_query":
                return "SUBJECT: NOT_CAT\nНа изображении собака."
            if name.endswith("_skip_prepared"):
                return json.dumps({"ok": True, "skipped_id": "dog-1"})
            raise AssertionError(name)

    result = loop_mod._maybe_run_post_broadcast_direct(
        messages=[{"role": "user", "content": "post_broadcast/cat_meme_post_broadcast"}],
        tools_registry=FakeTools(),
        tool_schemas=[{"type": "function", "function": {"name": name}} for name in tool_names],
        llm_trace={"reasoning_notes": [], "tool_calls": []},
        emit_progress=lambda _text: None,
    )

    assert result == "post_broadcast skipped non-cat image dog-1."


def test_research_digest_direct_route_requires_request_intent(monkeypatch):
    monkeypatch.setenv("OUROBOROS_MINIMAL_CONTEXT", "true")

    result = loop_mod._maybe_run_research_digest_direct(
        messages=[{"role": "user", "content": "где хранится код research digest?"}],
        tools_registry=SimpleNamespace(execute=lambda *_a, **_k: "should not run"),
        tool_schemas=[{"type": "function", "function": {"name": "ext_17_r_research_digest_prepare_digest"}}],
        llm_trace={"reasoning_notes": [], "tool_calls": []},
        emit_progress=lambda _text: None,
    )

    assert result == ""


def test_trusted_research_digest_direct_dispatch_skips_llm_safety(monkeypatch):
    from ouroboros.tools.extension_dispatch import dispatch_extension_tool

    def fail_safety(*_args, **_kwargs):
        raise AssertionError("safety LLM check should not run for trusted prepare_digest")

    monkeypatch.setattr("ouroboros.safety.check_safety", fail_safety)
    monkeypatch.setattr("ouroboros.extension_loader.is_extension_live", lambda *_args, **_kwargs: True)

    ctx = SimpleNamespace(
        _trusted_direct_extension_tool="ext_17_r_research_digest_prepare_digest",
        task_metadata={},
        drive_root="/tmp/drive",
        messages=[{"role": "user", "content": "подготовь дайджест"}],
    )
    ext_tool = {
        "skill": "research_digest",
        "handler": lambda _ctx, **_kwargs: json.dumps({
            "ok": True,
            "final_response_mode": "direct",
            "final_response": "digest ready",
        }),
    }

    result = dispatch_extension_tool(
        ctx,
        "ext_17_r_research_digest_prepare_digest",
        ext_tool,
        {"hours": 168, "limit": 6},
    )

    assert json.loads(result)["final_response"] == "digest ready"


def test_maybe_inject_self_check_handles_assistant_none_content():
    messages = [
        {"role": "user", "content": "inspect"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": "call-1",
                "type": "function",
                "function": {"name": "read_file", "arguments": "{}"},
            }],
        },
        {"role": "tool", "tool_call_id": "call-1", "content": "done"},
    ]
    progress = []

    injected = _maybe_inject_self_check(
        15,
        30,
        messages,
        {"cost": 0.0},
        progress.append,
    )

    assert injected is True
    assert messages[-1]["role"] == "user"
    assert "[CHECKPOINT 1" in messages[-1]["content"]
    assert progress


def test_task_acceptance_auto_is_llm_first_not_host_enforced(monkeypatch):
    trace = {
        "tool_calls": [
            {"tool": "write_file", "args": {"path": "x.py"}},
            {"tool": "run_command", "args": {"cmd": ["pytest"]}},
        ]
    }

    # auto stays LLM-first (host never enforces), regardless of effects.
    assert _task_acceptance_eligible("auto", trace, True)[0] is False
    # required is effect-gated: this trace has a workspace write -> eligible.
    assert _task_acceptance_eligible("required", trace, True)[0] is True
    assert _task_acceptance_eligible("off", trace, True)[0] is False

    monkeypatch.setattr(loop_mod, "get_task_review_mode", lambda: "required")
    ctx = SimpleNamespace(_task_acceptance_reviewed=False, is_direct_chat=False, drive_root="/tmp")
    reviewed_trace = {
        "tool_calls": [{"tool": "task_acceptance_review", "args": {}}],
        "review_runs": [{"request": {"surface": "task_acceptance"}, "aggregate_signal": "PASS"}],
    }
    assert _run_task_acceptance_review_once(
        tools=SimpleNamespace(_ctx=ctx),
        content="done",
        task_id="task1",
        task_type="task",
        llm_trace=reviewed_trace,
        drive_root=None,
        messages=[{"role": "system", "content": ""}, {"role": "user", "content": "goal"}],
        emit_progress=lambda _msg: None,
    ) is False
    assert ctx._task_acceptance_reviewed is True
    assert reviewed_trace["review_decision"]["trigger"] == "agent_called_tool_result"


# ---------------------------------------------------------------------------
# Skill finalization gate (self-authored skills must reach ready+enabled
# before the loop accepts a final text response)
# ---------------------------------------------------------------------------


def _write_self_authored_skill(drive_root, name: str = "alpha"):
    skill_dir = drive_root / "skills" / "external" / name
    state_dir = drive_root / "state" / "skills" / name
    skill_dir.mkdir(parents=True)
    state_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: alpha\ntype: instruction\nversion: 0.1.0\n---\nbody\n",
        encoding="utf-8",
    )
    marker = {
        "schema_version": 1,
        "origin": "self_authored",
        "task_id": "task-1",
        "created_at": "2026-05-07T00:00:00+00:00",
    }
    (skill_dir / ".self_authored.json").write_text(json.dumps(marker), encoding="utf-8")
    (state_dir / "self_authored.json").write_text(json.dumps(marker), encoding="utf-8")
    return skill_dir


def test_skill_names_touched_by_trace_detects_data_skill_edits():
    trace = {
        "tool_calls": [
            {"tool": "write_file", "args": {"path": "skills/external/alpha/plugin.py"}},
            {"tool": "edit_text", "args": {"path": "data/skills/external/beta/SKILL.md"}},
            {"tool": "claude_code_edit", "args": {"cwd": "skills/external/gamma"}},
            {"tool": "write_file", "args": {"path": "SKILL.md", "bucket": "external", "skill_name": "delta"}},
        ]
    }

    assert _skill_names_touched_by_trace(trace) == ["alpha", "beta", "gamma", "delta"]


def test_skill_finalization_message_blocks_unreviewed_self_authored_skill(tmp_path):
    drive_root = tmp_path / "drive"
    drive_root.mkdir()
    _write_self_authored_skill(drive_root)
    trace = {"tool_calls": [{"tool": "write_file", "args": {"path": "skills/external/alpha/SKILL.md"}}]}

    message = _skill_finalization_message(drive_root, trace)

    assert "SKILL_NOT_FINALIZED" in message
    assert "alpha" in message


def test_skill_finalization_message_allows_ready_self_authored_skill(tmp_path):
    drive_root = tmp_path / "drive"
    drive_root.mkdir()
    skill_dir = _write_self_authored_skill(drive_root)
    content_hash = compute_content_hash(skill_dir)
    save_review_state(drive_root, "alpha", SkillReviewState(status="pass", content_hash=content_hash))
    save_enabled(drive_root, "alpha", True)
    trace = {"tool_calls": [{"tool": "write_file", "args": {"path": "skills/external/alpha/SKILL.md"}}]}

    assert _skill_finalization_message(drive_root, trace) == ""


def test_run_llm_loop_preserves_assistant_tool_call_metadata(tmp_path, monkeypatch):
    from ouroboros.tools.registry import ToolRegistry

    messages = [{"role": "user", "content": "inspect"}]
    assistant_metadata = {
        "role": "assistant",
        "content": None,
        "tool_calls": [{
            "id": "call-1",
            "type": "function",
            "function": {"name": "read_file", "arguments": "{}"},
        }],
        "reasoning": "I need the file first.",
        "reasoning_details": [{"type": "reasoning.text", "text": "I need the file first."}],
        "response_id": "gen-123",
        "finish_reason": "tool_calls",
    }
    seen_second_request = {}
    calls = {"count": 0}

    class FakeLLM:
        def default_model(self):
            return "test-model"

    def fake_call_llm_with_retry(_llm, request_messages, *_args, **_kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            return dict(assistant_metadata), 0.0
        seen_second_request["messages"] = [dict(item) for item in request_messages]
        return {"role": "assistant", "content": "done"}, 0.0

    def fake_handle_tool_calls(tool_calls, _tools, _drive_logs, _task_id, _executor, request_messages, _trace, _progress):
        request_messages.append({"role": "tool", "tool_call_id": tool_calls[0]["id"], "content": "file"})
        return 0

    monkeypatch.setattr(loop_mod, "call_llm_with_retry", fake_call_llm_with_retry)
    monkeypatch.setattr(loop_mod, "handle_tool_calls", fake_handle_tool_calls)

    result, _usage, _trace = run_llm_loop(
        messages=messages,
        tools=ToolRegistry(repo_dir=tmp_path, drive_root=tmp_path),
        llm=FakeLLM(),
        drive_logs=tmp_path,
        emit_progress=lambda _text: None,
        incoming_messages=queue.Queue(),
        task_id="roundtrip",
        drive_root=tmp_path,
    )

    assert result == "done"
    assistant_msg = next(item for item in seen_second_request["messages"] if item.get("response_id") == "gen-123")
    assert assistant_msg["tool_calls"] == assistant_metadata["tool_calls"]
    assert assistant_msg["reasoning"] == assistant_metadata["reasoning"]
    assert assistant_msg["reasoning_details"] == assistant_metadata["reasoning_details"]
    assert assistant_msg["response_id"] == "gen-123"
    assert "finish_reason" not in assistant_msg


def test_run_llm_loop_keeps_task_model_override_across_tool_rounds(tmp_path, monkeypatch):
    from ouroboros.tools.registry import ToolRegistry

    messages = [{"role": "user", "content": "inspect"}]
    seen_models: list[str] = []
    seen_use_local: list[bool] = []
    calls = {"count": 0}

    class FakeLLM:
        def default_model(self):
            return "default-model"

    def fake_call_llm_with_retry(_llm, request_messages, model, *_args, **kwargs):
        seen_models.append(model)
        seen_use_local.append(bool(kwargs.get("use_local")))
        calls["count"] += 1
        if calls["count"] == 1:
            return {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": "{}"},
                }],
            }, 0.0
        return {"role": "assistant", "content": "done"}, 0.0

    def fake_handle_tool_calls(tool_calls, _tools, _drive_logs, _task_id, _executor, request_messages, _trace, _progress):
        request_messages.append({"role": "tool", "tool_call_id": tool_calls[0]["id"], "content": "file"})
        return 0

    registry = ToolRegistry(repo_dir=tmp_path, drive_root=tmp_path)
    registry._ctx.task_model_override = "subagent-light"
    registry._ctx.task_use_local_override = True
    monkeypatch.setattr(loop_mod, "call_llm_with_retry", fake_call_llm_with_retry)
    monkeypatch.setattr(loop_mod, "handle_tool_calls", fake_handle_tool_calls)

    result, _usage, _trace = run_llm_loop(
        messages=messages,
        tools=registry,
        llm=FakeLLM(),
        drive_logs=tmp_path,
        emit_progress=lambda _text: None,
        incoming_messages=queue.Queue(),
        task_id="subagent1",
        drive_root=tmp_path,
    )

    assert result == "done"
    assert seen_models == ["subagent-light", "subagent-light"]
    assert seen_use_local == [True, True]


def test_run_llm_loop_enforces_consilium_force_plan_before_final(tmp_path, monkeypatch):
    from ouroboros.tools.registry import ToolRegistry

    messages = [{"role": "user", "content": "ship"}]
    calls = {"count": 0}
    seen_second_request = {}

    class FakeLLM:
        def default_model(self):
            return "test-model"

    def fake_call_llm_with_retry(_llm, request_messages, *_args, **_kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            return {"role": "assistant", "content": "premature final"}, 0.0
        if calls["count"] == 2:
            seen_second_request["messages"] = [dict(item) for item in request_messages]
            return {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": "call-plan",
                    "type": "function",
                    "function": {"name": "plan_task", "arguments": "{}"},
                }],
            }, 0.0
        return {"role": "assistant", "content": "done after plan"}, 0.0

    def fake_handle_tool_calls(tool_calls, _tools, _drive_logs, _task_id, _executor, request_messages, trace, _progress):
        trace["tool_calls"].append({
            "tool": tool_calls[0]["function"]["name"],
            "args": {},
            "result": "## Plan Review Results\n\nAGGREGATE: GREEN",
            "is_error": False,
        })
        request_messages.append({"role": "tool", "tool_call_id": tool_calls[0]["id"], "content": "## Plan Review Results\n\nAGGREGATE: GREEN"})
        return 0

    registry = ToolRegistry(repo_dir=tmp_path, drive_root=tmp_path)
    registry._ctx.task_metadata = {"force_plan": True, "force_plan_source": "consilium"}
    monkeypatch.setattr(loop_mod, "call_llm_with_retry", fake_call_llm_with_retry)
    monkeypatch.setattr(loop_mod, "handle_tool_calls", fake_handle_tool_calls)

    result, _usage, trace = run_llm_loop(
        messages=messages,
        tools=registry,
        llm=FakeLLM(),
        drive_logs=tmp_path,
        emit_progress=lambda _text: None,
        incoming_messages=queue.Queue(),
        task_id="task1",
        drive_root=tmp_path,
    )

    assert result == "done after plan"
    assert calls["count"] == 3
    assert any("plan_task is required" in str(item.get("content") or "") for item in seen_second_request["messages"])
    assert trace["tool_calls"][0]["tool"] == "plan_task"


def test_run_llm_loop_does_not_accept_failed_plan_task_for_consilium_force_plan(tmp_path, monkeypatch):
    from ouroboros.tools.registry import ToolRegistry

    messages = [{"role": "user", "content": "ship"}]
    calls = {"count": 0}

    class FakeLLM:
        def default_model(self):
            return "test-model"

    def fake_call_llm_with_retry(_llm, _request_messages, *_args, **_kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            return {"role": "assistant", "content": "premature final"}, 0.0
        if calls["count"] == 2:
            return {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": "call-plan",
                    "type": "function",
                    "function": {"name": "plan_task", "arguments": "{}"},
                }],
            }, 0.0
        return {"role": "assistant", "content": "still finalizing without a valid plan"}, 0.0

    def fake_handle_tool_calls(tool_calls, _tools, _drive_logs, _task_id, _executor, request_messages, trace, _progress):
        trace["tool_calls"].append({
            "tool": tool_calls[0]["function"]["name"],
            "args": {},
            "result": "ERROR: plan_task planning swarm failed closed: no planning subagent completed.",
            "is_error": False,
        })
        request_messages.append({"role": "tool", "tool_call_id": tool_calls[0]["id"], "content": "ERROR: plan_task planning swarm failed closed."})
        return 0

    registry = ToolRegistry(repo_dir=tmp_path, drive_root=tmp_path)
    registry._ctx.task_metadata = {"force_plan": True, "force_plan_source": "consilium"}
    monkeypatch.setattr(loop_mod, "call_llm_with_retry", fake_call_llm_with_retry)
    monkeypatch.setattr(loop_mod, "handle_tool_calls", fake_handle_tool_calls)

    result, usage, trace = run_llm_loop(
        messages=messages,
        tools=registry,
        llm=FakeLLM(),
        drive_logs=tmp_path,
        emit_progress=lambda _text: None,
        incoming_messages=queue.Queue(),
        task_id="task1",
        drive_root=tmp_path,
    )

    assert result.startswith("⚠️ CONSILIUM_FORCE_PLAN_BLOCKED")
    assert calls["count"] == 4
    assert usage["reason_code"] == "consilium_force_plan_not_called"
    assert trace["tool_calls"][0]["tool"] == "plan_task"


def test_run_llm_loop_injects_subagent_handoff_before_final_text(tmp_path, monkeypatch):
    from ouroboros.task_results import STATUS_COMPLETED, write_task_result
    from ouroboros.tools.registry import ToolRegistry

    write_task_result(
        tmp_path,
        "child1",
        STATUS_COMPLETED,
        parent_task_id="parent1",
        root_task_id="parent1",
        delegation_role="subagent",
        role="reviewer",
        result="child handoff",
    )
    messages = [{"role": "user", "content": "inspect"}]
    calls = {"count": 0}
    seen_second_request = {}
    progress = []

    class FakeLLM:
        def default_model(self):
            return "test-model"

    def fake_call_llm_with_retry(_llm, request_messages, *_args, **_kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            return {"role": "assistant", "content": "premature final"}, 0.0
        seen_second_request["messages"] = [dict(item) for item in request_messages]
        return {"role": "assistant", "content": "final after handoff"}, 0.0

    monkeypatch.setattr(loop_mod, "call_llm_with_retry", fake_call_llm_with_retry)

    result, _usage, trace = run_llm_loop(
        messages=messages,
        tools=ToolRegistry(repo_dir=tmp_path, drive_root=tmp_path),
        llm=FakeLLM(),
        drive_logs=tmp_path,
        emit_progress=progress.append,
        incoming_messages=queue.Queue(),
        task_id="parent1",
        drive_root=tmp_path,
    )

    assert result == "final after handoff"
    assert calls["count"] == 2
    assert any("Subagent handoff status refreshed" in item for item in progress)
    assert any("Subagent handoff status refreshed" in item for item in trace["reasoning_notes"])
    second_text = "\n".join(str(item.get("content") or "") for item in seen_second_request["messages"])
    assert "[SUBAGENT_HANDOFF_STATUS]" in second_text
    assert "result_available" in second_text
    assert "child handoff" in second_text
    assert "Use get_task_result" in second_text


def test_run_llm_loop_reinjects_incomplete_subagent_handoff_until_final_acknowledges_status(tmp_path, monkeypatch):
    from ouroboros.task_results import STATUS_RUNNING, write_task_result
    from ouroboros.tools.registry import ToolRegistry

    write_task_result(
        tmp_path,
        "child1",
        STATUS_RUNNING,
        parent_task_id="parent1",
        root_task_id="parent1",
        delegation_role="subagent",
        role="reviewer",
        result="still collecting evidence",
    )
    messages = [{"role": "user", "content": "inspect"}]
    calls = {"count": 0}
    progress = []

    class FakeLLM:
        def default_model(self):
            return "test-model"

    def fake_call_llm_with_retry(_llm, _request_messages, *_args, **_kwargs):
        calls["count"] += 1
        if calls["count"] <= 2:
            return {"role": "assistant", "content": "done"}, 0.0
        return {"role": "assistant", "content": "child1 is still running and incomplete; final answer will wait."}, 0.0

    monkeypatch.setattr(loop_mod, "call_llm_with_retry", fake_call_llm_with_retry)

    result, _usage, trace = run_llm_loop(
        messages=messages,
        tools=ToolRegistry(repo_dir=tmp_path, drive_root=tmp_path),
        llm=FakeLLM(),
        drive_logs=tmp_path,
        emit_progress=progress.append,
        incoming_messages=queue.Queue(),
        task_id="parent1",
        drive_root=tmp_path,
    )

    assert result == "child1 is still running and incomplete; final answer will wait."
    assert calls["count"] == 3
    assert sum(1 for item in progress if "Subagent handoff status refreshed" in item) == 2
    assert sum(1 for item in trace["reasoning_notes"] if "Subagent handoff status refreshed" in item) == 2


def test_run_llm_loop_does_not_include_current_subagent_in_own_handoff(tmp_path, monkeypatch):
    from ouroboros.task_results import STATUS_RUNNING, write_task_result
    from ouroboros.tools.registry import ToolRegistry

    write_task_result(
        tmp_path,
        "child1",
        STATUS_RUNNING,
        parent_task_id="parent1",
        root_task_id="parent1",
        delegation_role="subagent",
        role="reviewer",
        result="my own running mirror",
    )
    messages = [{"role": "user", "content": "inspect"}]
    calls = {"count": 0}
    progress = []
    tools = ToolRegistry(repo_dir=tmp_path, drive_root=tmp_path)
    tools._ctx.task_metadata = {
        "parent_task_id": "parent1",
        "root_task_id": "parent1",
        "delegation_role": "subagent",
    }

    class FakeLLM:
        def default_model(self):
            return "test-model"

    def fake_call_llm_with_retry(_llm, _request_messages, *_args, **_kwargs):
        calls["count"] += 1
        return {"role": "assistant", "content": "subagent final"}, 0.0

    monkeypatch.setattr(loop_mod, "call_llm_with_retry", fake_call_llm_with_retry)

    result, _usage, trace = run_llm_loop(
        messages=messages,
        tools=tools,
        llm=FakeLLM(),
        drive_logs=tmp_path,
        emit_progress=progress.append,
        incoming_messages=queue.Queue(),
        task_id="child1",
        drive_root=tmp_path,
    )

    assert result == "subagent final"
    assert calls["count"] == 1
    assert not any("Subagent handoff status refreshed" in item for item in progress)
    assert not any("Subagent handoff status refreshed" in item for item in trace["reasoning_notes"])
