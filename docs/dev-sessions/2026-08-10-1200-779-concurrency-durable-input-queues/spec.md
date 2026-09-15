**Concept from opencode:**
User inputs and background events are never directly pushed into an actively running agent loop. They are persisted to a `SessionInputTable` in SQLite. A detached background worker constantly "drains" this queue. This provides flawless concurrency, persistent state across bot restarts, and an elegant way to handle UI interruptions.

**How `decafclaw` could implement this:**
`decafclaw` uses `conversation_manager.py` with an in-memory `asyncio` task to coordinate `enqueue_turn()`. It relies on a `_busy_flags` dictionary which can sometimes cause race conditions with fast UI inputs or background schedule wakes (`child_agent`).

**Proposed Implementation:**
- Migrate `conversation_manager.py`'s input handling from in-memory dispatch to a durable, SQLite-backed `Inbox` queue (similar to how `notifications.py` uses JSONL, but for incoming turns).
- Have a single `asyncio.Task` per active conversation that loops and drains this inbox.
- Ensures that if the server crashes mid-turn, pending human inputs or schedule wakes are processed exactly once on restart.

### Acceptance Criteria

- CRITERION: WHEN `enqueue_turn` is called, THEN the system SHALL write the incoming turn to a durable JSONL session inbox file instead of an in-memory queue.
  CHECK: `pytest tests/test_conversation_manager.py::test_enqueue_turn_writes_to_jsonl` passes (asserting a file append occurs and the line exists).

- CRITERION: GIVEN an active conversation, THEN the system SHALL run a detached background worker task that continuously polls and drains its JSONL inbox serially.
  CHECK: `pytest tests/test_conversation_manager.py::test_inbox_drained_by_worker` passes (asserting queued items are dispatched and subsequently removed from the file/queue).

- CRITERION: GIVEN a server restart, WHEN the system initializes, THEN it SHALL process any pending turns found in the JSONL session inbox exactly once.
  CHECK: `pytest tests/test_conversation_manager.py::test_pending_inputs_survive_restart` passes (asserting a manager initialized against a pre-populated JSONL file processes those turns on startup).

### Regression Guards

- GUARD: Existing integrations (HTTP server, Mattermost) still successfully enqueue turns and receive responses without knowing about the JSONL layer. Passes today.
- GUARD: The `busy` state lock mechanism continues to prevent concurrent processing of turns for the same conversation. Passes today.

## Tier: auto-ok
**Reason:** All criteria are verifiable via unit tests without human judgment. Adding a JSONL-backed queue for incoming turns does not touch risk-gated paths (no secrets, no auth, no production data migration since the previous queue was purely in-memory).

### Design decisions
- The session inbox will be backed by JSONL files rather than SQLite, per user correction on the initial proposal. This aligns with how `notifications.py` uses JSONL.

