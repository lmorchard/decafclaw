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

#### Section delimiters

Each assembled section is wrapped in an XML tag at assembly time so the
model can reliably distinguish identity from instructions from per-
deployment facts from skill metadata from skill bodies. Source files
stay plain markdown; wrapping happens in `load_system_prompt` (and the
deferred-tool catalog is wrapped in `build_deferred_list_text`).

| Tag | Source | Gating |
|-----|--------|--------|
| `<soul>` | `SOUL.md` | Always present (bundled default or admin override) |
| `<agent_role>` | `AGENT.md` | Always present (bundled default or admin override) |
| `<user_context>` | `USER.md` | Only when the file exists in the agent dir and has non-empty content |
| `<skill_catalog>` | `build_catalog_text` output (listing of Active + Available skills) | Only when at least one skill was discovered |
| `<loaded_skills>` | Bodies of always-loaded bundled skills, one nested `<skill name="…">` block per body | Only when at least one bundled always-loaded skill exists |
| `<deferred_tools>` | `build_deferred_list_text` output (separate system message) | Only when at least one deferred tool entry is emitted |

Empty sections emit nothing — no dangling `<tag></tag>` wrappers.

Rationale follows Anthropic's [Effective Context Engineering for AI
Agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents).
See #304 for the change history and #357 for the follow-up that
applies the same convention to the reflection / memory-sweep /
compaction prompts.

#### Auxiliary prompt surfaces

The reflection judge, pre-compaction memory sweep, and compaction
summarizer follow the same delimiter convention for their dynamic
inputs (#357). The shared helper is `wrap_xml(tag, body)` exported
from `src/decafclaw/prompts/__init__.py` — same empty-body gating as
the main system prompt assembly. Static instruction bodies are not
wrapped: the imperative-voice prose + message role (system for
sweep/compaction, user for reflection) carry the framing already.

| Surface | Tags | Where |
|---------|------|-------|
| Reflection judge (user message) | `<retrieved_context>`, `<prior_turn_tools>`, `<user_request>`, `<tool_results>`, `<assistant_response>` | `src/decafclaw/prompts/REFLECTION.md` (placeholders wrapped in the template) |
| Memory sweep (user message) | `<messages_to_compact>` | `_build_sweep_user_input` in `compaction.py` |
| Compaction full mode (user message) | `<decision_slice>` (optional), `<messages_to_compact>` | `_build_compaction_user_input` in `compaction.py` |
| Compaction incremental mode (user message) | `<decision_slice>` (optional), `<previous_summary>`, `<new_messages>` | `_build_compaction_user_input` in `compaction.py` |

The `<decision_slice>` block is pre-wrapped by `format_slice` in
`compaction_decisions.py` (#302). `DECISIONS_PROMPT_ADDENDUM`
references this tag name directly so the LLM's instructions and the
input it sees stay aligned. The chunked-compaction fallback for
oversized inputs does not wrap chunks in `<messages_to_compact>` —
the slice guidance still lives in the system prompt via the addendum.

### Context window layout

```
┌─────────────────────────────────────────┐
│ SYSTEM PROMPT                           │
│ - SOUL.md (identity, personality)       │
│ - AGENT.md (tools, memory guidance)     │
│ - USER.md (user context, if present)    │
│ - Skill catalog (name + description)    │
│ - Deferred tools list (if over budget)  │
│ - Vault guide (AGENTS.md, if present)   │
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

#### History accounting

`compose()` treats the input `history` list as read-only during all token-budget and diagnostics calculation. Fresh-this-turn injections (`vault_references`, `conversation_notes`, `vault_retrieval`, and the current user message) are each tracked via their own `SourceEntry` (`wiki_entry`, `notes_entry`, `memory_entry`, `user_msg_entry`). The `history` source entry counts everything archived from prior turns, including messages whose role is in `ROLE_REMAP` (auto-injected role messages archived from earlier turns) — they're sent to the LLM after remap, so they count.

`ROLE_REMAP` also covers two turn-closure markers (both remapped to `user`): `cancel_marker`, written when a user cancels a turn, and `turn_aborted`, written when a turn aborts via an unexpected exception. See [Cancelled turns](conversations.md#cancelled-turns) and [Aborted turns](conversations.md#aborted-turns-unexpected-exceptions) for producer-side details. The remap is what guarantees each marker reaches the LLM at the right position regardless of provider (Vertex collapses `system`-role messages into `systemInstruction`).

After all token accounting and message-list assembly, `compose()` appends this turn's injections to `history` so the caller (agent loop) sees the post-turn state on subsequent turns. The single mutation point at the end of `compose()` replaces a previous shape that mixed input and scratchpad uses of `history`.

This contract closes a class of underreporting bugs (#393) where a role-based filter excluded archived auto-injected messages even though the LLM still sees them after remap.

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

| Mode | Memory | Wiki | Vault guide | Tools | Use case |
|------|--------|------|-------------|-------|----------|
| `INTERACTIVE` | Yes | Yes | Yes | Full | Mattermost, web UI, terminal |
| `HEARTBEAT` | No | No | No | Full | Periodic heartbeat (set via `ctx.task_mode="heartbeat"`) |
| `SCHEDULED` | No | No | No | Full | Scheduled tasks (set via `ctx.task_mode="scheduled"`) |
| `CHILD_AGENT` | No | No | No | Full | `delegate_task` sub-agents (set via `ctx.is_child`) |

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

Environment variable prefix: `MEMORY_CONTEXT_` (e.g., `MEMORY_CONTEXT_ENABLED=false`). The config section was renamed from `memory_context` to `vault_retrieval`, but the env-var prefix was left unchanged for backward compatibility.

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

## Memory retrieval modes (#301)

By default, every interactive turn auto-injects scored full-body candidates from the vault into a `vault_retrieval` message. That's costly for short turns where the user message doesn't need memory. The `vault_retrieval.mode` config controls the trade-off:

| Mode | What's injected | When to use |
|---|---|---|
| `always` (default) | Full bodies of scored candidates | Back-compat; deployments with small vaults or where the cost of a stray `vault_read` round-trip outweighs the inject cost. |
| `headlines` | One compact line per candidate (`file_path · summary · score`); no full bodies | Larger vaults where most retrieved candidates aren't actually consulted. The agent sees a directory of available pages and pulls full bodies via `vault_read` only when warranted. |
| `on_demand` | Nothing | Aggressive just-in-time strategy: skip auto-retrieval entirely, let the agent drive via `vault_search` / `vault_read`. |

`@[[Page]]` mentions inject regardless of mode (those are user-driven explicit references, not auto-retrieval).

**Empty result sets** (no candidates, all suppressed because already-injected, or all below `relevance.min_composite_score`) emit a `SourceEntry` with `mode` and `injection_skipped: true` so the context inspector can show retrieval ran but didn't inject.

**Headlines format** uses each result's `summary` frontmatter field when present, falls back to a truncated body excerpt. Configurable cap via `vault_retrieval.headline_summary_max_chars` (default 120).

**Unknown mode values** in config log a warning and fall back to `always`.

## Vault guide

When a guide file exists at the vault root (default `AGENTS.md`, configurable via `vault_guide.path`), `ContextComposer._compose_vault_guide` reads it fresh each interactive turn and injects it as a `<vault_guide>` system message immediately after the main system prompt — before the deferred-tools list and any preempt-skill content.

### Purpose

The vault guide carries always-applies rules: vault folder layout, which paths are the user's vs the agent's, and any protocols the agent must always follow. These rules must be present before the model makes any tool decision. Similarity-gated memory retrieval can miss a procedural rule when the user's message embeds far from it — the vault guide is not gated at all.

### Role and trust

The guide is injected as a **system** message, unlike retrieved vault content (remapped to `user`). This signals binding instructions, not advisory context. The trust assumption is that `AGENTS.md` is the user's own authored file — treat it the same way as `SOUL.md`/`AGENT.md`.

### Independence from vault retrieval

The vault guide is a plain file read — no embeddings, no semantic scoring. It works even when `vault_retrieval` is disabled or no embedding model is configured.

### Skip conditions

Skipped for HEARTBEAT, SCHEDULED, and CHILD_AGENT modes. Only interactive turns inject the guide.

### Failure modes

Fail-open: a missing file, unreadable path, or empty file produces no section. No error is surfaced to the model.

### Token cap

Oversized guides are truncated (head kept) at `vault_guide.max_tokens` (default 2000) with a logged warning. The cap protects against accidentally pointing the config at a large document.

### Configuration

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `vault_guide.enabled` | bool | `true` | Enable/disable the feature |
| `vault_guide.path` | string | `"AGENTS.md"` | Path relative to the vault root |
| `vault_guide.max_tokens` | int | `2000` | Token cap; oversized guides are truncated at the head |

Config dataclass: `VaultGuideConfig` in `config_types.py`. Settable in `data/{agent_id}/config.json` under the `vault_guide` key, or via the `VAULT_GUIDE_` env prefix (`VAULT_GUIDE_ENABLED`, `VAULT_GUIDE_PATH`, `VAULT_GUIDE_MAX_TOKENS`), following the standard resolution order (defaults → config.json → env).

## Tool-result clearing (lightweight tier)

Before the compaction threshold check fires, every iteration runs a cheap pass — `clear_old_tool_results` in `src/decafclaw/context_cleanup.py` — that replaces large old tool-message bodies with a short stub (`[tool output cleared: 4213 bytes]`). This is the simplest tier of context management Anthropic's [Effective Context Engineering for AI Agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) recommends: remove raw tool output the agent has already synthesized and won't re-examine. See #298.

**Eligibility** (all must hold for a tool message to be cleared):

- `role == "tool"`
- Message is older than `cleanup.min_turn_age` user-turn boundaries (default 2 — the current and immediately prior user turn stay intact).
- UTF-8 byte length of `content` >= `cleanup.min_size_bytes` (default 1024).
- The originating tool name is not in `cleanup.preserve_tools` (default: `activate_skill` and the `checklist_*` family).
- `content` is not already a stub (idempotent re-runs).
- The stub itself would be strictly smaller than the original (skips the pathological case where `min_size_bytes` is configured below the stub length).

**What's preserved:** `tool_call_id`, `role`, `display_short_text`, `widget` are all untouched — the model still sees the tool call happened, the UI still has the original short-text and widget for display. Only `content` changes.

**Durability:** the original body remains durably written to the per-conversation JSONL archive at the moment it landed. In-memory clearing doesn't touch that, so debugging from the archive is always available.

**Compaction interaction:** when full compaction eventually fires, it sees the stubs as ordinary (small) messages and produces a fine summary. Cleanup stats are reset on compaction since the summarized history supersedes the previous in-memory view.

The clear pass writes per-conversation cumulative stats (`cleanup.cleared_count`, `cleanup.cleared_bytes`) into the context sidecar — see below.

Configuration tunables: `cleanup.enabled`, `min_turn_age`, `min_size_bytes`, `preserve_tools`. See [config.md#cleanup](config.md#cleanup).

## Decision slice through compaction

Compaction's prose summary is lossy for high-signal facts — architectural decisions, unresolved questions, artifacts produced — and that loss compounds across iterated compactions. Alongside the prose, compaction emits a **structured slice** that's threaded forward into every subsequent compaction so once an entry lands it's not re-derived from prose each cycle. See `src/decafclaw/compaction_decisions.py` and #302.

**Three lists**, each holding short string entries with creation timestamps:

- `decisions` — choices made and still in effect (architecture, product, conventions, preferences locked in).
- `open_questions` — unresolved questions the agent should remember to follow up on.
- `artifacts` — concrete things produced (files written, vault pages created, PRs opened).

**Flow on each compaction:**

1. Load the existing slice from `{workspace}/conversations/{conv_id}/decisions.json` (empty when missing).
2. If non-empty, prepend `Current state slice:\n<formatted>` to the prompt input so the LLM sees what's already captured.
3. The LLM emits its prose summary plus a fenced ```json block with the new state of the three lists. The prompt instructs it to **reuse existing entries verbatim** when they still apply (so timestamps survive), add new entries, and drop entries that have been obsoleted.
4. `parse_slice_from_response` extracts the JSON; `merge_slice` reconciles old + new (preserve verbatim entries' `created_at`, add new entries with `now`, drop missing entries, FIFO cap per category).
5. The merged slice persists to the sidecar and renders as a `<decision_slice>` block prepended to the prose summary in the rebuilt history's first message. The XML envelope mirrors the system-prompt section convention from #304 — outer XML, markdown sub-headings inside — so the model can distinguish the structured slice from the prose summary that follows it within the same message.

**Failure modes are silent.** If the LLM forgets the JSON block or emits invalid JSON, parse returns `None` and the prose-only path runs unchanged — the existing slice persists untouched.

**Disable** via `compaction.decisions_enabled = false` (config). The prompt addendum is skipped, no parse, no slice persist. Useful for A/B testing or debugging.

**Cap** via `compaction.decisions_max_per_category` (default 30; `0` = no cap). FIFO drop by `created_at` when a category exceeds the cap.

The rebuilt history's summary message looks like:

```
[Conversation summary]: <decision_slice>
### Decisions
- use vertex by default
- skip openai for now

### Open Questions
- when to add openai support?

### Artifacts
- vault://decisions/llm
</decision_slice>

Earlier turns covered ... (prose summary)
```

## Context inspection

After each turn, the agent writes a diagnostics sidecar file (`workspace/conversations/{conv_id}/context.json`) with per-source token estimates, scoring details, memory candidate breakdowns, and cumulative cleanup stats from the lightweight clear tier (see above).

**REST endpoint:** `GET /api/conversations/{id}/context` returns the sidecar data.

**Web UI:** Click the context usage bar in the sidebar to open a popover with:
- Waffle chart (grid map) showing token allocation by source
- Summary stats (estimated vs actual tokens, window size, compaction threshold)
- Source breakdown table with token counts and item details
- Memory candidates with composite scores, score breakdowns, and graph expansion provenance

## Canvas tools

Five canvas tools are always-loaded (non-deferrable) so the agent can drive
the canvas panel from any context without activating a skill. Phase 4 uses
explicit tab IDs; the Phase 3 implicit-active-tab tools (`canvas_set`,
no-id `canvas_update`) are removed.

- `canvas_new_tab(widget_type, data, label?)` — append a new tab to the
  canvas, set it active, return `tab_id` in `ToolResult.data["tab_id"]`.
  Validates widget type + data schema. Reveals the panel.
- `canvas_update(tab_id, data)` — replace the data payload of the
  identified tab in place; errors `[error: tab '{id}' not found]` on
  unknown id. Preserves panel-hidden state.
- `canvas_close_tab(tab_id)` — remove the identified tab; activates left
  neighbor (else right; else clears active). Last tab closed → panel hides.
- `canvas_clear()` — empty all tabs and clear `active_tab`; hide the panel.
- `canvas_read()` — return full canvas state `{active_tab, tabs: [{id,
  label, widget_type, data}, ...]}` via `ToolResult.data`.

Each successful call emits a `canvas_update` WebSocket event (with a `kind`
field: `new_tab`, `update`, `close_tab`, `set_active`, or `clear`) to
connected clients. The canvas state persists in
`workspace/conversations/{conv_id}/canvas.json`.

## Key files

- `src/decafclaw/context_composer.py` — unified context assembly pipeline
- `src/decafclaw/memory_context.py` — vault retrieval, graph expansion, scoring
- `src/decafclaw/prompts/SOUL.md` — bundled identity prompt
- `src/decafclaw/prompts/AGENT.md` — bundled capability/tool prompt
- `src/decafclaw/prompts/__init__.py` — prompt assembly logic
- `src/decafclaw/agent.py` — agent loop, iteration-level tool list building
- `src/decafclaw/tools/tool_registry.py` — tool classification and deferral
- `src/decafclaw/compaction.py` — conversation summary generation
- `src/decafclaw/context_cleanup.py` — lightweight tool-result clearing tier (#298)
- `src/decafclaw/compaction_decisions.py` — structured decision slice threaded through compaction (#302)
- `src/decafclaw/frontmatter.py` — YAML frontmatter parsing
