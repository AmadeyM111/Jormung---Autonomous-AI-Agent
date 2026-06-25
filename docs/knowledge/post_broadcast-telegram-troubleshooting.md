# Post Broadcast And Telegram Troubleshooting

This page records the concrete failure modes we hit while bringing
`post_broadcast` and the Telegram runtime online.

## 1. `post_broadcast` Shows As `External`

Symptoms:

- Skills UI shows `post_broadcast External`.
- Review state says `Review failed`.
- The UI asks for `TELEGRAM_BOT_TOKEN` access even though the skill was already repaired.

Root cause:

- The runtime was reading a different data root than the one we repaired.
- `~/Ouroboros/data` and repository `./data` can diverge.
- For native payloads, the loader treats `data/skills/native/<name>` as native only when `.seed-origin` exists.
- Missing `.seed-origin` makes the same payload classify as `External`, which changes the review/grant hash surface.

Fix:

- Repair the data root that the running server actually uses.
- Keep `skills/post_broadcast/*` and `data/skills/native/post_broadcast/*` in sync when testing Docker.
- Restore `.seed-origin` before writing the final review/grant state.
- Refresh Skills after the runtime restarts; do not use `Re-review` as a recovery step.

## 2. `TELEGRAM_BOT_TOKEN is missing`

Symptoms:

- Launch log says `TELEGRAM_BOT_TOKEN is missing` or `bridge will install but cannot poll Telegram`.
- The bridge installs but cannot poll Telegram.

Root cause:

- Docker receives `.env` through `env_file`.
- A plain local `python -m ...` launch does not read `.env` automatically.
- The launcher only sees values that are in `os.environ` or in the active `settings.json`.

Fix:

- For local runs, use the `ouroboros telegram` launcher, which now reads the repo `.env` fallback.
- Keep `TELEGRAM_BOT_TOKEN` in the active `settings.json` if you want the setting to persist.
- For Docker runs, keep the token in `.env` and let compose inject it.

## 3. `enable failed: cannot enable until review status is a fresh executable review`

Symptoms:

- The runtime shuts down after enable.
- The log says the skill cannot enable until review is fresh and executable.

Root cause:

- `review.json` is stale relative to the current content hash.
- `review_job.json` can still point at an older failed hash.
- Re-running the generic review can keep returning the same parse/quorum failure.

Fix:

- Reconcile the skill state first.
- Use the local bootstrap fallback for bundled skills.
- Refresh the skill after the reviewed hash and grants are written.

## 4. `telegram-bridge is not a verified official OuroborosHub payload`

Symptoms:

- Official bridge bootstrap fallback refuses to run.
- The bridge review remains stale or failed.

Root cause:

- The installed bridge payload does not match the verified OuroborosHub sidecar/hash contract.
- The `official_hub` fast-path only applies when the installed files exactly match the catalog and sidecar markers.

Fix:

- Reinstall or restore the official `telegram-bridge` payload.
- Preserve the `.ouroboroshub.json` marker and the verified file hashes.
- If the local patch is intentional, make sure the patched payload is the one the bootstrap expects and re-review from that exact content hash.
