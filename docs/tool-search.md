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
| `query` | string | (required) | Keyword, or `select:name1,name2` for exact selection |
| `max_results` | integer | 10 | Max tools returned for keyword search |

**Keyword search**: case-insensitive substring match on tool name and description.

**Exact selection**: `select:vault_read,vault_show` fetches those specific tools.

Returns full JSON schema definitions. Tools become callable on the next LLM iteration.

## Deferred Catalog Layout

The deferred tool list sent to the LLM is grouped into sections:

- `### Core` — core tools deferred by priority
- `### Skills` — tools from activated skills that were deferred (rare; skill tools are usually critical)
- `### MCP: <server>` — one section per connected MCP server

Within each section, tools sort by `(priority desc, source asc, name asc)` so that high-priority tools appear first and tools from the same skill or MCP server cluster together.

## Auto-Fetch

If the model calls a deferred tool without searching first (by inferring the name from the deferred list), `execute_tool` auto-fetches it — the call succeeds and the tool is added to the fetched set.

## Child Agents

Children inherit the parent's fetched tools but do not get `tool_search`. If a child needs a tool the parent hasn't fetched, it can't get it.
