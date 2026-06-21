# ADR 0001: Runtime Boundaries

Date: 2026-06-21
Status: accepted

## Context

Ouroboros работает одновременно как desktop/web runtime, CLI/headless агент,
Colab/Telegram runtime и self-modifying source repo. Без явного разделения
source checkout, runtime data и browser-facing API легко смешать секреты,
логи, память и исходный код.

## Decision

- `server.py` владеет runtime process lifecycle.
- `ouroboros/gateway/` владеет HTTP/WS контрактом.
- `supervisor/` владеет queue, workers, events и persistent state.
- `ouroboros/agent.py` и task pipeline исполняются внутри worker context.
- Runtime data root (`~/Ouroboros/data`) не является source repo и не должен
  коммититься.

## Consequences

- UI/CLI/transport skills должны идти через gateway/supervisor, а не напрямую
  мутировать runtime internals.
- Debugging должен начинаться с task result/logs/state в runtime data root.
- Документация должна явно различать source repo и `/Users/amadey/Ouroboros`
  как локальный runtime каталог.

