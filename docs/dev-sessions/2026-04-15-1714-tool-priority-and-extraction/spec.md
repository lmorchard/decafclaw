# Tool Priority System & Core Tool Extraction

Combines GitHub issues #246 (tool priority system) and #247 (move rarely-used core tools to bundled skills).

## Problem

The project skill work (#17, #260, #261) exposed that the agent sees 60+ tool definitions when a skill like `project` is active. Gemini Flash starts hallucinating parameter names for tools at positions 51–61 in long lists. The current stopgap is `max_active_tools=30`, which forces deferral but uses a hardcoded `DEFAULT_ALWAYS_LOADED` set as the "what survives" rule.

We want to:
1. Reduce the default core tool count by extracting rarely-used groups into bundled skills.
2. Replace the hardcoded always-loaded set with a declarative priority system that determines what fills the active set under budget pressure.

## Goals

- Drop the default core tool count from ~43 to ~34 by extracting two skill groups.
- Make tool visibility a property of each tool (priority) rather than a hardcoded list, so it's expressive and consistent.
- Provide a low-friction path for the agent to activate trusted bundled skills (auto-approve).
- Preserve the existing `tool_search` mechanism for fetching deferred tools on demand.

## Non-goals (out of scope)

- Situational/dynamic tool scoring based on conversation context (#257).
- Pre-emptive tool search by user message keywords.
- Refactoring tool descriptions or eval coverage expansion (#240).
- Changes to the MCP namespace or tool naming conventions.

## Design

### Priority tiers

Three named tiers:

- **`critical`** — Always included in the active tool set, regardless of budget. Replaces the current `DEFAULT_ALWAYS_LOADED` set.
- **`normal`** — Included while under `tool_context_budget` and `max_active_tools`. Default for any tool without an explicit declaration.
- **`low`** — Only included if room remains after `normal`. Effectively "fetch on demand by default."

Tiers are extensible — we can add more levels later without breaking declarations.

### Where priority is declared

| Source | Mechanism | Default |
|---|---|---|
| Core tools | `"priority"` field on each TOOL_DEFINITIONS dict, alongside `"function"` | `normal` |
| Skill tools | Not declared. Always treated as `critical` when the skill is activated | n/a |
| MCP tools | Not declared. Always `normal` | `normal` |
| User override | `CRITICAL_TOOLS` env var (renamed from `ALWAYS_LOADED_TOOLS`) — set of names forced to `critical` | n/a |

### Deferral algorithm

Replace the current binary classifier in `tool_registry.classify_tools()`:

1. Compute the priority for each tool from declarations + activation state + env overrides.
2. Build the active set in priority order:
   a. All `critical` tools (hard floor — included even if over budget).
   b. All activated skill tools (treated as `critical`).
   c. All previously fetched tools for this conversation (treated as `critical`).
   d. `normal` tools added one at a time while under `tool_context_budget` and `max_active_tools`.
   e. `low` tools added only if room remains after `normal`.
3. Anything not in the active set goes to deferred.

If the active set blows the budget because of critical/fetched alone, that's a configuration issue — log a warning but don't drop critical tools.

### Deferred catalog rendering

Keep the existing section headings (Core / Skill: *name* / MCP: *server*) for visual clarity, since deferred skill tools don't carry the skill name in their tool ID. Within each section, sort by:

1. Priority descending
2. Source ascending — empty string for core, skill name for skill tools, MCP server name for MCP tools
3. Tool name ascending

This keeps related tools proximate while letting priority dominate the visual order within a section.

### Tool extraction (#247)

Extract two new bundled skills under `src/decafclaw/skills/`:

**`background` skill** (4 tools moved from `tools/background_tools.py`):
- `shell_background_start`
- `shell_background_status`
- `shell_background_stop`
- `shell_background_list`

**`mcp` skill** (5 tools moved from `tools/mcp_tools.py`):
- `mcp_status`
- `mcp_list_resources`
- `mcp_read_resource`
- `mcp_list_prompts`
- `mcp_get_prompt`

Both skills declare `auto-approve: true` in SKILL.md so the agent can activate without a confirmation prompt.

### Auto-approve frontmatter

New SKILL.md frontmatter field:
```yaml
auto-approve: true
```

Honored **only for bundled skills** (under `src/decafclaw/skills/`). Admin-level (`data/{agent_id}/skills/`) and workspace-level skills with this flag are silently ignored — same trust boundary as `schedule:` frontmatter.

When `auto-approve: true` and the skill is bundled, `tool_activate_skill()` skips both:
- The user confirmation prompt
- Writing to `skill_permissions.json` (no need)

**Precedence when both `auto-approve: true` and explicit user denial exist:** if `skill_permissions.json` records `"deny"` for the skill, the explicit denial wins — the agent gets the standard denial result. The user is the trump card.

Other paths (existing `"always"` permission saved to `skill_permissions.json`, the `is_heartbeat` admin bypass) are unchanged.

### Tools that stay in core but get demoted to `low`

These remain in their current modules but are marked `priority: low`:

- `debug_context`, `context_stats` — debug introspection
- `health_status` — diagnostics
- `heartbeat_trigger` — manual heartbeat invocation
- `wait` — utility
- `http_request` — situational
- `shell_patterns` — utility
- `refresh_skills` — admin-y

Actual per-tool assignments will be finalized during the plan phase but this is the intent.

### Tools that get `critical` priority

Migrated 1:1 from the existing `DEFAULT_ALWAYS_LOADED` set, plus `tool_search` (which the agent needs to discover deferred tools):

- `activate_skill`
- `shell`
- `workspace_read`
- `workspace_write`
- `web_fetch`
- `current_time`
- `delegate_task`
- `checklist_create`, `checklist_step_done`, `checklist_abort`, `checklist_status`
- `tool_search`

Migration guideline: every existing core tool gets an explicit `priority` field during this work — even `normal` — so the value is visible at the call site rather than inferred from a default.

### Config changes

- Rename env var `ALWAYS_LOADED_TOOLS` → `CRITICAL_TOOLS`. Hard rename — no backward-compat shim. Update docs and any examples.
- Rename the matching dataclass field `config.agent.always_loaded_tools` → `config.agent.critical_tools` and update `config.json` schema. Same hard-rename policy.
- Existing `tool_context_budget_pct` and `max_active_tools` settings unchanged.
- The hardcoded `DEFAULT_ALWAYS_LOADED` constant in `tool_registry.py` is removed.

### Interaction with non-interactive contexts

Heartbeat, scheduled tasks, and child agents (via `Context.for_task()` or `delegate_task`) can specify `allowed_tools` to restrict what the agent sees. The priority system applies *within* that allowed subset:

1. Filter all candidate tools to those in `allowed_tools` (if specified).
2. Apply the priority-based active/deferred classification on the filtered set.

This means a child agent with `allowed_tools={"workspace_read", "vault_search"}` will see exactly those tools, classified by their declared priority. The `critical` floor still applies — if a critical tool is filtered out by `allowed_tools`, it's just not available, no warning needed.

### Activated skill tool merge

Skill tools enter the candidate pool via `ctx.tools.extra_definitions` (set by `activate_skill`). The classifier merges:

- Static `TOOL_DEFINITIONS` from the core tool registry
- `ctx.tools.extra_definitions` from activated skills
- MCP tool definitions from the MCP registry

into a single candidate list, then applies the priority + budget algorithm. Activated skill tools are tagged `critical` after the merge, regardless of any priority field they might have declared (skill tools don't declare priority — they're either deferred via the skill catalog or critical via activation).

## Acceptance criteria

- `python -m py_compile` clean across all touched files.
- `make check` (lint + type) passes.
- `make test` passes — including new unit tests covering:
  - Priority field is respected in active/deferred classification.
  - `critical` tools are included even when over budget.
  - `normal` and `low` tiers respect budget/count limits.
  - Activated skill tools get critical treatment.
  - Fetched tools get critical treatment.
  - `CRITICAL_TOOLS` env var override works.
  - `auto-approve: true` is honored only for bundled skills (workspace/admin skills with the flag still prompt).
  - The deferred catalog sorts by `(priority desc, source asc, name asc)` within sections.
  - The `background` and `mcp` skills load correctly and expose their tools when activated.
- Manual smoke test in the web UI: a conversation that activates the `project` skill (the original 60-tool problem) shows a tool count consistent with the new system, and basic interactions (read/write files, run shell, activate skills) still work.
- `docs/` updated for the new priority system, extracted skills, and config changes (`CLAUDE.md`, `README.md`, `docs/context-map.md`, any per-feature pages).

## Open questions for plan phase

- Exact priority assignment for borderline tools (e.g., `health_status` — `normal` or `low`?).
- Whether to keep `mcp_status` in core (so the agent can quickly check connectivity without activating the skill) or move it into the `mcp` skill with the rest.
- Migration order: extract skills first, then add priority? Or priority framework first, then extract? Plan phase will decide.
