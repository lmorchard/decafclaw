# Context Inspection — Spec

## Related issue

GitHub issue #159 — Improved context inspection

## Goal

Surface the context composer's per-source diagnostics through a REST endpoint and a web UI popover, giving users a visual breakdown of what's in context and why. Inspired by Claude Code's context usage display.

## Background

The context composer (PR #195, #198) already tracks per-source token estimates, scoring details, and budget allocation in `ComposerState`. This data is currently invisible — only accessible via `LOG_LEVEL=DEBUG`. This feature makes it inspectable on demand.

## Design

### 1. Sidecar file for diagnostics

After each turn, persist the latest composer diagnostics to a JSON sidecar file alongside the conversation archive:

```
workspace/conversations/{conv_id}.context.json
```

(Same directory as the conversation archive JSONL files.)

Contents:

```json
{
  "timestamp": "2026-04-02T14:58:39Z",
  "total_tokens_estimated": 12500,
  "total_tokens_actual": 13200,
  "context_window_size": 100000,
  "compaction_threshold": 100000,
  "sources": [
    {
      "source": "system_prompt",
      "tokens_estimated": 3200,
      "items_included": 1,
      "items_truncated": 0,
      "details": {}
    },
    {
      "source": "history",
      "tokens_estimated": 5400,
      "items_included": 22,
      "items_truncated": 0,
      "details": {"total_llm_messages": 24}
    },
    {
      "source": "tools",
      "tokens_estimated": 2100,
      "items_included": 15,
      "items_truncated": 90,
      "details": {"deferred_mode": true}
    },
    {
      "source": "wiki",
      "tokens_estimated": 800,
      "items_included": 1,
      "items_truncated": 0,
      "details": {}
    },
    {
      "source": "memory",
      "tokens_estimated": 1000,
      "items_included": 5,
      "items_truncated": 13,
      "details": {
        "top_score": 0.761,
        "min_score": 0.573,
        "candidates_considered": 18,
        "token_budget": 82827,
        "budget_source": "dynamic"
      }
    }
  ],
  "memory_candidates": [
    {
      "file_path": "agent/pages/AI Agents.md",
      "source_type": "page",
      "composite_score": 0.761,
      "similarity": 0.84,
      "recency": 0.92,
      "importance": 0.5,
      "modified_at": "2026-04-01T12:00:00",
      "tokens_estimated": 250
    },
    {
      "file_path": "agent/pages/Claw Projects.md",
      "source_type": "graph_expansion",
      "composite_score": 0.65,
      "similarity": 0.59,
      "recency": 0.88,
      "importance": 0.5,
      "modified_at": "2026-03-31T08:00:00",
      "linked_from": "agent/pages/AI Agents.md",
      "tokens_estimated": 180
    }
  ]
}
```

**Rules:**
- Written after each turn by the agent loop (after `compose()` and the LLM response)
- Overwrites on each turn (only latest state, not a history)
- Fail-open: write errors are logged, never block the turn
- File is small (typically <5KB)

**Data enrichment needed:**
- `_score_candidates` must store `recency` on each candidate dict (currently only stores `composite_score`)
- Per-candidate `tokens_estimated` must be computed during scoring or sidecar write (currently only aggregate exists on SourceEntry)
- The `memory_candidates` list in the sidecar is built from the `ComposedContext.memory_results` field

### 2. REST endpoint

`GET /api/conversations/{conv_id}/context`

Returns the sidecar JSON file contents. 404 if no context data exists for that conversation.

Authentication: same as other conversation endpoints (session cookie).

Reusable by:
- Web UI popover (fetch on click)
- Mattermost `!context` command (future)
- Agent self-inspection tool (future)

### 3. Web UI popover

Triggered by clicking the context usage bar in the sidebar. Displays:

#### Waffle chart / grid map

A grid of small colored cells where each cell represents a chunk of tokens (e.g., 100 tokens per cell). Cells colored by source type:

| Source | Color |
|--------|-------|
| System prompt | blue |
| History | gray |
| Tools | purple |
| Wiki (explicit refs) | green |
| Memory (retrieved) | amber/orange |
| Unused capacity | very light gray / empty |

The grid gives a visceral sense of how much of the context window each source occupies.

#### Summary stats

Below the grid:
- Total tokens: estimated vs actual
- Context window size
- Compaction threshold
- Budget source (dynamic vs fixed)

#### Source breakdown table

Per-source rows:
- Source name + color swatch
- Token count
- Items included / truncated
- Key details (deferred mode for tools, score range for memory)

#### Memory candidates list

For each included memory entry:
- File path (truncated)
- Source type label (Agent page, Linked page, Journal, etc.)
- Composite score
- Score breakdown (similarity, recency, importance) as small inline bars or numbers
- Graph expansion provenance ("linked from X") if applicable

#### Interaction

- Click context bar → fetch REST endpoint → show popover
- Click outside or X → dismiss
- Popover positioned anchored to the context bar, expanding toward the center of the screen
- No live updates — snapshot at time of click

### 4. Mattermost command (future)

Not in this session's scope, but the REST endpoint enables a future `!context` command that would format the diagnostics as a Mattermost message (text table or attachment).

## Success criteria

1. **Sidecar file written** after each turn with full diagnostics
2. **REST endpoint** returns context diagnostics for a conversation
3. **Waffle chart** renders proportional grid colored by source
4. **Source breakdown** table with token counts and details
5. **Memory candidates** listed with scores and provenance
6. **Popover** triggered by clicking context bar, dismissed on click-outside
7. **All existing tests pass** — no regressions

## Out of scope

- Mattermost `!context` command (future, uses same REST endpoint)
- Agent self-inspection tool (future)
- Historical context snapshots (only latest turn)
- Live-updating popover during a turn
