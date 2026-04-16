# Context Map — What the Agent Sees

This documents what goes into the LLM context on each agent turn.

## System Prompt Composition

Assembled from markdown files at startup. Workspace files override bundled.

| Order | File | Source | Purpose |
|-------|------|--------|---------|
| 1 | `SOUL.md` | `src/decafclaw/prompts/` or workspace override | Identity, personality, behavioral guidelines |
| 2 | `AGENT.md` | `src/decafclaw/prompts/` or workspace override | Capabilities, tool guidance, memory instructions |
| 3 | `USER.md` | Workspace only (`data/workspace/{agent_id}/prompts/`) | User-specific context (optional) |

Override path: `data/{agent_id}/{SOUL,AGENT,USER}.md` (admin, read-only to agent)

## Context Window Layout

```
┌─────────────────────────────────────────┐ 0x0000
│ SYSTEM PROMPT                           │
│ - SOUL.md (identity, personality)       │
│ - AGENT.md (tools, memory guidance)     │
│ - USER.md (user context, if present)    │
├─────────────────────────────────────────┤
│ TOOL DEFINITIONS (sent as `tools` param)│
│ - Core: shell, read_file, web_fetch,    │
│   debug_context, think, compact_convo   │
│ - Memory: memory_save, memory_search,   │
│   memory_recent                         │
│ - Tabstack: extract, generate, automate,│
│   research                              │
│ - Each description consumes tokens!     │
├─────────────────────────────────────────┤
│ [CONVERSATION SUMMARY]                  │
│ - Only present after compaction         │
│ - Single user message with prefix       │
├─────────────────────────────────────────┤
│ CONVERSATION HISTORY                    │
│ - user messages                         │
│ - assistant messages (with tool_calls)  │
│ - tool result messages                  │
│ - Grows until compaction triggers       │
├─────────────────────────────────────────┤
│ ~~~ FREE SPACE ~~~                      │
│ - Room for LLM response                │
│ - Room for tool call/result cycles      │
└─────────────────────────────────────────┘ CONTEXT_WINDOW_MAX
```

## Token Budget

- `COMPACTION_MAX_TOKENS` triggers compaction based on `prompt_tokens`
  from the API, which includes system prompt + tools + all messages
- Tool definitions are fixed overhead on every turn
- As tools are added, free space shrinks — argues for skills system
  (load tools on demand)
- Tool priority system (see [tool-priority.md](tool-priority.md)) defers
  `normal`/`low` tools behind `tool_search` when the active set exceeds
  `tool_context_budget_pct × COMPACTION_MAX_TOKENS`. `critical` tools
  are always in the active set regardless of budget.

## Files Involved

- `src/decafclaw/prompts/SOUL.md` — bundled identity prompt
- `src/decafclaw/prompts/AGENT.md` — bundled capability/tool prompt
- `src/decafclaw/prompts/__init__.py` — prompt assembly logic
- `src/decafclaw/config.py` — `system_prompt` field (assembled at startup)
- `src/decafclaw/agent.py` — builds `messages` array from system prompt + history
- `src/decafclaw/tools/*.py` — `TOOL_DEFINITIONS` lists (sent as `tools` param)
- `src/decafclaw/compaction.py` — manages conversation summary
