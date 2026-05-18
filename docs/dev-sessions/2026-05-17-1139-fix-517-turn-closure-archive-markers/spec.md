# Spec — Turn-closure archive marker for exception path (#517)

## Goal

Follow-up to #491. The generic exception handler in
`conversation_manager.py` currently emits a websocket error event and
resolves the caller's future with `[error: …]`, but writes **nothing**
to the conversation archive. When the user sends a new message after a
turn-aborting exception, the LLM sees the prior user prompt with no
assistant response and may re-attempt the cancelled work, mixing the
old intent with the new — same symptom #491 fixed for the cancel path.

This spec adds a `turn_aborted` archive marker for the exception path
only. Max-iterations exhaustion and the circuit breaker are out of
scope (see "Out of scope" below).

## Cross-link

The `abort_recovery.yaml` eval suite from #528 depends on reliable
turn-closure markers. Without them, evals can't distinguish "agent did
the wrong thing after an abort" from "agent saw a stale user message
that looked like a new request." This fix is a prerequisite for that
whole eval axis.

## Design decisions (from brainstorm)

1. **Single marker role.** One new role `turn_aborted` added to
   `context_composer.ROLE_REMAP`, remapped to `user`. The reason
   travels in the text body. Keeps composer logic small and avoids
   per-reason role proliferation.

2. **Exception path only.** Max-iterations already archives a normal
   `assistant` message with a clear limit notice from
   `_finalize_max_iterations` — that IS the closure signal, no marker
   needed. The circuit breaker doesn't abort mid-flight (only declines
   new turns), so it leaves no half-archived turn.

3. **Streamed partials are preserved.** If an SSE stream produced
   partial text before the exception, archive it as an `assistant` row
   first, then the marker. Mirrors `_write_cancel_marker_once`.

4. **Marker text** (verbatim from issue):

   ```
   [The previous turn failed unexpectedly. Treat the prior request as not fulfilled and wait for the user to clarify.]
   ```

   Defensive — does not echo the raw exception (could leak internal
   state).

5. **Separate write-once latch.** New
   `turn_aborted_marker_written: bool = False` on `ConversationState`
   parallel to `cancel_marker_written`. New helper
   `_write_turn_aborted_marker_once` parallel to
   `_write_cancel_marker_once`. Cancel and exception paths are
   independent in practice (`CancelledError` vs `Exception` are
   mutually exclusive in the `try`), but the parallel structure keeps
   each path independently rewritable.

## Changes

### `conversation_manager.py`

- Add module-level constant `TURN_ABORTED_MARKER_TEXT` next to
  `CANCEL_MARKER_TEXT`.
- Add `turn_aborted_marker_written: bool = False` to
  `ConversationState` (next to `cancel_marker_written`).
- Reset it alongside `cancel_marker_written` in
  `_start_turn` (line ~1320 — the per-turn reset block).
- Add `_write_turn_aborted_marker_once` method mirroring
  `_write_cancel_marker_once`:
  - Latch on `state.turn_aborted_marker_written`.
  - Archive any unflushed `partial_assistant_chunks` first (gated on
    `partial_assistant_archived`).
  - Append `{role: "turn_aborted", content: TURN_ABORTED_MARKER_TEXT}`.
  - Same fail-open logging as cancel helper (`log.warning` on
    archive-write exceptions).
- In the `except Exception as e:` block (line ~1440), call the new
  helper **before** the `emit("error", ...)` (archive write is the
  load-bearing part; emit is best-effort UI).

### `context_composer.py`

- Add `"turn_aborted": "user"` to `ROLE_REMAP`.

### Tests

Add to `tests/test_conversation_manager.py` (or wherever the cancel
tests live — `tests/test_491_*.py` if there's a dedicated file):

- **`test_exception_writes_turn_aborted_marker`**: Run a turn whose
  `run_agent_turn` raises. Assert the archive contains a
  `turn_aborted` row with the canonical text after the user message.
- **`test_exception_archives_partial_then_marker`**: Streamed partial
  in `partial_assistant_chunks` + raise. Assert order is
  `user → assistant(partial) → turn_aborted`.
- **`test_exception_marker_skipped_when_already_written`**: Call the
  helper twice; assert only one marker row.
- **`test_turn_aborted_remapped_to_user_for_llm`**: Compose with a
  `turn_aborted` row in history; assert the LLM-side messages list
  has it as `role: "user"`.
- **`test_next_turn_after_exception_does_not_resynthesize`**: Build a
  history `user → turn_aborted → user`; pass through composer; assert
  the prior user request isn't re-fulfilled (LLM messages list shows
  the marker between the two user turns at the correct position).

## Out of scope

- **Max-iterations marker.** `_finalize_max_iterations` already
  archives a normal `assistant` message with the limit notice. That is
  a clear LLM-visible closure signal. Adding a marker on top would be
  double-signaling.
- **Circuit breaker marker.** Confirmed during code review: the
  breaker only declines new turns; it never aborts a turn mid-flight,
  so there is no half-archived turn to mark.
- **Refactoring cancel and abort to share a generic latch/helper.**
  Tempting but deliberately deferred — the cancel pattern works,
  refactoring it is unrelated risk.
- **UI-visible "abort reason" badge.** Out of scope. The `type: "error"`
  websocket event already drives the UI's error display.

## Acceptance criteria

- Archive contains an unambiguous `turn_aborted` row after a
  turn-aborting exception (with the canonical text).
- If a streamed partial exists, it is archived as `assistant` BEFORE
  the marker (chronological order preserved).
- LLM-side composed messages list shows the marker remapped to `user`
  between the user-turn and the next user-turn at the right position.
- The marker is written exactly once per turn (latch survives a
  pathological double-call).
- Existing cancel tests still pass — separate latches do not
  interfere.
- New tests reproduce the bug shape: exception → next user turn →
  assert LLM messages list does not synthesize a retry.

## Self-review notes

- **Defensive against exception in the marker write itself.** Helper
  wraps `append_message` in try/except and logs at `warning` —
  matches the cancel helper's fail-open posture. If the disk is full
  or the archive file is unwritable, we log and continue rather than
  re-raising and tripping the outer error handler.
- **Ordering with `emit("error", ...)`.** Marker write first, then
  emit. If the marker write itself raises (it shouldn't, given the
  internal try/except), the outer handler's `finally` block still
  runs cleanup. The UI's error event is best-effort and arrives over
  websocket — losing it is recoverable.
- **CancelledError path is unaffected.** It's in its own `except`
  branch and uses its own latch. The `try` block's `except
  Exception` catches `Exception` only, so `CancelledError` (which is
  `BaseException` in Python 3.8+) doesn't fall through.
- **Future resolution.** The caller's future still resolves with
  `[error: {e}]` (unchanged) — that string is for programmatic
  callers, not the LLM. LLM-visible content is the archive row.
- **Ephemeral task kinds (heartbeat/scheduled/child).** They use
  one-shot fork convs; the marker write goes to whatever
  `append_message` does with that conv's archive. Matches the cancel
  pattern's behavior — not changing it here.
- **No new evals required.** This change is purely
  archive/composer-side wiring; the LLM behavior consuming the
  marker is identical to how it consumes the existing
  `cancel_marker`. The eval coverage lives in #528's
  `abort_recovery.yaml`, which is the explicit consumer.
- **Docs.** Update `docs/conversations.md` (or wherever the cancel
  marker / archive roles are documented) in the same PR. Possibly
  also `docs/context-composer.md` if the role list there mentions
  `cancel_marker`.
