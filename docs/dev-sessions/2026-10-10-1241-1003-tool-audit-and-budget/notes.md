# Session Notes: Tool Audit & Active-Tool Budget (#1003)

Date: 2026-10-10
Worktree: `.claude/worktrees/issue-1003-tool-audit-and-budget/`
Branch: `issue/1003-tool-audit-and-budget`

## Summary of Accomplishments

1. **Implemented per-tool priority declarations for skills:**
   - Supported `"priority": "critical" | "normal" | "low"` on skill `TOOL_DEFINITIONS`.
   - Updated `classify_tools` and `get_always_loaded_tool_names` so always-loaded skills (`vault`, `background`, `mcp`) respect declared priority rather than unconditionally forcing all their tools to `critical`.
   - On-demand skills (`project`, etc.) continue to promote undeclared tools to `critical` when activated, preserving full backward compatibility.
   - Updated `context_composer.py` pre-emptive tool candidate pool so non-critical always-loaded skill tools can be promoted via keyword matches when relevant.

2. **Audited and right-sized always-loaded skills:**
   - `vault` (15 tools):
     - `critical` (5 tools): `vault_read`, `vault_write`, `vault_journal_append`, `vault_search`, `vault_list`.
     - `normal` (4 tools): `vault_delete`, `vault_recent`, `vault_tags`, `vault_backlinks`.
     - `low` (6 tools): `vault_rename`, `vault_grant_folder`, `vault_show_sections`, `vault_move_lines`, `vault_section`, `vault_update_frontmatter`.
   - `background` (4 tools):
     - `low` (all 4 tools): `shell_background_start`, `shell_background_status`, `shell_background_stop`, `shell_background_list`.
   - `mcp` (5 tools):
     - `low` (all 5 tools): `mcp_status`, `mcp_list_resources`, `mcp_read_resource`, `mcp_list_prompts`, `mcp_get_prompt`.

3. **Core tool demotions, consolidations & pruning:**
   - `delegate_task`: consolidated `delegate_tasks` into `delegate_task`. It now accepts either a single task string or a list of tasks (`task: str | list[str]`) for parallel batch execution, removing `delegate_tasks` from the catalog while keeping it in the python tool registry for backward-compatibility.
   - `notes_read`: demoted from `critical` to `normal` (`ContextComposer` already injects recent notes at turn start).
   - `admin_*` (all 6 tools): demoted from `normal` to `low` so admin config tools don't crowd out workspace tools in `_fill(normal)`.
   - `workspace_insert`, `workspace_replace_lines`: demoted from `normal` to `low` in favor of surgical `workspace_edit`.

4. **Assertive checklist steering:**
   - Updated `checklist_create` description to proactively instruct the agent: *"When you start on a task with multiple steps (3 or more distinct steps or non-trivial multi-file work), begin by assembling a checklist to track the work and execute it methodically."*
   - Added positive imperative guidance to `AGENT.md`: *"Track multi-step work with checklists. When starting on a task with 3 or more distinct steps or non-trivial multi-file work, begin by calling checklist_create to assemble and track the steps. Execute methodically, calling checklist_step_done after each step finishes."*

## Loadout Comparison

### Before
- **Critical tools:** 39 tools (15 core + 24 always-loaded skill tools)
- **Active tools:** 39 tools hard-floored + 1 `tool_search` = 40 tools
- **Normal tools active:** **0**
- **Token usage:** ~8,077 tokens
- **Status:** **Exceeded `max_active_tools = 30` on every iteration, emitting warning logs.** `workspace_search`, `workspace_list`, `conversation_search` were never available in the prompt.

### After
- **Critical tools:** 18 tools (13 core + 5 vault)
- **Active tools:** 30 tools + 1 `tool_search` = 31 tools
- **Normal tools active:** **12** (`vault_delete`, `vault_recent`, `vault_tags`, `vault_backlinks`, `conversation_search`, `conversation_compact`, `workspace_search`, `workspace_glob`, `workspace_list`, `workspace_diff`, `workspace_preview_markdown`, `workspace_append`)
- **Token usage:** 5,817 tokens (comfortably below 10,000 budget)
- **Status:** **Zero warning noise.** High-value workspace search and listing tools are immediately available in the prompt.

## Verification
- `make check`: 0 errors, 0 warnings (message types, ruff format check, ruff lint, pyright, tsc, static module graph all clean).
- `make test`: 4,440 passed, 2 skipped.
