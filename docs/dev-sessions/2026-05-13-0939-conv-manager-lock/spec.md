# ConversationManager asyncio.Lock Migration Spec

**Goal:** Replace the bare boolean/scalar concurrency flags in `ConversationManager` with an `asyncio.Lock` per `ConversationState`, closing a narrow but real race window for same-conversation `USER`-kind turns and aligning the module with the project's concurrency-guard convention.

**Source:** [Issue #440](https://github.com/lmorchard/decafclaw/issues/440)

## Current state

`src/decafclaw/conversation_manager.py` guards per-conversation state (`busy`, `paused_until`, `confirmation_event`) with bare booleans / scalars. CLAUDE.md explicitly says: *"Use `asyncio.Lock` for concurrency guards. Not boolean flags — locks auto-release on exception."*

The race window in `enqueue_turn` (`conversation_manager.py:273`):

```python
# :273 USER-kind only:
if kind is TurnKind.USER:
    if self._circuit_breaker_tripped(state):   # reads state.paused_until
        ...
    await self.emit(conv_id, {"type": "user_message", ...})   # :279 YIELD POINT

# :285
if state.busy:
    ...append to pending_messages and return
...
await self._start_turn(...)   # :315 — sets state.busy=True at _start_turn:650 (after more awaits)
```

Between the `await self.emit(...)` at `:279` and the `state.busy = True` at `_start_turn:650`, another `enqueue_turn(USER)` for the same `conv_id` can land, see `busy=False`, and start a second concurrent agent task. Same shape applies for `paused_until` (read at `:216`, written at `:221`) and `confirmation_event` swap/null/signal at `:410`/`:581`/`:617`/`:623`.

Failure mode is invisible in production today because Mattermost websocket events are processed serially and per-user web sockets are serial too — but rapid double-send from a single tab, two tabs on the same conv, or a `USER` turn racing a `WAKE`/scheduled turn at the same `conv_id` would all trigger it, and "two concurrent agent loops on the same conv" is brutal to debug from logs.

## Desired end state

1. `ConversationState` gains an `asyncio.Lock` field (suggested name: `lock`).
2. `enqueue_turn`'s busy-check → enqueue-or-start sequence is wrapped in `async with state.lock:` — covers reading `state.busy` and the dispatch into `_start_turn` / `pending_messages.append`.
3. `_circuit_breaker_tripped` / `_circuit_breaker_record` read-then-write of `paused_until` runs under the same lock.
4. `confirmation_event` mutations (`request_confirmation` create at `:581`, signal at `:410`, null-out at `:617`/`:623`) run under the same lock.
5. `_start_turn` does NOT hold the lock across the agent task lifetime — it sets `state.busy = True`, schedules the task, and releases. The agent task clears `busy` from `_drain_pending` (`:894`); that mutation runs inside the lock too.
6. New regression test: dispatch two simultaneous `enqueue_turn(USER)` calls for the same `conv_id`; assert exactly one runs and the other lands in `pending_messages`.
7. New regression test: `request_confirmation` racing a duplicate `respond_to_confirmation` doesn't drop or double-deliver.

## Design decisions

- **Decision:** One lock per `ConversationState`, not per-attribute locks.
  - **Why:** The flags are interdependent — `busy`, `paused_until`, and `confirmation_event` participate in the same dispatch decision. Splitting locks would introduce ordering hazards. One lock per conv is also the project's stated convention.
  - **Rejected:** A single global manager-wide lock — kills cross-conversation throughput for no benefit.

- **Decision:** Lock is acquired during the dispatch decision but released before the agent task runs.
  - **Why:** Holding the lock across the agent loop would serialize *all* updates to that conv (including `_drain_pending`) for the duration of a turn. The lock protects the *decision*, not the *execution*.
  - **Rejected:** Holding lock through the agent task — would deadlock anything `_drain_pending` waits on.

- **Decision:** Bug fix → write a failing test first, per project convention.
  - **Why:** The race is narrow; the regression test is what proves we actually closed the window. Without a test we can't tell the new code from a no-op.

## Patterns to follow

- Test concurrency primitives use `asyncio.gather` with deliberate yields (`await asyncio.sleep(0)`) to interleave coroutines deterministically — see existing patterns in `tests/` for prior art.
- Don't `asyncio.sleep(X)` to wait for work in tests; wait on actual signals (`event.wait()`, `await task`, patched clocks) per CLAUDE.md "Test speed discipline."

## What we're NOT doing

- **NOT introducing a global manager lock** — per-conv only.
- **NOT changing `TurnKind` semantics or the heartbeat/scheduled/child-agent paths** beyond making them use the lock for the same state reads.
- **NOT refactoring `_start_turn` internals** — only the mutations that touch the guarded state.
- **NOT touching `_drain_pending` beyond putting the `busy = False` write under the lock.**
- **NOT changing the confirmation persistence model** — the lock only protects in-memory `confirmation_event`.
- **NOT modifying the websocket/transport surface.**

## Validation

- `make check` (lint + typecheck) green.
- `make test` green.
- New regression tests added per "Desired end state" #6 and #7, watched fail then pass (TDD).
- `#241` background-wake integration test still passes.

## Open questions

- None blocking — issue body fully specifies which mutation sites need locking. Naming (`lock` vs `state_lock`) is plan-phase detail.
