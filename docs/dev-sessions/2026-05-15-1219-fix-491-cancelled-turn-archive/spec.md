# Spec — fix #491: cancelled turns cause replay on resume

## Source

GitHub issue [#491](https://github.com/lmorchard/decafclaw/issues/491).

## Goal

After a user cancels a streaming agent turn, the next user message must NOT cause the agent to re-fulfill the cancelled request. The conversation archive must carry an unambiguous signal — both for the next LLM turn *and* for the human reading the archive — that says "this turn was cancelled; don't retry it."

## Current state

Three cancel paths, none of which writes a strong cancel signal to the archive:

| Path | Where | Archive write |
|---|---|---|
| `_check_cancelled` at iteration start | `agent.py:106-115` | `assistant: "[Agent turn cancelled by user]"` — only fires if cancel observed *between* iterations |
| Provider-level cancel during streaming | `openai_compat.py:191-196`, `vertex.py:177-181` | Partial content via normal `_handle_no_tool_calls` flow, may be empty |
| `asyncio.CancelledError` propagates to `run()` | `conversation_manager.py:1076-1085` | **Nothing.** Only a WebSocket `message_complete` event with `text: "[cancelled]"` is emitted |

The most common cancel path (path 3) leaves the archive looking like:

```
user:      write me a 600-word essay about anything at all
user:      hello
```

The LLM sees an open user request with no assistant response in between and treats it as still-pending, re-fulfilling it before responding to "hello." (The issue body's claim that `assistant: [cancelled]` is in the archive is inaccurate — that string only ever exists on the WebSocket wire, never on disk.)

## Desired end state

After any cancel, the archive contains:

```
user:           write me a 600-word essay about anything at all
assistant:      <partial streamed content, or empty if none>
cancel_marker:  [User cancelled this turn. Do not retry the cancelled request unless they explicitly ask for it again.]
user:           hello
```

- The `assistant` partial message is omitted only if no content was streamed before cancel (no point archiving an empty message).
- The `cancel_marker` row is always written, on every cancel path.
- The cancel marker is a **new archive-only role** (`cancel_marker`) added to `context_composer.py:ROLE_REMAP` so it survives the LLM transform by being remapped to `user` (consistent with how `vault_retrieval` / `conversation_notes` work today). This preserves the "between user turns" position across all providers.
- The Web UI / TUI display the cancel marker as a distinct, muted line (small styling change — out of scope for THIS PR if the existing display handles it gracefully via the default fallback, see "What we're NOT doing").

## Design decisions

### Why a new role, not `system`

Vertex/Gemini's `_build_request_body` (`vertex.py:445-447, 500-501`) collapses ALL `system`-role messages into a single top-level `systemInstruction`, losing the mid-conversation position. A new `cancel_marker` role remapped to `user` (via the existing `ROLE_REMAP` pattern in `context_composer.py:24-28`) keeps the marker at the correct point in the conversation across all providers.

### Why preserve partial content

The TUI already preserves partial content client-side (commit `567cc8e`). Persisting it to the archive matches user expectation: cancelling shouldn't silently delete what was already streamed. The partial assistant message tells the LLM "I did start answering" — combined with the cancel marker, the LLM understands the user interrupted intentionally rather than the response being lost.

### Single canonical cancel write

All three cancel paths funnel through a single helper that writes:
1. Optional `assistant: <partial>` (only if partial content non-empty).
2. Always `cancel_marker: [...]`.

Path 1 (`_check_cancelled`) and path 3 (`run()` CancelledError handler) call the helper directly. Path 2 (streaming cancel returning empty/partial through the normal flow) needs a guard so the regular `_handle_no_tool_calls` doesn't archive an empty assistant message on cancel — instead, the cancel helper handles it.

### Cancel marker text

```
[User cancelled this turn. Do not retry the cancelled request unless they explicitly ask for it again.]
```

Clear instruction; doesn't presume what the cancelled content was. Falls in line with the bracketed-marker idiom already used in the codebase (`"[Agent turn cancelled by user]"`, `"[User approved: ...]"`).

### Partial content source

Decision deferred to plan phase (see Open questions). Default: if partial content isn't trivially reachable from the `run()` CancelledError handler, accumulate streamed chunks at the `ConversationManager` level — a small new state field on `ConversationState` cleared at turn start and appended to in the same place where `message_complete` deltas already pass through the manager's event bus.

## Patterns to follow

- `ROLE_REMAP` pattern — `src/decafclaw/context_composer.py:24-28`, `src/decafclaw/archive.py:13`.
- Archive write helper — `src/decafclaw/agent.py:50-55` `_archive(ctx, msg)`.
- Cancel detection — existing `cancel_event` + `agent_task.cancel()` plumbing in `cancel_turn()`.
- Test pattern — existing cancel tests in `tests/test_agent_turn.py:59-90` and `tests/test_conversation_manager.py:355` show the fixture shape.

## What we're NOT doing

- **Adding a `cancel_marker` UI affordance.** If the existing message renderer falls through to "display unknown role as muted text," that's good enough for this PR. Visual polish (icon, distinct styling) gets a follow-up if needed after live-smoke.
- **Updating the WebSocket payload schema.** The `message_complete` event with `text: "[cancelled]"` keeps its current shape so existing UI clients don't break. The archive-side marker is internal to the agent ↔ LLM contract.
- **Fixing the equivalent shape for other turn-aborting paths** — LLM error in `conversation_manager.py:1086-1093`, max_iterations exhaustion, circuit breaker. Same class of bug (open user request + weak/missing assistant turn) but out of scope. Flag in `notes.md` for follow-up issue.
- **Compaction / decision-slice interaction.** Cancel markers should pass through compaction prose summaries naturally; not adding special handling unless tests show otherwise.
- **Reworking `_check_cancelled` semantics.** It already does the right thing for its narrow case (between iterations). The fix folds it into the same canonical helper rather than replacing it.
- **Preempt search defensive `"[cancelled]"` filter** at `preempt_search.py:133` — dead code now (and arguably always was). Leave it; harmless. Removing it is a separate cleanup.

## Acceptance criteria

1. After a cancel — by any of the three paths — the archive JSONL contains a `{"role": "cancel_marker", "content": "[User cancelled this turn. ...]"}` row.
2. If partial assistant content was streamed before cancel, the archive contains it as `{"role": "assistant", "content": "<partial>"}` immediately before the cancel marker.
3. The LLM-facing messages list (from `ContextComposer.compose()`) sees the cancel marker as a `user`-role message at the correct position.
4. A new test reproduces the bug shape: archive a cancelled turn, then queue a fresh user turn, and assert the LLM message stream does NOT contain a synthesized retry of the cancelled request. (Easiest pin: assert the composed messages list has the cancel marker between the two user messages, in the right shape.)
5. Existing cancel tests still pass (`test_check_cancelled_returns_result_when_cancelled`, `test_cancel_turn_sets_event`, `test_run_agent_turn_cancellation`).
6. `make check` and `make test` clean.

## Open questions

- **Where exactly does partial assistant content get accumulated server-side that's reachable from the `run()` CancelledError handler?**
  - *Default if no clean source exists:* add `partial_assistant_text: str = ""` to `ConversationState`, reset at turn start, append in the existing `on_stream_chunk` callback (or in the event-bus forwarder for `chunk`/`text_before_tools` events), read by the cancel helper.
  - The plan phase should answer this conclusively before writing code; if the default is needed, it's a 5-line addition.

## Risks

- **Vertex provider** discovery surfaced one provider-specific transform; OpenAI-compat should pass-through cleanly since `cancel_marker → user` happens before the provider sees the message. Still: smoke against both providers (Gemini + OpenAI) post-merge.
- **Partial content accumulation** is the load-bearing detail. If the partial isn't reachable at cancel time, the implementation degrades to "marker only" — still better than current.
- **No live integration test runs the full WebSocket → ConversationManager → archive cycle.** The PR's coverage is unit/integration at the manager level; Les should live-smoke in TUI + web UI after merge.
