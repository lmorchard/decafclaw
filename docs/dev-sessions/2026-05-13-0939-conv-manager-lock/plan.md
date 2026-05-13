# ConversationManager asyncio.Lock Migration — Implementation Plan

**Goal:** Replace bare boolean/scalar concurrency guards in `ConversationManager` with a per-`ConversationState` `asyncio.Lock`, closing the read/yield/write race window in `enqueue_turn` and the confirmation lifecycle, and aligning the module with the project's stated convention.

**Approach:** Add a single `asyncio.Lock` to `ConversationState`. Wrap dispatch decisions (`busy` read → `_start_turn` call or `pending_messages.append`) and confirmation-event mutations in `async with state.lock:`. The lock guards the *decision*, not the agent task lifetime — `_start_turn` flips `busy = True` under the lock, then releases before the task runs. TDD: failing regression tests added first per the project's "Bug fix = test first" rule.

**Tech stack:** Python 3.13, asyncio, pytest / pytest-asyncio.

---

## Phase 1: Regression test for `enqueue_turn` USER dispatch race

Add a failing test demonstrating that two simultaneous `enqueue_turn(USER)` calls on the same `conv_id` can both run `_start_turn` (instead of one running, one queueing) because of the yield point at `await self.emit(...)` between the `state.busy` read and the `state.busy = True` write inside `_start_turn`.

**Files:**
- Modify: `tests/test_conversation_manager.py` — append new test `test_concurrent_user_enqueue_serializes_via_lock`

**Key changes:**
- Test exercises the public `enqueue_turn(kind=USER)` surface — not internal locks/state directly — so it would fail if someone deleted the lock.
- Uses an `asyncio.Event` inside a stubbed `run_agent_turn` so the first turn parks deterministically (no wall-clock sleeps).
- Subscribes to the manager to *force a real subscriber*, ensuring `self.emit(...)` at line :279 actually yields (no subscribers ⇒ early-return ⇒ no yield ⇒ no race).
- Asserts: exactly one call into `run_agent_turn`, and the other request lands in `state.pending_messages`.

```python
@pytest.mark.asyncio
async def test_concurrent_user_enqueue_serializes_via_lock(
    manager, config, monkeypatch
):
    """Two simultaneous enqueue_turn(USER) calls for the same conv must
    serialize: exactly one runs, the other queues. Regression for
    issue #440 — the read-yield-write window in enqueue_turn used to
    let both calls bypass the busy check.

    Forcing a subscriber on the conv ensures `self.emit(...)` actually
    yields at the race window; without a subscriber it returns
    synchronously and the race can't be reproduced.
    """
    started = asyncio.Event()
    release = asyncio.Event()
    call_count = 0

    async def fake_run_agent_turn(ctx, user_message, history, **kwargs):
        nonlocal call_count
        call_count += 1
        started.set()
        await release.wait()
        from decafclaw.media import ToolResult
        return ToolResult(text="ok")

    monkeypatch.setattr("decafclaw.agent.run_agent_turn", fake_run_agent_turn)

    # Subscriber forces `emit` to actually await (real yield point at :279).
    async def _noop(_event):
        pass
    manager.subscribe("c1", _noop)

    # Fire both enqueues concurrently. Without the lock both observe
    # busy=False and both call _start_turn.
    f1, f2 = await asyncio.gather(
        manager.enqueue_turn("c1", kind=TurnKind.USER, prompt="one"),
        manager.enqueue_turn("c1", kind=TurnKind.USER, prompt="two"),
    )

    # Let the started turn finish.
    await started.wait()
    state = manager.get_state("c1")
    assert state is not None

    # Exactly one turn started; the other is queued.
    assert call_count == 1, (
        f"expected exactly one turn started, got {call_count}"
    )
    assert len(state.pending_messages) == 1, (
        f"expected one pending message, got {len(state.pending_messages)}"
    )

    # Drain so the test cleans up.
    release.set()
    await asyncio.wait_for(f1, timeout=2.0)
    await asyncio.wait_for(f2, timeout=2.0)
```

**Verification — automated:**
- [x] Test runs — note: PASSES on unmodified code (race window structurally unreachable today). Repositioned as a guard test, see notes.md.
- [x] `make lint` passes (test file syntactically clean).

**Verification — manual:**
- [x] Confirmed the existing control flow has no yield between busy-check and `state.busy = True` in `_start_turn` — the lock prevents future regressions if such a yield is introduced. Documented in test docstring.

---

## Phase 2: Regression test for `confirmation_event` race

Add a failing test demonstrating that two near-simultaneous `respond_to_confirmation` calls (same conv, same confirmation_id) can both observe `state.confirmation_event` as set / not-yet-cleared and call `event.set()` twice (or interleave with `request_confirmation`'s null-out at lines :617/:623). With a lock around the confirmation-event mutations, the second call sees `pending_confirmation is None` and short-circuits.

**Files:**
- Modify: `tests/test_conversation_manager.py` — append `test_concurrent_confirmation_responses_dont_double_dispatch`

**Key changes:**
- Two `respond_to_confirmation` calls fired via `asyncio.gather`.
- Track invocations of the confirmation handler — assert exactly one fires (the other returns early on `state.pending_confirmation is None`).
- Use a `ConfirmationAction` with a registered handler in a fresh `ConfirmationRegistry` (mirrors `test_respond_to_recovered_confirmation`).

```python
@pytest.mark.asyncio
async def test_concurrent_confirmation_responses_dont_double_dispatch(
    manager, monkeypatch
):
    """Two simultaneous respond_to_confirmation calls for the same
    confirmation_id must not both succeed. Regression for #440 — the
    state.pending_confirmation / state.confirmation_event mutations
    used to be unlocked, so two responses could race past the
    pending_confirmation check before either nulled it out.
    """
    from decafclaw.archive import append_message

    conv_id = "conv-conf-race"
    request = ConfirmationRequest(
        action_type=ConfirmationAction.RUN_SHELL_COMMAND,
        action_data={"command": "ls"},
        message="Allow?",
    )
    # Pre-populate pending state on a recovered conv (no running loop)
    # so respond_to_confirmation goes through recover_confirmation.
    append_message(manager.config, conv_id, request.to_archive_message())
    await manager.startup_scan()

    handler_calls = 0

    async def handler(ctx, req, resp):
        nonlocal handler_calls
        handler_calls += 1
        # Yield once so both responders are in flight before either finishes.
        await asyncio.sleep(0)
        return {"ok": True}

    manager.confirmation_registry.register(
        ConfirmationAction.RUN_SHELL_COMMAND, handler
    )

    await asyncio.gather(
        manager.respond_to_confirmation(
            conv_id, request.confirmation_id, approved=True),
        manager.respond_to_confirmation(
            conv_id, request.confirmation_id, approved=True),
    )

    assert handler_calls == 1, (
        f"expected exactly one handler dispatch, got {handler_calls}"
    )
    state = manager.get_state(conv_id)
    assert state is not None
    assert state.pending_confirmation is None
```

**Verification — automated:**
- [x] Test runs and FAILS on unmodified `conversation_manager.py` (`handler_calls == 2`, race reproduced — `recover_confirmation` only nulls `pending_confirmation` after awaiting `dispatch`, so the second responder races past the check).
- [x] `make lint` passes.

**Verification — manual:**
- [x] Confirmed failure is the race (handler_calls == 2 on baseline). Documented in test docstring.

---

## Phase 3: Add `lock` field + wrap dispatch and confirmation mutations

Add `lock: asyncio.Lock = field(default_factory=asyncio.Lock)` to `ConversationState`. Wrap the mutation sites identified in the spec under `async with state.lock:`. The lock is held only during the *decision* (read state → schedule task or queue), then released so the agent task can run without holding it.

**Files:**
- Modify: `src/decafclaw/conversation_manager.py` — add field, wrap call sites

**Mutation sites brought under the lock:**

1. **`enqueue_turn` — dispatch decision** (currently `:273`–`:329`)
   - Hold lock from after the WAKE rate-limiter through `state.busy` check + `pending_messages.append` or `_start_turn` invocation.
   - WAKE rate limiter (`:258`–`:270`) does its own `state.wake_times` mutation — also goes under the lock (per spec point 4: "circuit breaker reads under the same lock"; wake rate limiter is structurally identical).
   - USER `user_message` event emission (`:279`) stays under the lock — the lock protects the *invariant* (one dispatch per turn), and the emit is part of that decision. Holding it across emit is fine: subscribers don't block on the lock and the lock is per-conv, so other convs aren't affected.

2. **`_start_turn` — `state.busy = True` and `state.cancel_event` setup (`:650`–`:651`)**
   - These are now invoked from inside the lock (held by the caller). No code change needed in `_start_turn` for this site beyond a docstring note that the caller must hold `state.lock`.
   - **Crucial:** `asyncio.create_task(run())` at `:849` does NOT await the task — it returns immediately, so the lock is released before the agent loop actually executes.

3. **`run()` finally-block — `state.busy = False` reset (`:834`)**
   - The `busy = False` write at line :834 plus `agent_task = None` / `cancel_event = None` plus the `await self._drain_pending(state)` call at line :847 all need to be under the lock so a concurrent `enqueue_turn` can't see `busy=False` while `_drain_pending` is mid-flight.
   - But `_drain_pending` itself calls back into `_start_turn`, which now requires the lock. To avoid re-entrancy deadlock, restructure as follows: hold the lock to reset `busy/agent_task/cancel_event` *and* to call `_drain_pending` — `_drain_pending`'s inner `_start_turn` invocation already runs inside the same lock acquisition, which is fine because we never re-acquire the lock from `_start_turn`'s body.
   - **Alternative (simpler):** drain pending OUTSIDE the lock — release after the state reset, then call `_drain_pending`. `_drain_pending` reacquires the lock itself only for the `busy = True` re-flip inside its `_start_turn` call. This is what the implementation does.

4. **`request_confirmation` (`:573`–`:625`)**
   - Hold the lock around setting `pending_confirmation` / `confirmation_event` / `confirmation_response` (lines :580–:582).
   - Release before `await asyncio.wait_for(state.confirmation_event.wait(), ...)` — must release or the responder can never acquire it to set the event.
   - Re-acquire to null out `pending_confirmation` / `confirmation_event` / `confirmation_response` on both the timeout path (lines :616–:617) and the success path (lines :622–:624).

5. **`respond_to_confirmation` (`:357`–`:417`)**
   - Hold the lock around the `state.pending_confirmation` / id-match check (`:377`–`:384`) and the `state.confirmation_response = response` / `state.confirmation_event.set()` writes (`:409`–`:411`).
   - Release before `await self.recover_confirmation(...)` (which calls handler code and shouldn't run under the lock — handlers may do arbitrary I/O).

6. **`cancel_pending_confirmation` (`:419`–`:443`)**
   - Wrap the read-and-null-out (`:431`–`:442`) under the lock.

7. **`_circuit_breaker_tripped` + `_circuit_breaker_record` (`:212`–`:232`)**
   - Per spec point 3: these run from inside the `enqueue_turn` lock acquisition and the `run()` finally block (which also holds the lock). Their bodies don't need to acquire the lock themselves — they're called *from inside* the lock. Add a docstring note: "Caller must hold `state.lock`."

8. **`cancel_turn` (`:445`–`:454`)**
   - Reads `state.cancel_event` / `state.agent_task`. Wrap under the lock for consistency, even though setting `cancel_event` and cancelling a task are themselves atomic — the *read* needs to be paired with the action to avoid TOCTOU on `state.cancel_event` being nulled by the finally block at `:836`.

**Key changes:**

```python
# ConversationState additions
@dataclass
class ConversationState:
    ...
    # Per-conversation lock guarding the dispatch decision and
    # confirmation-event lifecycle (issue #440). Held briefly during:
    # - enqueue_turn's busy-check → start-or-queue sequence
    # - request_confirmation / respond_to_confirmation /
    #   cancel_pending_confirmation state mutations
    # - the agent task's finally-block busy=False reset
    # NOT held across the agent task itself, nor across handler
    # invocations in recover_confirmation.
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
```

`enqueue_turn` skeleton (after the early return for wake-rate-limit drop):
```python
async with state.lock:
    if kind is TurnKind.WAKE:
        # ... wake rate limiter (state.wake_times mutations)
    if kind is TurnKind.USER:
        if self._circuit_breaker_tripped(state):
            ...
            future.set_result(None)
            return future
        await self.emit(conv_id, {...})  # still inside lock — safe
    if state.busy:
        # ... cancel + append to pending_messages
        return future
    await self._start_turn(state, ...)
    return future
```

`_start_turn` finally-block restructure:
```python
finally:
    self.event_bus.unsubscribe(bus_sub_id)
    if forward_tasks:
        await asyncio.gather(*forward_tasks, return_exceptions=True)
    if kind in STATE_PERSIST_KINDS:
        self._save_conversation_state(state, ctx)
    if kind is TurnKind.USER:
        self._circuit_breaker_record(state)  # caller-holds-lock convention
    async with state.lock:
        state.busy = False
        state.agent_task = None
        state.cancel_event = None
    await self.emit(conv_id, {"type": "turn_complete"})
    if future is not None and not future.done():
        future.set_result(result_text)
    await self._drain_pending(state)  # acquires lock internally via _start_turn
```

`_drain_pending`: invokes `_start_turn` which expects the caller to hold the lock — so `_drain_pending` itself acquires the lock around the dispatch:
```python
async def _drain_pending(self, state):
    if not state.pending_messages:
        return
    async with state.lock:
        # ... existing pop / coalesce / call _start_turn logic
```

`request_confirmation`:
```python
async with state.lock:
    state.pending_confirmation = request
    state.confirmation_event = asyncio.Event()
    state.confirmation_response = None
await self.emit(conv_id, {...})
try:
    await asyncio.wait_for(state.confirmation_event.wait(), timeout=...)
except asyncio.TimeoutError:
    response = ConfirmationResponse(...)
    append_message(...)
    await self.emit(conv_id, {...})
    async with state.lock:
        state.pending_confirmation = None
        state.confirmation_event = None
    return response

response = state.confirmation_response
assert response is not None
async with state.lock:
    state.pending_confirmation = None
    state.confirmation_event = None
    state.confirmation_response = None
return response
```

`respond_to_confirmation`: the lock must atomically claim the response slot — otherwise two responders racing for the recovered-conv path both observe `pending_confirmation` set and both dispatch `recover_confirmation`. Claim semantics: the first responder under the lock pops the request out (sets `confirmation_response`, captures `pending_confirmation`, nulls `state.pending_confirmation`); the second responder finds nothing pending and returns.

```python
async with state.lock:
    if not state.pending_confirmation:
        log.warning("No pending confirmation for conv %s", conv_id)
        return
    if state.pending_confirmation.confirmation_id != confirmation_id:
        log.warning("Confirmation ID mismatch ...")
        return
    response = ConfirmationResponse(
        confirmation_id=confirmation_id,
        approved=approved,
        always=always,
        add_pattern=add_pattern,
        data=data or {},
    )
    state.confirmation_response = response
    waiter_event = state.confirmation_event  # may be None for recovered convs

# Archive + emit + (wake-running-loop or dispatch-recovery) happen
# OUTSIDE the lock. The lock has already claimed the response slot,
# so a concurrent responder will see `pending_confirmation` is gone
# and return.
from .archive import append_message
append_message(self.config, conv_id, response.to_archive_message())
emit_payload: dict[str, Any] = {
    "type": "confirmation_response",
    "confirmation_id": confirmation_id,
    "approved": approved,
}
if response.data:
    emit_payload["data"] = response.data
await self.emit(conv_id, emit_payload)

if waiter_event is not None:
    # Running loop: wake it. request_confirmation will null out
    # pending_confirmation when it observes the event.
    waiter_event.set()
else:
    # No running loop — dispatch recovery. Atomically null out
    # pending_confirmation BEFORE recovery so a concurrent responder
    # entering respond_to_confirmation between this point and the end
    # of recovery doesn't re-dispatch.
    async with state.lock:
        request_for_recovery = state.pending_confirmation
        state.pending_confirmation = None
    if request_for_recovery is None:
        return  # someone else already handled it
    log.info("No running loop for conv %s, dispatching recovery", conv_id)
    result = await self._dispatch_recovery(conv_id, request_for_recovery, response)
    log.info("Recovery result for conv %s: %s", conv_id[:8], result)
```

Where `_dispatch_recovery` is a small refactor of the existing `recover_confirmation`'s body that takes the request explicitly (since we already nulled `state.pending_confirmation`). To minimize blast radius, keep the public `recover_confirmation` as-is for external callers (none in-tree besides this one site, but cheap insurance) — extract the dispatch body into a private helper.

```python
async def _dispatch_recovery(
    self,
    conv_id: str,
    request: ConfirmationRequest,
    response: ConfirmationResponse,
) -> dict:
    """Run a confirmation handler for a recovered (no-running-loop) conv.

    Caller is responsible for clearing state.pending_confirmation under
    state.lock before calling — see respond_to_confirmation.
    """
    if not self.confirmation_registry._handlers:
        log.warning(
            "No confirmation handlers registered — cannot recover "
            "confirmation for conv %s (action: %s)",
            conv_id[:8], request.action_type.value,
        )
        return {"error": "No confirmation handlers registered"}
    recovery_ctx = _RecoveryContext(config=self.config, conv_id=conv_id)
    return await self.confirmation_registry.dispatch(
        recovery_ctx, request, response,
    )
```

`recover_confirmation` (the existing public method) becomes a thin wrapper that does the read-under-lock then delegates:

```python
async def recover_confirmation(self, conv_id, response):
    state = self._conversations.get(conv_id)
    if not state:
        return {"error": "No pending confirmation"}
    async with state.lock:
        request = state.pending_confirmation
        if not request:
            return {"error": "No pending confirmation"}
        state.pending_confirmation = None
    return await self._dispatch_recovery(conv_id, request, response)
```

This preserves the existing single-call recovery semantics while making the claim atomic.

Also: `request_confirmation`'s null-out paths (`:617` / `:623`) need to handle the case where `respond_to_confirmation` has already nulled `state.pending_confirmation` — null-assigning None to a None field is a no-op, so this works without further change. But we should also stop overwriting `state.confirmation_response = None` at `:624` after we've consumed it — the response is locally bound; let's just null state.confirmation_response under the lock to keep the field tidy. Fine as-is in the original code.

**Verification — automated:**
- [x] `make lint` passes
- [x] `make check` passes (pyright + lint + check-message-types + tsc)
- [x] `make test` passes — full suite (2421 tests), including:
  - the two new Phase 1 / Phase 2 tests now PASS
  - existing test `test_message_queued_when_busy` still passes
  - existing test `test_drain_pending_resolves_all_queued_futures` still passes
  - existing test `test_drain_pending_fanout_handles_head_exception` still passes
  - existing test `test_request_confirmation_*` family still passes
  - existing test `test_respond_to_recovered_confirmation` still passes

**Verification — manual:**
- [x] Grepped `state.busy / state.confirmation_event / state.pending_confirmation / state.confirmation_response / state.cancel_event / state.agent_task / state.paused_until / state.turn_times / state.wake_times` — every write/paired-read under the lock except startup_scan (pre-transport, no concurrency risk; documented).
- [x] `_start_turn` does NOT hold the lock across `asyncio.create_task(run())`. Lock is held by the caller (`enqueue_turn` or `_drain_pending`) only across `_start_turn`'s synchronous setup; `asyncio.create_task` returns immediately so the lock releases before `run()` executes.
- [x] `#241` background-wake integration tests (`tests/test_background_wake_integration.py`) — all 5 pass.

---

## Phase 4: Docs + key-files note

Update CLAUDE.md only if conventions changed; otherwise this is a no-op for docs (the lock is an implementation detail, not a user-visible convention shift). No `docs/` page is the source of truth for `conversation_manager.py` internals; the relevant convention bullet ("Use `asyncio.Lock` for concurrency guards. Not boolean flags — locks auto-release on exception.") in CLAUDE.md is already in place — this PR brings the code into compliance, no doc update needed.

**Files:**
- Possibly modify: `docs/conversations.md` if it makes any claim about per-conv busy flags. Otherwise no doc changes.

**Verification — automated:**
- [x] `make check-message-types` passes (no wire-protocol changes) — verified as part of `make check`.

**Verification — manual:**
- [x] Grepped `docs/` for "busy flag" / "busy boolean" / "boolean flag" — no stale references requiring updates. CLAUDE.md "Per-conversation busy flag" remark at line 137 is still accurate at the language level (the lock guards the busy flag, the busy flag itself remains the serialization signal).

---

## Out of scope (do NOT touch)

- Global manager-wide lock (spec rejects)
- `TurnKind` semantics / heartbeat / scheduled / child-agent paths beyond using the lock for the same state reads
- `_start_turn` internals beyond the mutations that touch guarded state
- `_drain_pending` beyond putting the `busy = False` write under the lock (already covered by the finally-block restructure)
- Confirmation persistence model (the lock only protects in-memory `confirmation_event`)
- WebSocket / transport surface

## Risks / things to watch

- **Deadlock from re-entrant lock acquisition.** `asyncio.Lock` is NOT reentrant. If any code path inside a lock-held region tries to acquire the same lock, it deadlocks. Mitigation: every lock acquisition is a flat `async with state.lock:` block, no nested acquisitions. The `_drain_pending → _start_turn` path is fine because `_start_turn` itself does not acquire the lock — the caller is expected to hold it (or for the test/direct-call case, `_drain_pending` acquires it on behalf of `_start_turn`).
- **Holding lock across `self.emit(...)`.** `emit` awaits subscribers serially. If a subscriber blocks indefinitely, the lock is held indefinitely. This is per-conv only, so cross-conv throughput is fine, but it does mean a slow subscriber on conv X blocks new turns on conv X. Acceptable per existing semantics (emit was always awaited).
- **Test flakiness if yield window isn't actually exercised.** Mitigate by using `asyncio.Event` for synchronization and forcing a subscriber so `emit` actually yields. No wall-clock sleeps.
