# Local Ollama / Qwen3 Debug Notes

## Context

Target project: `oil-ai-agent`.

The failing runtime used Ollama through the OpenAI-compatible provider with Qwen3 30B A3B models:

- Primary: `openai-compatible::qwen3:30b-a3b`
- Fallback: `openai-compatible::qwen3-30b-a3b-16k`
- Base URL: `http://localhost:11434/v1`

## Symptoms

Initial failures looked like a provider/model outage:

- `provider_incomplete_response`
- `finish_reason=null`
- `no content, no tool_calls`
- `All models are down`

After preserving provider finish reasons, logs showed the real signal:

- `prompt_tokens` was almost the whole context window: `16383` or `32767`
- `completion_tokens=1`
- `finish_reason="length"`
- response content was empty

Even a simple user input like `привет` was sent together with a very large system/runtime prompt, leaving almost no room for the answer.

## Root Cause

The OpenAI-compatible/Ollama route did not apply the local-context preparation used by the local model route.

Increasing `_context_length` alone was not enough. The request payload still filled the whole context because the route did not compact the prompt or reserve enough completion space before sending the request to Ollama.

## Fixes Applied

Implemented in `ouroboros/llm.py`:

1. Preserve `choices[0].finish_reason` from OpenAI-compatible responses.
   - This exposed `finish_reason="length"` instead of hiding it as an incomplete provider response.

2. Detect local Ollama OpenAI-compatible endpoints.
   - `localhost`, `127.0.0.1`, or `::1` on port `11434` are treated as local Ollama-compatible targets.

3. Add OpenAI-compatible context handling.
   - `OPENAI_COMPATIBLE_CONTEXT_LENGTH` explicitly controls the context window for prompt preparation.
   - For local Ollama, `OLLAMA_CONTEXT_LENGTH` is used as fallback.
   - Default local Ollama context is `16384` if no env value is set.

4. Add OpenAI-compatible completion cap.
   - `OPENAI_COMPATIBLE_MAX_TOKENS` limits output tokens so the prompt cannot consume the whole context.
   - Default local Ollama cap is `2048`.
   - `OPENAI_COMPATIBLE_MAX_TOKENS=0` disables the cap.

5. Reuse existing safe compaction.
   - `_prepare_messages_for_local_context(...)` is now applied before building OpenAI-compatible payloads when a context length is known.

## Tests Added

Updated `tests/test_llm_provider_routing.py` with coverage for:

- preserving OpenAI-compatible `finish_reason`
- default Ollama `max_tokens` cap
- `OPENAI_COMPATIBLE_MAX_TOKENS` override
- `OPENAI_COMPATIBLE_MAX_TOKENS=0` disabling the cap
- remote OpenAI-compatible endpoints staying uncapped by default
- prompt compaction with `OPENAI_COMPATIBLE_CONTEXT_LENGTH`

Verification performed:

```bash
./.venv/bin/python -m pytest tests/test_llm_provider_routing.py -q
```

Result:

```text
32 passed
```

## Working Launch Command

Run from the target project directory:

```bash
cd /Users/amadey/devwork/ai-agents/oil-ai-agent

OPENAI_COMPATIBLE_BASE_URL=http://localhost:11434/v1 \
OPENAI_COMPATIBLE_CONTEXT_LENGTH=16384 \
OLLAMA_CONTEXT_LENGTH=16384 \
OPENAI_COMPATIBLE_MAX_TOKENS=2048 \
OUROBOROS_MODEL=openai-compatible::qwen3-30b-a3b-16k \
OUROBOROS_MODEL_CODE=openai-compatible::qwen3-30b-a3b-16k \
OUROBOROS_MODEL_LIGHT=openai-compatible::qwen3-30b-a3b-16k \
OUROBOROS_MODEL_FALLBACK=openai-compatible::qwen3-30b-a3b-16k \
poetry run python -m server
```

## If Answers Are Cut Off

The current answer length is intentionally capped by:

```bash
OPENAI_COMPATIBLE_MAX_TOKENS=2048
```

This prevents empty responses caused by context exhaustion, but it also limits long answers.

Recommended tuning:

- For 16k context: try `2048` to `4096`.
- For confirmed 32k context: try `4096` to `8192`.
- If output is too short, increase `OPENAI_COMPATIBLE_MAX_TOKENS` gradually.
- If empty responses return, lower `OPENAI_COMPATIBLE_MAX_TOKENS` or reduce/compact prompt memory further.

## Practical Conclusion

What helped was not just increasing context length. The working combination was:

- correctly exposing provider `finish_reason`
- reserving completion tokens
- compacting oversized runtime/system prompt before the request
- aligning env config with the actual Ollama model context
- using the created `qwen3-30b-a3b-16k` model as the primary model, not only as fallback
