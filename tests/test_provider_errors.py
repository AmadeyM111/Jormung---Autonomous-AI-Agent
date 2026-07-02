import asyncio

import pytest

from ouroboros.loop_llm_call import call_llm_with_retry, output_token_budget
from ouroboros.provider_errors import ProviderErrorKind, classify_provider_error


@pytest.mark.parametrize(
    ("error", "kind", "retryable"),
    [
        (
            "Error code: 402 - This request requires more credits; can only afford 847",
            ProviderErrorKind.BUDGET_EXCEEDED,
            False,
        ),
        ("Error code: 429 - Too Many Requests", ProviderErrorKind.RATE_LIMITED, True),
        ("Error code: 401 - invalid_api_key", ProviderErrorKind.AUTHENTICATION_ERROR, False),
        ("Error code: 400 - maximum context length exceeded", ProviderErrorKind.CONTEXT_TOO_LARGE, False),
        ("Error code: 503 - Service Unavailable", ProviderErrorKind.PROVIDER_DOWN, True),
        ("Error code: 400 - unsupported parameter", ProviderErrorKind.INVALID_REQUEST, False),
    ],
)
def test_classify_provider_error(error, kind, retryable):
    info = classify_provider_error(RuntimeError(error))

    assert info.kind is kind
    assert info.retryable is retryable


def test_output_token_budget_profiles():
    assert output_token_budget("classification", has_tools=False) == 512
    assert output_token_budget("summarize", has_tools=False) == 2_048
    assert output_token_budget("review", has_tools=False) == 8_192
    assert output_token_budget("evolution", has_tools=True) == 16_384
    assert output_token_budget("task", has_tools=False) == 4_096
    assert output_token_budget("task", has_tools=True) == 8_192


def test_main_loop_passes_dynamic_budget_to_client(tmp_path):
    calls = []

    class _LLM:
        def chat(self, **kwargs):
            calls.append(kwargs)
            return {"content": "done"}, {"provider": "test", "resolved_model": "test/model"}

    msg, _ = call_llm_with_retry(
        _LLM(),
        [{"role": "user", "content": "classify"}],
        "test/model",
        None,
        "low",
        1,
        tmp_path,
        "classification-task",
        1,
        None,
        {},
        "classification",
        False,
    )

    assert msg == {"content": "done"}
    assert calls[0]["max_tokens"] == 512


def test_non_retryable_auth_error_stops_outer_retries(tmp_path):
    calls = []

    class _LLM:
        def chat(self, **kwargs):
            calls.append(kwargs)
            raise RuntimeError("Error code: 401 - invalid_api_key")

    usage = {}
    msg, _ = call_llm_with_retry(
        _LLM(),
        [{"role": "user", "content": "hello"}],
        "test/model",
        None,
        "low",
        3,
        tmp_path,
        "auth-task",
        1,
        None,
        usage,
        "task",
        False,
    )

    assert msg is None
    assert len(calls) == 1
    assert usage["_last_llm_error_kind"] == "authentication_error"


def test_async_transport_shrinks_budget_error_to_minimum():
    from ouroboros.llm import LLMClient

    client = LLMClient()
    calls = []

    class _Resp:
        def model_dump(self):
            return {"choices": [{"message": {"content": "ok"}}], "usage": {}}

    async def fake_create(**kwargs):
        calls.append(kwargs["max_tokens"])
        if kwargs["max_tokens"] > 512:
            raise RuntimeError("Error code: 402 - requested output exceeds available credits; can only afford 847")
        return _Resp()

    response = asyncio.run(
        client._create_chat_completion_with_retries_async(
            fake_create,
            {"model": "qwen/test", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 8192},
            client._resolve_remote_target("qwen/test"),
        )
    )

    assert response.model_dump()["choices"][0]["message"]["content"] == "ok"
    assert calls == [8192, 4096, 2048, 1024, 512]


def test_sync_transport_retries_provider_reported_affordable_budget_below_minimum():
    from ouroboros.llm import LLMClient

    client = LLMClient()
    calls = []

    class _Resp:
        def model_dump(self):
            return {"choices": [{"message": {"content": "ok"}}], "usage": {}}

    def fake_create(**kwargs):
        calls.append(kwargs["max_tokens"])
        if kwargs["max_tokens"] > 463:
            raise RuntimeError(
                "Error code: 402 - You requested up to 512 tokens, but can only afford 463."
            )
        return _Resp()

    response = client._create_chat_completion_with_retries(
        fake_create,
        {"model": "qwen/test", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 512},
        client._resolve_remote_target("qwen/test"),
    )

    assert response.model_dump()["choices"][0]["message"]["content"] == "ok"
    assert calls == [512, 463]


def test_async_transport_retries_provider_reported_affordable_budget_below_minimum():
    from ouroboros.llm import LLMClient

    client = LLMClient()
    calls = []

    class _Resp:
        def model_dump(self):
            return {"choices": [{"message": {"content": "ok"}}], "usage": {}}

    async def fake_create(**kwargs):
        calls.append(kwargs["max_tokens"])
        if kwargs["max_tokens"] > 463:
            raise RuntimeError(
                "Error code: 402 - You requested up to 512 tokens, but can only afford 463."
            )
        return _Resp()

    response = asyncio.run(
        client._create_chat_completion_with_retries_async(
            fake_create,
            {"model": "qwen/test", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 512},
            client._resolve_remote_target("qwen/test"),
        )
    )

    assert response.model_dump()["choices"][0]["message"]["content"] == "ok"
    assert calls == [512, 463]


def test_shrinking_stops_if_error_changes_to_authentication():
    from ouroboros.llm import LLMClient

    client = LLMClient()
    calls = []

    def fake_create(**kwargs):
        calls.append(kwargs["max_tokens"])
        if len(calls) == 1:
            raise RuntimeError("Error code: 402 - can only afford 847")
        raise RuntimeError("Error code: 401 - invalid_api_key")

    with pytest.raises(RuntimeError, match="401"):
        client._create_chat_completion_with_retries(
            fake_create,
            {"model": "qwen/test", "messages": [], "max_tokens": 8192},
            client._resolve_remote_target("qwen/test"),
        )

    assert calls == [8192, 4096]
