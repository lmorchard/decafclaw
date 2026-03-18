# Tool Search / Deferred Loading — Spec

## Status: Ready

## Background

DecafClaw currently loads all tool definitions into every LLM call — 34 core tools, plus skill tools (vault alone adds 27), plus MCP server tools. With multiple skills activated and several MCP servers connected, the tool list can exceed 77 definitions, consuming significant context tokens and causing Gemini to produce malformed function calls.

Claude Code solves this with deferred tool loading: only essential tools are sent to the model, everything else is behind a search tool. OpenClaw takes a similar approach with always-load vs deferred sets. Both converge on the same pattern.

## Goals

1. Reduce tool definition context usage by deferring non-essential tools behind a search mechanism.
2. Unify deferral across all tool sources: core tools, skill tools, and MCP server tools.
3. Keep simple setups simple — no search overhead when the tool set is small enough.

## Design

### Token Budget Threshold

At the start of each agent turn, before building the tool list, measure the total token cost of all available tool definitions. If it exceeds the **tool context budget**, switch to deferred mode.

- **Budget**: configurable as a proportion of `compaction_max_tokens`. Default: 10%.
- **Config**: `tool_context_budget_pct` (float, default 0.10). Effective budget = `compaction_max_tokens * tool_context_budget_pct`.
- Below budget: load all tools normally. No search tool, no deferred list. Simple setups unaffected.
- Above budget: switch to deferred mode.

### Always-Loaded Tools

A set of essential tools that are always sent to the LLM, even in deferred mode. These are the tools the agent uses on nearly every turn.

**Default always-loaded set** (hardcoded):
- `think`
- `memory_save`, `memory_search`, `memory_recent`
- `activate_skill`
- `shell`
- `workspace_read`, `workspace_write`
- `web_fetch`
- `current_time`
- `delegate_task`

**User override**: `ALWAYS_LOADED_TOOLS` env var — comma-separated tool names added to the default set (extends, does not replace). Stored as `always_loaded_tools` in Config.

### Deferred Mode

When active:

1. **Tool list sent to LLM**: only always-loaded tools + any previously-fetched tools (from earlier search calls in this conversation) + the `tool_search` tool itself.

2. **Deferred tool list in system prompt**: a block listing all deferred tools by name and one-line description, grouped by source. Injected into the system prompt (or appended to it). Format:

```
## Available tools (use tool_search to load)

### Core
- workspace_edit — Edit a file by exact string replacement
- workspace_search — Search for a regex pattern across workspace files
- todo_add — Add an item to the conversation's to-do list
- ...

### Skill: markdown_vault
- vault_read — Read an entire markdown file as text
- vault_show — Show a section's content or document outline
- vault_items — List checklist items with indices
- ...

### MCP: playwright
- mcp__playwright__browser_navigate — Navigate to a URL
- mcp__playwright__browser_click — Click an element
- ...
```

3. **`tool_search` tool**: added to the tool list only when deferred mode is active.

### tool_search Tool

**Parameters**:
- `query` (string, required): keyword search term or exact name selection. Use `"select:name1,name2"` prefix for exact selection by name. Otherwise treated as keyword search.
- `max_results` (integer, optional, default 10): maximum number of tools to return.

**Keyword search**: case-insensitive substring match against tool name and one-line description. Returns up to `max_results` matches.

**Exact selection**: `"select:vault_read,vault_show"` returns exactly those tools by name. No limit applied.

**Return value**: full JSON schema definitions for matched tools, in the same format as the tool list. Once returned, these tools become callable for the rest of the conversation — they are added to the "fetched" set and included in subsequent LLM calls.

**Schema**:
```json
{
  "type": "function",
  "function": {
    "name": "tool_search",
    "description": "Search for and load tool definitions. Use 'select:name1,name2' for exact tools by name, or a keyword to search tool names and descriptions. Returns full tool schemas, making matched tools callable. Check the deferred tools list in the system prompt to see what's available.",
    "parameters": {
      "type": "object",
      "properties": {
        "query": {
          "type": "string",
          "description": "Keyword search term, or 'select:name1,name2' for exact selection"
        },
        "max_results": {
          "type": "integer",
          "description": "Max tools to return for keyword search (default 10)"
        }
      },
      "required": ["query"]
    }
  }
}
```

### Fetched Tool Persistence

Once a tool is fetched via `tool_search`, it stays in the conversation's tool list for all subsequent LLM calls. No re-fetching needed. Stored as a set of tool names in `ctx.skill_data["fetched_tools"]` — this is already persisted to disk via the archive sidecar system, so fetched tools survive agent restarts.

### Calling a Deferred Tool Without Searching

If the model tries to call a tool that exists in the deferred pool but hasn't been fetched yet, `execute_tool` will find the callable (it's registered) but the model shouldn't have been able to generate the call (the schema wasn't in the tool list). In practice this can happen with aggressive models that infer tool names from the deferred list.

**Behavior**: auto-fetch the tool definition, execute the call, and add the tool to the fetched set. This is more graceful than erroring — the model's intent was correct, it just skipped the search step. Log a debug message noting the implicit fetch.

### Child Agents (delegate_task)

Children inherit the parent's fetched tools set. In deferred mode:
- Children get the same always-loaded tools + parent's fetched tools
- Children do NOT get the `tool_search` tool or the deferred list — they work with what the parent has already loaded
- If a child needs a tool the parent hasn't fetched, it can't get it (the parent should fetch before delegating, or the task description should be specific enough for the available tools)

This keeps children simple and avoids nested search complexity.

### System Prompt Injection

The deferred tool list is dynamic — it changes as skills are activated and tools are fetched during a conversation. It cannot be assembled at startup.

**Approach**: build the deferred list block in `_build_tool_list` (or a companion function) and inject it into the messages array as a system message, appended after the static system prompt. This keeps the static prompt unchanged and adds the deferred list per-turn.

### Skill Activation Integration

`activate_skill` continues to work as before — it loads the SKILL.md body (instructions) into conversation history. However, the skill's tool definitions are **not** immediately added to the LLM's tool list. Instead, they go into the deferred pool. The model uses `tool_search` to load specific skill tools as needed.

The SKILL.md body gives the model the context to know *which* tools to search for (e.g. "use vault_read to read a file" → model searches for `vault_read`).

When deferred mode is **not** active (below token budget), skill tools load immediately on activation as they do today — no behavior change.

### Token Estimation

Need a fast, approximate way to estimate token cost of tool definitions without calling an actual tokenizer. Options:
- Character count / 4 (rough approximation)
- JSON serialized length / 4
- Exact tokenizer (slow, adds dependency)

Use JSON length / 4 as a cheap approximation. Good enough for threshold decisions.

## Out of Scope

- Semantic/embedding-based tool search (substring matching is sufficient to start)
- Hierarchical namespacing (e.g. searching "browser" returns a namespace, not individual tools)
- Tool search for the Anthropic API's native `tool_search_tool` beta (we're using OpenAI-compatible endpoints via LiteLLM)
- Per-tool `always_load` metadata on MCP servers or skills
- Tool output truncation/compression (separate concern)
