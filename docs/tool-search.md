# Tool Search / Deferred Loading

When the total token cost of tool definitions exceeds a configurable budget, non-essential tools are deferred behind a `tool_search` tool. The model sees a list of deferred tool names with one-line descriptions and fetches full schemas on demand.

See also:
- [tool-priority.md](tool-priority.md) — the priority system that determines which tools fill the active set.
- [preemptive-tool-search.md](preemptive-tool-search.md) — automatic promotion based on the current user message, often removes the need for explicit `tool_search`.

## How It Works

1. **Priority classification**: each tool resolves to a priority (`critical` / `normal` / `low`). Activated skill tools, fetched tools, and tools in the `CRITICAL_TOOLS` env override are treated as critical.
2. **Fill the active set**: the classifier starts with all `critical` tools (hard floor, included regardless of budget). Then it fills `normal` tools while under both `tool_context_budget` and `max_active_tools`, followed by `low` tools if room remains.
3. **Everything else is deferred**: deferred tools are listed by name and description in a system prompt block. The `tool_search` tool is added to the active set so the model can fetch full schemas.
4. **Persistence**: fetched tools stay loaded for the rest of the conversation and survive agent restarts.

## Configuration

| Env var | Default | Description |
|---------|---------|-------------|
| `TOOL_CONTEXT_BUDGET_PCT` | 0.10 | Fraction of `COMPACTION_MAX_TOKENS` used as the tool definition budget |
| `MAX_ACTIVE_TOOLS` | 30 | Hard cap on the number of tools in the active set |
| `CRITICAL_TOOLS` | (empty) | Comma-separated tool names to force-promote to `critical` priority |

The effective budget is `COMPACTION_MAX_TOKENS * TOOL_CONTEXT_BUDGET_PCT`. With the default 100K token compaction threshold and 10% budget, normal tools start being deferred once the active set exceeds ~10K tokens.

## Critical Tools (defaults)

Tools with declared `priority: "critical"` are always in the active set. As of this writing:

- `activate_skill`, `tool_search`
- `shell`
- `workspace_read`, `workspace_write`
- `web_fetch`, `current_time`, `delegate_task`
- `checklist_create`, `checklist_step_done`, `checklist_abort`, `checklist_status`
- Vault tools are always-loaded via the vault skill (not listed here; see the vault skill docs)

Use `CRITICAL_TOOLS` to force-promote additional tools (extends, does not replace the declared set).

## tool_search

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `query` | string | (required) | Keyword, or `select:name1,name2` for exact selection by skill or tool name |
| `max_results` | integer | 10 | Max combined skill + tool matches for keyword search |

**Keyword search** matches against three sources, case-insensitive substring on names and descriptions:

1. Non-skill deferred tools (core demoted + MCP) — returns the tool schema and fetches the tool.
2. Skill catalog entries (skill name + description) — returns the skill name, NOT the individual tools. The agent must call `activate_skill(name)` to load the skill's body and tools.
3. Hidden skill-tool inventory (tool names + descriptions of tools provided by unactivated skills) — surfaces the OWNING SKILL, so an agent that recalled a specific tool name still gets routed to `activate_skill` rather than a bypass-load that would skip the skill body.

Keyword matches are **ranked by relevance**, not returned in pool order: an exact name match scores highest, a partial name match next, and a description-only match last (`3 / 2 / +1`; a match scoring in both name and description sums). This keeps a query that names a tool (e.g. `wait`) from being outranked by an unrelated tool that merely mentions the word in its description (e.g. `heartbeat_trigger`'s "without waiting"). Skills and tools are each rendered in ranked order, and `max_results` truncation keeps the highest-scored matches. Ties preserve discovery / pool order.

**Exact selection**: `select:writing-clearly,workspace_edit` accepts both skill names (returns skill) and tool names. Hidden skill-tool names also surface their owning skill.

Tools become callable on the next LLM iteration. Skills must be explicitly activated via `activate_skill`.

## Deferred Catalog Layout

The deferred tool list sent to the LLM is grouped into sections:

- `### Core` — core tools deferred by priority
- `### MCP: <server>` — one section per connected MCP server

Skill-owned tools are NOT rendered in the deferred list — they remain in the deferred pool (so `tool_search` can match against them and surface the owning skill) but their names are never advertised directly to the agent. This is the skill-level progressive disclosure model: the catalog (in the main system prompt, not the deferred list) advertises skills, not individual tools.

Within each rendered section, tools sort by `(priority desc, source asc, name asc)`.

## Auto-Fetch

If the model calls a deferred non-skill tool without searching first, `execute_tool` auto-fetches it — the call succeeds and the tool is added to the fetched set.

**Skill-owned tools are exempt from auto-fetch.** Calling a skill tool directly produces a targeted error naming the owning skill and suggesting `activate_skill(...)`. This preserves the invariant that the skill body lands in context before any of the skill's tools execute.

## Child Agents

Children inherit the parent's fetched tools but do not get `tool_search`. If a child needs a tool the parent hasn't fetched, it can't get it.
