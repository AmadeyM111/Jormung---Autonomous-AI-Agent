# Research digest

This skill is the focused data-ingestion layer for an AI/ML research and
business-adoption news assistant.

It keeps the base Ouroboros runtime untouched and adds:

- RSS and Atom feed collection.
- Public Telegram channel page collection through `https://t.me/s/<channel>`.
- URL/title-based deduplication.
- Topic scoring for AI, engineering, ML in production, agentic systems,
  business adoption, and research.
- Agent-callable tools for refresh, compact digest generation, one-shot
  Telegram-ready digest preparation, source listing, and source updates.
- A daily schedule entry that reminds the agent to refresh and summarize.

The skill stores only its own state under `api.get_state_dir()`. It does not
request provider keys and does not publish to Telegram by itself. For Telegram
requests, use the `prepare_digest` tool and return its `final_response` directly;
this avoids an extra LLM rewrite on low-TPM providers.
