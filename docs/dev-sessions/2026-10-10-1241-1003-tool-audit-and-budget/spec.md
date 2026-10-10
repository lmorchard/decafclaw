# Tool Audit, Per-Tool Skill Priority, and Active-Tool Budget Spec

**Goal:** Establish per-tool priority declarations for skills, audit and right-size the critical tool set from 39 down to 18 tools, demote low-frequency/redundant tools so normal workspace tools fit within the 30-tool active cap without warnings, and steer the agent toward disciplined checklist usage for multi-step tasks.

**Source:** GitHub Issue #1003 (https://github.com/lmorchard/decafclaw/issues/1003)

## Current state

Today, 39 tools are forced into `critical` priority:
- 15 core tools in `src/decafclaw/tools/__init__.py:TOOL_DEFINITIONS`.
- 24 always-loaded skill tools (`vault` 15, `background` 4, `mcp` 5).
In `src/decafclaw/tools/tool_registry.py:38-50`, `get_critical_names` unconditionally forces every native tool of an always-loaded skill into `critical`.
Under default config (`max_active_tools = 30`), all 39 critical tools are included as a hard floor (~8,077 tokens), `classify_tools` emits a budget overflow warning on every iteration, and 0 `normal` tools (such as `workspace_list`, `workspace_search`, `workspace_glob`, `conversation_search`) are ever active.
Furthermore:
- `checklist_create` has passive phrasing and `AGENT.md` only has negative steering ("Don't invoke for 1-step asks"), so checklists are rarely used despite being always-loaded.
- `notes_read` is critical even though `ContextComposer` already auto-injects recent notes into context.
- `delegate_tasks` is critical (501 tokens) alongside `delegate_task` (601 tokens).
- `admin_*` tools are `normal` and sit before `workspace_*` tools in `TOOL_DEFINITIONS`, crowding out workspace tools when slots are available.
- `workspace_insert`, `workspace_replace_lines`, and `admin_replace_lines` are line-number editors that compete with `workspace_edit` / `admin_edit`.

## Desired end state

1. **Per-tool priority for skills:** Skill tools can declare `"priority": "critical" | "normal" | "low"` on their definition dictionaries.
2. For always-loaded skills (`vault`, `background`, `mcp`):
   - Only tools explicitly declared as `critical` enter the critical floor. Undeclared or other tools default to their declared tier (`normal` or `low`).
   - `vault`: 5 tools are `critical` (`vault_read`, `vault_write`, `vault_search`, `vault_list`, `vault_journal_append`). The remaining 10 tools are `normal`.
   - `background`: all 4 tools are `low`.
   - `mcp`: all 5 tools are `low`.
3. For on-demand skills (e.g. `project`): when activated, tools with declared priority use that priority (or default to `critical` if undeclared, ensuring backward compatibility).
4. **Core tool priority adjustments:**
   - `notes_read` demoted to `normal`.
   - `delegate_tasks` demoted to `normal`.
   - `admin_*` tools (all 6) set to `low` so they don't crowd out workspace tools.
   - `workspace_insert`, `workspace_replace_lines`, and `admin_replace_lines` set to `low`.
5. **Resulting critical set:**
   - 13 core tools + 5 vault tools = **18 critical tools (~4,200 tokens)**.
   - Fits comfortably within `max_active_tools = 30` (leaves 12 slots for `normal` tools).
   - `classify_tools` runs cleanly with no overflow warning.
   - High-value `normal` tools (`workspace_search`, `workspace_list`, `workspace_glob`, `conversation_search`, `workspace_diff`) are admitted to the active tool set by default.
6. **Assertive checklist steering:**
   - `checklist_create` description updated to actively instruct the agent to assemble a checklist when starting multi-step tasks (3+ steps).
   - `AGENT.md` updated with positive guidance to start multi-step work with `checklist_create`.

## Design decisions

- **Decision:** Support per-tool `"priority"` field on skill tool definitions and respect it for always-loaded skills.
  - **Why:** Always-loaded skills are auto-activated at startup. Without per-tool priority, every tool in an always-loaded skill becomes critical, causing the 39-tool explosion.
  - **Rejected:** Treating all always-loaded tools as normal (breaks `vault_read` / `vault_write` which must always be present).
- **Decision:** Keep `checklist_*` tools critical and steer the agent to use them.
  - **Why:** Lightweight markdown checklists in `workspace/todos/` auto-emit a live `progress_tracker` widget in the UI without the ceremony of the `project` skill.
  - **Rejected:** Demoting `checklist_*` to deferred, which would require the agent to search for them before tracking multi-step work.
- **Decision:** Demote `admin_*`, `workspace_insert`, and `workspace_replace_lines` to `low`.
  - **Why:** `admin_*` mutations are rare and confirmation-gated. Line-number editing tools are brittle compared to `workspace_edit`. Demoting them to `low` keeps them accessible via `tool_search` while prioritizing `workspace_search` and `workspace_list` for active slots.
  - **Rejected:** Deleting the tools immediately (breaking backwards compatibility with existing skills or workflows).

## Patterns to follow

- Priority declaration pattern in `src/decafclaw/tools/workspace_tools.py` (`"priority": "critical"`, `"normal"`, `"low"`).
- Test invariant in `tests/test_tool_registry.py:TestCoreToolsDeclarePriority`.

## What we're NOT doing

- Not deleting any tools (legacy tools are demoted to `low`, preserving compatibility).
- Not altering MCP protocol or external MCP tool namespaces.
- Not altering the `project` skill's internal phase workflow.

## Open questions

None. The design is fully specified and agreed upon.
