# Frozen acceptance checks

**Source:** https://github.com/lmorchard/decafclaw/issues/779
**Frozen at:** 98bb7c4 (recorded below)
**Check files — read-only from Phase 1 onward:**
- `tests/test_conversation_manager.py`

## C1
CRITERION: WHEN `enqueue_turn` is called, THEN the system SHALL write the incoming turn to a durable JSONL session inbox file instead of an in-memory queue.
CHECK: `pytest tests/test_conversation_manager.py::test_enqueue_turn_writes_to_jsonl` passes.
AT FREEZE: (pending)

## C2
CRITERION: GIVEN an active conversation, THEN the system SHALL run a detached background worker task that continuously polls and drains its JSONL inbox serially.
CHECK: `pytest tests/test_conversation_manager.py::test_inbox_drained_by_worker` passes.
AT FREEZE: (pending)

## C3
CRITERION: GIVEN a server restart, WHEN the system initializes, THEN it SHALL process any pending turns found in the JSONL session inbox exactly once.
CHECK: `pytest tests/test_conversation_manager.py::test_pending_inputs_survive_restart` passes.
AT FREEZE: (pending)

## Guards
- G1: `pytest tests/test_conversation_manager.py -k "not test_enqueue_turn_writes_to_jsonl and not test_inbox_drained_by_worker and not test_pending_inputs_survive_restart"`
- G2: `pytest tests/test_runner.py` (and the rest of the test suite)

## Adjudication
- C1: strengthened — explicitly asserts `pending_messages` is empty to prove legacy queue is unused.
- C2: strengthened — added active counter and delay to fake runner to prove serialization.
- C3: strengthened — tightened assertion to `called.count("surviving turn") == 1` to prove exactly-once execution.
- G1: accepted — the suite still passes at freeze (before changes).
- G2: accepted — the suite still passes at freeze.

## Amendments
