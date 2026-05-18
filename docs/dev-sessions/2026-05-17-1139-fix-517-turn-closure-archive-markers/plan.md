# Plan — Turn-closure archive marker for exception path (#517)

Small, focused fix mirroring the cancel-marker pattern from #491. Five
phases. Each phase ends with `make lint && make test` (the relevant
subset) and a commit. The whole change is sized S/M — keep it tight.

---

## Phase 1: Composer remap (failing test → fix)

**Goal:** `turn_aborted` role in history reaches the LLM as
`role: "user"`, matching the existing `cancel_marker` pattern.

### Files

- `tests/test_context_composer.py` — extend `TestCancelMarker` (or add a
  new `TestTurnAbortedMarker` class) with a parallel test:
  `test_turn_aborted_remapped_to_user`. History contains
  `[user, assistant(partial), turn_aborted]`; assert composed messages
  list shows a `user`-role message whose content includes "failed
  unexpectedly", and that no `turn_aborted` role survives in the
  composed messages.
- `src/decafclaw/context_composer.py` — add `"turn_aborted": "user"`
  to `ROLE_REMAP` (line 24-29), in alphabetical position after
  `cancel_marker`.

### Verify

```
pytest tests/test_context_composer.py -k turn_aborted -v
```

Test passes. Commit:

> feat(conversation): remap turn_aborted role to user in composer (#517)

---

## Phase 2: Marker constant + helper + latch in `conversation_manager`

**Goal:** `_write_turn_aborted_marker_once` exists and behaves
identically to `_write_cancel_marker_once`, gated on its own latch.

### Files

- `src/decafclaw/conversation_manager.py`:
  - Add module-level constant after `CANCEL_MARKER_TEXT`:

    ```python
    # Canonical archive text written when a turn aborts via an
    # unexpected exception (issue #517). Follow-up to #491: same
    # "turn closed" signal shape as CANCEL_MARKER_TEXT but for the
    # exception path, so the next user turn doesn't see an open prior
    # request and re-fulfill it. Defensive wording — does not echo
    # the raw exception, which could leak internal state.
    TURN_ABORTED_MARKER_TEXT = (
        "[The previous turn failed unexpectedly. Treat the prior "
        "request as not fulfilled and wait for the user to clarify.]"
    )
    ```

  - Add field to `ConversationState` right after
    `cancel_marker_written` (line ~240):

    ```python
    # `turn_aborted_marker_written` is a write-once latch for the
    # exception-path marker, parallel to cancel_marker_written
    # (issue #517).
    turn_aborted_marker_written: bool = False
    ```

  - Extend the docstring block above the streaming/cancel fields to
    mention the new field briefly (one sentence).

  - Add `_write_turn_aborted_marker_once` method next to
    `_write_cancel_marker_once` (~line 806). Mirror its structure
    exactly:
    - Early return if `state.turn_aborted_marker_written`.
    - Archive any unflushed `partial_assistant_chunks` (gated on
      `state.partial_assistant_archived`), set the flag on success.
    - Append `{role: "turn_aborted", content:
      TURN_ABORTED_MARKER_TEXT}`. Set latch on success.
    - Fail-open `log.warning` on archive-write exceptions.

  - In `_start_turn`'s per-turn reset block (~line 1320), add:

    ```python
    state.turn_aborted_marker_written = False
    ```

### Verify

```
make lint
pytest tests/test_conversation_manager.py -v
```

Existing tests still pass; new helper compiles. (No new test yet — call
sites land in Phase 3.) Commit:

> feat(conversation): add turn_aborted marker helper + latch (#517)

---

## Phase 3: Wire helper into the exception handler

**Goal:** Generic `except Exception` block in `_start_turn`'s `run()`
task writes the marker before emitting the `type: "error"` event.

### Files

- `src/decafclaw/conversation_manager.py`, around line 1440:

  ```python
  except Exception as e:
      log.error("Agent turn failed for conv %s: %s",
                conv_id[:8], e, exc_info=True)
      response_text_holder.append(f"[error: {e}]")
      # Persist a turn-closure marker so the next turn's LLM doesn't
      # re-fulfill the failed request (issue #517).
      self._write_turn_aborted_marker_once(conv_id, state)
      await self.emit(conv_id, {
          "type": "error",
          "message": f"Agent turn failed: {e}",
      })
  ```

### Verify

```
make lint
pytest tests/test_conversation_manager.py -v
```

Commit:

> feat(conversation): write turn_aborted marker on exception path (#517)

---

## Phase 4: Test coverage for the exception path

**Goal:** Reproduce the bug shape and lock in the fix with unit tests.
Mirror the cancel-marker test style.

### Files

`tests/test_conversation_manager.py` — add a `TestTurnAbortedMarker`
section (or just standalone tests) after the cancel tests (~line 540):

1. **`test_write_turn_aborted_marker_once_latches`**: Call the helper
   twice on the same state; assert only one `turn_aborted` row exists
   in the archive and the latch is `True`.

2. **`test_exception_during_turn_archives_marker`**: End-to-end fake
   `run_agent_turn` that archives a user row then raises
   `RuntimeError("boom")`. Assert archive ends with a `turn_aborted`
   row whose content is `TURN_ABORTED_MARKER_TEXT`. Assert no
   `assistant` row (no partial streamed).

3. **`test_exception_during_turn_archives_partial_then_marker`**:
   Fake agent streams partial chunks via `ctx.on_stream_chunk`, then
   raises `RuntimeError`. Archive order:
   `["user", "assistant", "turn_aborted"]`. Partial content
   preserved.

4. **`test_exception_partial_already_archived_no_duplicate`**: Fake
   agent streams chunks, calls
   `ctx.manager.note_partial_assistant_archived(ctx.conv_id)`, then
   raises. Assert no duplicate `assistant` row — order is
   `["user", "assistant", "turn_aborted"]` (one assistant, written by
   the loop).

5. **`test_turn_aborted_marker_written_resets_each_turn`**: Mirrors
   the existing `test_partial_assistant_chunks_resets_each_turn`.
   Force the flag True, run a non-failing turn, assert it's reset
   to False at turn start.

Import `TURN_ABORTED_MARKER_TEXT` from the module.

### Verify

```
pytest tests/test_conversation_manager.py -v
```

All new tests pass; all existing tests still pass. Commit:

> test(conversation): cover turn_aborted marker write paths (#517)

---

## Phase 5: Documentation update

**Goal:** Same-PR doc updates per CLAUDE.md's "Keeping docs current"
convention.

### Files

- `docs/conversations.md` — the "Cancelled turns" section (around
  line 30) is the obvious neighbor. Add a parallel sub-section
  "Aborted turns (exceptions)" or extend the existing block to
  describe the `turn_aborted` marker:
  - Same shape as cancel: optional partial → marker, latched write,
    role remapped to `user`.
  - Note that this covers the generic `except Exception` branch in
    `conversation_manager.py`; max-iterations has its own assistant
    closure message; circuit breaker doesn't abort mid-flight.
  - Reference issue #517.

- `docs/context-composer.md` — extend the ROLE_REMAP paragraph (line
  119) to mention `turn_aborted` alongside `cancel_marker`. Same
  rationale (remap preserves marker position across provider
  message-shape differences).

### Verify

Re-read both doc sections — they read coherently with the existing
cancel-marker prose.

```
make lint
make test
```

Commit:

> docs: describe turn_aborted archive marker (#517)

---

## Post-plan: full validation

After Phase 5:

```
make check     # lint + typecheck (Python + JS)
make test      # full suite
```

If `make check` flags anything (typing, JSON message-types drift), fix
in a follow-up commit before opening the PR.

No `make eval-*` needed — this change is purely archive/composer-side
wiring; LLM-visible role remap parallels existing `cancel_marker`. The
behavior consumer is #528's `abort_recovery.yaml` eval suite, which
lives in its own issue.

---

## Risk notes

- **Latch reset ordering.** The reset must happen at turn START, not
  end (matches `cancel_marker_written`). A prior failed turn might
  leave the latch True; if we forgot to reset, a future failure in
  the SAME conversation would silently skip the marker write. Phase 2
  + Phase 4 test #5 catch this.
- **Exception during marker write.** The helper's internal try/except
  on `append_message` matches the cancel helper's posture: log at
  `warning`, swallow, leave the latch unset so a subsequent path
  (none exists today, but parallel to cancel's defense) could retry.
  Not setting the latch on failure is intentional — same as cancel.
- **Order of operations in the except block.** Marker write goes
  BEFORE `emit("error", ...)`. Archive write is load-bearing for the
  LLM's next turn; the websocket emit is best-effort UI. If the
  marker write itself somehow raises through its internal guard, the
  outer `finally` cleanup still runs.
- **No interaction with `CancelledError`.** `except Exception` does
  not catch `BaseException` subclasses like `CancelledError`. The two
  latches are independent. Phase 4 test #1's latch behavior + existing
  cancel tests cover this.
- **Ephemeral conv kinds (heartbeat/scheduled/child).** Marker writes
  go to whatever `append_message` does for those convs (one-shot
  fork). Matches cancel's behavior — not changed here.
