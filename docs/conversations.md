# Conversation Archive & Compaction

DecafClaw persists all conversations to disk and compacts them when they grow too long for the LLM's context window.

## Conversation Archive

Every message in every conversation is appended to a JSONL file:

```
data/{agent_id}/workspace/conversations/{conv_id}/archive.jsonl
```

Each line is a JSON object with `role`, `content`, optional `tool_calls`/`tool_call_id`, and a `timestamp`.

### Conversation IDs

- **Mattermost threads**: `conv_id` = `root_id` (the thread's root post)
- **Mattermost top-level**: `conv_id` = `channel_id`
- **Interactive mode**: `conv_id` = `"interactive"`

### Resume on restart

When DecafClaw restarts, it reads the archive for any conversation that receives a new message. The full history is replayed into memory, so the agent picks up where it left off.

### Cancelled turns

When a user cancels an in-progress agent turn (Ctrl+C in the TUI, the cancel button in the web UI), `ConversationManager` persists:

1. Any partial assistant text that was streamed before the cancel (as an `assistant` row). Omitted if no content streamed.
2. A `cancel_marker` row carrying `CANCEL_MARKER_TEXT`: `"[User cancelled this turn. Do not retry the cancelled request unless they explicitly ask for it again.]"`.

The write happens on **whichever cancel path fires first** — either the `CancelledError` handler when `task.cancel()` propagates an exception, or the normal-completion path when the agent loop observes the cancel cleanly (via `_check_cancelled` at iteration start, or the streaming short-circuit in `_handle_no_tool_calls`). The agent reports the clean-observation via `ConversationManager.note_cancel_observed_by_agent`. A write-once latch (`ConversationState.cancel_marker_written`) makes both entries safe when both fire.

The normal-path write is gated on the agent's explicit `cancel_observed_by_agent` flag rather than the raw `cancel_event` state — so a cancel signal that arrives *after* the agent has already returned a real response doesn't spuriously mark a delivered response as cancelled.

`cancel_marker` is an archive-only role; the [context composer](context-composer.md) remaps it to `user` before sending history to the LLM (matching the `vault_retrieval` / `conversation_notes` pattern, see `ROLE_REMAP`). The marker sits between the cancelled-user-turn and the next user turn at the right position across all providers — Gemini/Vertex collapses `system`-role messages into the top-level `systemInstruction` field, which would lose the positional meaning.

If the agent loop happened to archive a complete assistant message before the cancel propagated, the helper skips its partial write (tracked via `ConversationState.partial_assistant_archived`) so the archive doesn't carry a duplicate body.

### Aborted turns (unexpected exceptions)

When an agent turn aborts via an unexpected exception (issue #517, follow-up to #491), the generic `except Exception` branch in `_start_turn`'s run task persists the same shape as the cancel marker, with its own role and latch:

1. Any streamed partial assistant text (skipped if the agent loop already archived it, same `partial_assistant_archived` flag as cancel).
2. A `turn_aborted` row carrying `TURN_ABORTED_MARKER_TEXT`: `"[The previous turn failed unexpectedly. Treat the prior request as not fulfilled and wait for the user to clarify.]"`.

The marker text deliberately does not echo the raw exception — that could leak internal state into the LLM-visible history. The write-once latch (`ConversationState.turn_aborted_marker_written`) is independent of `cancel_marker_written`: `CancelledError` is a `BaseException` (not `Exception`) so the two `except` branches never fire on the same turn in practice. Like `cancel_marker`, the composer remaps `turn_aborted` to `user` via `ROLE_REMAP` so the marker reaches the LLM at the right position across all providers.

Two adjacent abort paths intentionally do **not** write this marker:

- **Max-iterations exhaustion**: `_finalize_max_iterations` in `agent.py` already archives an `assistant` row with the limit-reached notice. That row is itself a clear LLM-visible closure signal — adding a marker on top would be double-signaling.
- **Circuit breaker**: declines new turns but does not abort a turn mid-flight, so there is no half-archived turn to mark.

## Compaction

When the conversation grows too large (exceeding the token budget), the agent automatically compacts older messages into a summary.

### How it works

1. Every LLM call records its `prompt_tokens` into the runtime context
2. At the end of each agent turn (any exit path except cancellation), the loop checks the latest `prompt_tokens` against `COMPACTION_MAX_TOKENS`
3. If tokens exceed the threshold, compaction triggers
4. The compaction LLM reads the **archive** (source of truth) and produces a summary
5. In-memory history is replaced with: `[summary message] + [recent turns]`
6. The archive is not modified — it remains the complete record

### Configuration

Compaction is configured via the `compaction` section in `config.json` or environment variables:

```json
{
  "compaction": {
    "max_tokens": 100000,
    "preserve_turns": 5,
    "model": "",
    "url": "",
    "api_key": "",
    "llm_max_tokens": 0
  }
}
```

| config.json key | Env variable | Default | Description |
|----------------|----------|---------|-------------|
| `max_tokens` | `COMPACTION_MAX_TOKENS` | `100000` | Trigger compaction when prompt exceeds this |
| `preserve_turns` | `COMPACTION_PRESERVE_TURNS` | `5` | Keep this many recent turns intact |
| `url` | `COMPACTION_LLM_URL` | Falls back to `LLM_URL` | LLM endpoint for compaction |
| `model` | `COMPACTION_LLM_MODEL` | Falls back to `LLM_MODEL` | Model for compaction |
| `api_key` | `COMPACTION_LLM_API_KEY` | Falls back to `LLM_API_KEY` | API key for compaction |
| `llm_max_tokens` | `COMPACTION_LLM_MAX_TOKENS` | `0` (use `max_tokens`) | Compaction LLM's context budget |

Empty `url`, `model`, and `api_key` fields fall back to the main LLM config. Env vars take precedence over config.json.

### Custom compaction prompt

Place a `COMPACTION.md` file at `data/{agent_id}/COMPACTION.md` to customize the summarization instructions. If absent, a built-in default is used that preserves key facts, decisions, user preferences, tool results, and open questions.

### Tools

- **`conversation_compact`** — manually trigger compaction without waiting for the token budget to be exceeded
- **`conversation_search`** — search past conversations using semantic search (across all archived conversations, not just the current one)

## Web UI conversations

The web UI provides conversation management with folders, archiving, and a REST API. See [Web UI](web-ui.md#conversations) for the full details and API reference.

## Files on disk

```
data/{agent_id}/workspace/
  conversations/
    {conv_id}.jsonl          # Append-only archive per conversation
    {conv_id}.context.json   # Context diagnostics sidecar (written each turn)
    {conv_id}.canvas.json    # Canvas widget state sidecar (written on canvas ops)
  embeddings.db              # Semantic search index (includes conversation messages)
```

**Canvas sidecar shape (Phase 4 multi-tab):** `{schema_version: 1, active_tab: "canvas_2" | null, next_tab_id: 3, tabs: [{id, label, widget_type, data}]}`. `next_tab_id` is a monotonic counter — closed tab IDs are never reused. Phase 3 sidecars without `next_tab_id` get the field synthesized on first read.

All files are human-readable (JSON/JSONL) and crash-recoverable (append-only writes, atomic folder index updates).
