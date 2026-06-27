import os

import pytest
import ouroboros.pricing as pricing_module
from unittest.mock import patch
from ouroboros.llm import LLMClient


def test_resolve_openai_target(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")

    target = LLMClient()._resolve_remote_target("openai::gpt-4.1")

    assert target["provider"] == "openai"
    assert target["resolved_model"] == "gpt-4.1"
    assert target["usage_model"] == "openai/gpt-4.1"
    assert target["base_url"] == "https://api.openai.com/v1"


def test_build_remote_kwargs_uses_max_completion_tokens_for_openai_gpt5(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")

    client = LLMClient()
    target = client._resolve_remote_target("openai::gpt-5.2")
    kwargs = client._build_remote_kwargs(
        target,
        [{"role": "user", "content": "hi"}],
        "high",
        512,
        "auto",
        None,
        None,
    )

    assert kwargs["max_completion_tokens"] == 512
    assert "max_tokens" not in kwargs


def test_build_remote_kwargs_keeps_max_tokens_for_openai_gpt41(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")

    client = LLMClient()
    target = client._resolve_remote_target("openai::gpt-4.1")
    kwargs = client._build_remote_kwargs(
        target,
        [{"role": "user", "content": "hi"}],
        "high",
        512,
        "auto",
        None,
        None,
    )

    assert kwargs["max_tokens"] == 512
    assert "max_completion_tokens" not in kwargs


def test_normalize_remote_response_preserves_choice_finish_reason(monkeypatch):
    monkeypatch.setenv("OPENAI_COMPATIBLE_BASE_URL", "http://localhost:11434/v1")
    client = LLMClient()
    target = client._resolve_remote_target("openai-compatible::qwen3:30b-a3b")

    msg, usage = client._normalize_remote_response(
        {
            "id": "chatcmpl-test",
            "choices": [
                {
                    "message": {"role": "assistant", "content": ""},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 0,
            },
        },
        target,
    )

    assert msg["finish_reason"] == "stop"
    assert usage["provider"] == "openai-compatible"


def test_build_remote_kwargs_strips_assistant_finish_reason_for_strict_providers(monkeypatch):
    monkeypatch.setenv("OPENAI_COMPATIBLE_API_KEY", "groq-key")
    monkeypatch.setenv("OPENAI_COMPATIBLE_BASE_URL", "https://api.groq.com/openai/v1")

    client = LLMClient()
    target = client._resolve_remote_target("openai-compatible::groq/compound")
    kwargs = client._build_remote_kwargs(
        target,
        [
            {"role": "user", "content": "lookup"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{"id": "call-1", "type": "function", "function": {"name": "lookup", "arguments": "{}"}}],
                "finish_reason": "tool_calls",
            },
            {"role": "tool", "tool_call_id": "call-1", "content": "ok"},
        ],
        "medium",
        128,
        "auto",
        None,
        None,
    )

    assistant_msg = next(item for item in kwargs["messages"] if item.get("role") == "assistant")
    assert "finish_reason" not in assistant_msg
    assert assistant_msg["tool_calls"][0]["id"] == "call-1"


def test_openai_compatible_ollama_caps_default_max_tokens(monkeypatch):
    monkeypatch.setenv("OPENAI_COMPATIBLE_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.delenv("OPENAI_COMPATIBLE_MAX_TOKENS", raising=False)

    client = LLMClient()
    target = client._resolve_remote_target("openai-compatible::qwen3-30b-a3b-16k")
    kwargs = client._build_remote_kwargs(
        target,
        [{"role": "user", "content": "hi"}],
        "medium",
        8192,
        "auto",
        None,
        None,
    )

    assert kwargs["max_tokens"] == 2048


def test_openai_compatible_max_tokens_env_override(monkeypatch):
    monkeypatch.setenv("OPENAI_COMPATIBLE_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("OPENAI_COMPATIBLE_MAX_TOKENS", "1024")

    client = LLMClient()
    target = client._resolve_remote_target("openai-compatible::qwen3-30b-a3b-16k")
    kwargs = client._build_remote_kwargs(
        target,
        [{"role": "user", "content": "hi"}],
        "medium",
        8192,
        "auto",
        None,
        None,
    )

    assert kwargs["max_tokens"] == 1024


def test_openai_compatible_max_tokens_setting_applies_to_env(monkeypatch):
    from ouroboros.config import apply_settings_to_env

    monkeypatch.delenv("OPENAI_COMPATIBLE_MAX_TOKENS", raising=False)
    monkeypatch.delenv("OPENAI_COMPATIBLE_CONTEXT_LENGTH", raising=False)

    try:
        apply_settings_to_env({
            "OPENAI_COMPATIBLE_MAX_TOKENS": "4096",
            "OPENAI_COMPATIBLE_CONTEXT_LENGTH": "131072",
        })

        assert os.environ["OPENAI_COMPATIBLE_MAX_TOKENS"] == "4096"
        assert os.environ["OPENAI_COMPATIBLE_CONTEXT_LENGTH"] == "131072"
    finally:
        os.environ.pop("OPENAI_COMPATIBLE_MAX_TOKENS", None)
        os.environ.pop("OPENAI_COMPATIBLE_CONTEXT_LENGTH", None)


def test_openai_compatible_max_tokens_zero_disables_cap(monkeypatch):
    monkeypatch.setenv("OPENAI_COMPATIBLE_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("OPENAI_COMPATIBLE_MAX_TOKENS", "0")

    client = LLMClient()
    target = client._resolve_remote_target("openai-compatible::qwen3-30b-a3b-16k")
    kwargs = client._build_remote_kwargs(
        target,
        [{"role": "user", "content": "hi"}],
        "medium",
        8192,
        "auto",
        None,
        None,
    )

    assert kwargs["max_tokens"] == 8192


def test_openai_compatible_remote_endpoint_keeps_max_tokens_by_default(monkeypatch):
    monkeypatch.setenv("OPENAI_COMPATIBLE_BASE_URL", "https://llm.example.test/v1")
    monkeypatch.delenv("OPENAI_COMPATIBLE_MAX_TOKENS", raising=False)

    client = LLMClient()
    target = client._resolve_remote_target("openai-compatible::custom-model")
    kwargs = client._build_remote_kwargs(
        target,
        [{"role": "user", "content": "hi"}],
        "medium",
        8192,
        "auto",
        None,
        None,
    )

    assert kwargs["max_tokens"] == 8192


def test_openai_compatible_ollama_compacts_prompt_for_context(monkeypatch):
    monkeypatch.setenv("OPENAI_COMPATIBLE_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("OPENAI_COMPATIBLE_CONTEXT_LENGTH", "4096")

    client = LLMClient()
    target = client._resolve_remote_target("openai-compatible::qwen3-30b-a3b-16k")
    long_system = "## BIBLE.md\ncore rules\n\n## Large Context\n" + ("x" * 40000)
    kwargs = client._build_remote_kwargs(
        target,
        [
            {"role": "system", "content": long_system},
            {"role": "user", "content": "hi"},
        ],
        "medium",
        1024,
        "auto",
        None,
        None,
    )

    assert "Compacted for local-model context" in kwargs["messages"][0]["content"]
    assert len(kwargs["messages"][0]["content"]) < len(long_system)


def test_openai_compatible_context_length_env_compacts_remote_endpoint(monkeypatch):
    monkeypatch.setenv("OPENAI_COMPATIBLE_BASE_URL", "https://llm.example.test/v1")
    monkeypatch.setenv("OPENAI_COMPATIBLE_CONTEXT_LENGTH", "4096")

    client = LLMClient()
    target = client._resolve_remote_target("openai-compatible::custom-model")
    long_system = "## BIBLE.md\ncore rules\n\n## Large Context\n" + ("x" * 40000)
    kwargs = client._build_remote_kwargs(
        target,
        [
            {"role": "system", "content": long_system},
            {"role": "user", "content": "hi"},
        ],
        "medium",
        1024,
        "auto",
        None,
        None,
    )

    assert "Compacted for local-model context" in kwargs["messages"][0]["content"]


def test_build_remote_kwargs_normalizes_tool_descriptions_for_openrouter():
    client = LLMClient()
    target = client._resolve_remote_target("anthropic/claude-sonnet-4.6")

    kwargs = client._build_remote_kwargs(
        target,
        [{"role": "user", "content": "hi"}],
        "high",
        512,
        "auto",
        None,
        [{
            "type": "function",
            "function": {
                "name": "bad_tool",
                "description": ("first half ", "second half"),
                "parameters": {"type": "object", "properties": {}},
            },
        }],
    )

    assert kwargs["tools"][0]["function"]["description"] == "first half second half"


def test_build_remote_kwargs_deduplicates_tool_names_for_openrouter():
    client = LLMClient()
    target = client._resolve_remote_target("anthropic/claude-sonnet-4.6")

    kwargs = client._build_remote_kwargs(
        target,
        [{"role": "user", "content": "hi"}],
        "high",
        512,
        "auto",
        None,
        [
            {
                "type": "function",
                "function": {
                    "name": "dup_tool",
                    "description": "first",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "dup_tool",
                    "description": "second",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
        ],
    )

    assert [tool["function"]["name"] for tool in kwargs["tools"]] == ["dup_tool"]
    assert kwargs["tools"][0]["function"]["description"] == "first"


def test_openrouter_reasoning_returned_by_default_and_env_disables(monkeypatch):
    client = LLMClient()
    monkeypatch.setattr(client, "_get_supported_parameters", lambda _model_id: None)
    target = client._resolve_remote_target("anthropic/claude-sonnet-4.6")

    monkeypatch.delenv("OUROBOROS_RETURN_REASONING", raising=False)
    default_kwargs = client._build_remote_kwargs(
        target,
        [{"role": "user", "content": "hi"}],
        "high",
        512,
        "auto",
        None,
        None,
    )

    assert default_kwargs["extra_body"]["reasoning"] == {"effort": "high", "exclude": False}

    monkeypatch.setenv("OUROBOROS_RETURN_REASONING", "false")
    disabled_kwargs = client._build_remote_kwargs(
        target,
        [{"role": "user", "content": "hi"}],
        "high",
        512,
        "auto",
        None,
        None,
    )

    assert disabled_kwargs["extra_body"]["reasoning"] == {"effort": "high", "exclude": True}

    monkeypatch.setenv("OUROBOROS_RETURN_REASONING", "")
    empty_disabled_kwargs = client._build_remote_kwargs(
        target,
        [{"role": "user", "content": "hi"}],
        "high",
        512,
        "auto",
        None,
        None,
    )

    assert empty_disabled_kwargs["extra_body"]["reasoning"] == {"effort": "high", "exclude": True}


def test_non_openrouter_payload_strips_reasoning_roundtrip_metadata(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    client = LLMClient()
    messages = [
        {"role": "user", "content": "inspect"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "call-1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}],
            "reasoning": "read first",
            "reasoning_details": [{"type": "reasoning.text", "text": "read first"}],
            "response_id": "gen-123",
        },
    ]

    kwargs = client._build_remote_kwargs(
        client._resolve_remote_target("openai::gpt-5.2"),
        messages,
        "medium",
        512,
        "auto",
        None,
        None,
    )

    assistant_msg = kwargs["messages"][1]
    assert "reasoning" not in assistant_msg
    assert "reasoning_details" not in assistant_msg
    assert "response_id" not in assistant_msg
    assert messages[1]["reasoning_details"] == [{"type": "reasoning.text", "text": "read first"}]


def test_openrouter_payload_keeps_reasoning_roundtrip_metadata(monkeypatch):
    client = LLMClient()
    monkeypatch.setattr(client, "_get_supported_parameters", lambda _model_id: None)
    messages = [
        {"role": "user", "content": "inspect"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "call-1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}],
            "reasoning": "read first",
            "reasoning_details": [{"type": "reasoning.text", "text": "read first"}],
            "response_id": "gen-123",
        },
    ]

    kwargs = client._build_remote_kwargs(
        client._resolve_remote_target("anthropic/claude-sonnet-4.6"),
        messages,
        "medium",
        512,
        "auto",
        None,
        None,
    )

    assistant_msg = kwargs["messages"][1]
    assert assistant_msg["reasoning"] == "read first"
    assert assistant_msg["reasoning_details"] == [{"type": "reasoning.text", "text": "read first"}]
    assert assistant_msg["response_id"] == "gen-123"


def test_system_message_placement_demotes_late_notices_preserving_tool_adjacency():
    client = LLMClient()
    messages = [
        {"role": "system", "content": "authoritative"},
        {"role": "user", "content": "start"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "call-1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}],
        },
        {"role": "system", "content": "late notice"},
        {"role": "tool", "tool_call_id": "call-1", "content": "result"},
        {"role": "user", "content": "continue"},
    ]

    normalized = client._normalize_system_message_placement(messages)

    assert [m["role"] for m in normalized] == ["system", "user", "assistant", "tool", "user", "user"]
    assert normalized[0]["content"] == "authoritative"
    assert normalized[3]["role"] == "tool"
    assert normalized[4]["content"].startswith("[SYSTEM NOTICE]\nlate notice")


def test_build_remote_kwargs_never_sends_non_leading_system_to_strict_providers(monkeypatch):
    monkeypatch.setenv("OPENAI_COMPATIBLE_BASE_URL", "https://compatible.example/v1")
    client = LLMClient()
    target = client._resolve_remote_target("openai-compatible::qwen-test")

    kwargs = client._build_remote_kwargs(
        target,
        [
            {"role": "system", "content": "root"},
            {"role": "user", "content": "start"},
            {"role": "system", "content": "late"},
        ],
        "medium",
        512,
        "auto",
        None,
        None,
    )

    assert [m["role"] for m in kwargs["messages"]] == ["system", "user", "user"]
    assert kwargs["messages"][2]["content"].startswith("[SYSTEM NOTICE]\nlate")


def test_openrouter_reasoning_details_disable_provider_fallbacks(monkeypatch):
    client = LLMClient()
    monkeypatch.setattr(client, "_get_supported_parameters", lambda _model_id: None)
    messages = [
        {"role": "user", "content": "inspect"},
        {
            "role": "assistant",
            "content": "thinking",
            "reasoning_details": [{"type": "reasoning.encrypted", "data": "sig"}],
        },
    ]

    kwargs = client._build_remote_kwargs(
        client._resolve_remote_target("google/gemini-3.5-flash"),
        messages,
        "medium",
        512,
        "auto",
        None,
        None,
    )

    assert kwargs["extra_body"]["provider"]["allow_fallbacks"] is False
    assert kwargs["messages"][1]["reasoning_details"] == [{"type": "reasoning.encrypted", "data": "sig"}]


def test_openrouter_signature_error_retries_once_with_reasoning_stripped(monkeypatch):
    client = LLMClient()
    target = client._resolve_remote_target("google/gemini-3.5-flash")
    kwargs = {
        "model": "google/gemini-3.5-flash",
        "extra_body": {"provider": {"allow_fallbacks": False}},
        "messages": [
            {"role": "user", "content": "inspect"},
            {
                "role": "assistant",
                "content": "thinking",
                "reasoning": "private",
                "reasoning_details": [{"type": "reasoning.encrypted", "data": "sig"}],
                "response_id": "gen-1",
            },
        ],
    }
    calls = []

    class _Resp:
        def model_dump(self):
            return {"choices": [{"message": {"content": "ok"}}], "usage": {}}

    def fake_create(**call_kwargs):
        calls.append(call_kwargs)
        if len(calls) == 1:
            raise RuntimeError("400 INVALID_ARGUMENT: Corrupted thought signature")
        return _Resp()

    resp = client._create_chat_completion_with_retries(fake_create, kwargs, target)

    assert resp.model_dump()["choices"][0]["message"]["content"] == "ok"
    assert len(calls) == 2
    retried_assistant = calls[1]["messages"][1]
    assert "reasoning" not in retried_assistant
    assert "reasoning_details" not in retried_assistant
    assert "response_id" not in retried_assistant
    assert "extra_body" not in calls[1]


def test_openai_compatible_tpm_error_retries_with_lower_completion_budget():
    from ouroboros.llm import LLMClient

    client = LLMClient()
    target = client._resolve_remote_target("openai-compatible::openai/gpt-oss-20b")
    kwargs = {
        "model": "openai/gpt-oss-20b",
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 256,
    }
    calls = []

    class _Resp:
        def model_dump(self):
            return {"choices": [{"message": {"content": "ok"}}], "usage": {}}

    def fake_create(**call_kwargs):
        calls.append(call_kwargs)
        if call_kwargs["max_tokens"] > 64:
            raise RuntimeError(
                "Error code: 413 - {'error': {'message': 'Request too large for model openai/gpt-oss-20b in organization org_x service tier on_demand on tokens per minute (TPM): Limit 800, Requested 900'}}"
            )
        return _Resp()

    resp = client._create_chat_completion_with_retries(fake_create, kwargs, target)

    assert resp.model_dump()["choices"][0]["message"]["content"] == "ok"
    assert [call["max_tokens"] for call in calls] == [256, 128, 64]


def test_openrouter_budget_error_retries_down_to_affordable_output():
    from ouroboros.llm import LLMClient

    client = LLMClient()
    target = client._resolve_remote_target("qwen/qwen3.6-flash")
    kwargs = {
        "model": "qwen/qwen3.6-flash",
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 8192,
    }
    calls = []

    class _Resp:
        def model_dump(self):
            return {"choices": [{"message": {"content": "ok"}}], "usage": {}}

    def fake_create(**call_kwargs):
        calls.append(call_kwargs)
        if call_kwargs["max_tokens"] > 512:
            raise RuntimeError(
                "Error code: 402 - This request requires more credits. "
                "You requested up to 8192 tokens, but can only afford 847."
            )
        return _Resp()

    resp = client._create_chat_completion_with_retries(fake_create, kwargs, target)

    assert resp.model_dump()["choices"][0]["message"]["content"] == "ok"
    assert [call["max_tokens"] for call in calls] == [8192, 4096, 2048, 1024, 512]


def test_openrouter_default_caps_main_loop_max_tokens(monkeypatch):
    from ouroboros.llm import LLMClient

    monkeypatch.delenv("OPENROUTER_MAX_TOKENS", raising=False)
    monkeypatch.delenv("OPENAI_COMPATIBLE_MAX_TOKENS", raising=False)
    client = LLMClient()

    kwargs = client._build_remote_kwargs(
        client._resolve_remote_target("qwen/qwen3.6-flash"),
        [{"role": "user", "content": "hi"}],
        reasoning_effort="medium",
        max_tokens=65536,
        tool_choice="auto",
        temperature=None,
        tools=None,
    )

    assert kwargs["max_tokens"] == 8192


def test_openrouter_max_tokens_env_override(monkeypatch):
    from ouroboros.llm import LLMClient

    monkeypatch.setenv("OPENROUTER_MAX_TOKENS", "4096")
    client = LLMClient()

    kwargs = client._build_remote_kwargs(
        client._resolve_remote_target("qwen/qwen3.6-flash"),
        [{"role": "user", "content": "hi"}],
        reasoning_effort="medium",
        max_tokens=65536,
        tool_choice="auto",
        temperature=None,
        tools=None,
    )

    assert kwargs["max_tokens"] == 4096


def test_openrouter_gemini_preserves_message_cache_blocks_and_strips_tool_cache(monkeypatch):
    client = LLMClient()
    monkeypatch.setattr(client, "_get_supported_parameters", lambda _model_id: None)
    messages = [
        {
            "role": "system",
            "content": [
                {"type": "text", "text": "stable", "cache_control": {"type": "ephemeral", "ttl": "1h"}},
                {"type": "text", "text": "dynamic"},
            ],
        },
        {"role": "user", "content": "hi"},
    ]
    tools = [{
        "type": "function",
        "function": {
            "name": "alpha_tool",
            "description": "a",
            "parameters": {"type": "object", "properties": {}},
        },
        "cache_control": {"type": "ephemeral", "ttl": "1h"},
    }]

    kwargs = client._build_remote_kwargs(
        client._resolve_remote_target("google/gemini-3.5-flash"),
        messages,
        "medium",
        512,
        "auto",
        None,
        tools,
    )

    assert isinstance(kwargs["messages"][0]["content"], list)
    assert kwargs["messages"][0]["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert "ttl" not in kwargs["messages"][0]["content"][0]["cache_control"]
    assert "cache_control" not in kwargs["tools"][0]
    assert messages[0]["content"][0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert tools[0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}

    messages = [
        {"role": "tool", "tool_call_id": "call-1", "content": [
            {"type": "text", "text": "cached result", "cache_control": {"type": "ephemeral"}},
        ]},
        {"role": "user", "content": [
            {"type": "text", "text": "hi", "cache_control": {"type": "ephemeral"}},
        ]},
    ]

    kwargs = client._build_remote_kwargs(
        client._resolve_remote_target("openai/gpt-4.1"),
        messages,
        "medium",
        512,
        "auto",
        None,
        [{
            "type": "function",
            "function": {"name": "alpha_tool", "description": "a", "parameters": {"type": "object"}},
            "cache_control": {"type": "ephemeral"},
        }],
    )

    assert kwargs["messages"][0]["content"] == "cached result"
    assert "cache_control" not in kwargs["messages"][1]["content"][0]
    assert "cache_control" not in kwargs["tools"][0]
    assert client._prompt_cache_ttl_from_payload(kwargs["messages"], kwargs["tools"]) is None
    assert "cache_control" in messages[0]["content"][0]
    assert "cache_control" in messages[1]["content"][0]

    kwargs = client._build_remote_kwargs(
        client._resolve_remote_target("openai/gpt-4.1"),
        [{"role": "user", "content": "hi"}],
        "medium",
        512,
        "auto",
        None,
        [{
            "type": "function",
            "function": {
                "name": "schema_tool",
                "description": "schema property collision",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "cache_control": {"type": "string"},
                    },
                },
            },
        }],
    )
    assert client._prompt_cache_ttl_from_payload(kwargs["messages"], kwargs["tools"]) is None


def test_build_anthropic_tools_deduplicates_tool_names():
    tools = [
        {
            "type": "function",
            "function": {
                "name": "dup_tool",
                "description": "first",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "dup_tool",
                "description": "second",
                "parameters": {"type": "object", "properties": {}},
            },
        },
    ]

    anthropic_tools = LLMClient._build_anthropic_tools(tools)

    assert anthropic_tools == [
        {
            "name": "dup_tool",
            "description": "first",
            "input_schema": {"type": "object", "properties": {}},
        }
    ]
    sorted_tools = [
        {"type": "function", "function": {"name": "zeta_tool", "description": "z", "parameters": {"type": "object"}}},
        {"type": "function", "function": {"name": "alpha_tool", "description": "a", "parameters": {"type": "object"}}},
    ]

    anthropic_tools = LLMClient._build_anthropic_tools(sorted_tools, cache_control=True)

    assert [tool["name"] for tool in anthropic_tools] == ["alpha_tool", "zeta_tool"]
    assert "cache_control" not in anthropic_tools[0]
    assert anthropic_tools[-1]["cache_control"] == {"type": "ephemeral"}


def test_chat_anthropic_sends_tool_cache_control_without_ttl(monkeypatch):
    from types import SimpleNamespace
    import requests

    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-key")
    captured = {}
    fake_response = SimpleNamespace(
        raise_for_status=lambda: None,
        json=lambda: {"content": [{"type": "text", "text": "ok"}], "usage": {}},
    )
    monkeypatch.setattr(
        requests,
        "post",
        lambda _url, headers=None, json=None, timeout=None: (
            captured.update({"payload": json}) or fake_response
        ),
    )
    client = LLMClient()
    message, usage = client._chat_anthropic(
        client._resolve_remote_target("anthropic::claude-sonnet-4.6"),
        [{"role": "user", "content": "hi"}],
        [
            {"type": "function", "function": {"name": "zeta_tool", "description": "z", "parameters": {"type": "object"}}},
            {"type": "function", "function": {"name": "alpha_tool", "description": "a", "parameters": {"type": "object"}}},
        ],
        "medium",
        128,
        "auto",
    )

    assert message["content"] == "ok"
    assert [tool["name"] for tool in captured["payload"]["tools"]] == ["alpha_tool", "zeta_tool"]
    assert captured["payload"]["tools"][-1]["cache_control"] == {"type": "ephemeral"}
    assert "ttl" not in captured["payload"]["tools"][-1]["cache_control"]
    assert usage["prompt_cache_ttl"] == "default"


def test_resolve_anthropic_target_normalizes_direct_model(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-key")

    target = LLMClient()._resolve_remote_target("anthropic::claude-sonnet-4.6")

    assert target["provider"] == "anthropic"
    assert target["resolved_model"] == "claude-sonnet-4-6"
    assert target["usage_model"] == "anthropic/claude-sonnet-4-6"
    assert target["base_url"] == "https://api.anthropic.com/v1"


def test_normalize_anthropic_response_maps_tool_use(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-key")

    client = LLMClient()
    target = client._resolve_remote_target("anthropic::claude-sonnet-4-6")
    with patch("ouroboros.pricing.estimate_cost", return_value=0.012345):
        message, usage = client._normalize_anthropic_response(
            {
                "content": [
                    {"type": "text", "text": "Working on it."},
                    {"type": "tool_use", "id": "toolu_1", "name": "echo_tool", "input": {"text": "hello"}},
                ],
                "usage": {
                    "input_tokens": 11,
                    "output_tokens": 7,
                    "cache_read_input_tokens": 3,
                    "cache_creation_input_tokens": 2,
                },
            },
            target,
        )

    assert message["content"] == "Working on it."
    assert message["tool_calls"][0]["function"]["name"] == "echo_tool"
    assert message["tool_calls"][0]["function"]["arguments"] == '{"text": "hello"}'
    assert usage["provider"] == "anthropic"
    assert usage["resolved_model"] == "anthropic/claude-sonnet-4-6"
    assert usage["cached_tokens"] == 3
    assert usage["cache_write_tokens"] == 2
    assert usage["cost_estimated"] is True


def test_build_anthropic_messages_preserves_system_blocks_and_cache_control(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-key")

    client = LLMClient()
    system_blocks, anthropic_messages = client._build_anthropic_messages([
        {
            "role": "system",
            "content": [
                {"type": "text", "text": "stable", "cache_control": {"type": "ephemeral", "ttl": "1h"}},
                {"type": "text", "text": "dynamic"},
            ],
        },
        {"role": "user", "content": "hi"},
    ])

    assert system_blocks == [
        {"type": "text", "text": "stable", "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": "dynamic"},
    ]
    assert anthropic_messages == [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]


def test_resolve_openai_compatible_target_prefers_dedicated_credentials(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "legacy-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://legacy.example/v1")
    monkeypatch.setenv("OPENAI_COMPATIBLE_API_KEY", "compat-key")
    monkeypatch.setenv("OPENAI_COMPATIBLE_BASE_URL", "https://compat.example/v1")

    target = LLMClient()._resolve_remote_target("openai-compatible::meta-llama/compatible")

    assert target["provider"] == "openai-compatible"
    assert target["api_key"] == "compat-key"
    assert target["base_url"] == "https://compat.example/v1"
    assert target["usage_model"] == "openai-compatible/meta-llama/compatible"


def test_resolve_bare_groq_target_uses_direct_groq_credentials(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "groq-key")
    monkeypatch.delenv("GROQ_BASE_URL", raising=False)

    target = LLMClient()._resolve_remote_target("groq/compound")

    assert target["provider"] == "groq"
    assert target["resolved_model"] == "groq/compound"
    assert target["api_key"] == "groq-key"
    assert target["base_url"] == "https://api.groq.com/openai/v1"
    assert target["usage_model"] == "groq/compound"


def test_resolve_explicit_qwen_target_uses_direct_qwen_credentials(monkeypatch):
    monkeypatch.setenv("QWEN_API_KEY", "qwen-key")
    monkeypatch.delenv("QWEN_BASE_URL", raising=False)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.delenv("DASHSCOPE_BASE_URL", raising=False)

    target = LLMClient()._resolve_remote_target("qwen::qwen3.6-plus")

    assert target["provider"] == "qwen"
    assert target["resolved_model"] == "qwen3.6-plus"
    assert target["api_key"] == "qwen-key"
    assert target["base_url"] == "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
    assert target["usage_model"] == "qwen/qwen3.6-plus"


def test_bare_qwen_target_stays_on_openrouter(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-key")

    target = LLMClient()._resolve_remote_target("qwen/qwen3.6-flash")

    assert target["provider"] == "openrouter"
    assert target["resolved_model"] == "qwen/qwen3.6-flash"
    assert target["api_key"] == "openrouter-key"


def test_groq_direct_provider_caps_default_max_tokens(monkeypatch):
    monkeypatch.delenv("GROQ_MAX_TOKENS", raising=False)

    client = LLMClient()
    target = client._resolve_remote_target("groq/compound")
    kwargs = client._build_remote_kwargs(
        target,
        [{"role": "user", "content": "hi"}],
        "medium",
        4096,
        "auto",
        None,
        None,
    )

    assert kwargs["max_tokens"] == 128


def test_direct_provider_settings_apply_to_env(monkeypatch):
    from ouroboros.config import apply_settings_to_env

    for key in ("GROQ_API_KEY", "GROQ_MAX_TOKENS", "QWEN_API_KEY", "QWEN_BASE_URL"):
        monkeypatch.delenv(key, raising=False)

    apply_settings_to_env(
        {
            "GROQ_API_KEY": "groq-key",
            "GROQ_MAX_TOKENS": "256",
            "QWEN_API_KEY": "qwen-key",
            "QWEN_BASE_URL": "https://qwen.example/v1",
        }
    )

    assert os.environ["GROQ_API_KEY"] == "groq-key"
    assert os.environ["GROQ_MAX_TOKENS"] == "256"
    assert os.environ["QWEN_API_KEY"] == "qwen-key"
    assert os.environ["QWEN_BASE_URL"] == "https://qwen.example/v1"


def test_resolve_cloudru_target_uses_default_base_url(monkeypatch):
    monkeypatch.setenv("CLOUDRU_FOUNDATION_MODELS_API_KEY", "cloudru-key")
    monkeypatch.delenv("CLOUDRU_FOUNDATION_MODELS_BASE_URL", raising=False)

    target = LLMClient()._resolve_remote_target("cloudru::giga-model")

    assert target["provider"] == "cloudru"
    assert target["api_key"] == "cloudru-key"
    assert target["base_url"] == "https://foundation-models.api.cloud.ru/v1"
    assert target["usage_model"] == "cloudru/giga-model"


def test_resolve_gigachat_target_uses_defaults(monkeypatch):
    monkeypatch.setenv("GIGACHAT_CREDENTIALS", "giga-creds")
    monkeypatch.delenv("GIGACHAT_SCOPE", raising=False)
    monkeypatch.delenv("GIGACHAT_BASE_URL", raising=False)
    monkeypatch.delenv("GIGACHAT_VERIFY_SSL_CERTS", raising=False)

    target = LLMClient()._resolve_remote_target("gigachat::GigaChat-2-Max")

    assert target["provider"] == "gigachat"
    assert target["api_key"] == "giga-creds"
    assert target["scope"] == "GIGACHAT_API_PERS"
    assert target["base_url"] == "https://gigachat.devices.sberbank.ru/api/v1"
    assert target["verify_ssl_certs"] is True
    assert target["usage_model"] == "gigachat/GigaChat-2-Max"


def test_resolve_gigachat_target_honors_overrides(monkeypatch):
    monkeypatch.setenv("GIGACHAT_CREDENTIALS", "giga-creds")
    monkeypatch.setenv("GIGACHAT_SCOPE", "GIGACHAT_API_CORP")
    monkeypatch.setenv("GIGACHAT_BASE_URL", "https://giga.example/api/v1")
    monkeypatch.setenv("GIGACHAT_VERIFY_SSL_CERTS", "false")

    target = LLMClient()._resolve_remote_target("gigachat::GigaChat")

    assert target["scope"] == "GIGACHAT_API_CORP"
    assert target["base_url"] == "https://giga.example/api/v1"
    assert target["verify_ssl_certs"] is False


def test_normalize_remote_response_estimates_cost_for_direct_openai(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")

    client = LLMClient()
    target = client._resolve_remote_target("openai::gpt-5.2")
    seen = {}

    def fake_estimate_cost(model, prompt_tokens, completion_tokens, cached_tokens=0, cache_write_tokens=0, prompt_cache_ttl=None, allow_live_fetch=True):
        seen["args"] = (model, prompt_tokens, completion_tokens, cached_tokens, cache_write_tokens, prompt_cache_ttl, allow_live_fetch)
        return 0.123456

    monkeypatch.setattr(pricing_module, "estimate_cost", fake_estimate_cost)

    message, usage = client._normalize_remote_response(
        {
            "choices": [{"message": {"role": "assistant", "content": "ok"}}],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 40,
                "prompt_tokens_details": {"cached_tokens": 10, "cache_write_tokens": 5},
            },
        },
        target,
    )

    assert message["content"] == "ok"
    assert usage["provider"] == "openai"
    assert usage["resolved_model"] == "openai/gpt-5.2"
    assert usage["cached_tokens"] == 10
    assert usage["cache_write_tokens"] == 5
    assert usage["cost"] == 0.123456
    assert usage["cost_estimated"] is True
    assert seen["args"] == ("openai/gpt-5.2", 100, 40, 10, 5, None, True)


def test_normalize_remote_response_preserves_reasoning_and_response_id():
    client = LLMClient()
    target = client._resolve_remote_target("anthropic/claude-sonnet-4.6")
    message, usage = client._normalize_remote_response(
        {
            "id": "gen-123",
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{"id": "call-1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}],
                    "reasoning": "look up the file",
                    "reasoning_details": [{"type": "reasoning.text", "text": "look up the file"}],
                },
            }],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        },
        target,
        skip_cost_fetch=True,
    )

    assert message["response_id"] == "gen-123"
    assert message["reasoning"] == "look up the file"
    assert message["reasoning_details"] == [{"type": "reasoning.text", "text": "look up the file"}]
    assert usage["provider"] == "openrouter"


def test_build_anthropic_messages_rejects_tool_result_without_tool_call_id(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-key")

    client = LLMClient()

    with pytest.raises(ValueError, match="tool_call_id"):
        client._build_anthropic_messages([
            {"role": "user", "content": "hi"},
            {"role": "tool", "content": "done"},
        ])
