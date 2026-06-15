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
