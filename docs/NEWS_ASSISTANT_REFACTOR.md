# News Assistant Refactor Notes

The current Ouroboros codebase is optimized for a self-modifying desktop agent,
not for a narrow information-gathering assistant. For AI/ML/news monitoring, keep
the runtime stable and move domain behavior into reviewed skills.

## Keep

- `supervisor/queue.py` and `ouroboros/gateway/schedules.py`: already provide
  cron-backed queued tasks.
- `ouroboros/extension_loader.py` and `contracts/plugin_api.py`: good boundary
  for source collectors, widgets, and tools.
- `ouroboros/tools/search.py`: useful for occasional web search enrichment when
  official OpenAI search credentials are configured.
- `memory.py`, `task_results.py`, and event logs: useful for durable digests and
  traceability.

## De-emphasize for this product profile

- Autonomous self-modification/evolution campaigns.
- Deep review/subagent machinery for routine feed refreshes.
- Marketplace/public skill publishing flows.
- Heavy desktop packaging paths until the ingestion workflow is stable.

## First implemented slice

`skills/research_digest` adds RSS/Atom and public Telegram web-page ingestion,
deduplication, topic scoring, a digest widget, tools, and a daily scheduled
refresh reminder. It is intentionally a skill rather than core code so it can be
iterated or replaced without destabilizing the base runtime.

## Next slices

1. Add a Telegram publish skill or connect an existing Telegram bridge so digest
   markdown can be posted to the owner's channel after review.
2. Add per-topic source profiles, for example `research`, `business_adoption`,
   and `engineering`.
3. Add summarization/enrichment using the configured LLM only after deterministic
   collection and deduplication are working.
4. Add source health metrics: last success, failures, item counts, and stale
   source detection.
