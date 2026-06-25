# ADR 0004: Local Telegram Runtime Launcher

Date: 2026-06-22
Status: accepted

## Context

Telegram startup for this project originally lived in
`notebooks/colab_quickstart.py`. That path is correct for Google Colab because
it owns Drive mounting, ephemeral checkout refresh, Colab secret collection, and
the owner bootstrap sequence.

The same script is not a valid local Docker entrypoint:

- it hard-depends on `google.colab` and `/content`;
- it mixes source bootstrap, provider setup, skill patching, and bridge
  enablement in one Colab-only flow;
- it makes the local runtime depend on notebook semantics instead of a stable
  CLI contract.

The actual Telegram runtime requirement is simpler: start the server, make the
Telegram bridge skill available, review/enable it, and switch the bridge to
`full_access`.

## Decision

- Keep `notebooks/colab_quickstart.py` as the Colab-only bootstrap path.
- Add a dedicated local launcher in the runtime CLI for Telegram:
  `ouroboros telegram`.
- Make Docker use that launcher by default when the container is intended to
  come up directly in Telegram mode.
- Keep the launcher responsible only for local runtime orchestration:
  server start, bridge bootstrap, bridge enablement, and command mode setup.
- Keep skill discovery pointing at the runtime data plane, including an optional
  `OUROBOROS_SKILLS_REPO_PATH` checkout when present.
- For local development, let the launcher read the repo `.env` fallback so
  `TELEGRAM_BOT_TOKEN` reaches the active settings store without a manual shell
  export. Docker still owns `.env` through `env_file`, but the CLI launcher must
  also make the token available when started directly from the workspace.

## Consequences

- Telegram can now be started from Docker without Colab-specific dependencies.
- The Colab bootstrap remains the right place for Drive/fork/personal origin
  setup and notebook ergonomics.
- Bridge bootstrap logic is shared, but the execution surface is separated:
  Colab remains notebook bootstrap; Docker/runtime uses the CLI launcher.
- If the bridge review flow changes again, the local launcher is the place to
  adapt the runtime orchestration, while the notebook script stays Colab-only.
