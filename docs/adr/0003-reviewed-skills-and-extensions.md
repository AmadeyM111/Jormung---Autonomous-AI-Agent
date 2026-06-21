# ADR 0003: Reviewed Skills And Extensions

Date: 2026-06-21
Status: accepted

## Context

Skills и extensions расширяют агента внешним кодом, UI, transport bridges,
tools и companion processes. Это полезно только если payload, grants и
execution mode остаются проверяемыми и воспроизводимыми.

## Decision

- Исполнение skill требует свежего executable review и совпадающего content hash.
- Grants owner-controlled и не должны неявно передавать секреты.
- Isolated/native extensions исполняются через child process surface, когда
  in-process импорт опасен или невозможен.
- Companion processes регистрируются как reviewed surface и управляются host
  side lifecycle, а worker-side изменения проходят через reconcile markers.
- Extension health хранится durably в runtime state.

## Consequences

- Runtime может переживать restart и восстанавливать companion state.
- Skill install/update/review/enable/disable должен идти через lifecycle queue.
- Любой bypass review/grants/content hash считается trust-boundary изменением и
  требует отдельного ADR/review.

