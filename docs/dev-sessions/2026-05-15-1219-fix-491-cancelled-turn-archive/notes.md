# Notes — fix #491

## Session summary

**Goal:** stop the agent from re-fulfilling a cancelled request when the user sends a fresh message after cancel.

**Shape of the fix:**
- New archive role `cancel_marker`, added to `context_composer.ROLE_REMAP` → `user`.
- `_write_cancel_archive()` helper in `conversation_manager.py` writes optional `assistant: <partial>` + always `cancel_marker: [...]`.
- Partial text accumulated server-side via the existing `on_stream_chunk` callback into `ConversationState.partial_assistant_text`.
- Cross-path latch (`ConversationState.cancel_marker_written`) ensures the marker is written exactly once whether the cancel propagated via `CancelledError` or was observed cleanly by `_check_cancelled` / streaming-cancel returning empty.
- `_archive()` in `agent.py` sets `partial_assistant_archived` via `ctx.manager.note_partial_assistant_archived()` after archiving an assistant body, so the helper skips its partial-write to avoid duplicates.

## Surprises during the work

**Issue body's archive claim was wrong.** Issue said the archive contained `assistant: [cancelled]`. The actual code never writes that string to the archive — it's only a WebSocket payload. The fact that nothing (or only an empty assistant) lands in the archive is the actual mechanism by which the LLM treats the prior request as still-open.

**Vertex/Gemini collapses `system`-role messages.** `vertex._build_request_body` lifts all `role: system` messages into a single top-level `systemInstruction` field. A mid-conversation system message would lose positional meaning. That's why the marker uses a new role remapped to `user` rather than `system`.

**Iteration-boundary race surfaced in self-review.** The first pass at Phase 3 only wired the marker write into the `CancelledError` handler. But `_check_cancelled` at iteration start returns `_Final` cleanly, and the streaming provider's SSE-loop cancel observation also returns cleanly. Both paths go through the manager's normal-completion branch, NOT the `CancelledError` handler. Required adding a check on the normal path (gated by `state.cancel_event.is_set()`) and a write-once latch.

## What we're NOT doing (deferred follow-ups)

- **LLM-error path's missing archive write.** `conversation_manager.py:1160-1167` catches generic exceptions and emits `type: "error"` but doesn't archive anything. Same class of bug shape — open user request + missing/weak assistant → next turn re-fulfills. File a follow-up issue.
- **Max-iterations exhaustion + circuit breaker** paths likely have the same missing-archive shape. Same follow-up.
- **Removing the dead `preempt_search.py:133` filter** for `"[cancelled]"` — it's defensive code for a string that was never actually archived. Harmless; leave for a future cleanup pass.
- **UI affordance for the new `cancel_marker` role.** Default UI fallback (rendering unknown roles) is the assumption — needs live-smoke confirmation in TUI + web UI after merge.

## Live-smoke checklist after merge

1. Web UI: start a long streaming response, cancel mid-stream, send "hello" — agent should NOT regenerate the cancelled essay.
2. TUI: same test once #489 lands.
3. Verify the cancel_marker row renders sensibly in both UIs (or is invisible — both acceptable).
4. Verify after page reload, archive replay still shows the partial + marker pattern (no LLM retry on resume).
