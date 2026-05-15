# Research — fix #491

Codebase findings for the cancelled-turn-replay bug.

## Cancel paths (three, not one)

The issue body says the archive contains `assistant: [cancelled]`. **That string is never written to the archive.** It exists only as a WebSocket payload and as a `response_text_holder` sentinel used to resolve the caller's future.

Three actual cancel paths exist:

1. **`_check_cancelled` at iteration start** — `src/decafclaw/agent.py:106-115`. Writes `{"role": "assistant", "content": "[Agent turn cancelled by user]"}` to history + archive. Only fires if cancel is observed at the *start* of an iteration.

2. **Cancel during streaming (provider-level)** — `src/decafclaw/llm/providers/openai_compat.py:191-196` (and `vertex.py:177-181`). `_consume_sse_events` polls `cancel_event` between SSE chunks and breaks. Whatever partial content was streamed sits in `state` and gets returned as a normal response. Agent then processes it via `_handle_no_tool_calls` (`agent.py:668-709`), which archives `{"role": "assistant", "content": <partial>}` if content is non-empty, or skips after empty-retry.

3. **`asyncio.CancelledError` propagation** — `src/decafclaw/conversation_manager.py:1076-1085`. When `cancel_turn()` calls `agent_task.cancel()` (line 653), the exception propagates through the agent loop and is caught in `run()`. The handler **only emits a WebSocket event** (`type: "message_complete"`, `text: "[cancelled]"`); **no archive write**. This is the path the issue references.

## What the archive actually looks like after cancel

Depends on timing:
- Cancel hits between iterations → marker from path 1 archived: `assistant: "[Agent turn cancelled by user]"`
- Cancel hits mid-stream, provider returns cleanly → partial content archived as `assistant: "<partial>"`, possibly empty
- `task.cancel()` propagates before archive write completes → **nothing archived for the assistant turn**

None of these is the literal `assistant: "[cancelled]"` the issue describes (the user inferred that from the WS payload they saw in the UI).

## Cancel signal flow

`conversation_manager.py:636-654` `cancel_turn(conv_id)`:
1. Reads `state.cancel_event` + `state.agent_task` under `state.lock`.
2. `cancel_event.set()` — observed by `_check_cancelled` (start-of-iteration) and provider SSE loops.
3. `agent_task.cancel()` — raises `CancelledError` at the next await point in `run()`.

## Archive schema

`src/decafclaw/archive.py:13`: `LLM_ROLES = {"system", "user", "assistant", "tool"}`.

Other archive-only roles (filtered before LLM):
- `confirmation_request`, `confirmation_response` — skipped entirely (`context_composer.py`).
- `reflection` — skipped.
- `vault_retrieval`, `vault_references`, `conversation_notes` — remapped to `user` for LLM (`context_composer.py:24-28` `ROLE_REMAP`).
- `background_event` — expanded into synthetic tool_call + tool_result pair (`context_composer.py:408-412`).

No precedent for an "interrupted" or `cancelled` role/marker field. Messages have no `metadata`/`props` field on the message level (only timestamp).

## History → LLM transformation

`src/decafclaw/context_composer.py:404-424` `compose()`:
- Combines `[*history, *wiki_msgs, *notes_msgs, *memory_msgs, user_msg]`.
- Filters: `LLM_ROLES` kept; `ROLE_REMAP` keys remapped to `"user"`; everything else dropped.
- Tool result reordering ensures each tool result follows its matching assistant tool_call.

No existing "rewrite this message" or "inject cancel context" code path that conditions on the last assistant message being a cancel marker.

## `[cancelled]` literal references

- `conversation_manager.py:1078` — `response_text_holder.append("[cancelled]")` (future resolution).
- `conversation_manager.py:1082` — WS `message_complete` payload `text: "[cancelled]"`.
- `preempt_search.py:133` — defensive filter (`stripped == "[cancelled]"`); defensive because that exact string isn't actually archived anywhere.

## Tests pinning cancel behavior

- `tests/test_agent_turn.py:59-90` — `_check_cancelled` unit tests.
- `tests/test_agent_turn.py:155` — `test_execute_tool_calls_cancellation` (tool path).
- `tests/test_agent_turn.py:555` — `test_run_agent_turn_cancellation` — cancels *before* LLM; asserts `"cancelled" in result.text`.
- `tests/test_conversation_manager.py:355` — `test_cancel_turn_sets_event` — verifies signals fire; doesn't inspect archive.

**No test exercises a cancelled turn followed by a fresh user message** — the bug-shape isn't covered.

## TUI cancel-preserve

Commit `567cc8e` on `tui-spike` preserves partial content **client-side display only** when the user cancels — does not affect the archive. Web UI may behave differently (need to check).

## Class-of-bug analogues

The same shape (open user request + weak/missing assistant turn + new user message → re-fulfill) could appear when:
- LLM call errors mid-turn and the error handler at `conversation_manager.py:1086-1093` emits `type: "error"` (also no archive write).
- A turn aborts due to circuit breaker, max_iterations exhaustion, etc.

Out of scope for this fix; flagged for follow-up.
