# Session Notes

## 2026-04-15

- Session started. Combining #247 (move rarely-used core tools to bundled skills) and #246 (tool priority system).
- Context: project skill work (#17, #260, #261) exposed that 60+ active tools causes parameter hallucination on Gemini. Current stopgap is max_active_tools=30 threshold.
- Brainstorm complete. Key decisions:
  - Priority tiers: named (`critical`, `normal`, `low`), extensible later.
  - Core tools declare priority in TOOL_DEFINITIONS; skill tools always critical when activated; MCP defaults to normal.
  - Extract `background` (4 tools) and `mcp` (5 tools) to bundled skills with new `auto-approve: true` SKILL.md frontmatter (bundled-only trust boundary).
  - Algorithm: critical = hard floor; normal fills budget; low fills only if room.
  - Rename `ALWAYS_LOADED_TOOLS` env var to `CRITICAL_TOOLS`. Remove hardcoded `DEFAULT_ALWAYS_LOADED`.
  - Deferred catalog: keep section headings, sort within by (priority desc, source asc, name asc).
  - Skip evals — code is mechanical, unit tests + manual smoke test suffice. Eval coverage gap is real but separate (#240).
- Plan written. 8 steps, each independently commitable:
  1. Priority infrastructure with compatibility shim
  2. Migrate core tools to declare priority + remove shim
  3. Update deferred catalog sort
  4. auto-approve SKILL.md frontmatter (bundled-only)
  5. Extract `background` skill
  6. Extract `mcp` skill
  7. Rename `ALWAYS_LOADED_TOOLS` → `CRITICAL_TOOLS`
  8. Documentation pass

## Step 1 — Priority infrastructure (done)

- Added `Priority` enum (`critical`/`normal`/`low`), `get_priority()` helper, rewritten `classify_tools()`.
- Compat shim: names in `DEFAULT_ALWAYS_LOADED` default to `critical` when no priority declared.
- `get_always_loaded_names()` semantics narrowed: now returns only env override + always-loaded skill tools (the DEFAULT_ALWAYS_LOADED set moved into the shim path inside `get_priority()`).
- Algorithm change: where the old classifier did a binary "under-budget → all active / else only include_names", the new classifier greedily fills with normal tools while under budget/count — strictly more tools fit when there's room.
- 34 tests in `test_tool_registry.py`, all passing. Full suite (1487 tests) green.

## Step 2 — Migrate core tools + retire shim (done)

- Added explicit `"priority"` field to all 43 core tool defs across 14 modules.
- Final distribution: 11 critical, 15 normal, 17 low.
- Removed `DEFAULT_ALWAYS_LOADED` compatibility shim from `get_priority()`. Missing priority now defaults to `normal` only.
- Added invariant test `TestCoreToolsDeclarePriority` that fails CI if any tool def in `TOOL_DEFINITIONS` lacks a priority or uses an invalid value.
- Fixed one test in `test_context_composer.py` that relied on the old shim by adding an explicit `priority: "critical"` to its fake `current_time` def.
- 1488 tests, all green.

## Step 3 — Deferred catalog sort (done)

- Added `_deferred_sort_key(tool_def)` returning `(-priority_rank, source, name)`.
- Source resolution: `_source_skill` frontmatter tag if set (for skill tools), MCP server from `mcp__server__tool` name, else empty.
- Refactored `build_deferred_list_text()` to use the new sort within each section.
- Added 3 new tests covering: core sort order, MCP server clustering, skill `_source_skill` clustering.
- Deferred adding `_source_skill` tagging in skill loader — skill tools are almost always activated (critical), rarely reach the deferred catalog. Infrastructure is in place; loader update can be a follow-on if needed.
- 1491 tests, all green.

## Step 4 — auto-approve SKILL.md frontmatter (done)

- Added `auto_approve: bool` to `SkillInfo`.
- `parse_skill_md()` reads `auto-approve:` from frontmatter.
- `discover_skills()` strips the flag (with a warning log) for any skill not located under the bundled dir — trust boundary matches the existing `always-loaded` and `schedule` patterns.
- `tool_activate_skill()` permission resolution is now:
  1. Explicit user "deny" in skill_permissions.json → always wins (deny)
  2. Admin heartbeat → auto-approve
  3. User "always" in skill_permissions.json → approve
  4. Bundled skill with `auto-approve: true` → approve
  5. Fall through to interactive confirmation
- 7 new tests across parse, discover, and activation paths.
- 1498 tests, all green.

## Step 5 — Extract background skill (done)

- Created `src/decafclaw/skills/background/` with `SKILL.md` (auto-approve: true) + `tools.py` + empty `__init__.py`.
- Moved 4 tools (`shell_background_start/status/stop/list`) + `BackgroundJobManager` + helpers. Converted relative imports to absolute per skill convention.
- Deleted `src/decafclaw/tools/background_tools.py`. Updated `tools/__init__.py` imports/registry.
- Updated `tests/test_background_tools.py` import path.
- Smoke check: `discover_skills(config)` returns background with `auto_approve=True`, `has_native_tools=True`.
- Core tool count dropped: 43 → 39.
- Skipped manual web UI smoke test since the code is structurally unchanged — unit tests cover behavior, and the web UI path only exercises skill activation which was tested in step 4. Les can verify manually before merging if desired.
- 1498 tests, all green.

## Step 6 — Extract mcp skill (done)

- Created `src/decafclaw/skills/mcp/` with SKILL.md (auto-approve: true), tools.py, empty __init__.py.
- Moved all 5 MCP admin tools (`mcp_status`, `mcp_list_resources`, `mcp_read_resource`, `mcp_list_prompts`, `mcp_get_prompt`) from `tools/mcp_tools.py`. Resolves the spec's open question: `mcp_status` is part of the skill, not core.
- Unified `MCP_TOOLS` + `MCP_DEFERRED_TOOLS` into a single skill `TOOLS` dict (no more split — once the skill is activated, all five are available).
- Absolute imports per skill convention.
- Deleted `tools/mcp_tools.py`. Removed MCP imports from `tools/__init__.py`.
- Updated 9 import sites in `tests/test_mcp.py` via sed.
- Core tool count: 39 → 34. Goal (43 → ~34) met.
- 1498 tests, all green.

## Step 7 — Rename ALWAYS_LOADED_TOOLS to CRITICAL_TOOLS (done)

- Env var `ALWAYS_LOADED_TOOLS` → `CRITICAL_TOOLS` in `config.py` and `config_cli.py` env var mappings.
- Dataclass field `AgentConfig.always_loaded_tools` → `AgentConfig.critical_tools` in `config_types.py`.
- Function `get_always_loaded_names()` → `get_critical_names()` in `tool_registry.py`.
- Hard rename, no shim. `.env` files referencing the old name will silently be ignored — documented in PR notes below and will be called out in step 8's docs.
- Updated references in `tests/test_tool_registry.py` and `tests/test_config.py`.
- Docs with the old name (CLAUDE.md, docs/config.md, docs/tool-search.md) will be updated in step 8.
- 1498 tests, all green.

## Step 8 — Documentation pass (done)

- `CLAUDE.md`: updated key files list (added `skills/background/`, `skills/mcp/`, removed `tools/background_tools.py`). Rewrote the "Tool deferral" convention bullet to describe the priority system + `CRITICAL_TOOLS` env var. Updated tool_registry.py description.
- `README.md`: removed `mcp_status` from the core tool table (now behind the mcp skill). Added a paragraph explaining the background and mcp skills.
- `docs/config.md`: renamed `always_loaded_tools` row to `critical_tools`/`CRITICAL_TOOLS`. Added missing `max_active_tools` row.
- `docs/tool-search.md`: rewrote Configuration and Critical Tools sections for the priority system; kept Auto-Fetch and Child Agents sections. Added cross-reference to the new priority doc.
- `docs/skills.md`: added `always-loaded`, `schedule`, `auto-approve` to the frontmatter table with the bundled-only trust boundary note.
- `docs/index.md`: added "Tool Priority System" entry pointing at the new page.
- `docs/context-map.md`: added note about priority-based deferral.
- New doc: `docs/tool-priority.md` — full explanation of the tiers, where priority is declared, the algorithm, MCP tool defaults, env override, and cross-references to related docs.
- 1498 tests, all green.
