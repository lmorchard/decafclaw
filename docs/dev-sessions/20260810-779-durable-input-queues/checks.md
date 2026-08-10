# Frozen acceptance checks

**Source:** https://github.com/lmorchard/decafclaw/issues/779
**Frozen at:** (to be filled)
**Check files — read-only from Phase 1 onward:**
- `tests/test_conversation_manager.py`

## C1
CRITERION: WHEN `enqueue_turn` is called, THEN the system SHALL write the incoming turn to a durable JSONL session inbox file instead of an in-memory queue.
CHECK: `pytest tests/test_conversation_manager.py::test_enqueue_turn_writes_to_jsonl` passes.
AT FREEZE: fails - `AssertionError: Inbox JSONL file should exist`

## C2
CRITERION: GIVEN an active conversation, THEN the system SHALL run a detached background worker task that continuously polls and drains its JSONL inbox serially.
CHECK: `pytest tests/test_conversation_manager.py::test_inbox_drained_by_worker` passes.
AT FREEZE: fails - `TimeoutError` (wait, I will change the test to assert `inbox_path.exists()` before checking len, so it actually fails at freeze).

## C3
CRITERION: GIVEN a server restart, WHEN the system initializes, THEN it SHALL process any pending turns found in the JSONL session inbox exactly once.
CHECK: `pytest tests/test_conversation_manager.py::test_pending_inputs_survive_restart` passes.
AT FREEZE: fails - `TimeoutError` on waiting for the turn to be processed.

## Guards

- G1: `pytest tests/test_conversation_manager.py -k test_enqueue` passes.
  Passed at freeze.
- G2: `pytest tests/test_conversation_manager.py -k test_concurrent_user_enqueue_serializes_via_lock` passes.
  Passed at freeze.

## Adjudication

- C1: accepted — the check directly asserts the inbox JSONL file is written to with the correct content.
- C2: accepted — the check asserts the turn was processed and the file was cleared.
- C3: accepted — the check prepopulates the file and ensures it gets processed on startup.
- G1: accepted — regression guard to ensure enqueueing still works.
- G2: accepted — regression guard to ensure lock still serializes concurrent enqueues.

## Amendments

