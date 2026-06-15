"""Smoke test Groq's OSS models through Ouroboros's OpenAI-compatible route."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict

from ouroboros.colab_bootstrap import (
    DEFAULT_GROQ_CONTEXT_LENGTH,
    DEFAULT_GROQ_OSS_MODEL,
    GROQ_OPENAI_COMPATIBLE_BASE_URL,
)
from ouroboros.llm import LLMClient

DEFAULT_GROQ_SMOKE_MAX_TOKENS = 128


def _strip_prefix(model: str) -> str:
    text = str(model or "").strip()
    if text.startswith("openai-compatible::"):
        return text.removeprefix("openai-compatible::").strip()
    return text


def _default_model() -> str:
    explicit = _strip_prefix(os.environ.get("GROQ_MODEL", "")) or _strip_prefix(
        os.environ.get("OPENAI_COMPATIBLE_MODEL", "")
    )
    if explicit:
        return explicit
    ouroboros_model = str(os.environ.get("OUROBOROS_MODEL", "") or "").strip()
    if ouroboros_model.startswith("openai-compatible::"):
        return _strip_prefix(ouroboros_model)
    return DEFAULT_GROQ_OSS_MODEL


def _configure_env(api_key: str, base_url: str, max_tokens: str, context_length: str) -> None:
    os.environ["OPENAI_COMPATIBLE_API_KEY"] = api_key
    os.environ["OPENAI_COMPATIBLE_BASE_URL"] = base_url
    if max_tokens:
        os.environ["OPENAI_COMPATIBLE_MAX_TOKENS"] = max_tokens
    if context_length:
        os.environ["OPENAI_COMPATIBLE_CONTEXT_LENGTH"] = context_length
    for key in (
        "USE_LOCAL_MAIN",
        "USE_LOCAL_CODE",
        "USE_LOCAL_LIGHT",
        "USE_LOCAL_CONSCIOUSNESS",
        "USE_LOCAL_FALLBACK",
    ):
        os.environ.pop(key, None)


def _message_text(message: Dict[str, Any]) -> str:
    content = message.get("content") if isinstance(message, dict) else ""
    if isinstance(content, list):
        return "".join(
            str(block.get("text") or "") if isinstance(block, dict) else str(block)
            for block in content
        )
    return str(content or "")


def run_smoke(
    *,
    api_key: str,
    base_url: str,
    model: str,
    max_tokens: int,
    request_timeout: float,
    skip_tools: bool = False,
) -> Dict[str, Any]:
    """Run basic text and optional tool-call checks, returning a JSON-safe summary."""
    _configure_env(
        api_key=api_key,
        base_url=base_url,
        max_tokens=str(max_tokens),
        context_length=os.environ.get("GROQ_CONTEXT_LENGTH", DEFAULT_GROQ_CONTEXT_LENGTH),
    )
    qualified_model = f"openai-compatible::{_strip_prefix(model)}"
    client = LLMClient()

    message, usage = client.chat(
        messages=[{
            "role": "user",
            "content": "Reply with exactly OK.",
        }],
        model=qualified_model,
        max_tokens=min(max_tokens, 16),
        temperature=0,
        reasoning_effort="low",
        timeout=request_timeout,
    )
    text = _message_text(message)
    text_ok = text.strip().upper() == "OK"

    summary: Dict[str, Any] = {
        "ok": True,
        "base_url": base_url,
        "model": qualified_model,
        "text_chars": len(text),
        "text_smoke_ok": text_ok,
        "usage": {
            "prompt_tokens": int((usage or {}).get("prompt_tokens") or 0),
            "completion_tokens": int((usage or {}).get("completion_tokens") or 0),
            "provider": str((usage or {}).get("provider") or ""),
            "resolved_model": str((usage or {}).get("resolved_model") or ""),
        },
    }
    if not text_ok:
        summary["text_warning"] = "text smoke returned non-OK output; proceeding with tool smoke"

    if not skip_tools:
        tool_message, _tool_usage = client.chat(
            messages=[{
                "role": "user",
                "content": "Call the lookup_oil_grade tool for Brent crude and do not answer in prose.",
            }],
            model=qualified_model,
            max_tokens=min(max_tokens, 512),
            tools=[{
                "type": "function",
                "function": {
                    "name": "lookup_oil_grade",
                    "description": "Look up metadata for a crude oil benchmark.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "grade": {
                                "type": "string",
                                "description": "Crude oil benchmark name.",
                            }
                        },
                        "required": ["grade"],
                        "additionalProperties": False,
                    },
                },
            }],
            tool_choice="auto",
            temperature=0,
            timeout=request_timeout,
        )
        tool_calls = tool_message.get("tool_calls") or []
        if not tool_calls:
            summary["tool_smoke_ok"] = False
            summary["tool_warning"] = "tool smoke returned no tool_calls; proceeding"
        else:
            first = tool_calls[0]
            name = str(((first or {}).get("function") or {}).get("name") or "")
            summary["tool_smoke_ok"] = name == "lookup_oil_grade"
            if name != "lookup_oil_grade":
                summary["tool_warning"] = f"unexpected tool call {name!r}"
            summary["tool_call"] = {
                "name": name,
                "arguments": str(((first or {}).get("function") or {}).get("arguments") or "")[:500],
            }

    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=_default_model())
    parser.add_argument("--base-url", default=os.environ.get("OPENAI_COMPATIBLE_BASE_URL") or GROQ_OPENAI_COMPATIBLE_BASE_URL)
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=int(os.environ.get("GROQ_SMOKE_MAX_TOKENS") or DEFAULT_GROQ_SMOKE_MAX_TOKENS),
    )
    parser.add_argument("--timeout", type=float, default=float(os.environ.get("GROQ_TIMEOUT_SEC") or "90"))
    parser.add_argument("--skip-tools", action="store_true")
    args = parser.parse_args(argv)

    api_key = (
        os.environ.get("GROQ_API_KEY")
        or os.environ.get("OPENAI_COMPATIBLE_API_KEY")
        or ""
    ).strip()
    if not api_key:
        print(
            "GROQ_API_KEY or OPENAI_COMPATIBLE_API_KEY is required for Groq smoke.",
            file=sys.stderr,
        )
        return 2

    summary = run_smoke(
        api_key=api_key,
        base_url=str(args.base_url),
        model=str(args.model),
        max_tokens=int(args.max_tokens),
        request_timeout=float(args.timeout),
        skip_tools=bool(args.skip_tools),
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
