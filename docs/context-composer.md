# Context Composer

The `ContextComposer` (`src/decafclaw/context_composer.py`) is the unified pipeline for assembling everything that gets sent to the LLM each turn: system prompt, conversation history, memory/wiki context, and tool definitions.

## Why

Context assembly was previously scattered across `agent.py` (`_prepare_messages`), `memory_context.py`, `tool_registry.py`, and `prompts/__init__.py`. Each source competed for the same token budget with no holistic view. The composer centralizes this into a single entry point with per-source diagnostics.

See [issue #182](https://github.com/lmorchard/decafclaw/issues/182) for the full design rationale and research references.

## How it works

### Lifecycle

`ContextComposer` is stateful per-conversation. `ComposerState` lives on the `Context` object (`ctx.composer`) and is shared across forks (same conversation). The composer tracks what was included each turn and actual token usage from LLM responses.

### compose()

`compose()` is the single entry point. It:

1. Truncates oversized user messages
2. Assembles the system prompt (from `config.system_prompt`)
3. Retrieves and injects wiki context (vault page references, open pages)
4. Retrieves and injects memory context (semantic search over vault/journal)
5. Builds the user message and archives it
6. Filters/remaps history roles for the LLM
7. Classifies tools into active vs deferred sets
8. Returns a `ComposedContext` with everything ready to send

### ComposedContext

```python
@dataclass
class ComposedContext:
    messages: list[dict]          # Ready-to-send message array
    tools: list[dict]             # Active tool definitions
    deferred_tools: list[dict]    # Deferred tool definitions
    total_tokens_estimated: int   # Sum across all sources
    sources: list[SourceEntry]    # Per-source diagnostics
    retrieved_context_text: str   # Formatted memory context (for reflection)
```

### Modes

The composer is mode-aware via `ComposerMode`:

| Mode | Memory | Wiki | Tools | Use case |
|------|--------|------|-------|----------|
| `INTERACTIVE` | Yes | Yes | Full | Mattermost, web UI, terminal |
| `HEARTBEAT` | No | No | Full | Periodic heartbeat (set via `ctx.task_mode="heartbeat"`) |
| `SCHEDULED` | No | No | Full | Scheduled tasks (set via `ctx.task_mode="scheduled"`) |
| `CHILD_AGENT` | No | No | Full | `delegate_task` sub-agents (set via `ctx.is_child`) |

`skip_memory_context` on the context is an independent flag — it skips memory retrieval without affecting wiki injection or the composer mode.

### Source diagnostics

Each context source produces a `SourceEntry` with token estimates, item counts, and source-specific details. These are stored on `ComposerState.last_sources` and can be inspected for debugging (not published as events every turn).

### Token budget

- `config.llm.context_window_size` — the model's actual context window (hard upper bound)
- `config.compaction.max_tokens` — policy threshold for when to compact (comfort zone)
- The composer reports `total_tokens_estimated`; the agent loop decides when to compact

## Relationship to agent loop

The agent loop (`run_agent_turn`) creates a `ContextComposer` at the start of each turn and calls `compose()` once. The iteration loop still uses `_build_tool_list()` per-iteration because fetched tools change mid-turn as the model calls `tool_search`. After each LLM response, `record_actuals()` stores the real token counts for future calibration.

## Context Inspection

After each turn, the agent writes a diagnostics sidecar file (`workspace/conversations/{conv_id}.context.json`) with per-source token estimates, scoring details, and memory candidate breakdowns.

**REST endpoint:** `GET /api/conversations/{id}/context` returns the sidecar data.

**Web UI:** Click the context usage bar in the sidebar to open a popover with:
- Waffle chart (grid map) showing token allocation by source
- Summary stats (estimated vs actual tokens, window size, compaction threshold)
- Source breakdown table with token counts and item details
- Memory candidates with composite scores, score breakdowns, and graph expansion provenance

## Future work (deferred)

- Calibrating estimates from actuals
- Model switching as alternative to compaction
- Mattermost `!context` command (uses REST endpoint)
- Agent self-inspection tool
