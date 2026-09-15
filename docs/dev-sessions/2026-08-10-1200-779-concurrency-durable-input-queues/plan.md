# Concurrency: Durable Input Queues Implementation Plan

**Goal:** Migrate `conversation_manager.py`'s input handling to a durable JSONL-backed inbox to provide flawless concurrency and survive bot restarts.

**Source issue:** https://github.com/lmorchard/decafclaw/issues/779 — **Tier:** `auto-ok` (All criteria are verifiable via unit tests without human judgment. Adding a JSONL-backed queue for incoming turns does not touch risk-gated paths.)

**Approach:**
- Migrate `conversation_manager.py`'s input handling from in-memory dispatch to a durable, SQLite-backed Inbox queue (similar to how `notifications.py` uses JSONL, but for incoming turns). Wait, design decisions specify "backed by JSONL files rather than SQLite".
- Have a single `asyncio.Task` per active conversation that loops and drains this inbox.
- Ensures that if the server crashes mid-turn, pending human inputs or schedule wakes are processed exactly once on restart.

**Criteria:** 
- C1: `enqueue_turn` writes to JSONL inbox instead of memory queue.
- C2: Detached background worker drains JSONL inbox serially.
- C3: Startup scan processes pending turns in JSONL inbox.

---

## Phase 0: Freeze the acceptance checks

Write `checks.md` and author the tests the checks name, per `references/frozen-checks.md`.
No implementation in this phase.

**Files:**
- Create: `{session-dir}/checks.md`
- Modify: `tests/test_conversation_manager.py`

**Verification — automated:**
- [x] Every criterion's check runs and fails for the expected reason.
- [x] Every guard runs and passes.
- [x] Check-reviewer dispatched read-only; `## Adjudication` in `checks.md` recorded.
- [x] Freeze commit made; sha recorded in `checks.md`.

---

## Phase 1: Migrate enqueue to write JSONL and run detached drain tasks

Migrate `ConversationManager` to use JSONL files for the inbox, starting background worker tasks to drain them.

**Advances:** C1, C2, C3

**Micro-tasks (Atomic checkbox steps):**
- [x] Add logic in `enqueue_turn` to append the incoming turn payload (JSON) to `{conv_id}/inbox.jsonl` using `sidecar_path`.
- [x] Instead of pushing to an in-memory queue or waiting inline, `enqueue_turn` should now create/ensure a `_drain_inbox` `asyncio.Task` is running for the conversation.
- [x] Implement `_drain_inbox` task: loops reading lines from `inbox.jsonl`. For each line, runs `run_agent_turn`, then rewrites the file without the processed line (or renames and starts a new one). It should run serially and sleep briefly or use an `asyncio.Event` to wake up when new items are added.
- [x] Update `startup_scan` to scan for pending turns in `inbox.jsonl` files and start `_drain_inbox` tasks.
- [x] Update test assertions that assume `enqueue_turn` directly returns a completed future or `pending_messages` exists. (Refactor breaking tests).

**Files:**
- Modify: `src/decafclaw/conversation_manager.py`

**Key changes:**
- `def enqueue_turn` writes to JSONL and triggers worker.
- `async def _drain_inbox(self, state: ConversationState)`

**Verification — automated:**
- [x] C1's check passes: `uv run pytest tests/test_conversation_manager.py::test_enqueue_turn_writes_to_jsonl`
- [x] C2's check passes: `uv run pytest tests/test_conversation_manager.py::test_inbox_drained_by_worker`
- [x] C3's check passes: `uv run pytest tests/test_conversation_manager.py::test_pending_inputs_survive_restart`
- [x] Guards still pass: `uv run pytest tests/test_conversation_manager.py -k "not test_enqueue_turn_writes_to_jsonl and not test_inbox_drained_by_worker and not test_pending_inputs_survive_restart"`
- [x] `make test` passes (no regression)
