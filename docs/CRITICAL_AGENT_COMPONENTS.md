# Critical Agent Components

This document is an operational map of the critical Ouroboros agent nodes:
which files own them, what they depend on, what they emit, and what usually
breaks when they fail. It complements `docs/ARCHITECTURE.md`; it is optimized
for debugging, runtime setup, and change-risk assessment.

## 1. Runtime Shape

```text
User / Telegram / CLI / Web UI
  |
  v
launcher.py or ouroboros.cli
  |
  v
server.py
  |
  +-- web/                         browser SPA
  +-- ouroboros/gateway/           HTTP, WebSocket, settings, tasks, skills
  +-- supervisor/                  queue, worker pool, persistent state, events
  |     |
  |     v
  |   worker process
  |     |
  |     v
  +-- ouroboros/agent.py           task orchestrator
        |
        +-- context/memory
        +-- llm routing
        +-- tool loop
        +-- safety/review gates
        +-- skill/extension tools
        +-- task result finalization
```

The local runtime data root is normally `~/Ouroboros/data`. In this developer
environment `/Users/amadey/Ouroboros` is runtime data, not the source checkout.
Do not commit that directory.

## 2. Criticality Tiers

Tier 0 means the agent cannot run or can corrupt trust boundaries if broken.
Tier 1 means core tasks, Telegram, or tool execution degrade heavily.
Tier 2 means build, packaging, or developer feedback loops break.

| Tier | Node | Primary files | Why critical |
| --- | --- | --- | --- |
| 0 | Settings and paths | `ouroboros/config.py` | SSOT for app root, data root, model settings, keys, ports, locks. |
| 0 | Server process | `server.py`, `ouroboros/server_entrypoint.py`, `ouroboros/server_runtime.py` | Owns HTTP/WS runtime, supervisor hosting, startup lifecycle. |
| 0 | Gateway boundary | `ouroboros/gateway/router.py`, `ouroboros/gateway/contracts.py` | Browser/CLI API contract and all `/api/*` routes. |
| 0 | Supervisor | `supervisor/*.py` | Queue, worker pool, state, events, git operations. |
| 0 | Agent task pipeline | `ouroboros/agent.py`, `ouroboros/agent_task_pipeline.py` | Turns a user request into loop execution and final result. |
| 0 | LLM provider routing | `ouroboros/llm.py`, `ouroboros/provider_models.py` | Model selection, fallback/reserve chain, provider payload shaping. |
| 0 | Tool loop | `ouroboros/loop.py`, `ouroboros/loop_llm_call.py`, `ouroboros/loop_tool_execution.py` | Repeated LLM/tool execution and tool-result handling. |
| 0 | Safety policy | `ouroboros/safety.py`, `ouroboros/runtime_mode_policy.py`, `ouroboros/tool_policy.py` | Prevents unsafe operations and protected-path mutation. |
| 0 | Memory/context | `ouroboros/context.py`, `ouroboros/memory.py`, `ouroboros/context_budget.py` | Builds model messages and controls context size. |
| 1 | Skills/extensions | `ouroboros/skill_loader.py`, `ouroboros/extension_loader.py`, `skills/*` | External capability loading and reviewed tool surfaces. |
| 1 | Telegram/Colab bootstrap | `ouroboros/colab_bootstrap.py`, `notebooks/colab_quickstart.py` | Source-mode Colab runtime, Telegram bridge setup, Groq profile. |
| 1 | Research digest skill | `skills/research_digest/*` | Digest user flow and direct tool route for Telegram/Groq. |
| 1 | MCP client | `ouroboros/mcp_client.py`, `ouroboros/gateway/mcp.py` | Optional external MCP tools. Disabled by default. |
| 1 | Local model lifecycle | `ouroboros/local_model.py`, `ouroboros/local_model_autostart.py` | Local llama-cpp runtime path. Separate from Ollama-compatible route. |
| 1 | Observability/results | `ouroboros/observability.py`, `ouroboros/task_results.py`, `ouroboros/outcomes.py` | Forensic traces, durable task outputs, verification ledger. |
| 2 | Web UI | `web/index.html`, `web/modules/*`, `web/*.css` | Human-facing control surface; no build step. |
| 2 | Build/release | `build.sh`, `build_linux.sh`, `build_windows.ps1`, `scripts/build_repo_bundle.py` | Packaged app invariants and release artifacts. |
| 2 | Tests/dev tools | `tests/*`, `Makefile`, `scripts/run_external_review.py` | Regression detection and pre-commit review dry-runs. |

## 3. Entrypoints

### Desktop and Web Runtime

| Entrypoint | Owner | Notes |
| --- | --- | --- |
| `python launcher.py` | `launcher.py` | Desktop PyWebView shell; spawns server. |
| `python server.py` | `server.py` | Direct Starlette/uvicorn server. |
| `ouroboros server` | `ouroboros/cli.py` | Source CLI server wrapper. |
| `ouroboros-web` | `pyproject.toml` script | Console script mapped to `server:main`. |
| `Dockerfile` | repo root | Container web runtime, entrypoint is `python server.py`. |

### CLI and Headless

| Entrypoint | Owner | Notes |
| --- | --- | --- |
| `ouroboros run --start "<task>"` | `ouroboros/cli.py` | Starts/attaches to gateway, creates task, streams events. |
| `ouroboros tasks ...` | `ouroboros/cli.py` | Queue/task control over gateway APIs. |
| `ouroboros schedule ...` | `ouroboros/cli.py` | Schedule API wrapper. |
| `packaging/cli/ouroboros` | `packaging/cli/` | Packaged shell wrapper. |
| `packaging/cli/ouroboros.cmd` | `packaging/cli/` | Packaged Windows wrapper. |

### Colab and Telegram

| Entrypoint | Owner | Notes |
| --- | --- | --- |
| `notebooks/colab_quickstart.py` | notebook script | Colab cell script; mounts Drive, installs repo, starts no-UI server. |
| `notebooks/colab_quickstart.ipynb` | notebook | Notebook wrapper around the script. |
| `ouroboros/colab_bootstrap.py` | bootstrap helpers | Applies Groq profile, Telegram bridge setup, Drive-persisted settings. |

## 4. Core Runtime Nodes

### Settings and Data Root

Primary files:

- `ouroboros/config.py`
- `ouroboros/settings_setup_contract.py`
- `ouroboros/onboarding_wizard.py`
- `ouroboros/gateway/settings.py`

Critical settings:

```text
OUROBOROS_APP_ROOT
OUROBOROS_REPO_DIR
OUROBOROS_DATA_DIR
OUROBOROS_SETTINGS_PATH
OUROBOROS_PORT_FILE
OUROBOROS_SERVER_HOST
OUROBOROS_NETWORK_PASSWORD
```

Model/provider settings:

```text
OUROBOROS_MODEL
OUROBOROS_MODEL_CODE
OUROBOROS_MODEL_LIGHT
OUROBOROS_MODEL_CONSCIOUSNESS
OUROBOROS_MODEL_DEEP_SELF_REVIEW
OUROBOROS_MODEL_FALLBACK
OUROBOROS_MODEL_RESERVE
OUROBOROS_RESERVE_MODEL
OPENAI_COMPATIBLE_BASE_URL
OPENAI_COMPATIBLE_API_KEY
OPENAI_COMPATIBLE_CONTEXT_LENGTH
OPENAI_COMPATIBLE_MAX_TOKENS
OPENROUTER_API_KEY
OPENAI_API_KEY
ANTHROPIC_API_KEY
GITHUB_TOKEN
```

Risk:

- Wrong data root can mix source checkout and runtime state.
- Wrong provider key/base URL causes model outage.
- Context/token mismatch causes empty responses, provider 400/413, or rate-limit
  amplification.

### Server and Gateway

Primary files:

- `server.py`
- `ouroboros/server_entrypoint.py`
- `ouroboros/server_runtime.py`
- `ouroboros/server_web.py`
- `ouroboros/server_auth.py`
- `ouroboros/gateway/router.py`
- `ouroboros/gateway/contracts.py`

Critical routes/modules:

- `ouroboros/gateway/tasks.py` - create/list/get/cancel task endpoints.
- `ouroboros/gateway/ws.py` - WebSocket events and extension WS dispatch.
- `ouroboros/gateway/settings.py` - settings and onboarding endpoints.
- `ouroboros/gateway/extensions.py` - skill install/toggle/review/grants.
- `ouroboros/gateway/models.py` - model catalog and local-model lifecycle.
- `ouroboros/gateway/mcp.py` - MCP settings surface.
- `ouroboros/gateway/host_service.py` - loopback-only reviewed skill callbacks.

Risk:

- Route/contract drift breaks Web UI and CLI simultaneously.
- Non-localhost exposure without `OUROBOROS_NETWORK_PASSWORD` is unsafe.
- Server lifecycle bugs can orphan workers, companions, or port files.

### Supervisor

Primary files:

- `supervisor/message_bus.py`
- `supervisor/queue.py`
- `supervisor/workers.py`
- `supervisor/state.py`
- `supervisor/events.py`
- `supervisor/git_ops.py`

Responsibilities:

- Accept queue tasks from gateway.
- Run worker processes.
- Persist task and runtime state.
- Relay progress/events to Web UI, CLI, and transport skills.
- Own git operations for managed repo workflows.

Risk:

- Queue/state corruption loses tasks.
- Worker lifecycle bugs can duplicate or drop execution.
- Event loss makes Telegram/CLI appear frozen even if the task runs.

### Agent Task Pipeline

Primary files:

- `ouroboros/agent.py`
- `ouroboros/agent_startup_checks.py`
- `ouroboros/agent_task_pipeline.py`
- `ouroboros/task_results.py`
- `ouroboros/task_status.py`
- `ouroboros/task_continuation.py`
- `ouroboros/outcomes.py`

Responsibilities:

- Normalize task request.
- Load memory/context.
- Select model lane.
- Run loop.
- Finalize durable task result.
- Emit outcome and verification metadata.

Risk:

- Final result can be missing while tools actually ran.
- Restart/outage continuation can repeat or drop work if state is inconsistent.
- User-visible errors often surface here even when root cause is provider,
  context, or safety.

## 5. Model and Context Path

### Context Builder

Primary files:

- `ouroboros/context.py`
- `ouroboros/context_budget.py`
- `ouroboros/context_layout.py`
- `ouroboros/context_compaction.py`
- `ouroboros/memory.py`
- `ouroboros/consolidator.py`

Important parameters:

```text
OUROBOROS_CONTEXT_MODE=max|low
OUROBOROS_MINIMAL_CONTEXT=true|false
OPENAI_COMPATIBLE_CONTEXT_LENGTH
OPENAI_COMPATIBLE_MAX_TOKENS
LOCAL_MODEL_CONTEXT_LENGTH
```

Risk:

- Too much context causes request-too-large errors.
- Too little context causes identity/tool/source confusion.
- `OPENAI_COMPATIBLE_MAX_TOKENS=0` disables the compatible-provider output cap.

### LLM Router

Primary files:

- `ouroboros/llm.py`
- `ouroboros/loop_llm_call.py`
- `ouroboros/provider_models.py`
- `ouroboros/llm_observability.py`
- `ouroboros/pricing.py`

Model chain:

```text
primary:  OUROBOROS_MODEL
fallback: OUROBOROS_MODEL_FALLBACK
reserve:  OUROBOROS_MODEL_RESERVE or OUROBOROS_RESERVE_MODEL
```

Important compatible-provider settings:

```text
OPENAI_COMPATIBLE_BASE_URL
OPENAI_COMPATIBLE_API_KEY
OPENAI_COMPATIBLE_CONTEXT_LENGTH
OPENAI_COMPATIBLE_MAX_TOKENS
```

Risk:

- Fallback on the same provider/quota pool does not fix provider-wide 429.
- Some compatible providers reject tool-calling payloads or optional sampling
  parameters.
- Token-per-minute limits can be exceeded by tool results and safety prompts,
  not just by the user's message.

### Local Model Runtime

Primary files:

- `ouroboros/local_model.py`
- `ouroboros/local_model_autostart.py`
- `ouroboros/gateway/models.py`

Settings:

```text
USE_LOCAL_MAIN
USE_LOCAL_CODE
USE_LOCAL_LIGHT
USE_LOCAL_CONSCIOUSNESS
USE_LOCAL_FALLBACK
LOCAL_MODEL_SOURCE
LOCAL_MODEL_FILENAME
LOCAL_MODEL_PORT
LOCAL_MODEL_N_GPU_LAYERS
LOCAL_MODEL_CONTEXT_LENGTH
LOCAL_MODEL_CHAT_FORMAT
```

For Ollama through the OpenAI-compatible route, keep these aligned:

```text
PARAMETER num_ctx 32768
PARAMETER num_predict 2048
OPENAI_COMPATIBLE_CONTEXT_LENGTH=32768
OPENAI_COMPATIBLE_MAX_TOKENS=2048
```

Risk:

- `LOCAL_MODEL_CONTEXT_LENGTH` controls built-in local runtime assumptions.
- Ollama `num_ctx` is external to this repo and must be configured in the model
  runtime or Modelfile.
- If the provider context length is smaller than the prompt builder assumes,
  responses can be empty or truncated.

## 6. Tool, Safety, and Review Nodes

### Tool Loop and Registry Surface

Primary files:

- `ouroboros/loop.py`
- `ouroboros/loop_tool_execution.py`
- `ouroboros/tool_capabilities.py`
- `ouroboros/tool_access.py`
- `ouroboros/tool_policy.py`
- `ouroboros/tools/*`

Responsibilities:

- Expose allowed tools to model.
- Execute tool calls.
- Enforce resource-root and runtime-mode restrictions.
- Return bounded tool results to the LLM loop.

Risk:

- Tool result bloat can trigger provider 413/429.
- Tool names leaked into chat are a UX bug unless intentionally surfaced.
- Parallel-safe tool metadata must stay accurate.

### Safety and Runtime Mode

Primary files:

- `ouroboros/safety.py`
- `ouroboros/runtime_mode_policy.py`
- `ouroboros/protected_artifacts.py`
- `ouroboros/git_shell_policy.py`
- `ouroboros/shell_parse.py`

Settings:

```text
OUROBOROS_RUNTIME_MODE=advanced|pro|light
OUROBOROS_TOOL_TIMEOUT_SEC
OUROBOROS_SOFT_TIMEOUT_SEC
OUROBOROS_HARD_TIMEOUT_SEC
OUROBOROS_FINALIZATION_GRACE_SEC
```

Risk:

- Protected prompt/core/release files must not become ordinary writable files.
- Direct digest route has a narrow trusted bypass only for reviewed
  `research_digest.prepare_digest`; broad bypasses are unsafe.

### Code Review and Commit Gate

Primary files:

- `ouroboros/review.py`
- `ouroboros/review_substrate.py`
- `ouroboros/triad_review.py`
- `ouroboros/review_state.py`
- `ouroboros/review_evidence.py`
- `ouroboros/tools/parallel_review.py`
- `ouroboros/tools/scope_review.py`
- `ouroboros/tools/commit_gate.py`
- `ouroboros/preflight_runner.py`

Settings:

```text
OUROBOROS_REVIEW_MODELS
OUROBOROS_REVIEW_ENFORCEMENT
OUROBOROS_SCOPE_REVIEW_MODEL
OUROBOROS_SCOPE_REVIEW_MODELS
OUROBOROS_SCOPE_REVIEW_DEGRADED
OUROBOROS_TASK_REVIEW_MODE
```

Risk:

- Review gates are part of the self-modification trust boundary.
- Provider outage can block reviewed commits unless degraded/advisory behavior
  is explicitly allowed.

## 7. Skills, Extensions, and Marketplace

### Skill Loading and Execution

Primary files:

- `ouroboros/skill_loader.py`
- `ouroboros/skill_readiness.py`
- `ouroboros/skill_dependencies.py`
- `ouroboros/skill_lifecycle_queue.py`
- `ouroboros/skill_review.py`
- `ouroboros/skill_review_runner.py`
- `ouroboros/skill_review_status.py`
- `ouroboros/tools/skill_exec.py`

Critical data paths:

```text
data/skills/native/
data/skills/external/
data/skills/clawhub/
data/skills/ouroboroshub/
data/state/skills/<skill>/
```

Risk:

- Skill content hash and executable review must stay fresh.
- Skill env should not inherit secrets except through reviewed grants.
- Skill lifecycle actions should remain serialized by the lifecycle queue.

### Extensions and Companions

Primary files:

- `ouroboros/extension_loader.py`
- `ouroboros/extension_process_runner.py`
- `ouroboros/extension_companion.py`
- `ouroboros/extension_reconcile_queue.py`
- `ouroboros/extension_health.py`
- `ouroboros/event_bus.py`
- `ouroboros/contracts/plugin_api.py`

Risk:

- In-process extension import failures must not abort the whole server.
- Companion processes are host-supervised and must clean up on unload/panic.
- Worker-side enable/disable must be reconciled by the server process.

### Marketplace

Primary files:

- `ouroboros/marketplace/*`
- `ouroboros/gateway/marketplace.py`
- `ouroboros/tools/skill_publish.py`

Settings:

```text
OUROBOROS_CLAWHUB_REGISTRY_URL
OUROBOROS_HUB_CATALOG_URL
OUROBOROS_SKILLS_REPO_PATH
```

Risk:

- Provenance sidecars must not be lost.
- Marketplace-installed code still needs review and fresh hash before execution.

## 8. Telegram, Colab, and Research Digest Path

Primary files:

- `ouroboros/colab_bootstrap.py`
- `notebooks/colab_quickstart.py`
- `skills/research_digest/plugin.py`
- `skills/research_digest/skill.json`
- `skills/research_digest/README.md`

Colab/Groq settings:

```text
GROQ_API_KEY
TELEGRAM_BOT_TOKEN
GITHUB_TOKEN
OPENAI_COMPATIBLE_BASE_URL=https://api.groq.com/openai/v1
OUROBOROS_MODEL=openai-compatible::groq/compound
OUROBOROS_MODEL_FALLBACK=openai-compatible::llama-3.1-8b-instant
OPENAI_COMPATIBLE_CONTEXT_LENGTH=8192
OPENAI_COMPATIBLE_MAX_TOKENS=128
OUROBOROS_CONTEXT_MODE=low
OUROBOROS_MINIMAL_CONTEXT=true
OUROBOROS_EFFORT_TASK=low
OUROBOROS_EFFORT_CONSCIOUSNESS=low
```

Digest request path:

```text
Telegram message
  -> transport skill / message bus
  -> supervisor queue
  -> agent task pipeline
  -> minimal-context digest detector
  -> reviewed research_digest.prepare_digest
  -> compact final_response
  -> Telegram reply
```

Known failure modes:

- `429 RateLimitError`: provider quota exhausted. A fallback on the same Groq
  quota pool is not a real reserve.
- `413 Request Entity Too Large`: prompt, tool result, or safety prompt is too
  large for the provider.
- `400 tool_use failed`: compatible provider failed native function calling.
  Digest should use the direct reviewed tool route instead.
- Tool-call text such as `<ext_...>{...}` in chat is a parsing/UX failure; it
  should be converted to a real tool call or hidden behind the direct route.

Operational rule:

- Keep digest output compact and source-diverse.
- Do not send full refresh/digest tool payloads back into Groq.
- Keep `OPENAI_COMPATIBLE_MAX_TOKENS` low on Groq/Colab unless there is a
  measured reason to raise it.

## 9. Memory, Identity, and Background Consciousness

Primary files:

- `BIBLE.md`
- `prompts/SYSTEM.md`
- `prompts/CONSCIOUSNESS.md`
- `ouroboros/memory.py`
- `ouroboros/consciousness.py`
- `ouroboros/reflection.py`
- `ouroboros/consolidator.py`
- `ouroboros/deep_self_review.py`

Settings:

```text
OUROBOROS_BG_MAX_ROUNDS
OUROBOROS_BG_WAKEUP_MIN
OUROBOROS_BG_WAKEUP_MAX
OUROBOROS_MODEL_CONSCIOUSNESS
OUROBOROS_MODEL_DEEP_SELF_REVIEW
OUROBOROS_EFFORT_CONSCIOUSNESS
OUROBOROS_EFFORT_DEEP_SELF_REVIEW
```

Risk:

- Persisted runtime memory can preserve old identity text after source prompts
  are changed.
- Background consciousness can amplify provider spend and rate limits if pointed
  at constrained providers.
- Deep self-review should use a large-context model and is not suitable for
  small Groq/Colab token budgets.

## 10. Observability and Durable Outputs

Primary files:

- `ouroboros/observability.py`
- `ouroboros/llm_observability.py`
- `ouroboros/task_results.py`
- `ouroboros/outcomes.py`
- `ouroboros/artifacts.py`
- `ouroboros/gateway/logs.py`
- `ouroboros/gateway/history.py`

Critical runtime paths:

```text
data/logs/
data/task_results/
data/state/
data/observability/
data/memory/
data/dialogue_blocks.json
```

Risk:

- Logs and traces must redact secrets.
- Task artifacts should be task-scoped, not arbitrary filesystem exposure.
- Losing task results breaks CLI/Telegram continuation and postmortem debugging.

## 11. MCP and External Tool Servers

Primary files:

- `ouroboros/mcp_client.py`
- `ouroboros/gateway/mcp.py`

Settings:

```text
MCP_ENABLED
MCP_SERVERS
MCP_TOOL_TIMEOUT_SEC
```

Risk:

- MCP is disabled by default and should stay explicit.
- Auth headers/tokens must be masked in logs and UI.
- MCP tool names are normalized as `mcp_<server>__<tool>`.

## 12. Build, Release, and Packaging

Primary files:

- `build.sh`
- `build_linux.sh`
- `build_windows.ps1`
- `scripts/build_repo_bundle.py`
- `scripts/download_node_standalone.sh`
- `scripts/download_node_standalone.ps1`
- `scripts/download_python_standalone.sh`
- `scripts/download_python_standalone.ps1`
- `.github/workflows/ci.yml`
- `Dockerfile`
- `pyproject.toml`

Release invariants:

- Build scripts rely on version/tag consistency.
- `scripts/build_repo_bundle.py` is the release bundle SSOT.
- Packaged CLI wrappers live under `packaging/cli/`.
- Build script changes must stay in sync with README and architecture docs.

Risk:

- Packaged app can ship with stale repo bundle if tag/version checks are
  bypassed.
- PyInstaller bytecode/cache policy matters for signed app bundles.

## 13. Test and Diagnostic Commands

Fast local checks:

```bash
python3 -m pytest tests/ -q --tb=short
git diff --check
python -m py_compile ouroboros/context.py ouroboros/memory.py ouroboros/loop.py
```

Makefile wrappers:

```bash
make test
make test-v
make health
make clean
```

Groq smoke:

```bash
python -m ouroboros.groq_api_smoke
```

External review dry-run:

```bash
python scripts/run_external_review.py
```

Release scripts:

```bash
bash build.sh
bash build_linux.sh
powershell -ExecutionPolicy Bypass -File build_windows.ps1
python scripts/build_repo_bundle.py
```

## 14. High-Risk Parameter Groups

### Context and Output Budget

```text
PARAMETER num_ctx
PARAMETER num_predict
LOCAL_MODEL_CONTEXT_LENGTH
OPENAI_COMPATIBLE_CONTEXT_LENGTH
OPENAI_COMPATIBLE_MAX_TOKENS
OUROBOROS_CONTEXT_MODE
OUROBOROS_MINIMAL_CONTEXT
```

Rule:

- `num_ctx`, `LOCAL_MODEL_CONTEXT_LENGTH`, and
  `OPENAI_COMPATIBLE_CONTEXT_LENGTH` must describe the same practical context
  budget.
- `num_predict` and `OPENAI_COMPATIBLE_MAX_TOKENS` must be compatible.

### Provider Chain

```text
OUROBOROS_MODEL
OUROBOROS_MODEL_FALLBACK
OUROBOROS_MODEL_RESERVE
OUROBOROS_RESERVE_MODEL
OPENAI_COMPATIBLE_BASE_URL
OPENAI_COMPATIBLE_API_KEY
```

Rule:

- A reserve model should be on a different provider or quota pool when the root
  failure is provider-wide 429.

### Runtime Limits

```text
TOTAL_BUDGET
OUROBOROS_PER_TASK_COST_USD
OUROBOROS_MAX_WORKERS
OUROBOROS_MAX_ACTIVE_SUBAGENTS_PER_ROOT
OUROBOROS_MAX_SUBAGENT_DEPTH
OUROBOROS_SOFT_TIMEOUT_SEC
OUROBOROS_HARD_TIMEOUT_SEC
OUROBOROS_TOOL_TIMEOUT_SEC
OUROBOROS_FINALIZATION_GRACE_SEC
```

Rule:

- Small provider quotas need low worker/subagent concurrency.
- Background consciousness should be reduced or disabled on constrained
  providers.

### Review and Safety Effort

```text
OUROBOROS_EFFORT_TASK
OUROBOROS_EFFORT_REVIEW
OUROBOROS_EFFORT_SCOPE_REVIEW
OUROBOROS_EFFORT_CONSCIOUSNESS
OUROBOROS_EFFORT_DEEP_SELF_REVIEW
OUROBOROS_RETURN_REASONING
```

Rule:

- High reasoning effort increases quality but also token load.
- On Groq/Telegram minimal mode, low effort is safer for routine tasks.

## 15. Known-Good Profiles

### Local Ollama/Qwen

```text
PARAMETER num_ctx 32768
PARAMETER num_predict 2048
PARAMETER temperature 0.2

OPENAI_COMPATIBLE_BASE_URL=http://localhost:11434/v1
OUROBOROS_MODEL=openai-compatible::qwen3:30b-a3b
OUROBOROS_MODEL_LIGHT=openai-compatible::qwen3-30b-a3b-16k:latest
OPENAI_COMPATIBLE_CONTEXT_LENGTH=32768
OPENAI_COMPATIBLE_MAX_TOKENS=2048
OUROBOROS_CONTEXT_MODE=low
```

### Conservative Local Ollama

```text
PARAMETER num_ctx 16384
PARAMETER num_predict 2048
PARAMETER temperature 0.2

OPENAI_COMPATIBLE_CONTEXT_LENGTH=16384
OPENAI_COMPATIBLE_MAX_TOKENS=2048
OUROBOROS_CONTEXT_MODE=low
```

### Colab/Groq/Telegram

```text
OPENAI_COMPATIBLE_BASE_URL=https://api.groq.com/openai/v1
OUROBOROS_MODEL=openai-compatible::groq/compound
OUROBOROS_MODEL_FALLBACK=openai-compatible::llama-3.1-8b-instant
OUROBOROS_MODEL_RESERVE=openai-compatible::<different-provider-model>
OPENAI_COMPATIBLE_CONTEXT_LENGTH=8192
OPENAI_COMPATIBLE_MAX_TOKENS=128
OUROBOROS_CONTEXT_MODE=low
OUROBOROS_MINIMAL_CONTEXT=true
OUROBOROS_EFFORT_TASK=low
OUROBOROS_EFFORT_CONSCIOUSNESS=low
```

## 16. Debugging Checklist

When a user-visible task fails:

1. Check whether the failure is provider-side: 400, 413, 429, timeout, empty
   response.
2. Check active model chain: primary, fallback, reserve, provider base URL.
3. Check context budget: context mode, compatible context length, max tokens.
4. Check whether a tool result or safety prompt was sent back into the model.
5. Check supervisor state and task result files for durable failure details.
6. Check whether the request should have used a direct reviewed tool route.
7. Check whether runtime data root differs from source checkout.
8. For Telegram/Colab, verify that the notebook pulled the current branch and
   resynced native skills.

