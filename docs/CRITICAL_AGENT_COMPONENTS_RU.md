# Критичные Компоненты Агента

Документ описывает только значимые узлы Ouroboros: те, без которых агент не
запускается, не выполняет задачи, теряет безопасность, ломает Telegram/digest
или становится трудно диагностируемым. Подробная полная карта остается в
`docs/ARCHITECTURE.md`.

## 1. Общий Поток

```text
Пользователь / Telegram / CLI / Web UI
  |
  v
launcher.py или ouroboros.cli
  |
  v
server.py
  |
  +-- ouroboros/gateway/      HTTP, WebSocket, настройки, задачи, skills
  +-- supervisor/             очередь, worker-процессы, состояние, события
  |     |
  |     v
  |   worker process
  |     |
  |     v
  +-- ouroboros/agent.py      агентная задача
        |
        +-- сбор контекста и памяти
        +-- выбор модели
        +-- LLM/tool loop
        +-- safety/review gates
        +-- выполнение tools/skills
        +-- сохранение результата
```

Главное разделение:

- исходный код проекта: текущий git-репозиторий;
- runtime data root: обычно `~/Ouroboros/data`;
- локально у пользователя `/Users/amadey/Ouroboros` является runtime/data
  каталогом, а не актуальным source checkout.

## 2. Самые Важные Узлы

| Уровень | Узел | Файлы | Что ломается |
| --- | --- | --- | --- |
| 0 | Настройки и пути | `ouroboros/config.py` | Модели, ключи, data root, порты, settings. |
| 0 | Сервер | `server.py`, `ouroboros/server_runtime.py` | Web UI, CLI, Telegram и supervisor не получают runtime. |
| 0 | Gateway | `ouroboros/gateway/router.py`, `ouroboros/gateway/contracts.py` | Ломаются HTTP/WS API, UI и CLI. |
| 0 | Supervisor | `supervisor/*.py` | Задачи не ставятся в очередь, не исполняются или теряют события. |
| 0 | Agent pipeline | `ouroboros/agent.py`, `ouroboros/agent_task_pipeline.py` | Пользовательский запрос не превращается в результат. |
| 0 | LLM router | `ouroboros/llm.py`, `ouroboros/provider_models.py` | Ошибки моделей, fallback/reserve, 400/413/429. |
| 0 | Context/memory | `ouroboros/context.py`, `ouroboros/memory.py` | Агент теряет контекст, личность, историю или переполняет запрос. |
| 0 | Tool loop | `ouroboros/loop.py`, `ouroboros/loop_tool_execution.py` | Модель не вызывает tools или получает плохие tool results. |
| 0 | Safety | `ouroboros/safety.py`, `ouroboros/runtime_mode_policy.py` | Риск опасных операций и повреждения защищенных файлов. |
| 1 | Skills/extensions | `ouroboros/skill_loader.py`, `ouroboros/extension_loader.py`, `skills/*` | Внешние возможности и reviewed tools недоступны. |
| 1 | Telegram/Colab | `ouroboros/colab_bootstrap.py`, `notebooks/colab_quickstart.py` | Colab runtime, Groq profile и Telegram bridge не поднимаются. |
| 1 | Research digest | `skills/research_digest/*` | Дайджест не формируется корректным tool route. |
| 1 | Observability/results | `ouroboros/observability.py`, `ouroboros/task_results.py` | Нельзя понять, что произошло, и восстановить результат. |

## 3. Запуск И Runtime

Критичные entrypoints:

```bash
ouroboros server --host 127.0.0.1 --port 8765
python -m ouroboros.cli server --host 127.0.0.1 --port 8765 --no-ui
python server.py
python launcher.py
```

CLI/headless:

```bash
ouroboros run --start "<задача>"
ouroboros tasks list
ouroboros tasks watch <task_id>
```

Colab/Telegram:

```text
notebooks/colab_quickstart.py
ouroboros/colab_bootstrap.py
```

Значимые runtime-пути:

```text
data/settings.json
data/logs/
data/task_results/
data/state/
data/observability/
data/memory/
data/dialogue_blocks.json
```

## 4. Настройки Моделей

Главная цепочка моделей:

```text
OUROBOROS_MODEL          primary
OUROBOROS_MODEL_FALLBACK fallback
OUROBOROS_MODEL_RESERVE  reserve
OUROBOROS_RESERVE_MODEL  совместимый alias reserve
```

Отдельные роли:

```text
OUROBOROS_MODEL_CODE
OUROBOROS_MODEL_LIGHT
OUROBOROS_MODEL_CONSCIOUSNESS
OUROBOROS_MODEL_DEEP_SELF_REVIEW
CLAUDE_CODE_MODEL
```

OpenAI-compatible/Ollama/Groq:

```text
OPENAI_COMPATIBLE_BASE_URL
OPENAI_COMPATIBLE_API_KEY
OPENAI_COMPATIBLE_CONTEXT_LENGTH
OPENAI_COMPATIBLE_MAX_TOKENS
```

Локальная модель через встроенный local runtime:

```text
USE_LOCAL_MAIN
USE_LOCAL_CODE
USE_LOCAL_LIGHT
USE_LOCAL_CONSCIOUSNESS
USE_LOCAL_FALLBACK
LOCAL_MODEL_PORT
LOCAL_MODEL_CONTEXT_LENGTH
LOCAL_MODEL_N_GPU_LAYERS
LOCAL_MODEL_SOURCE
LOCAL_MODEL_FILENAME
```

Правило синхронизации:

```text
PARAMETER num_ctx
LOCAL_MODEL_CONTEXT_LENGTH
OPENAI_COMPATIBLE_CONTEXT_LENGTH
```

должны описывать один и тот же практический размер контекста.

```text
PARAMETER num_predict
OPENAI_COMPATIBLE_MAX_TOKENS
```

должны быть совместимы по максимальному размеру ответа.

## 5. Контекст И Память

Значимые файлы:

- `ouroboros/context.py`
- `ouroboros/context_budget.py`
- `ouroboros/context_compaction.py`
- `ouroboros/memory.py`
- `ouroboros/consolidator.py`
- `BIBLE.md`
- `prompts/SYSTEM.md`
- `prompts/CONSCIOUSNESS.md`

Критичные параметры:

```text
OUROBOROS_CONTEXT_MODE=max|low
OUROBOROS_MINIMAL_CONTEXT=true|false
OPENAI_COMPATIBLE_CONTEXT_LENGTH
OPENAI_COMPATIBLE_MAX_TOKENS
```

Типовые ошибки:

- слишком большой контекст: 413 `Request Entity Too Large`;
- слишком большой tool result: 413 или 429;
- слишком маленький контекст: агент забывает личность, историю или назначение
  tools;
- `OPENAI_COMPATIBLE_MAX_TOKENS=0`: отключает cap ответа для compatible route.

## 6. LLM Router, Fallback И Reserve

Значимые файлы:

- `ouroboros/llm.py`
- `ouroboros/loop_llm_call.py`
- `ouroboros/provider_models.py`
- `ouroboros/llm_observability.py`

Цепочка должна быть такой:

```text
primary  -> fallback -> reserve
```

Для 429 важно:

- fallback на другую модель того же Groq/account quota pool часто не решает
  корневую причину;
- reserve лучше держать на другом провайдере или другом quota pool;
- background consciousness и параллельные worker/subagent задачи могут
  незаметно сжигать TPM.

## 7. Tool Loop И Safety

Значимые файлы:

- `ouroboros/loop.py`
- `ouroboros/loop_tool_execution.py`
- `ouroboros/tool_capabilities.py`
- `ouroboros/tool_access.py`
- `ouroboros/tool_policy.py`
- `ouroboros/safety.py`
- `ouroboros/runtime_mode_policy.py`

Критичные параметры:

```text
OUROBOROS_RUNTIME_MODE
OUROBOROS_TOOL_TIMEOUT_SEC
OUROBOROS_SOFT_TIMEOUT_SEC
OUROBOROS_HARD_TIMEOUT_SEC
OUROBOROS_FINALIZATION_GRACE_SEC
```

Что важно:

- tool result должен быть компактным;
- защищенные prompt/core/release файлы не должны становиться обычной зоной
  записи;
- bypass safety допустим только точечно и для проверенного route, например для
  trusted `research_digest.prepare_digest`.

## 8. Skills И Extensions

Значимые файлы:

- `ouroboros/skill_loader.py`
- `ouroboros/skill_readiness.py`
- `ouroboros/skill_lifecycle_queue.py`
- `ouroboros/skill_review.py`
- `ouroboros/extension_loader.py`
- `ouroboros/extension_process_runner.py`
- `ouroboros/extension_companion.py`
- `ouroboros/extension_reconcile_queue.py`

Значимые runtime-пути:

```text
data/skills/native/
data/skills/external/
data/skills/clawhub/
data/skills/ouroboroshub/
data/state/skills/<skill>/
```

Инварианты:

- executable skill требует свежего review verdict;
- content hash должен совпадать с проверенным payload;
- secrets должны попадать в skill только через reviewed grants;
- lifecycle install/update/review/enable/disable должен идти через очередь.

## 9. Telegram И Research Digest

Значимые файлы:

- `ouroboros/colab_bootstrap.py`
- `notebooks/colab_quickstart.py`
- `skills/research_digest/plugin.py`
- `skills/research_digest/skill.json`

Нормальный путь дайджеста:

```text
Telegram message
  -> transport skill / message bus
  -> supervisor queue
  -> agent pipeline
  -> detector digest request
  -> reviewed research_digest.prepare_digest
  -> compact final_response
  -> Telegram reply
```

Colab/Groq профиль:

```text
OPENAI_COMPATIBLE_BASE_URL=https://api.groq.com/openai/v1
OUROBOROS_MODEL=openai-compatible::groq/compound
OUROBOROS_MODEL_FALLBACK=openai-compatible::llama-3.1-8b-instant
OPENAI_COMPATIBLE_CONTEXT_LENGTH=8192
OPENAI_COMPATIBLE_MAX_TOKENS=128
OUROBOROS_CONTEXT_MODE=low
OUROBOROS_MINIMAL_CONTEXT=true
OUROBOROS_EFFORT_TASK=low
OUROBOROS_EFFORT_CONSCIOUSNESS=low
```

Ошибки, которые этот путь должен предотвращать:

- LLM не должен печатать пользователю `<ext_...>{...}`;
- digest не должен зависеть от fragile provider tool calling;
- полный refresh/digest payload нельзя отправлять обратно в Groq;
- safety check не должен создавать огромный Groq-запрос для доверенного
  `prepare_digest`.

## 10. Observability И Результаты

Значимые файлы:

- `ouroboros/observability.py`
- `ouroboros/llm_observability.py`
- `ouroboros/task_results.py`
- `ouroboros/outcomes.py`
- `ouroboros/artifacts.py`

Что смотреть при сбое:

```text
data/logs/
data/task_results/
data/state/
data/observability/
```

Инварианты:

- secrets в logs/traces должны редактироваться;
- task result должен сохраняться даже при частичном сбое;
- artifacts должны быть task-scoped, не произвольным доступом к файловой
  системе.

## 11. Значимые Скрипты

Запуск и диагностика:

```bash
python server.py
python launcher.py
python -m ouroboros.groq_api_smoke
python3 -m pytest tests/ -q --tb=short
git diff --check
make test
make health
```

Сборка:

```bash
bash build.sh
bash build_linux.sh
powershell -ExecutionPolicy Bypass -File build_windows.ps1
python scripts/build_repo_bundle.py
```

Dev/review:

```bash
python scripts/run_external_review.py
python scripts/cleanup_test_pollution.py
```

## 12. Практические Профили

### Local Ollama/Qwen

```text
PARAMETER num_ctx 32768
PARAMETER num_predict 2048
PARAMETER temperature 0.2

OPENAI_COMPATIBLE_BASE_URL=http://localhost:11434/v1
OUROBOROS_MODEL=openai-compatible::qwen3:30b-a3b
OUROBOROS_MODEL_LIGHT=openai-compatible::qwen3-30b-a3b-16k:latest
OPENAI_COMPATIBLE_CONTEXT_LENGTH=32768
OPENAI_COMPATIBLE_MAX_TOKENS=2048
OUROBOROS_CONTEXT_MODE=low
```

### Conservative Local Ollama

```text
PARAMETER num_ctx 16384
PARAMETER num_predict 2048
PARAMETER temperature 0.2

OPENAI_COMPATIBLE_CONTEXT_LENGTH=16384
OPENAI_COMPATIBLE_MAX_TOKENS=2048
OUROBOROS_CONTEXT_MODE=low
```

### Colab/Groq/Telegram

```text
OPENAI_COMPATIBLE_BASE_URL=https://api.groq.com/openai/v1
OUROBOROS_MODEL=openai-compatible::groq/compound
OUROBOROS_MODEL_FALLBACK=openai-compatible::llama-3.1-8b-instant
OUROBOROS_MODEL_RESERVE=openai-compatible::<model_on_other_provider_or_quota>
OPENAI_COMPATIBLE_CONTEXT_LENGTH=8192
OPENAI_COMPATIBLE_MAX_TOKENS=128
OUROBOROS_CONTEXT_MODE=low
OUROBOROS_MINIMAL_CONTEXT=true
OUROBOROS_EFFORT_TASK=low
OUROBOROS_EFFORT_CONSCIOUSNESS=low
```

## 13. Короткий Чеклист Диагностики

1. Проверить тип ошибки провайдера: 400, 413, 429, timeout, empty response.
2. Проверить активную цепочку моделей: primary, fallback, reserve.
3. Проверить provider base URL и quota pool.
4. Проверить context budget и max tokens.
5. Проверить, не ушел ли большой tool result обратно в LLM.
6. Проверить, не должен ли запрос идти direct tool route.
7. Проверить task result, logs и observability.
8. Для Telegram/Colab проверить, что notebook подтянул свежую ветку и
   пересинхронизировал native skills.

