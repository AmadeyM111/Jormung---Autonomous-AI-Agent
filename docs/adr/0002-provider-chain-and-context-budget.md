# ADR 0002: Provider Chain And Context Budget

Date: 2026-06-21
Status: accepted

## Context

Telegram/Groq digest показал несколько классов отказов: 429 TPM limit, 413
request too large и 400 tool_use failed. Fallback на модель внутри той же Groq
quota pool не устраняет provider-wide лимиты, а несогласованные context/output
caps увеличивают вероятность 413/429.

## Decision

- Модельная цепочка трактуется как `primary -> fallback -> reserve`.
- `OUROBOROS_MODEL_RESERVE` / `OUROBOROS_RESERVE_MODEL` должен использоваться
  для другого provider/quota pool, когда fallback не является независимым.
- Для OpenAI-compatible маршрутов `OPENAI_COMPATIBLE_CONTEXT_LENGTH` и
  `OPENAI_COMPATIBLE_MAX_TOKENS` являются обязательными эксплуатационными caps.
- Для Ollama/Modelfile `PARAMETER num_ctx` должен быть согласован с
  `OPENAI_COMPATIBLE_CONTEXT_LENGTH`, а `num_predict` - с
  `OPENAI_COMPATIBLE_MAX_TOKENS`.
- Для Colab/Groq digest default профиль должен быть low-context и compact.

## Consequences

- 429 лечится provider/quota diversification, снижением concurrency и
  уменьшением background token load, а не только сменой model id.
- 413 лечится compaction, caps и direct tool routes.
- Digest не должен отправлять полный refresh/tool payload обратно в LLM.

