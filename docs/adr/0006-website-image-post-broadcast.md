# ADR 0006: Website Image Post Broadcast Skill

Date: 2026-06-24
Status: accepted

## Context

The owner needs an automated Telegram broadcast skill that:

- collects posts from websites, starting with a public Pinterest board;
- requires an image for every post;
- sends to configured Telegram chats;
- runs by cron from 10:00 through 19:00 Moscow time every three hours;
- sends one post per run;
- uses an LLM rewrite in an intellectually funny Russian style;
- always includes the source link;
- does not reuse `research_digest`.

The primary source is a Pinterest board:

```text
https://ru.pinterest.com/odinokayabulka/%D0%BC%D0%B5%D0%BC%D1%8B-%D1%81-%D0%BA%D0%BE%D1%82%D0%B0%D0%BC%D0%B8/
```

Pinterest is a fragile website source: it is JavaScript-heavy, may change page
shape, and may restrict unauthenticated scraping. A generic CSS-selector
extractor would be brittle for this first version.

The existing `send_photo` core tool is not enough for this feature because it
targets the current active chat. Broadcast needs configured chat IDs and should
use the Telegram Bot API directly with `TELEGRAM_BOT_TOKEN`, without storing the
token in skill state.

## Decision

- Add a native extension skill named `post_broadcast`.
- Store configuration and durable state under the skill state directory:
  `config.json`, `records.json`, `prepared.json`, and downloaded `images/`.
- Implement a first source adapter for `kind=pinterest_board`.
- Extract image URLs from public Pinterest HTML without login bypasses or
  browser automation.
- Require images and skip broken or undersized images by default.
- Deduplicate records by stable fingerprint from source URL, image URL, title,
  and pin URL.
- Split the cron workflow into two tools:
  - `prepare_next(refresh=true)` fetches sources, downloads one image, and
    returns source text plus image metadata for the agent.
  - `send_prepared(prepared_id, caption)` sends the already prepared image with
    the LLM-rewritten caption.
- Keep LLM rewrite in the scheduled agent workflow rather than inside the
  extension, because extension tools should not create their own hidden model
  route or store provider credentials.
- Send Telegram photos with the Bot API and configured `chat_ids`.
- Use cron `0 10-19/3 * * *`, which runs at 10:00, 13:00, 16:00, and 19:00
  Europe/Moscow.

## Consequences

- The broadcast path is deterministic around fetch, image validation, state, and
  Telegram delivery, while the caption rewrite remains an explicit agent/LLM
  step.
- Missing `TELEGRAM_BOT_TOKEN` or empty `telegram.chat_ids` fails closed before
  delivery.
- Pinterest extraction may need adapter maintenance if Pinterest changes public
  HTML. The skill should report extraction errors instead of sending empty or
  source-less posts.
- The image cache must create the `images/` directory before writing downloads;
  tests cover this because an earlier implementation created only the parent
  state directory.
- Runtime registration depends on the same Python environment that starts the
  server having manifest dependencies such as `PyYAML` and `croniter`; otherwise
  the skill is discovered only as a broken manifest entry and will not become a
  usable extension.
- Groq reviewer slots can fail non-semantically for this payload by returning
  template/example JSON or "manifest not provided" instead of parseable findings.
  For the bundled native `post_broadcast` copy, local bootstrap may write a
  narrow clean review only after verifying native provenance, exact permissions,
  and the explicit `TELEGRAM_BOT_TOKEN` settings grant contract.
- Telegram delivery must request `TELEGRAM_BOT_TOKEN` explicitly through
  `env_from_settings` plus `read_settings`; silently reading global process env
  would bypass the reviewed skill-grant model.
- Keep timestamp helper names stable across the repo and runtime copies. A
  `_utc_now` to `_moscow_now` rename without updating call sites broke
  `refresh`, `prepare_next`, and `send_prepared`; the compatibility fix keeps
  `_utc_now()` as a wrapper around the configured Moscow-time timestamp helper.
- Runtime state can exist in multiple stores. Local desktop defaults to
  `~/Ouroboros/data`, while `docker-compose.yml` mounts repository `./data` as
  `/ouroboros/data`. A repair applied only to `~/Ouroboros/data` left Docker UI
  reading the old `post_broadcast` payload and old `content_hash=640e...`.
  Repair must target the data dir used by the running server, and the Docker
  mounted `./data/skills/native/post_broadcast` copy must be kept in sync with
  the bundled `skills/post_broadcast` payload when testing Docker UI.
- A payload under `data/skills/native/<name>` is classified as native only when
  it carries `.seed-origin`. If that marker is missing, the loader deliberately
  reports the skill as `External`; review/grants then bind to a different
  content hash and the UI can show both `Review failed` and `Needs access` even
  after a bundled bootstrap repair. The repair path must restore `.seed-origin`,
  then re-read the skill and write review/grants for the final hash.
- `.env` is not a universal runtime settings store. Docker imports it through
  `env_file`, and the local `telegram_bootstrap` launcher now also reads the
  repo `.env` fallback so `TELEGRAM_BOT_TOKEN` reaches the active
  `settings.json` without manual shell export. Before that fix, a local launch
  using `~/Ouroboros/data` failed with `TELEGRAM_BOT_TOKEN is missing` unless
  the token was exported before launch or already saved into the active
  settings store.
- The bootstrap fallback must not mutate native skill payload metadata after it
  has calculated the reviewed content hash. Creating `.seed-origin` after
  `find_skill()` caused the first clean review to be immediately stale. The
  fallback now re-reads the skill after repairing the marker so review, grants,
  and enablement bind to the final content hash.
- Future website sources should add source-specific adapters when HTML is
  dynamic or fragile. Generic selector mode can be added later for simple
  static sites.
