# Research: Tool Priority System, Critical Tool Audit, and Active Tool Budget

Date: 2026-10-10
Author: Les & opencode

## 1. Problem Statement (Issue #1003)

In default configuration:
- 39 tools are forced into the `critical` tier:
  - 15 core tools in `TOOL_DEFINITIONS`
  - 24 always-loaded skill tools: `vault` (15), `background` (4), `mcp` (5)
- `classify_tools` hard-floors all 39 critical tools (8,077 tokens).
- `AgentConfig.max_active_tools = 30` (budget 10,000 tokens).
- Critical tool set exceeds `max_active_tools`, causing `classify_tools` to log a warning on every turn.
- Zero `normal` tools are ever admitted to the active tool set. Tools like `workspace_search`, `workspace_list`, `workspace_glob`, and `conversation_search` are never in the prompt unless fetched via `tool_search` or pre-emptive match.
- Skills currently lack per-tool priority declarations. `get_critical_names` in `src/decafclaw/tools/tool_registry.py` unconditionally forces ALL native tools from always-loaded skills into `critical`.

## 2. Telemetry and Real Usage Evidence

From `data/decafclaw/workspace/tool_usage.jsonl` (133 calls across 18 tools):
- `vault_read`: 49 calls
- `vault_write`: 22 calls
- `vault_list`: 11 calls
- `vault_search`: 9 calls
- `current_time`: 6 calls
- `conversation_search`: 3 calls
- `vault_delete`: 3 calls
- `vault_backlinks`: 2 calls
- `workspace_edit`: 1 call
- `activate_skill`: 1 call
- `checklist_*`: 0 calls
- `delegate_*`: 0 calls
- `shell_background_*`: 0 calls
- `mcp_*`: 0 calls
- `admin_*`: 0 calls
- `workspace_insert` / `workspace_replace_lines`: 0 calls

## 3. Analysis of Critical Set (39 tools)

### Core Tools (15)
- `workspace_read` (213 tokens) - Essential. Keep critical.
- `workspace_write` (244 tokens) - Essential. Keep critical.
- `workspace_edit` (386 tokens) - Essential surgical editor (#989). Keep critical.
- `shell` (152 tokens) - Essential execution. Keep critical.
- `activate_skill` (162 tokens) - Dynamic skill entry point. Keep critical.
- `current_time` (61 tokens) - Lightweight, frequently called. Keep critical.
- `web_fetch` (105 tokens) - Standard web ingestion. Keep critical.
- `notes_append` (214 tokens) - Per-thread scratchpad. Keep critical.
- `notes_read` (127 tokens) - ContextComposer already auto-injects recent notes via `_compose_notes`. Demote to normal.
- `delegate_task` (601 tokens) - Subagent delegation. Keep critical.
- `delegate_tasks` (501 tokens) - Parallel delegation batch wrapper. Demote to normal.
- `checklist_create` (150 tokens) - Lightweight execution loop. Keep critical, strengthen description and steering.
- `checklist_step_done` (120 tokens) - Keep critical (paired with checklist_create).
- `checklist_abort` (88 tokens) - Keep critical.
- `checklist_status` (55 tokens) - Keep critical.

### Always-Loaded Skill Tools (24)
- **`vault` (15 tools):**
  - Core CRUD/search: `vault_read`, `vault_write`, `vault_search`, `vault_list`, `vault_journal_append` (5 tools) -> Keep critical.
  - Specialized section/line/metadata: `vault_show_sections`, `vault_move_lines`, `vault_section`, `vault_update_frontmatter`, `vault_rename`, `vault_grant_folder`, `vault_delete`, `vault_recent`, `vault_tags`, `vault_backlinks` (10 tools) -> Demote to normal.
- **`background` (4 tools):**
  - `shell_background_start`, `shell_background_status`, `shell_background_stop`, `shell_background_list` -> Demote all 4 to low.
- **`mcp` (5 tools):**
  - `mcp_status`, `mcp_list_resources`, `mcp_read_resource`, `mcp_list_prompts`, `mcp_get_prompt` -> Demote all 5 to low.

### Redundant Core Tools
- `admin_*` (6 tools): Currently normal. Because they precede `workspace_*` in `TOOL_DEFINITIONS`, they fill active slots first. Demote to low.
- `workspace_insert`, `workspace_replace_lines`, `admin_replace_lines`: Legacy line-number editing tools. Demote to low. `workspace_edit` is the superior surgical tool.

## 4. Key Files
- `src/decafclaw/tools/tool_registry.py` (classification, priority lookup, critical names)
- `src/decafclaw/tool_definitions.py` (loadout assembly)
- `src/decafclaw/skills/vault/tools.py` (vault tool definitions)
- `src/decafclaw/skills/background/tools.py` (background tool definitions)
- `src/decafclaw/skills/mcp/tools.py` (mcp tool definitions)
- `src/decafclaw/tools/notes_tools.py` (`notes_read` priority)
- `src/decafclaw/tools/delegate.py` (`delegate_tasks` priority)
- `src/decafclaw/tools/admin_tools.py` (admin tool priorities)
- `src/decafclaw/tools/workspace_tools.py` (workspace tool priorities)
- `src/decafclaw/tools/checklist_tools.py` (`checklist_create` description)
- `src/decafclaw/prompts/AGENT.md` (positive checklist steering)
