# Tool Search / Deferred Loading

When the total token cost of tool definitions exceeds a configurable budget, non-essential tools are deferred behind a `tool_search` tool. The model sees a list of deferred tool names with one-line descriptions and fetches full schemas on demand.

## How It Works

1. **Budget check**: at each agent iteration, the total token cost of all available tool definitions is estimated (JSON length / 4). If under the budget, all tools load normally — no search overhead.
2. **Deferred mode**: when over budget, only always-loaded tools + previously-fetched tools + `tool_search` are sent to the LLM. Everything else is listed by name and description in a system prompt block.
3. **Search**: the model calls `tool_search` with a keyword or exact name selection. Full schemas are returned, making those tools callable on subsequent iterations.
4. **Persistence**: fetched tools stay loaded for the rest of the conversation and survive agent restarts.

## Configuration

| Env var | Default | Description |
|---------|---------|-------------|
| `TOOL_CONTEXT_BUDGET_PCT` | 0.10 | Fraction of `COMPACTION_MAX_TOKENS` used as the tool definition budget |
| `ALWAYS_LOADED_TOOLS` | (empty) | Comma-separated tool names to add to the default always-loaded set |

The effective budget is `COMPACTION_MAX_TOKENS * TOOL_CONTEXT_BUDGET_PCT`. With the default 100K token compaction threshold and 10% budget, tools are deferred when definitions exceed ~10K tokens.

## Always-Loaded Tools (defaults)

These tools are always sent to the LLM, even in deferred mode:

- `think`, `activate_skill`, `shell`
- `workspace_read`, `workspace_write`
- `web_fetch`, `current_time`, `delegate_task`, `set_effort`
- Vault tools are always-loaded via the vault skill (not in this list)

Use `ALWAYS_LOADED_TOOLS` to add more (extends, does not replace the defaults).

## tool_search

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `query` | string | (required) | Keyword, or `select:name1,name2` for exact selection |
| `max_results` | integer | 10 | Max tools returned for keyword search |

**Keyword search**: case-insensitive substring match on tool name and description.

**Exact selection**: `select:vault_read,vault_show` fetches those specific tools.

Returns full JSON schema definitions. Tools become callable on the next LLM iteration.

## Auto-Fetch

If the model calls a deferred tool without searching first (by inferring the name from the deferred list), `execute_tool` auto-fetches it — the call succeeds and the tool is added to the fetched set.

## Child Agents

Children inherit the parent's fetched tools but do not get `tool_search`. If a child needs a tool the parent hasn't fetched, it can't get it.
