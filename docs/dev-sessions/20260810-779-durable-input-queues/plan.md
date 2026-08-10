# Plan

**Goal:** Migrate `conversation_manager.py`'s input handling from in-memory dispatch to a durable, SQLite-backed `Inbox` queue (JSONL per user correction).

## Phase 1: Persistent Inbox JSONL writes

- **Advances:** C1
- **Approach:** 
  - Update `ConversationManager.enqueue_turn` to write the incoming turn to a `inbox.jsonl` file using `sidecar_path` instead of simply appending to `state.pending_messages` in memory.
  - The format should be JSON lines.
- **Verification:**
  - [ ] `pytest tests/test_conversation_manager.py::test_enqueue_turn_writes_to_jsonl` passes.

## Phase 2: Background Worker per Conversation

- **Advances:** C2
- **Approach:**
  - Replace the current recursive `_drain_pending` loop with a dedicated, continuously running `asyncio.Task` per active conversation.
  - The task polls the `inbox.jsonl` file, reads the next item, removes it (or clears the file/re-writes remaining), and processes it serially under the conversation lock.
  - The worker gets started on demand if not already running for that conversation.
- **Verification:**
  - [ ] `pytest tests/test_conversation_manager.py::test_inbox_drained_by_worker` passes.
  - [ ] `pytest tests/test_conversation_manager.py -k test_enqueue` passes.
  - [ ] `pytest tests/test_conversation_manager.py -k test_concurrent_user_enqueue_serializes_via_lock` passes.

## Phase 3: Startup Recovery

- **Advances:** C3
- **Approach:**
  - In `ConversationManager.startup_scan()`, find all conversations with a non-empty `inbox.jsonl` file.
  - Start the background worker task for each of those conversations so that pending turns are processed upon bot restart.
- **Verification:**
  - [ ] `pytest tests/test_conversation_manager.py::test_pending_inputs_survive_restart` passes.
