# Tool Audit, Priority System, and Active-Tool Budget Implementation Plan

**Goal:** Implement per-tool priority for skills, right-size the critical tool set from 39 to 18 tools, demote low-frequency/redundant tools to low/normal, and assertively steer the agent toward checklist usage for multi-step tasks.

**Approach:**
1. Extend `classify_tools` / `get_priority` to respect `"priority"` declarations on skill tools, especially for always-loaded skills.
2. Add explicit `"priority"` declarations to all native tools of bundled skills (`vault`, `background`, `mcp`).
3. Adjust core tool priorities: demote `notes_read` and `delegate_tasks` to `normal`; demote `admin_*`, `workspace_insert`, `workspace_replace_lines` to `low`.
4. Strengthen `checklist_create` tool description and add positive guidance in `AGENT.md`.
5. Update tests and documentation.

**Tech stack:** Python 3.13, Pytest, Ruff, Pyright.

---

## Phase 1: Per-tool skill priority in classification

Enable skill tool definitions to declare a `"priority"` field (`critical`, `normal`, `low`) and ensure always-loaded skills respect declared priority rather than forcing all tools to `critical`.

**Files:**
- Modify: `src/decafclaw/tools/tool_registry.py`
- Modify: `src/decafclaw/skills/vault/tools.py`
- Modify: `src/decafclaw/skills/background/tools.py`
- Modify: `src/decafclaw/skills/mcp/tools.py`
- Test: `tests/test_tool_registry.py`

**Key changes:**
- In `tool_registry.py`:
  - `get_critical_names(config)`: Only include tools from always-loaded skills that explicitly declare `"priority": "critical"`.
  - In `get_priority(tool_def, config, force_critical)`: If tool definition has a declared `"priority"`, and it's from an always-loaded skill, honor its declared priority.
- In `skills/vault/tools.py`:
  - Tag `vault_read`, `vault_write`, `vault_search`, `vault_list`, `vault_journal_append` with `"priority": "critical"`.
  - Tag the remaining 10 vault tools with `"priority": "normal"`.
- In `skills/background/tools.py`:
  - Tag the 4 background tools with `"priority": "low"`.
- In `skills/mcp/tools.py`:
  - Tag the 5 mcp tools with `"priority": "low"`.

**Verification — automated:**
- [x] `make test` passes — **4439 passed, 2 skipped in 29.40s**
- [x] `uv run pytest tests/test_tool_registry.py` passes — **51 passed in 2.70s**

---

## Phase 2: Core tool demotions & priority adjustments

Demote low-frequency and redundant core tools.

**Files:**
- Modify: `src/decafclaw/tools/notes_tools.py` (`notes_read` -> `normal`)
- Modify: `src/decafclaw/tools/delegate.py` (`delegate_tasks` -> `normal`)
- Modify: `src/decafclaw/tools/admin_tools.py` (all 6 tools -> `low`)
- Modify: `src/decafclaw/tools/workspace_tools.py` (`workspace_insert`, `workspace_replace_lines` -> `low`)
- Test: `tests/test_tool_registry.py`

**Key changes:**
- Change `"priority"` fields on the specified tool definition dicts.
- Verify total critical tools is 18 (13 core + 5 vault).
- Verify default loadout tokens is ~4,200.

**Verification — automated:**
- [x] `uv run pytest tests/test_tool_registry.py` passes — **51 passed**
- [x] Invariant tests pass — **`TestCoreToolsDeclarePriority` passed**

---

## Phase 3: Assertive checklist steering

Make `checklist_create` description proactive and add positive guidance to `AGENT.md`.

**Files:**
- Modify: `src/decafclaw/tools/checklist_tools.py`
- Modify: `src/decafclaw/prompts/AGENT.md`
- Test: `tests/test_checklist_tools.py`

**Key changes:**
- Update `checklist_create` description to assertively tell the agent to start multi-step tasks by assembling a checklist.
- Update `AGENT.md` with explicit positive instructions for multi-step task tracking.

**Verification — automated:**
- [x] `make check` passes — **All gates clean (0 errors, 0 warnings)**
- [x] `make test` passes — **4439 passed, 2 skipped**

---

## Phase 4: Documentation and Final Verification

Update docs (`docs/tool-priority.md`, `docs/tools.md`) and verify zero warning output on default loadout classification.

**Files:**
- Modify: `docs/tool-priority.md`
- Modify: `docs/tools.md`
- Modify: `docs/dev-sessions/2026-10-10-1241-1003-tool-audit-and-budget/notes.md`

**Verification — automated:**
- [x] `make check` passes — **All gates clean (0 errors, 0 warnings)**
- [x] `make test` passes — **4439 passed, 2 skipped**
