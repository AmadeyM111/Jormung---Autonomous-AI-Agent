# Production Runbook

Практический runbook для ремонта runtime/production инцидентов Ouroboros.

## Первые 5 Минут

1. Определить вход: Web UI, CLI, Telegram, Colab, background consciousness.
2. Зафиксировать текст ошибки и task id.
3. Проверить, это provider failure или runtime failure.
4. Не удалять runtime data root и не сбрасывать git state без явного решения.
5. Если есть риск секрета в логах, не копировать полный лог наружу.

## Где Смотреть

```text
data/settings.json
data/logs/
data/task_results/
data/state/
data/observability/
data/memory/
```

В локальной среде пользователя runtime root часто:

```text
/Users/amadey/Ouroboros
```

Это не source repo.

## Быстрые Команды

```bash
git status --short
python3 -m pytest tests/ -q --tb=short
git diff --check
python -m ouroboros.groq_api_smoke
```

Запуск:

```bash
ouroboros server --host 127.0.0.1 --port 8765
python server.py
python launcher.py
```

## Docker

Сначала собрать image из текущего каталога:
```bash
docker build -t ouroboros-web .

docker run --rm -p 8765:8765 \
  -e OUROBOROS_SERVER_HOST=0.0.0.0 \
  ouroboros-web
```

Если нужен запуск сразу в Telegram, используй compose-режим с `telegram` launcher.
Он поднимает `server`, ждёт `/api/health`, ставит и включает `telegram-bridge`,
а затем выставляет `TELEGRAM_COMMAND_MODE=full_access`.

```bash
  docker compose down
  docker compose up -d --build
  docker compose logs -f ouroboros
```

  В этом режиме контейнер сам стартует в Telegram-launcher и должен сам поднять bridge. После этого exec уже будет работать.

  Если нужен разовый запуск в foreground, используй без лишнего аргумента:

```bash
  docker compose run --rm --service-ports ouroboros
```

Для веб-режима просто переопредели команду обратно на `server`.

### Проверка

Посмотреть локальные images:
```bash
docker images | grep ouroboros
```

Если image есть, увидишь что-то вроде:

ouroboros-web   latest   ...

## Provider Errors

### 400 Bad Request / tool_use failed

Вероятные причины:

- compatible provider не поддержал tool calling;
- tool schema/payload не принят провайдером;
- модель напечатала tool call как текст.

Что делать:

- для digest проверить direct route `research_digest.prepare_digest`;
- проверить `OPENAI_COMPATIBLE_BASE_URL` и model id;
- уменьшить сложность tool schema или отключить provider-native tool calling
  для конкретного route.

### 413 Request Entity Too Large

Вероятные причины:

- слишком большой context;
- слишком большой tool result;
- safety/review prompt получил большой payload;
- `OPENAI_COMPATIBLE_MAX_TOKENS=0`.

Что делать:

```text
OUROBOROS_CONTEXT_MODE=low
OUROBOROS_MINIMAL_CONTEXT=true
OPENAI_COMPATIBLE_CONTEXT_LENGTH=8192
OPENAI_COMPATIBLE_MAX_TOKENS=128
```

Для локального Ollama можно выше, но синхронизировать с `num_ctx`.

### 429 Rate Limit

Вероятные причины:

- TPM/RPM quota исчерпана;
- fallback находится на той же quota pool;
- background consciousness или subagents сжигают токены;
- tool result слишком большой.

Что делать:

- снизить worker/subagent concurrency;
- отключить/успокоить background consciousness;
- держать reserve на другом провайдере;
- не считать смену Groq model внутри того же аккаунта полноценным reserve.

## Telegram/Digest Инциденты

Нормальный результат:

```text
Telegram -> agent -> research_digest.prepare_digest -> compact final_response
```

Если пользователь видит `<ext_...>{...}`:

- это UX/parser failure;
- проверить parser text tool calls и direct digest detector;
- не просить пользователя вручную вводить tool JSON.

Если digest падает на 413:

- проверить, что safety bypass применен только к trusted
  `research_digest.prepare_digest`;
- проверить compact output;
- проверить, что refresh payload не отправляется обратно в Groq.

Если digest падает на 429:

- проверить provider quota;
- использовать reserve на другом quota pool;
- временно снизить background tasks.

## Server/Gateway

Симптомы:

- UI не открывается;
- CLI не может подключиться;
- WebSocket не получает события;
- task висит без прогресса.

Проверки:

```bash
ps aux | rg 'server.py|uvicorn|ouroboros'
cat data/state/server_port
```

Что смотреть:

- `OUROBOROS_SERVER_HOST`;
- порт 8765 или override;
- `OUROBOROS_NETWORK_PASSWORD` при non-localhost;
- supervisor state и worker events.

## Skills/Extensions

Симптомы:

- skill включен, но tool не появляется;
- extension сломан после restart;
- companion process не стартует.

Проверки:

```text
data/state/skills/<skill>/review.json
data/state/skills/<skill>/enabled.json
data/state/skills/<skill>/health.json
data/state/extension_reconcile/
```

Что делать:

- запустить `skill_preflight`;
- запустить `skill_review`;
- проверить content hash;
- проверить grants;
- перезапустить server, если нужен reconcile companion process.

## Git/Release

Перед push/release:

```bash
git status --short
git diff --check
python3 -m pytest tests/ -q --tb=short
```

Для packaging:

```bash
bash build.sh
bash build_linux.sh
powershell -ExecutionPolicy Bypass -File build_windows.ps1
```

Не использовать `git reset --hard` или destructive cleanup без явного решения.

## Escalation Checklist

Эскалировать как архитектурную проблему, если:

- нужен новый provider fallback/reserve policy;
- меняется `ToolEntry`/`ToolContext`;
- меняется gateway contract;
- меняется trust boundary skills/extensions;
- требуется safety bypass;
- требуется снижение review/scope coverage;
- runtime data layout меняется несовместимо.
