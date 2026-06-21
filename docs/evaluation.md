# Evaluation И Метрики

Документ описывает, как оценивать качество агента, какие проверки считать
обязательными и какие runtime-метрики смотреть при деградации.

## Цели Evaluation

Оценка должна отвечать на четыре вопроса:

1. Выполнена ли пользовательская цель.
2. Не нарушены ли safety/review/resource ограничения.
3. Не деградировали ли стоимость, latency и устойчивость к provider failures.
4. Достаточно ли данных осталось в task results/logs/observability для разбора.

## Обязательные Локальные Проверки

```bash
python3 -m pytest tests/ -q --tb=short
git diff --check
python -m py_compile ouroboros/context.py ouroboros/llm.py ouroboros/loop.py
```

Для изменений в конкретных зонах:

| Зона | Минимальная проверка |
| --- | --- |
| LLM/provider routing | `tests/test_llm_provider_routing.py`, `tests/test_groq_api_smoke.py` |
| Context/memory | `tests/test_doc_context.py`, context-related tests |
| Colab/Groq | `tests/test_colab_bootstrap.py`, `python -m ouroboros.groq_api_smoke` |
| Gateway/API | `tests/test_gateway_parity.py`, contract tests |
| Skills/extensions | skill preflight/review tests, extension health tests |
| Packaging | build/portable tests, platform build script tests |

## Review-Based Evaluation

Self-modification path:

```text
advisory_review -> commit_reviewed -> triad review + scope review
```

Важные критерии:

- advisory freshness должен соответствовать staged diff;
- scope review не должен молча пропускаться из-за budget/context overflow;
- triad reviewer slots считаются слотами, даже если model id повторяется;
- findings и obligations сохраняются в review state.

## Runtime Outcome Metrics

Значимые оси результата:

| Ось | Что означает |
| --- | --- |
| lifecycle | задача завершилась, отменена, упала или зависла |
| execution health | были ли tool/model/runtime ошибки |
| artifact state | созданы ли заявленные файлы/патчи/artifacts |
| review | был ли acceptance/review verdict |
| objective | достигнута ли цель, либо `not_evaluated` |

Смотреть:

```text
data/task_results/<task_id>.json
data/logs/
data/observability/
```

## Provider Metrics

Отслеживать:

- model id и provider;
- primary/fallback/reserve transition;
- status code: 400, 401, 403, 413, 429, 5xx;
- prompt tokens, completion tokens, total tokens;
- cost estimate;
- retry count;
- context length и output cap.

Для Groq/Telegram особенно важны:

```text
OPENAI_COMPATIBLE_CONTEXT_LENGTH
OPENAI_COMPATIBLE_MAX_TOKENS
OUROBOROS_CONTEXT_MODE
OUROBOROS_MINIMAL_CONTEXT
```

## Digest Evaluation

Критерии успешного дайджеста:

- ответ сформирован native tool route `research_digest.prepare_digest`;
- пользователь не видит `<ext_...>{...}`;
- список структурирован, compact, source-diverse;
- нет повторной подачи полного refresh/digest payload в LLM;
- нет 413 на safety/review prompt;
- при 429 есть понятное сообщение и reserve на другом quota pool.

## Benchmark Hooks

В репозитории есть вспомогательные bridges:

```text
scripts/swebench_cli_agent.py
scripts/terminal_bench_cli_agent.py
```

Они должны использовать существующий CLI/headless task API. Не добавлять второй
scheduler, benchmark-only finish tool или обход safety/review gates.

## Regression Budget

Любое изменение считается рискованным, если оно:

- увеличивает default context/tool-result payload для Groq/Colab;
- снижает review/scope coverage без явного ADR;
- меняет `ToolEntry`/`ToolContext` ABI;
- меняет gateway contracts;
- меняет runtime data layout;
- меняет build/release scripts.

