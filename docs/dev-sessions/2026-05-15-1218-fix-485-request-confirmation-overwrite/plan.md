# Queue concurrent request_confirmation — Implementation Plan

**Goal:** Replace `request_confirmation`'s unconditional pending-slot overwrite with a per-conversation FIFO queue so simultaneous approval requests (esp. from delegated background tasks) all reach the user serially.

**Approach:** Add a `confirmation_queue: list[_QueuedConfirmation]` on `ConversationState`. When `request_confirmation` lands while `state.pending_confirmation is not None`, enqueue a `_QueuedConfirmation(request, event)` and block on its own event until promoted. A single `_promote_next_confirmation_unlocked` helper, called from every clear-active path (respond, cancel, timeout, cancel-raced), pops the next waiter and signals it. Timeout starts at promote time, not at enqueue. #486 stays separate.

**Tech stack:** Python 3, `asyncio.Lock` / `asyncio.Event`, existing `confirmations.py` types, pytest + pytest-asyncio. No new dependencies.

---

## Phase 1: Queue + concurrent-request regression slice

End-to-end slice: failing regression test → queue scaffolding → enqueue / promote / wire-up at all clear-active call sites → test passes. After this phase, two concurrent `request_confirmation` calls on the same conv resolve to their own responses in submission order.

**Files:**
- Modify: `src/decafclaw/conversation_manager.py`
  - Add `_QueuedConfirmation` dataclass (module-level, near `ConversationState`).
  - Add `confirmation_queue: list[_QueuedConfirmation] = field(default_factory=list)` to `ConversationState` (next to the existing confirmation-state triple at L188–191).
  - Add `_promote_next_confirmation_unlocked(state, conv_id) -> ConfirmationRequest | None`. Caller must hold `state.lock`; returns the promoted request (None if queue empty); side-effect installs the promoted request into the active slot and signals its event.
  - Add a private helper `_confirmation_request_payload(request) -> dict` so the multiple emit sites share one payload-builder.
  - Rework `request_confirmation` (L763–889):
    - Archive (unchanged, pre-lock).
    - Under lock: if `state.pending_confirmation is None`, install active (old path); else create a `_QueuedConfirmation`, append to `state.confirmation_queue`, capture local handle.
    - For the queued branch only: `await queued.promoted_event.wait()` (no timeout) outside the lock. Promote helper signals this event when it activates the entry.
    - Once active (either path), execute the existing post-wait claim block (`asyncio.wait_for(event.wait(), timeout=request.timeout)` plus the three-branch resolution at L837–887). When the active slot is cleared in any of the three branches, call `_promote_next_confirmation_unlocked` under the same lock; capture its return value; emit the new `confirmation_request` outside the lock if one was promoted.
  - Modify `respond_to_confirmation` (L501–558):
    - After the existing claim-and-clear at L501–505 (still under lock), call `_promote_next_confirmation_unlocked` and capture the result.
    - Emit the promoted request's `confirmation_request` event outside the lock (same place as the existing `confirmation_response` emit).
    - Recovery branch (`waiter_event is None`): identity check stays as-is (out of scope per spec — that's #486).
  - Modify `cancel_pending_confirmation` (L625–633):
    - After the existing claim-and-clear at L625–627, call `_promote_next_confirmation_unlocked` and capture the result.
    - Emit promoted `confirmation_request` outside the lock (after the `waiter_event.set()` at L632–633).
- Add: `tests/test_conversation_manager.py`
  - `test_concurrent_request_confirmation_serializes` — two `asyncio.create_task` calls of `request_confirmation` on same `conv_id`. After awaiting an iteration of the event loop, assert task B is not done (it's queued behind A). `respond_to_confirmation` to A; A completes. Then `respond_to_confirmation` to B; B completes. Both responses have correct `confirmation_id` and `approved=True`.
  - `test_promoted_confirmation_emits_request_event` — install a subscriber capturing emits; submit two concurrent requests; respond to first; assert two `confirmation_request` events fire (one per submission), in correct order.

**Key changes:**

```python
# src/decafclaw/conversation_manager.py — module level near ConversationState

@dataclass
class _QueuedConfirmation:
    """A confirmation request waiting to become active.

    Holds the request and a per-request asyncio.Event the waiter
    blocks on. The promote helper signals promoted_event when the
    queued entry is moved to the active slot, releasing the waiter
    to run the post-wait claim block.
    """
    request: ConfirmationRequest
    promoted_event: asyncio.Event = field(default_factory=asyncio.Event)


# On ConversationState (next to pending_confirmation / confirmation_event / confirmation_response):

    # FIFO queue of confirmations awaiting their turn. Each entry is
    # a request that arrived while another was active; the waiter
    # blocks on the entry's promoted_event until
    # _promote_next_confirmation_unlocked moves it to the active slot.
    confirmation_queue: list[_QueuedConfirmation] = field(default_factory=list)


# Module-level payload helper:
def _confirmation_request_payload(request: ConfirmationRequest) -> dict:
    return {
        "type": "confirmation_request",
        "confirmation_id": request.confirmation_id,
        "action_type": request.action_type.value,
        "action_data": request.action_data,
        "message": request.message,
        "approve_label": request.approve_label,
        "deny_label": request.deny_label,
        "tool_call_id": request.tool_call_id,
    }


# ConversationManager — new helper method
def _promote_next_confirmation_unlocked(
    self, state: "ConversationState", conv_id: str,
) -> ConfirmationRequest | None:
    """Move the next queued confirmation (if any) into the active slot.

    Caller must hold ``state.lock``. The active slot must be clear
    (pending_confirmation / confirmation_event / confirmation_response
    all None) when this is called — promote is the *next step* after
    the active slot is cleared by respond/cancel/timeout/cancel-raced.

    Side effects (all under lock):
      - state.pending_confirmation = queued.request
      - state.confirmation_event = asyncio.Event()  (fresh)
      - state.confirmation_response = None
      - queued.promoted_event.set()  (wakes the queued waiter)

    Returns the promoted request so the caller can emit the
    confirmation_request event outside the lock, or None if the queue
    is empty.
    """
    if not state.confirmation_queue:
        return None
    queued = state.confirmation_queue.pop(0)
    state.pending_confirmation = queued.request
    state.confirmation_event = asyncio.Event()
    state.confirmation_response = None
    queued.promoted_event.set()
    log.info("Promoted queued confirmation for conv %s: %s",
             conv_id[:8], queued.request.action_type.value)
    return queued.request


# request_confirmation — restructured

async def request_confirmation(self, conv_id, request):
    state = self._get_or_create(conv_id)
    # Archive always (spec: archive at enqueue time so crash-recovery
    # sees every requested confirmation).
    from .archive import append_message
    append_message(self.config, conv_id, request.to_archive_message())

    async with state.lock:
        if state.pending_confirmation is None:
            state.pending_confirmation = request
            state.confirmation_event = asyncio.Event()
            state.confirmation_response = None
            event = state.confirmation_event
            queued = None
        else:
            queued = _QueuedConfirmation(request=request)
            state.confirmation_queue.append(queued)
            event = None
            log.info("Conv %s has active confirmation; queued new request "
                     "(%d in queue)", conv_id[:8], len(state.confirmation_queue))

    if queued is None:
        await self.emit(conv_id, _confirmation_request_payload(request))
    else:
        # Wait for promotion. Phase 2 hardens this branch.
        try:
            await queued.promoted_event.wait()
        except asyncio.CancelledError:
            async with state.lock:
                if queued in state.confirmation_queue:
                    state.confirmation_queue.remove(queued)
            raise
        async with state.lock:
            event = state.confirmation_event
        await self.emit(conv_id, _confirmation_request_payload(request))

    # Post-wait claim block — today's logic, with promote added to each
    # clear-active branch.
    try:
        await asyncio.wait_for(event.wait(), timeout=request.timeout)
        timed_out = False
    except asyncio.TimeoutError:
        timed_out = True

    needs_timeout_emit = False
    promoted_after: ConfirmationRequest | None = None
    async with state.lock:
        claimed = state.confirmation_response
        if claimed is not None:
            response = claimed
            state.pending_confirmation = None
            state.confirmation_event = None
            state.confirmation_response = None
            promoted_after = self._promote_next_confirmation_unlocked(
                state, conv_id)
        elif timed_out:
            log.info("Confirmation timed out for conv %s: %s",
                     conv_id[:8], request.message)
            response = ConfirmationResponse(
                confirmation_id=request.confirmation_id, approved=False,
            )
            try:
                append_message(
                    self.config, conv_id, response.to_archive_message(),
                )
            except Exception:
                log.exception(
                    "Timeout archive write failed for conv %s; leaving "
                    "pending state intact for retry", conv_id[:8])
                raise
            state.pending_confirmation = None
            state.confirmation_event = None
            state.confirmation_response = None
            promoted_after = self._promote_next_confirmation_unlocked(
                state, conv_id)
            needs_timeout_emit = True
        else:
            response = ConfirmationResponse(
                confirmation_id=request.confirmation_id, approved=False,
            )
            state.pending_confirmation = None
            state.confirmation_event = None
            state.confirmation_response = None
            promoted_after = self._promote_next_confirmation_unlocked(
                state, conv_id)
            log.info("Confirmation for conv %s was cancelled out from "
                     "under the waiter; returning denial", conv_id[:8])

    if needs_timeout_emit:
        await self.emit(conv_id, {
            "type": "confirmation_response",
            "confirmation_id": request.confirmation_id,
            "approved": False,
        })
    if promoted_after is not None:
        await self.emit(conv_id, _confirmation_request_payload(promoted_after))

    return response
```

**Verification — automated:**
- [x] `make lint` passes
- [x] `make typecheck` passes
- [x] `make test` passes — full suite green (2509 passed; +2 new tests)
- [x] `pytest tests/test_conversation_manager.py -k "concurrent_request_confirmation or promoted_confirmation_emits" -v` passes
- [x] `pytest --durations=10 tests/test_conversation_manager.py` — new tests do not appear in top-10 slowest (top is 0.66s; new tests at ~0.0s)

**Verification — manual** (deferred to branch self-review per express mode):
- [ ] Walk: tool A and tool B in same turn both call `request_confirmation`. A becomes active. B queues. User approves A. B promotes and emits its `confirmation_request`. User approves B. Both responses return correctly.
- [ ] Walk: A active, B queued, A times out. A's timeout denial archived and emitted. B promotes, gets emitted, runs its own timeout countdown.

---

## Phase 2: Queue-waiter cancellation cleanup

Harden the queued-wait branch so cancellation cleans up correctly whether the waiter was still queued OR had just been promoted before cancel arrived.

**Files:**
- Modify: `src/decafclaw/conversation_manager.py`
  - Refactor the `except asyncio.CancelledError` block in `request_confirmation`'s queued-wait branch to: re-acquire the lock, check whether `queued` is still in the queue (simple drop) or has been promoted (clear active slot + promote next + emit outside lock).
- Add: `tests/test_conversation_manager.py`
  - `test_cancelled_queued_waiter_removed_from_queue` — submit active A + queued B; cancel B's task; respond to A; assert state.confirmation_queue is empty, A resolves normally, B's task raised `CancelledError`.
  - `test_cancelled_after_promote_drains_to_next` — submit A + B + C; respond to A so B promotes; cancel B's task before B observes the promote; assert C promotes and is then resolvable via `respond_to_confirmation`. Guards the "cancelled-after-promote" edge case.

**Key changes:**

```python
# request_confirmation — queued-wait branch with hardened cleanup

if queued is not None:
    try:
        await queued.promoted_event.wait()
    except asyncio.CancelledError:
        promoted_after: ConfirmationRequest | None = None
        async with state.lock:
            if queued in state.confirmation_queue:
                state.confirmation_queue.remove(queued)
            elif state.pending_confirmation is request:
                # Promoted but cancelled before consuming. Clear and
                # promote the next entry so the queue doesn't stall.
                state.pending_confirmation = None
                state.confirmation_event = None
                state.confirmation_response = None
                promoted_after = self._promote_next_confirmation_unlocked(
                    state, conv_id)
        if promoted_after is not None:
            await self.emit(
                conv_id, _confirmation_request_payload(promoted_after))
        raise
    async with state.lock:
        event = state.confirmation_event
    await self.emit(conv_id, _confirmation_request_payload(request))
```

**Verification — automated:**
- [x] `make lint` passes
- [x] `make typecheck` passes
- [x] `make test` passes (2511 passed)
- [x] `pytest tests/test_conversation_manager.py -k "cancelled_queued or cancelled_after_promote" -v` passes

**Verification — manual** (deferred to branch self-review per express mode):
- [ ] Walk: A active, B+C queued. B's task is cancelled. B's `CancelledError` removes B from the queue. C is still queued. A continues normally; on A's resolution, C promotes.
- [ ] Walk: A resolves, B promotes, B's task is cancelled before consuming the promote signal. Cleanup clears B's active slot and promotes C.

---

## Phase 3: Timeout-from-promote + cancel-pending behavior tests

Lock down the two remaining behaviors from the spec via tests (no new production code — these follow from Phase 1's structure). Structural-invariant guard for the queue's two non-obvious semantics.

**Files:**
- Add: `tests/test_conversation_manager.py`
  - `test_queued_confirmation_timeout_starts_at_promote` — submit A with `timeout=10.0` (long); submit B with `timeout=0.05` (short). After ~0.2s of wall time, assert B's task is not done (it's queued; the 0.05s deadline only applies post-promote, not while queued). Respond to A. Then assert B times out 0.05s after promotion (B's task completes with a denial whose `confirmation_id` matches B's). Total wall < ~0.5s. No `asyncio.sleep` longer than ~0.05–0.2s — use `asyncio.wait_for(...task..., timeout=...)` patterns.
  - `test_cancel_pending_promotes_next_queued` — submit A active + B queued; call `cancel_pending_confirmation(conv_id)`; assert A's task returns a denial response (existing cancel behavior); assert B promotes (state.pending_confirmation is B's request); resolve B via `respond_to_confirmation`; assert B's task gets approval. Capture emits and assert ordering: `confirmation_response (A denial)` then `confirmation_request (B promoted)`.
  - `test_cancel_pending_with_empty_queue_unchanged` — control: submit only A; cancel; assert behavior identical to today's denial + no promote event emitted; `state.confirmation_queue` stays empty.

**Verification — automated:**
- [x] `make lint` passes
- [x] `make test` passes (2514 passed)
- [x] `pytest tests/test_conversation_manager.py -k "queued_confirmation_timeout or cancel_pending_promotes or cancel_pending_with_empty" -v` passes
- [x] `pytest --durations=25 tests/test_conversation_manager.py` — `test_queued_confirmation_timeout_starts_at_promote` lands at #9 (0.39s) because it intentionally awaits real time to verify the post-promote timeout property. This is by design, not an accidental sleep; the other Phase 3 tests are sub-0.05s. Acceptable.

**Verification — manual** (deferred to branch self-review per express mode):
- [ ] Re-read `test_queued_confirmation_timeout_starts_at_promote` and confirm the assertion is on *post-promote* elapsed time. The wrong assertion ("B's task finishes within 0.05s of submission") would be a false positive against the queue-deferred timeout semantics.

---

## Phase 4: Docs update + final commit

Update the one user-facing doc that describes the confirmation lifecycle. The internal docstrings on `request_confirmation` and `_promote_next_confirmation_unlocked` already cover implementation; this phase touches public-facing prose only.

**Files:**
- Modify: `docs/architecture.md` — "Confirmations" section. Append a one-line note: "Concurrent `request_confirmation` calls on the same conversation are queued FIFO; the user sees them one at a time and each gets its own response."
- Modify: `src/decafclaw/conversation_manager.py` — extend the docstring of `request_confirmation` to mention the queue path and the "timeout starts at promote" semantics. Extend the "Per-conversation lock guarding..." comment on `ConversationState.lock` (L150–186) to add a bullet for "the confirmation queue mutations and promote-the-next-on-clear".

No changes to:
- `CLAUDE.md` — confirmation conventions are unchanged; queue is internal.
- `docs/websocket-messages.md` — wire format unchanged (still one `confirmation_request` at a time).
- `docs/skills.md` — `request_confirmation` import/usage unchanged.
- `docs/index.md` — no new top-level page.

**Verification — automated:**
- [x] `make check` passes (Python lint + typecheck + JS check)
- [x] `make test` passes (2514 passed)
- [x] `grep -n "confirmation_queue\|_QueuedConfirmation\|_promote_next_confirmation_unlocked" src/decafclaw/conversation_manager.py` shows the new identifiers across the dataclass, the lock-comment, the helper definition, and the four call sites (queued-wait cleanup, post-wait-claimed/timeout/cancel-raced, respond-recovery, cancel-no-waiter)

**Verification — manual** (handled at branch self-review):
- [ ] `docs/architecture.md` Confirmations section reads cleanly with the new sentence — no contradiction with the existing "agent loop suspends mechanically" description.
- [ ] Skim full diff one more time against `spec.md`'s "What we're NOT doing" — no out-of-scope changes (no #486 fix, no new wire types, no API changes, no archive-format changes).
