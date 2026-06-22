# Architecture Decision Records

ADR фиксируют решения, которые меняют долгоживущую архитектуру, trust boundary,
runtime data layout, provider policy, tool ABI или review/safety gate.

## Формат

```text
# ADR NNNN: Название

Date: YYYY-MM-DD
Status: proposed|accepted|superseded

## Context
## Decision
## Consequences
```

## Индекс

- `0001-runtime-boundaries.md` - границы source repo, gateway, supervisor и runtime data.
- `0002-provider-chain-and-context-budget.md` - primary/fallback/reserve и контекстный бюджет.
- `0003-reviewed-skills-and-extensions.md` - reviewed skills, extensions, grants и companion lifecycle.
- `0004-local-telegram-runtime-launcher.md` - локальный Telegram runtime launcher vs Colab bootstrap.
