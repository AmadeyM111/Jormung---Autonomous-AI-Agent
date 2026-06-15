from __future__ import annotations


def test_groq_smoke_ignores_runtime_completion_cap(monkeypatch):
    import ouroboros.groq_api_smoke as smoke

    captured = {}

    def fake_run_smoke(**kwargs):
        captured.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(smoke, "run_smoke", fake_run_smoke)
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test_key_1234567890")
    monkeypatch.setenv("OPENAI_COMPATIBLE_MAX_TOKENS", "64")
    monkeypatch.setenv("GROQ_MAX_TOKENS", "64")

    rc = smoke.main([])

    assert rc == 0
    assert captured["max_tokens"] == smoke.DEFAULT_GROQ_SMOKE_MAX_TOKENS


def test_groq_smoke_treats_missing_tool_call_as_warning(monkeypatch):
    import ouroboros.groq_api_smoke as smoke

    class _Client:
        def chat(self, **kwargs):
            if kwargs.get("tools"):
                return {"content": "", "role": "assistant", "tool_calls": None, "reasoning": "tool-only reasoning"}, {}
            return {"content": "OK", "role": "assistant"}, {"prompt_tokens": 1, "completion_tokens": 1, "provider": "openai-compatible", "resolved_model": "openai/gpt-oss-20b"}

    monkeypatch.setattr(smoke, "LLMClient", lambda: _Client())
    out = smoke.run_smoke(
        api_key="gsk_test_key_1234567890",
        base_url="https://api.groq.com/openai/v1",
        model="openai/gpt-oss-20b",
        max_tokens=16,
        request_timeout=1.0,
    )

    assert out["ok"] is True
    assert out["tool_smoke_ok"] is False
    assert "tool_warning" in out


def test_groq_smoke_skips_user_tools_for_compound(monkeypatch):
    import ouroboros.groq_api_smoke as smoke

    calls = []

    class _Client:
        def chat(self, **kwargs):
            calls.append(kwargs)
            return {"content": "OK", "role": "assistant"}, {"prompt_tokens": 1, "completion_tokens": 1, "provider": "openai-compatible", "resolved_model": "groq/compound"}

    monkeypatch.setattr(smoke, "LLMClient", lambda: _Client())
    out = smoke.run_smoke(
        api_key="gsk_test_key_1234567890",
        base_url="https://api.groq.com/openai/v1",
        model="groq/compound",
        max_tokens=128,
        request_timeout=1.0,
    )

    assert out["ok"] is True
    assert out["tool_smoke_skipped"] == "Groq Compound does not support user-provided tools"
    assert len(calls) == 1
    assert "tools" not in calls[0]
