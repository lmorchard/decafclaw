# Proactive Memory Retrieval — Spec

*Session for GitHub issue #89*

## Problem

The agent only accesses memories when it explicitly chooses to call `memory_search` or `memory_recent`. It doesn't proactively surface relevant memories for the current conversation turn. This means the agent often misses relevant context it already knows — the user has to prompt it to search, or the agent has to think to do so.

## Solution

A lightweight pre-pass before each agent turn that automatically retrieves relevant memories/wiki/conversation entries and injects them as context the LLM can use.

## Design

### Retrieval

1. **Input:** The user's current message text (just the current message, not conversation history)
2. **Search:** Run semantic search against the embeddings index
3. **Source priority:** Wiki > Memory > Conversation — run a single search across all source types, then sort by (source_type_priority, similarity_score) descending
4. **Threshold:** Only include results above a minimum similarity score (default `0.3`)
5. **Budget:** Cap results at max count (default `5`) and max tokens (default `500`, estimated via `len(text) // 4` — consistent with the rest of the codebase)

### Injection

- Stored in history with role `"memory_context"` for identification and archival
- When building the LLM `messages` array, mapped to `"user"` role (to avoid prompt injection risks of `"system"`)
- Content framed with a clear prefix (e.g., `"[Automatically retrieved context — not from the user]:\n\n..."`)
- Positioned between the previous message and the current user message in the `messages` array
- **Kept in LLM context** on subsequent turns — compaction handles accumulation naturally
- Add `"memory_context"` to `LLM_ROLES` mapping so it's included in `llm_history` (mapped to `"user"`)
- **Empty results:** If no results pass the similarity threshold, inject nothing and emit no event

### UI Indicator

- Emit a `memory_context` event via `ctx.publish()` with the retrieved entries
- Progress subscriber renders via `conv_display.on_tool_status("memory_context", text)` — follows the reflection pattern
- Gated by `memory_context.show_in_ui` config flag (default: `true`)
- Shows as an expandable block in the conversation, similar to tool calls

### Skip Conditions

Add `skip_memory_context` flag to context (mirrors `skip_reflection`). Skip retrieval for:
- Delegated subtasks (child agents)
- Heartbeat turns
- Scheduled tasks
- Compaction/internal turns
- Any turn where `ctx.skip_memory_context` is `True`

Only run for interactive user conversations (Mattermost direct messages, web UI, terminal).

### Configuration

New `memory_context` config section:

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `enabled` | bool | `true` | Enable/disable the feature (silently skipped if no embedding model configured) |
| `similarity_threshold` | float | `0.3` | Minimum similarity score to include a result |
| `max_results` | int | `5` | Maximum number of entries to inject |
| `max_tokens` | int | `500` | Token budget for injected context |
| `show_in_ui` | bool | `true` | Show retrieval indicator in chat UI |

### Silent Skip (No Embedding Model)

If `config.embedding.model` is not configured, the feature silently does nothing — no error, no log, no UI indicator. Documented in docs.

## Non-Goals

- Replacing the existing memory tools — the agent can still explicitly search/save
- Complex RAG pipeline — just semantic similarity
- Memory summarization or compaction — separate concern
- Custom ranking algorithms — source type priority + similarity score is enough for now

## Acceptance Criteria

- [ ] Relevant memories/wiki entries appear in LLM context without the agent explicitly searching
- [ ] Results respect similarity threshold, max results, and token budget
- [ ] UI shows expandable indicator of what was retrieved (when `show_in_ui` is true)
- [ ] Retrieved context is archived with `memory_context` role
- [ ] Feature silently disabled when no embedding model is configured
- [ ] Skipped for non-interactive turns (heartbeat, scheduled, delegated, compaction)
- [ ] All config options respected
- [ ] Docs updated
