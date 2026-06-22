# ADR 0005: Minimal-Context Digest Routing

Date: 2026-06-22
Status: accepted

## Context

In minimal-context mode the agent may run on a provider with a very small TPM or
context budget. The context builder therefore trims memory and repo context and
adds a conservative instruction not to claim access to full memory, tools, or
live web data unless those are actually available in the turn.

That conservative instruction created a real user-facing failure mode for
Telegram/news digest requests. A valid request such as "prepare a digest" could
receive a refusal like:

```text
К сожалению, в моем текущем режиме minimal-context нет возможности подготовить
дайджест с актуальной информацией из внешних источников...
```

The refusal was wrong when the reviewed `research_digest` extension was live.
The model was seeing the minimal-context warning more strongly than the
available source-data route, or the route was not visible/ready early enough in
the local Telegram runtime.

The bug has four distinct causes:

- Minimal-context prompts discouraged live-source claims but did not clearly
  separate ordinary chat from explicit digest/source-data requests.
- Minimal-context tool schemas were too narrow for practical work and could hide
  the reviewed digest extension from the model.
- The common Telegram digest request depended on fragile provider tool-calling
  even though runtime intent detection can safely route it directly.
- Local Telegram startup enabled the bridge but did not ensure the digest skill
  was live before digest traffic arrived.

## Decision

- Keep minimal-context conservative, but scope the warning: for explicit
  fresh-news, RSS/Atom, Telegram-source, or research-digest requests the agent
  must use an available source-data tool instead of refusing or guessing from
  memory.
- Expose a tiny practical minimal-context tool allowlist:
  `read_file`, `list_files`, `write_file`, `edit_text`, `search_code`, plus live
  reviewed extension tools from `OUROBOROS_MINIMAL_CONTEXT_TOOLS`
  (`research_digest,duckduckgo` by default).
- Add a direct runtime route for explicit digest intent in minimal-context mode:
  detect the latest owner message, find the live
  `research_digest.*prepare_digest` extension tool, execute it with the standard
  Telegram digest arguments, and return the extension's direct final response.
- Mark only that direct `research_digest.prepare_digest` dispatch as trusted for
  skipping the extra LLM safety check. The extension still must be live and
  allowed for the current capability root.
- Make the local Telegram launcher call `ensure_research_digest_live` after the
  bridge bootstrap. A digest bootstrap failure is a warning, not a bridge
  startup failure, because Telegram chat should still come up.

## Consequences

- Explicit digest requests no longer depend on a small model deciding to call
  the correct tool; the runtime handles the stable intent route.
- Minimal-context ordinary chat remains terse and does not mention source tools
  unnecessarily.
- If the digest extension is absent, disabled, or not live, the system can now
  fail for the real reason instead of producing a generic minimal-context
  refusal.
- Future news/source skills should follow the same pattern only when the intent
  is narrow, audited, and mapped to a reviewed direct-response tool. Broad web
  research must not be silently routed through a hard-coded bypass.
- Regression tests should cover both sides: digest intent triggers the direct
  route, while non-digest mentions of `research_digest` do not execute it.
