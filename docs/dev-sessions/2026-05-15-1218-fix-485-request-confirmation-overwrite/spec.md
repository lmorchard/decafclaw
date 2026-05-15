# Queue concurrent request_confirmation calls per conversation

**Goal:** When two or more `request_confirmation` calls happen concurrently on the same conversation, serialize them through a FIFO queue instead of letting the second clobber the first's pending-confirmation slot.

**Source:** https://github.com/lmorchard/decafclaw/issues/485

## Current state

`ConversationManager.request_confirmation` (`src/decafclaw/conversation_manager.py:763`) unconditionally writes the per-conversation pending-confirmation triple under `state.lock`:

```python
state.pending_confirmation = request
state.confirmation_event = asyncio.Event()
state.confirmation_response = None
```

(Lines 790–794.) The lock from #484 makes that transition atomic, but doesn't stop the **overwrite** itself. Two tools dispatched concurrently by `execute_tool_calls` (`src/decafclaw/tool_execution.py:282–364`) — common in delegated background tasks producing parallel approval prompts — both hit `request_confirmation` on the same `conv_id`. The second's setup orphans the first waiter on a now-dead event; the first request never gets a response.

`cancel_pending_confirmation` (L560–634) already guards against overwriting a claimed response (L586–593). The asymmetry between cancel (defers to live claim) and request_confirmation (clobbers it) is the bug shape.

See `research.md` for the full trace.

## Desired end state

- A second concurrent `request_confirmation` on the same conv enqueues behind any active confirmation and waits its turn. Each request still resolves to its own `ConfirmationResponse` keyed by its own `confirmation_id`.
- The active slot (`state.pending_confirmation` / `state.confirmation_event` / `state.confirmation_response`) is mutated only by activation/completion of the queue head — never overwritten by a queued request.
- The UI/transport layer sees exactly one `confirmation_request` event at a time per conv. When the active confirmation resolves (respond / cancel / timeout), the next queued request is promoted and a fresh `confirmation_request` event fires for it.
- Queued requests are invisible to the UI until they reach the head — no "queued" state surfaced; queue is an implementation detail of `ConversationManager`.
- Existing `request_confirmation` callers (tool authors, widget input, skill confirmations) don't change. The semantics they observe are: "I get my response when the user gets around to it."

## Design decisions

- **Decision:** Implement a FIFO queue on `ConversationState`; the queue is the list of *not-yet-active* requests waiting for a turn. The currently-active confirmation continues to live in `pending_confirmation` / `confirmation_event` / `confirmation_response` exactly as today.
  - **Why:** Minimum change to the active-slot semantics. Existing code paths (`respond_to_confirmation`, `cancel_pending_confirmation`, startup recovery, archive serialization) keep working on the active slot. The queue only adds enqueue-on-collision and promote-on-completion behavior.
  - **Rejected:** Replacing the active triple with a list and always indexing by head — touches more surface, more risk for the same outcome.

- **Decision:** A queued request carries its own `asyncio.Event` and a `response: ConfirmationResponse | None` slot in a small dataclass `_QueuedConfirmation`. The waiter blocks on its own event, not on `state.confirmation_event`.
  - **Why:** Each request needs its own signaling primitive so promote-the-next-one can wake exactly that waiter. `state.confirmation_event` continues to belong to the active request.
  - **Rejected:** A single shared event with a "look at the head" check — fragile against spurious wakes and re-entry.

- **Decision:** Archive each request at **enqueue time**, not at promote time. Emit the `confirmation_request` UI event only at **promote time** (i.e. when the request becomes active).
  - **Why:** Archive faithfulness — the archive captures the agent's actual sequence of tool decisions in the order the tools made them. If we crash, all requested confirmations are durable. The UI event is the user-facing surface; emitting only on promote keeps the "one active confirmation at a time" abstraction clean.
  - **Rejected:** Archive-at-promote — loses queued requests on crash, agent loop can't tell what was "in flight" on restart.

- **Decision:** Timeout (`request.timeout`) **starts at promote time**, not at enqueue time. Specifically: the waiter first awaits its promote signal (no timeout), then once promoted, runs the existing post-wait claim block with `request.timeout`.
  - **Why:** A queued request shouldn't time out before the user has even seen it. Otherwise a long-running first confirmation would silently deny everything queued behind it.
  - **Rejected:** Timeout starts at enqueue — produces surprising mass-denials on contention.

- **Decision:** `cancel_pending_confirmation(conv_id)` (no id arg) continues to cancel **only the active confirmation**. Cancelling does not drop queued items — they promote naturally as today.
  - **Why:** The existing cancel API has no notion of "which one"; it cancels what the user sees. After cancel, the next queued item promotes and the user sees that one.
  - **Rejected:** Cancel-all (clear active + queue) — too aggressive; not what cancel means today.

- **Decision:** Promote-the-next-queued is centralized in a single helper, `_promote_next_confirmation(state, conv_id)`. Every code path that clears the active slot (`respond_to_confirmation`, `cancel_pending_confirmation`, `request_confirmation`'s post-wait timeout/cancel-raced branches) calls it under the lock immediately after clearing.
  - **Why:** Single source of truth for promotion ordering; avoids three near-identical inlinings.
  - **Rejected:** Inlining promotion at each call site — invites drift bugs.

- **Decision:** If a queued waiter's task is cancelled (asyncio.CancelledError raised mid-`event.wait()`), it removes its own entry from the queue in a `finally` block before re-raising.
  - **Why:** A dead waiter must not get promoted later. The cleanup needs to happen even on cancel.
  - **Rejected:** Lazy cleanup at promote time (skip dead entries) — leaves stale entries lying around indefinitely and complicates queue ordering.

- **Decision:** Pre-`request_confirmation`-overwrite logic now treats a non-None `state.pending_confirmation` as "must enqueue" — the **current** trailing-state setup at L790–794 becomes the "no-active-confirmation" branch of an if/else.
  - **Why:** Keeps the no-contention path identical to today's behavior.

- **Decision:** No change to the existing `confirmation_id` shape (12-char hex from uuid4), to the archive message format, to the `confirmation_request` / `confirmation_response` wire events, or to `ConfirmationRequest` / `ConfirmationResponse` dataclasses.
  - **Why:** Out of scope; this PR is a concurrency fix, not a contract change.

- **Decision:** #486 stays separate. Queueing does not subsume #486's L527 identity-check fix because a fresh queued request can still land between L498 (lock released after archive+emit) and L517 (lock re-acquired for the identity check) in the recovery branch.
  - **Why:** Issue #486 explicitly states: "If #485 is resolved by queuing, this specific path still needs Option B (drop the re-check) or Option A (compare by confirmation_id)."
  - **How to apply:** Don't touch the L527 identity-check logic in this PR. Leave #486 open for follow-up.

## Patterns to follow

- **Single lock per state, fine-grained mutations.** Existing pattern in `conversation_manager.py:186` — all confirmation mutations happen under `state.lock`. The queue list (and any promotion logic) lives there too.
- **Archive on outer thread / before lock.** Existing pattern at `conversation_manager.py:785–786` — `append_message` is sync I/O, runs outside the lock to avoid holding it across disk writes. Enqueue follows the same pattern: archive first (outside lock), then enqueue (inside lock).
- **Release lock across `event.wait()`.** Existing pattern at L809–812. The queue waiter follows the same: release lock, wait for own event, re-acquire for state inspection.
- **Test patterns for race conditions.** `tests/test_conversation_manager.py:1283` (`test_concurrent_confirmation_responses_dont_double_dispatch`) and L1699 / L1802 — use `asyncio.create_task` + explicit `asyncio.wait` / `asyncio.gather` to force interleaving, not `asyncio.sleep`. Match the deterministic style for the new regression tests.
- **Per-tool ctx fork in `execute_tool_calls`.** `tool_execution.py:297` — this is the reproduction surface; the regression test can call the manager directly with two tasks if mirroring the full tool path is overkill.

## What we're NOT doing

- **Not fixing #486.** The L527 identity check in `respond_to_confirmation`'s recovery branch keeps its current logic. Queueing reduces but does not eliminate the #486 race window. Tracked there, handled separately.
- **Not exposing queue depth or "you are #N in line" semantics** to the UI or transports. Queue is invisible; only the active confirmation surfaces. UX iteration on this can come later if it matters.
- **Not changing `cancel_pending_confirmation`'s API** to take a `confirmation_id` or to support cancel-all. Today's API is "cancel what's pending"; that meaning is preserved.
- **Not changing the timeout default or the timeout contract.** `request.timeout` is still the post-promote countdown.
- **Not changing the archive format** (no new `queued: true` marker, no per-request ordinal). Archive entries look identical to today.
- **Not changing how startup recovery scans for unresolved confirmations.** Recovery picks up requests with no matching response; queued requests that were archived but never promoted before crash will look identical to unresolved active confirmations. On restart, only one becomes "active"; if more land, the new queue logic handles them naturally. The edge case where multiple unresolved confirmations exist on restart and *no* new request_confirmation calls land is consistent with today (only one ends up "live"); we accept this and don't change recovery.
- **Not adding metrics, structured logging, or observability hooks** for queue depth. Standard `log.debug` / `log.info` at queue/promote points is sufficient.

## Open questions

None blocking. Defaults below; plan/execute proceed under these.

- _Default:_ promotion's `confirmation_request` event is emitted outside the lock (matching today's pattern at L798–807). Inside `_promote_next_confirmation`: set active fields under lock, return the promoted request, caller emits after lock release.
- _Default:_ logging level for queue enqueue/promote is `log.info` (matches today's confirmation-lifecycle log volume).
- _Default:_ no separate config flag for queue behavior; queueing is always-on. The behavior is strictly safer than the current overwrite.
