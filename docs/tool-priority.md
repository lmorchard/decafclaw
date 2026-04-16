# Tool Priority System

Every core tool declares a priority that determines whether it appears in the default active tool set when the LLM is called. The system replaces the earlier hardcoded "always-loaded" list and makes visibility a first-class, declarative property of each tool.

## Tiers

| Priority | Meaning | Behavior |
|---|---|---|
| `critical` | Must always be available | Hard floor — included in the active set even when it blows the budget |
| `normal` | Default tier | Included while the active set is under both the token budget and the `max_active_tools` count |
| `low` | Fetch on demand by default | Included only if budget and count room remain after the `normal` tier |

Tiers are string values on the tool definition, parsed as the `Priority` enum in `src/decafclaw/tools/tool_registry.py`.

## Where priority comes from

Priority is resolved per tool with this precedence (highest first):

1. **`CRITICAL_TOOLS` env override** — names in this set are promoted to `critical` regardless of what the tool declares. Useful for pinning a specific MCP tool or giving a custom skill priority.
2. **Activation-based promotion** — tools from activated skills, tools fetched via `tool_search`, and tool names from always-loaded skills all become `critical` at classification time (no declaration needed in the tool def itself).
3. **Declared `priority` field** on the tool definition dict.
4. **Default `normal`** — any tool without an explicit declaration (e.g. MCP server tools, which the MCP layer exposes without priority metadata).

## Declaring priority on a core tool

Every entry in `TOOL_DEFINITIONS` carries a `"priority"` field alongside `"function"`:

```python
{
    "type": "function",
    "priority": "critical",  # or "normal" or "low"
    "function": {
        "name": "shell",
        "description": "...",
        "parameters": {...},
    },
},
```

When adding a new core tool, you **must** declare a priority — an invariant test (`TestCoreToolsDeclarePriority` in `tests/test_tool_registry.py`) fails CI otherwise.

### Guidelines

- **`critical`**: tools the agent needs in every conversation regardless of context. File I/O (`workspace_read`, `workspace_write`), shell, skill activation, delegation, the checklist loop, `tool_search`, time.
- **`normal`**: widely useful but situational. File editing variants, conversation search/compact, attachments.
- **`low`**: debug/admin tools, rarely-called utilities (`wait`, `http_request`, `refresh_skills`, `debug_context`, `context_stats`, `health_status`, `heartbeat_trigger`, `shell_patterns`).

## Skill tools

Skill tools don't declare priority. They're treated specially:

- **Not activated** → not in the candidate pool at all (the skill catalog in the system prompt advertises the skill; activation is required).
- **Activated** → promoted to `critical` automatically, so they dominate the active set once the user has opted in.

This means the skill author doesn't need to worry about priority — activation *is* the priority signal.

## MCP tools

External MCP server tools (`mcp__server__tool`) default to `normal` since the MCP layer doesn't carry DecafClaw priority metadata. With many MCP servers connected, these tools will compete with core `normal` tools for budget and may end up deferred. If a specific MCP tool is used frequently enough to justify pinning, add it to the `CRITICAL_TOOLS` env override.

## Classification algorithm

See `classify_tools()` in `src/decafclaw/tools/tool_registry.py`:

1. Resolve every tool's priority.
2. Active set starts with all `critical` tools (hard floor — logged as a warning if it exceeds budget, but still included).
3. Append `normal` tools one by one while the active set is under `tool_context_budget` and `max_active_tools`.
4. Append `low` tools the same way, only if room remains.
5. Whatever didn't make the cut becomes the deferred set, surfaced via `tool_search` and the deferred catalog.

Input order within a tier is preserved, so callers can influence ordering by how they order the input list.

## Deferred catalog sort

The deferred catalog (rendered by `build_deferred_list_text()`) groups tools into sections — Core, Skills, and one section per MCP server. Within each section, tools sort by `(priority desc, source asc, name asc)`:

- Priority desc → high-priority tools appear first
- Source asc → tools from the same skill or MCP server cluster together
- Name asc → deterministic final tie-break

Skill tool defs can optionally include `_source_skill: "<name>"` so the source sort groups them by skill; the skill loader doesn't add this yet, but the classifier reads it if present.

## Env override

```bash
export CRITICAL_TOOLS="workspace_diff,mcp__github__create_issue"
```

Every name in the comma-separated list is promoted to `critical`, regardless of what the tool declares. This is the escape hatch for users who use a specific tool often enough that it should always be present.

## Related

- [tool-search.md](tool-search.md) — `tool_search` and the deferred catalog
- [skills.md](skills.md) — skill activation and the `auto-approve` frontmatter for bundled skills
