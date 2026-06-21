# Changelog

История изменений проекта. Старые подробные release rows также сохранены в
`README.md`, git tags и GitHub Releases.

## Unreleased

### Added

- Added Russian operational docs:
  - `docs/architecture.md`
  - `docs/tools.md`
  - `docs/evaluation.md`
  - `docs/runbook.md`
  - `docs/adr/`
- Added critical component maps:
  - `docs/CRITICAL_AGENT_COMPONENTS.md`
  - `docs/CRITICAL_AGENT_COMPONENTS_RU.md`

### Changed

- Documented Groq/Telegram digest failure modes: 400 tool-use failure, 413
  oversized request, and 429 provider rate limit.
- Documented primary/fallback/reserve model chain and context/output budget
  alignment for OpenAI-compatible and Ollama routes.

## 6.18.1 - 2026-06-06

### Fixed

- Kept macOS signing compatible with mobile browser checks.
- macOS packages bundle Chromium headless shell and use managed Playwright
  cache for first-use WebKit downloads.

## 6.18.0 - 2026-06-05

### Added

- Browser tools support explicit `engine=chromium|webkit` and Playwright
  device descriptors.
- Chat composer responsive toolbar and mobile control layout.
- Persisted owner-local widget card ordering.

## 6.17.0 - 2026-06-04

### Changed

- Task results moved to typed task contract, outcome axes and verification
  ledger.
- Headless/API/CLI tasks gained explicit deadlines, finalization grace and
  public result projection.

## 6.16.0 - 2026-06-04

### Added

- Server-side reconciliation for worker-enabled/disabled companion processes.

## 6.15.0 - 2026-06-04

### Added

- Out-of-process extension parity and durable extension health state.
- Host Service bridge for reviewed extension callbacks.

