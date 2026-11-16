# Epic 7 — ConversationManager decomposition

The most complex code in the project; methods are near their complexity ceiling
(the massive invariant-comment blocks attest to it). Decomposed into staged PRs.

---

### 7.1 — Extract the `message_complete` payload builder

**Size:** S
**Triage:** ready

**Problem.** `conversation_manager.py` (~2121-2144) assembles the
`message_complete` payload inline inside `_start_turn`'s nested `run()`.

**Result.** A pure `_build_message_complete(...)` helper returns the payload.

**Scope.** In: the payload assembly. Out: no payload shape change.

**Verify.** `make test` — `tests/test_conversation_manager.py`.

**Tests.** Existing message-complete assertions pass; add a direct unit case for
the extracted helper.

**Deps.** none

---

### 7.2 — Extract the context-construction half of `_start_turn`

**Size:** M
**Triage:** ready

**Problem.** `_start_turn` (~1828) interleaves context building with streaming/
confirmation wiring and turn execution.

**Result.** A `_build_context(...)` method returns the assembled context/state,
leaving `_start_turn` to run the turn.

**Scope.** In: `_start_turn` construction phase. Out: do not yet extract the run
coroutine (7.3).

**Verify.** `make test` — `tests/test_conversation_manager.py` +
`tests/test_context_composer.py`.

**Tests.** Existing turn-start tests pass unchanged.

**Deps.** none

---

### 7.3 — Extract the nested `run()` turn-lifecycle coroutine

**Size:** M
**Triage:** blocked-by #7.1 #7.2

**Problem.** The nested `run()` captures 15+ locals and mixes cancel/error/finish
sequencing (the #440/#491/#517 race history).

**Result.** A module-level turn-lifecycle object (or coroutine) with explicit
fields replaces the closure; `_start_turn` constructs it and awaits it.

**Scope.** In: the `run()` closure. Out: no behavior change to the lifecycle.

**Verify.** `make test` — `tests/test_conversation_manager.py`,
`tests/test_web_cancel_turn.py`.

**Tests.** Existing cancel/complete/finish tests pass unchanged (they are the
regression net).

**Deps.** blocked-by #7.1 #7.2

---

### 7.4 — Add a shared confirmation slot-resolve helper

**Size:** M
**Triage:** ready

**Problem.** The confirmation lifecycle (`respond_to_confirmation`,
`cancel_pending_confirmation`, `request_confirmation`, `post_confirmation`) each
re-implements the same pending/event/response triple under the lock.

**Result.** A `_resolve_slot(state, ...)` helper owning the under-lock archive/claim
ordering, returning a discriminated result (`claimed`/`timed_out`/`cancelled`/`promoted`).

**Scope.** In: the shared helper only. Out: do not yet rewire callers (7.5/7.6).

**Verify.** `make test` — `tests/test_confirmation.py`.

**Tests.** Add direct unit cases for each discriminated result.

**Deps.** none

---

### 7.5 — Adopt the helper in `respond_to_confirmation` + `cancel_pending_confirmation`

**Size:** M
**Triage:** blocked-by #7.4

**Result.** Both call `_resolve_slot` from 7.4 instead of inlining the ordering.

**Verify.** `make test` — `tests/test_confirmation.py`.

**Tests.** Existing confirm/cancel tests pass unchanged.

**Deps.** blocked-by #7.4

---

### 7.6 — Adopt the helper in `request_confirmation` + `post_confirmation`

**Size:** M
**Triage:** blocked-by #7.4

**Result.** Both call `_resolve_slot` (and the shared archive/clear) from 7.4.

**Verify.** `make test` — `tests/test_confirmation.py`.

**Tests.** Existing request/post-confirmation tests pass unchanged.

**Deps.** blocked-by #7.4

---

### 7.7 — Replace stringly-typed `asyncio.Future` attrs

**Size:** S
**Triage:** ready

**Problem.** `setattr(future, "rate_limited"/"queued", ...)` attaches undocumented
attributes to futures, invisible to type checking.

**Result.** A small typed result object (or dataclass) carries these flags instead.

**Scope.** In: the enqueue/queue result handling (`conversation_manager.py` ~625-706).
Out: no queue policy change.

**Verify.** `make test` — `tests/test_conversation_manager.py`.

**Tests.** Existing enqueue/queue tests pass.

**Deps.** none

---

### 7.8 — Remove the append-then-pop inbox round-trip in `enqueue_turn`

**Size:** S
**Triage:** ready

**Problem.** `enqueue_turn` appends to the inbox, then (when not busy) reads the
whole inbox back, scans for the turn, and rewrites it — O(n) disk I/O that is
later undone.

**Result.** The message is only written to the inbox when it is actually queued;
busy turns skip the round-trip.

**Scope.** In: `enqueue_turn` bookkeeping. Out: inbox recovery semantics.

**Verify.** `make test` — `tests/test_conversation_manager.py` +
`tests/test_confirmation.py` (recovery).

**Tests.** Existing queued-turn/resume tests pass; add a case asserting no inbox
write for a non-busy immediate turn.

**Deps.** none

---

### 7.9 — Dedupe side-question message flattening

**Size:** S
**Triage:** ready

**Problem.** `run_side_question` (~1243-1289) re-implements message
flattening/role mapping that `compaction.flatten_messages` and
`context_composer.ROLE_REMAP` already encode.

**Result.** Side questions reuse `ROLE_REMAP`/the shared transform instead of a
private copy.

**Scope.** In: `run_side_question`. Out: no side-question behavior change.

**Verify.** `make test` — `tests/test_conversation_manager.py` (side-question cases).

**Tests.** Existing side-question tests pass unchanged.

**Deps.** none