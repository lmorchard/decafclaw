# Context Composer

The `ContextComposer` (`src/decafclaw/context_composer.py`) is the unified pipeline for assembling everything that gets sent to the LLM each turn: system prompt, conversation history, vault context, and tool definitions.

## What the agent sees

### System prompt

Assembled from markdown files at startup. Admin-level files override bundled.

| Order | File | Source | Purpose |
|-------|------|--------|---------|
| 1 | `SOUL.md` | `src/decafclaw/prompts/` or admin override | Identity, personality, behavioral guidelines |
| 2 | `AGENT.md` | `src/decafclaw/prompts/` or admin override | Capabilities, tool guidance, memory instructions |
| 3 | `USER.md` | Workspace only | User-specific context (optional) |

Override path: `data/{agent_id}/{SOUL,AGENT,USER}.md` (admin-level, read-only to agent)

### Context window layout

```
┌─────────────────────────────────────────┐
│ SYSTEM PROMPT                           │
│ - SOUL.md (identity, personality)       │
│ - AGENT.md (tools, memory guidance)     │
│ - USER.md (user context, if present)    │
│ - Skill catalog (name + description)    │
│ - Deferred tools list (if over budget)  │
├─────────────────────────────────────────┤
│ TOOL DEFINITIONS (sent as `tools` param)│
│ - Always-loaded: shell, workspace_*,    │
│   web_fetch, current_time, etc.         │
│ - Vault tools (always-loaded skill)     │
│ - Activated skill tools                 │
│ - Fetched tools (via tool_search)       │
│ - Over budget → deferred behind search  │
├─────────────────────────────────────────┤
│ VAULT CONTEXT (injected by composer)    │
│ - @[[Page]] references                  │
│ - Open web UI page                      │
│ - Proactive vault retrieval results     │
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
└─────────────────────────────────────────┘
```

### Token budget

- `context_window_size` on the model config is the hard upper bound
- `compaction.max_tokens` triggers compaction based on `prompt_tokens` from the API (system prompt + tools + all messages)
- Tool definitions are fixed overhead on every turn — [tool deferral](tool-search.md) keeps this in check
- Vault context competes for remaining budget after fixed costs (see [Vault Retrieval](#vault-retrieval) below)

## How it works

### Lifecycle

`ContextComposer` is stateful per-conversation. `ComposerState` lives on the `Context` object (`ctx.composer`) and is shared across forks (same conversation). The composer tracks what was included each turn and actual token usage from LLM responses.

### compose()

`compose()` is the single entry point. It:

1. Truncates oversized user messages
2. Assembles the system prompt (from `config.system_prompt`)
3. Retrieves and injects vault context (page references, open pages)
4. Retrieves and injects memory context (semantic search over vault/journal)
5. Builds the user message and archives it
6. Filters/remaps history roles for the LLM
7. Runs pre-emptive tool search — keyword-matches the user message + last assistant response against tool names/descriptions, populating `ctx.tools.preempt_matches` for promotion to critical in tool classification. See [Pre-emptive Tool Search](preemptive-tool-search.md).
8. Classifies tools into active vs deferred sets using the priority system. See [Tool Priority](tool-priority.md).
9. Returns a `ComposedContext` with everything ready to send

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

`skip_vault_retrieval` on the context is an independent flag — it skips vault retrieval without affecting vault references or the composer mode.

### Source diagnostics

Each context source produces a `SourceEntry` with token estimates, item counts, and source-specific details. These are stored on `ComposerState.last_sources` and can be inspected for debugging (not published as events every turn).

## Vault retrieval

Before each interactive turn, the composer automatically surfaces relevant vault content — without the agent needing to explicitly search.

### How it works

1. Embeds the user's current message
2. Runs semantic search across all indexed content (vault pages, journal, conversation)
3. Scores candidates using a three-factor composite score (see [Scoring](#scoring) below)
4. Follows `[[wiki-links]]` one hop from top hits to expand the candidate pool
5. Fills remaining token budget with top-scoring candidates
6. Injects matching entries as context before the user's message

The context is archived for auditability and persists in conversation history.

### Configuration

All settings live under the `vault_retrieval` section in `config.json`:

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `enabled` | bool | `true` | Enable/disable the feature |
| `similarity_threshold` | float | `0.3` | Minimum similarity score to include a result |
| `max_results` | int | `5` | Maximum number of entries to inject |
| `max_tokens` | int | `500` | Token budget for injected context |
| `show_in_ui` | bool | `true` | Show retrieval indicator in chat UI |

Environment variable prefix: `VAULT_RETRIEVAL_` (e.g., `VAULT_RETRIEVAL_ENABLED=false`).

### Requirements

- An embedding model must be configured (`embedding.model`). If not set, the feature silently does nothing.
- The embedding index should be populated (run `make reindex` if starting fresh).

### Skip conditions

Vault retrieval is skipped for non-interactive turns: heartbeat, scheduled tasks, delegated subtasks, and any turn with `skip_vault_retrieval` set on the context. Only interactive conversations trigger retrieval.

### UI indicator

When `show_in_ui` is true:

- **Web UI**: An expandable block shows the full retrieved context with source types and relevance scores.
- **Mattermost**: A concise summary post shows the count of retrieved items by source type.

Note: `show_in_ui` gates the live progress event. The retrieval message is always archived for auditability.

## Scoring

Retrieved candidates are scored using a three-factor composite formula:

```
composite_score = w_similarity * similarity + w_recency * recency + w_importance * importance
```

All factors are normalized to [0, 1].

### Factors

| Factor | Source | Description |
|--------|--------|-------------|
| **Similarity** | Embedding cosine similarity | How semantically close the entry is to the user's message |
| **Recency** | File modification time | Exponential decay: `decay_rate ^ hours_since_modification` |
| **Importance** | Frontmatter field | Page significance, default 0.5 |

### Default weights

| Weight | Default | Rationale |
|--------|---------|-----------|
| `w_similarity` | 0.5 | Similarity dominates — relevance to the query matters most |
| `w_recency` | 0.3 | Recent content is more likely to be useful |
| `w_importance` | 0.2 | Lower weight until dream/garden actively tune importance |

### Source boosts

| Source type | Boost | Content |
|-------------|-------|---------|
| `page` | 1.3x | Agent curated pages |
| `user` | 1.2x | User's Obsidian pages |
| `journal` | 1.0x | Agent journal entries |
| `conversation` | 1.0x | Past conversation messages |

### Wiki-link graph expansion

After embedding search returns top-k results, the system follows `[[wiki-links]]` one hop from each hit:

1. Parse `[[PageName]]` links from top hit content
2. Resolve each link against the vault
3. Add linked pages to the candidate pool with discounted similarity (`parent_similarity * 0.7`)
4. All candidates (original + expanded) compete on composite score

This captures conceptual relationships that pure embedding similarity might miss.

### Dynamic budget allocation

Fixed costs (system prompt, history, tools, explicit `@[[Page]]` refs) are reserved first. Remaining tokens go to scored candidates, filled in composite_score order until budget is exhausted.

The budget is derived from `context_window_size` minus fixed costs minus a response reserve (4096 tokens).

### Scoring configuration

```json
{
  "relevance": {
    "w_similarity": 0.5,
    "w_recency": 0.3,
    "w_importance": 0.2,
    "recency_decay_rate": 0.99,
    "graph_expansion_enabled": true,
    "graph_expansion_similarity_discount": 0.7
  }
}
```

Environment variables: `RELEVANCE_W_SIMILARITY`, `RELEVANCE_W_RECENCY`, etc.

### Vault page frontmatter

Pages support optional YAML frontmatter that enriches scoring and search:

```markdown
---
summary: "One-line description"
keywords: [term1, term2, term3]
tags: [category1, category2]
importance: 0.5
---
```

The embedding index stores a composite document (summary + keywords + tags + body) for richer semantic search. Pages without frontmatter embed as body-only. Frontmatter fields are parsed and used for scoring today; the dream/garden processes don't yet auto-generate them (they use `> tl;dr:` blockquote summaries instead).

## Relationship to agent loop

The agent loop (`run_agent_turn`) creates a `ContextComposer` at the start of each turn and calls `compose()` once. The iteration loop still uses `_build_tool_list()` per-iteration because fetched tools change mid-turn as the model calls `tool_search`. After each LLM response, `record_actuals()` stores the real token counts for future calibration.

## Context inspection

After each turn, the agent writes a diagnostics sidecar file (`workspace/conversations/{conv_id}.context.json`) with per-source token estimates, scoring details, and memory candidate breakdowns.

**REST endpoint:** `GET /api/conversations/{id}/context` returns the sidecar data.

**Web UI:** Click the context usage bar in the sidebar to open a popover with:
- Waffle chart (grid map) showing token allocation by source
- Summary stats (estimated vs actual tokens, window size, compaction threshold)
- Source breakdown table with token counts and item details
- Memory candidates with composite scores, score breakdowns, and graph expansion provenance

## Key files

- `src/decafclaw/context_composer.py` — unified context assembly pipeline
- `src/decafclaw/memory_context.py` — vault retrieval, graph expansion, scoring
- `src/decafclaw/prompts/SOUL.md` — bundled identity prompt
- `src/decafclaw/prompts/AGENT.md` — bundled capability/tool prompt
- `src/decafclaw/prompts/__init__.py` — prompt assembly logic
- `src/decafclaw/agent.py` — agent loop, iteration-level tool list building
- `src/decafclaw/tools/tool_registry.py` — tool classification and deferral
- `src/decafclaw/compaction.py` — conversation summary generation
- `src/decafclaw/frontmatter.py` — YAML frontmatter parsing
