# Tools И Контракты

Документ фиксирует текущую форму tool-системы: ABI, категории tools,
ограничения исполнения и список зарегистрированных базовых tools. Динамические
extension/MCP tools добавляются отдельно во время runtime.

## ABI

Tool-модуль экспортирует:

```python
def get_tools() -> list[ToolEntry]:
    ...
```

`ToolEntry` задается в `ouroboros/tools/registry.py`:

```text
name: str
schema: dict
handler: callable(ctx, **args) -> str
is_code_tool: bool = False
timeout_sec: int = 360
```

Минимальный frozen ABI описан в:

- `ouroboros/contracts/tool_abi.py`
- `ouroboros/contracts/tool_context.py`

`ToolContext` несет корни файловой системы, task metadata, task contract,
pending events, emit_progress callback, model overrides, browser state и
помощники `repo_path`, `drive_path`, `active_repo_dir`.

## Resource Roots

Основные roots:

```text
active_workspace
system_repo
runtime_data
task_drive
artifact_store
skill_payload
user_files
```

Доступ к root зависит от runtime mode, task type, workspace mode, tool profile
и task constraints. Политика лежит в `ouroboros/tool_access.py` и
`ouroboros/tool_policy.py`.

## Категории

| Категория | Tools |
| --- | --- |
| Файлы и код | `read_file`, `list_files`, `write_file`, `edit_text`, `search_code`, `codebase_digest` |
| Shell/code execution | `run_command`, `run_script`, `claude_code_edit` |
| VCS | `vcs_status`, `vcs_diff`, `commit_reviewed`, `vcs_commit_reviewed`, `vcs_pull_ff`, `vcs_restore`, `vcs_revert`, `vcs_rollback` |
| GitHub/PR | `list_github_prs`, `get_github_pr`, `comment_on_pr`, `fetch_pr_ref`, `stage_pr_merge` |
| Review | `advisory_review`, `review_status`, `plan_task`, `task_acceptance_review`, `skill_review` |
| Runtime control | `schedule_subagent`, `cancel_task`, `wait_task`, `wait_tasks`, `switch_model`, `set_tool_timeout` |
| Memory/knowledge | `chat_history`, `update_scratchpad`, `update_identity`, `memory_map`, `knowledge_read` |
| Services/browser/vision | `start_service`, `service_status`, `browse_page`, `browser_action`, `analyze_screenshot`, `vlm_query` |
| Skills | `list_skills`, `skill_exec`, `toggle_skill`, `skill_preflight`, `submit_skill_to_hub` |
| Discovery | `list_available_tools`, `enable_tools`, dynamic `ext_*`, dynamic `mcp_*` |

## Текущие Базовые Tools

Список получен из `ToolEntry(...)` объявлений в `ouroboros/tools/*.py`.

```text
advisory_review
analyze_screenshot
browse_page
browser_action
cancel_task
chat_history
cherry_pick_pr_commits
claude_code_edit
close_github_issue
codebase_digest
codebase_health
comment_on_issue
comment_on_pr
commit_reviewed
compact_context
create_github_issue
create_integration_branch
edit_text
enable_tools
fetch_pr_ref
forward_to_worker
generate_evolution_stats
get_github_issue
get_github_pr
get_task_result
knowledge_list
knowledge_read
knowledge_write
list_available_tools
list_files
list_github_issues
list_github_prs
list_skills
memory_map
memory_update_registry
plan_task
promote_to_stable
read_file
recent_tasks
request_deep_self_review
request_restart
review_status
run_ci_tests
run_command
run_script
schedule_subagent
search_code
send_photo
send_user_message
send_video
service_logs
service_status
set_tool_timeout
skill_exec
skill_preflight
skill_review
stage_adaptations
stage_pr_merge
start_service
stop_service
submit_skill_to_hub
switch_model
task_acceptance_review
toggle_consciousness
toggle_evolution
toggle_skill
update_identity
update_scratchpad
vcs_commit_reviewed
vcs_diff
vcs_pull_ff
vcs_restore
vcs_revert
vcs_rollback
vcs_status
vlm_query
wait_task
wait_tasks
web_search
write_file
```

## Dynamic Tools

Extension tools получают имена вида:

```text
ext_<skill>_<tool>
```

MCP tools получают имена вида:

```text
mcp_<server>__<tool>
```

Они проходят через тот же registry/dispatch слой, но зависят от включенных
skills, MCP settings, grants и review state.

## Инварианты

- Tool result должен быть ограничен и пригоден для повторной подачи в LLM.
- Mutative tools обязаны проходить runtime-mode и resource-root gates.
- `commit_reviewed` и skill execution не должны обходить review/freshness gate.
- Секреты нельзя возвращать в tool result, logs или observability без redaction.
- `claude_code_edit` остается high-capability coding tool и требует корректных
  `cwd`, resource root и `outputs` для создаваемых deliverables.

