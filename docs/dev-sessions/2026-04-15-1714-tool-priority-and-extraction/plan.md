# Implementation Plan

Source spec: `spec.md` in the same directory. Read that for design context.

## Strategy

Sequence the work so each step is independently mergeable, behavior is preserved (or improved) at every commit, and tests pass at each gate. The order:

1. **Priority infrastructure** — add the field, default to `normal`, classify by priority. Keep `DEFAULT_ALWAYS_LOADED` as a compatibility shim mapped to `critical` so behavior is unchanged.
2. **Migrate all core tool definitions to declare priority explicitly** — once every tool declares, retire the `DEFAULT_ALWAYS_LOADED` shim.
3. **Update deferred catalog rendering** to the new sort key.
4. **Auto-approve frontmatter** for SKILL.md, bundled-only.
5. **Extract `background` skill.**
6. **Extract `mcp` skill.**
7. **Rename config** (`ALWAYS_LOADED_TOOLS` env / `always_loaded_tools` field → `CRITICAL_TOOLS` / `critical_tools`).
8. **Documentation pass.**

Each step ends with `make check` + `make test` clean and a focused git commit. Steps 5 and 6 also include an interactive smoke test in the web UI before commit (live agent behavior matters for skill activation flows).

---

## Step 1 — Priority infrastructure

**Goal:** Plumb a `priority` field through tool definitions and rewrite `classify_tools()` to use it. Behavior is preserved via a compatibility shim that maps `DEFAULT_ALWAYS_LOADED` names to `critical` priority during classification.

**Files:**
- `src/decafclaw/tools/tool_registry.py` — add `Priority` enum (`critical`, `normal`, `low`), `get_priority(tool_def, config)` helper, rewrite `classify_tools()` to use the priority-driven algorithm from spec §"Deferral algorithm". The compatibility shim: if a tool has no `priority` field, treat it as `critical` if its name is in `DEFAULT_ALWAYS_LOADED`, otherwise `normal`.
- `tests/test_tool_registry.py` — new tests for: priority parsing with field present/absent, `critical` hard floor under tight budget, `normal` competing for remaining budget, `low` only included if room, activated skill tools forced critical, fetched tools forced critical, env-override forced critical, `Context.for_task()` `allowed_tools` interaction.

**State after:** Priority machinery exists. No tool declares `priority` yet, so the shim makes the classifier behave identically to before. All tests pass.

**Note on test forward-compat:** Step 7 renames `always_loaded_tools` → `critical_tools`. To avoid double-touching the same tests, use the current field name in step 1's tests; they'll get updated in step 7 along with the rest of the rename.

**Commit:** `feat: add priority infrastructure for tool classification`

---

## Step 2 — Migrate core tool definitions to declare priority

**Goal:** Add explicit `priority` field to every entry in core TOOL_DEFINITIONS. Once all core tools declare, retire the `DEFAULT_ALWAYS_LOADED` shim and remove the constant.

**Priority assignments** (per spec). All 43 existing core tools must declare a priority — including the 9 tools slated for extraction in steps 5 and 6, since they're still core at this point:

| Priority | Tools |
|---|---|
| `critical` | `activate_skill`, `tool_search`, `shell`, `workspace_read`, `workspace_write`, `web_fetch`, `current_time`, `delegate_task`, `checklist_create`, `checklist_step_done`, `checklist_abort`, `checklist_status` |
| `normal` | `workspace_append`, `workspace_edit`, `workspace_insert`, `workspace_replace_lines`, `workspace_list`, `workspace_search`, `workspace_glob`, `workspace_move`, `workspace_delete`, `workspace_diff`, `file_share`, `conversation_search`, `conversation_compact`, `list_attachments`, `get_attachment` |
| `low` | `debug_context`, `context_stats`, `health_status`, `heartbeat_trigger`, `wait`, `http_request`, `shell_patterns`, `refresh_skills`, `shell_background_start`, `shell_background_status`, `shell_background_stop`, `shell_background_list`, `mcp_status`, `mcp_list_resources`, `mcp_read_resource`, `mcp_list_prompts`, `mcp_get_prompt` |

**Files:**
- All tool modules with `TOOL_DEFINITIONS` (`core.py`, `checklist_tools.py`, `conversation_tools.py`, `workspace_tools.py`, `shell_tools.py`, `background_tools.py`, `http_tools.py`, `skill_tools.py`, `mcp_tools.py`, `heartbeat_tools.py`, `health.py`, `delegate.py`, `attachment_tools.py`, `search_tools.py`) — add `"priority": "<tier>"` alongside `"function"` for every tool def.
- `src/decafclaw/tools/tool_registry.py` — remove `DEFAULT_ALWAYS_LOADED` constant and the compatibility shim from step 1. Add a startup invariant check (fail fast in tests, warn in prod) that every core tool def has an explicit `priority` field.
- `tests/test_tool_registry.py` — new test: every entry in `TOOL_DEFINITIONS` declares `priority`. (Iterate the registry, assert.)

**State after:** Priority is the sole driver of classification. Removing the shim is a no-op because every core tool now declares its priority. Tests confirm declaration completeness.

**Behavior change at this commit:** Tools that were previously "deferrable but unclassified" (treated as `normal` by the shim) now declare their actual priority. The 8 tools moving to `low` (`debug_context`, `context_stats`, `health_status`, `heartbeat_trigger`, `wait`, `http_request`, `shell_patterns`, `refresh_skills`) shift from "in active set unless budget is tight" to "deferred unless room remains." This is intentional — it's the whole point of the priority system. Watch for any test that depended on these tools being in the active set by default.

**Commit:** `feat: declare priority on all core tools, remove DEFAULT_ALWAYS_LOADED`

---

## Step 3 — Deferred catalog rendering

**Goal:** Update `build_deferred_list_text()` to sort within sections by `(priority desc, source asc, name asc)`. Source is `""` for core, skill name for skill tools, MCP server name for MCP tools.

**Files:**
- `src/decafclaw/tools/tool_registry.py` — update `build_deferred_list_text()`. Add a helper `_sort_key(tool_def)` returning `(-priority_rank, source, name)` where `priority_rank` maps `critical=2, normal=1, low=0`.
- `tests/test_tool_registry.py` — new tests for catalog rendering with mixed priorities + sources.

**State after:** Deferred catalog reads with high-priority items first within each section, related skill/MCP tools clustered.

**Commit:** `feat: sort deferred tool catalog by priority then source`

---

## Step 4 — `auto-approve` SKILL.md frontmatter

**Goal:** Add a new SKILL.md frontmatter field `auto-approve: true`. Honored only when the skill is bundled (location under `src/decafclaw/skills/`). Honor explicit `"deny"` in `skill_permissions.json` first — user denial always wins.

**Files:**
- `src/decafclaw/skills/__init__.py` — extend the SkillInfo dataclass with `auto_approve: bool = False`. Parse `auto-approve` from frontmatter. Set the field only when `skill.location` is under the bundled directory (`_BUNDLED_SKILLS_DIR`); for admin/workspace skills with the flag, log a warning and ignore.
- `src/decafclaw/tools/skill_tools.py` — in `tool_activate_skill()`, after the `is_heartbeat` check and the `perms.get(name) == "always"` shortcut, add a third check: if `skill_info.auto_approve` is true AND `perms.get(name) != "deny"`, skip the confirmation prompt.
- `tests/test_skills.py` — add tests for: parsing the field, ignoring it for admin/workspace skills, skipping confirmation when bundled+auto-approve, deny-precedence (deny still blocks even with auto-approve).

**State after:** Auto-approve mechanism exists but no skill uses it yet. No behavior change for existing skills.

**Commit:** `feat: SKILL.md auto-approve frontmatter for bundled skills`

---

## Step 5 — Extract `background` skill

**Goal:** Move the four `shell_background_*` tools from `tools/background_tools.py` into a new bundled skill at `src/decafclaw/skills/background/`. The skill declares `auto-approve: true` so the agent can activate it without ceremony.

**Files:**
- `src/decafclaw/skills/background/SKILL.md` — frontmatter: `name: background`, `description: <…>`, `auto-approve: true`. Body: brief guidance about background processes (when to use them, how to check status, how to clean up).
- `src/decafclaw/skills/background/tools.py` — move the four tool implementations + their TOOL_DEFINITIONS from `tools/background_tools.py`. Drop the `priority` field on the defs (skill tools become critical on activation, no declaration needed).
- `src/decafclaw/skills/background/__init__.py` — empty.
- `src/decafclaw/tools/__init__.py` — remove the import + registration of `BACKGROUND_TOOLS`/`BACKGROUND_TOOL_DEFINITIONS`.
- `src/decafclaw/tools/background_tools.py` — delete the file (the implementations move into the skill).
- `tests/test_background_tools.py` — update imports to load tool functions from `decafclaw.skills.background.tools` directly (the functions remain importable Python; the skill loader is a separate runtime concern).

**Verification before commit:** Manual smoke test in the web UI — start a fresh conversation, ask the agent to run a background process, observe that the skill auto-activates without a confirmation prompt and the tool succeeds.

**State after:** Default core tool count drops by 4. `background` skill is discoverable via the catalog and auto-activates when used.

**Commit:** `feat: extract background process tools into bundled skill`

---

## Step 6 — Extract `mcp` skill

**Goal:** Same shape as step 5, but for the five MCP admin tools (`mcp_status`, `mcp_list_resources`, `mcp_read_resource`, `mcp_list_prompts`, `mcp_get_prompt`). All five move into the skill — including `mcp_status`, resolving the spec's open question. The agent activates the `mcp` skill once (auto-approved), then has access to all of them.

This affects only the *admin* tools. Tools exposed by external MCP servers (`mcp__server__tool`) continue to be registered via the MCP client and appear in the deferred catalog as before.

**Files:**
- `src/decafclaw/skills/mcp/SKILL.md` — frontmatter: `name: mcp`, `description: …`, `auto-approve: true`. Body: brief guidance about when to use MCP admin tools (debugging, listing resources, fetching prompts).
- `src/decafclaw/skills/mcp/tools.py` — move all five MCP tool implementations + defs from `tools/mcp_tools.py`. Drop `priority`.
- `src/decafclaw/skills/mcp/__init__.py` — empty.
- `src/decafclaw/tools/__init__.py` — remove MCP tool imports + registration.
- `src/decafclaw/tools/mcp_tools.py` — delete the file.
- `tests/test_mcp.py` — update imports to load tool functions from `decafclaw.skills.mcp.tools` directly.

**Verification before commit:** Manual smoke test — ask the agent "what MCP servers are connected?" and verify the skill auto-activates and `mcp_status` returns.

**State after:** Default core tool count drops by another 5 — total drop is 9 tools (43 → 34). `mcp` skill discoverable + auto-activates.

**Commit:** `feat: extract MCP admin tools into bundled skill`

---

## Step 7 — Rename `ALWAYS_LOADED_TOOLS` → `CRITICAL_TOOLS`

**Goal:** Hard-rename the env var and dataclass field. No backward-compat shim.

**Files:**
- `src/decafclaw/config_types.py` — rename `always_loaded_tools` field to `critical_tools` on the agent config dataclass.
- `src/decafclaw/config.py` — update env-var mapping `ALWAYS_LOADED_TOOLS` → `CRITICAL_TOOLS`.
- `src/decafclaw/config_cli.py` — same update in the env var → field path map.
- `src/decafclaw/tools/tool_registry.py` — update `get_critical_names()` (renamed from `get_always_loaded_names()`) to read from the new field.
- All references found by `grep -r "always_loaded_tools\|ALWAYS_LOADED_TOOLS" src/` — update.
- `tests/test_config.py`, `tests/test_config_cli.py` — update test references.

**State after:** Config is consistent with the priority terminology.

**Commit:** `refactor: rename ALWAYS_LOADED_TOOLS to CRITICAL_TOOLS`

---

## Step 8 — Documentation pass

**Goal:** Bring docs in line with the new system.

**Files:**
- `CLAUDE.md` — update the "Tool deferral" bullet under Conventions to describe the priority system. Update the "Key files" list if any modules were removed (background_tools.py, mcp_tools.py).
- `README.md` — update the tool table (background and MCP rows now point at skills). Update the config section if `ALWAYS_LOADED_TOOLS` was documented.
- `docs/context-map.md` — update the tool definition section.
- `docs/index.md` — add new doc page if needed.
- New doc: `docs/tool-priority.md` — explain the priority tiers, where they're declared, the deferral algorithm, the env override, and how to add a new tool. Cross-reference the bundled skills page.
- Skill docs (verify exact path — likely `docs/skills.md` or under `docs/`) — document the `auto-approve` frontmatter and the bundled-only trust boundary.

Verify file paths via `ls docs/` before writing — some docs may need to be created vs. extended.

**Commit:** `docs: tool priority system, auto-approve frontmatter, extracted skills`

---

## Verification gates

At each step:
1. `make lint` (or equivalent compile-check)
2. `make typecheck`
3. `make test`
4. For steps 5–6: manual smoke test in web UI
5. Stage focused changes, write commit message, commit.

After step 8, before opening a PR: full `make check` + full `make test`, plus end-to-end smoke test (start a conversation that activates the project skill and verifies tool count is reduced).

## Risks and rollback plan

- **Risk:** Tool extraction misses an import somewhere → runtime crash. **Mitigation:** `make test` covers imports; specific tests for `background` and `mcp` skill loading; smoke test before commit.
- **Risk:** Renaming `ALWAYS_LOADED_TOOLS` breaks user `.env` files. **Mitigation:** Document the rename prominently in the PR description and in `CLAUDE.md`. Hard rename is the project's standard policy (per project conventions).
- **Risk:** Priority misclassification leaves a needed tool deferred under tight budgets. **Mitigation:** `critical` tier is the hard floor; `CRITICAL_TOOLS` env var lets users force-elevate any tool.
- **Rollback:** Each step is a focused commit. If a later step causes problems, revert that commit. The branch is a safety net before merging to main.
