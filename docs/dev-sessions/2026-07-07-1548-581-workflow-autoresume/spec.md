# Spec — Auto-resume `status="running"` workflow journals on server restart (#581)

<!-- dev-session:spec -->

## Context

If a server crash (SIGINT, OOM, deploy) interrupts a workflow turn mid-LLM, the journal persists at `status="running"` — but on server restart, nothing scans for in-flight workflows and re-enqueues them. The workflow stays stuck until a user action triggers it.

Surfaced during the live smoke for #574 ([PR #579](https://github.com/lmorchard/decafclaw/pull/579), Finding 2). The replay machinery itself is correct — unit tests in `tests/test_workflow_parallel.py` and `tests/test_workflow_pipeline.py` cover full-cache + mid-fan-out resume against synthetic journals. The gap is the wiring at the harness layer: **nothing re-enters the engine when the server comes back up**.

Contrast with `status="suspended"` (waiting on a user_input confirmation): those recover via the existing confirmation-recovery scan in `ConversationManager.startup_scan()`. A user-approve-later triggers `WorkflowUserInputHandler.on_approve`, which re-enqueues. `status="running"` has no equivalent trigger.

## Goal

On server startup, scan each conversation for a `workflow.json` at `status="running"` and re-enqueue that conversation's workflow turn. The replay engine short-circuits cached entries, so the resumed turn picks up exactly where the crash left off.

Bound the risk of replay-storms with a per-journal attempt counter — a workflow that crashes deterministically on the same replay position gets marked `status="error"` after N attempts and stops retrying.

## Non-goals

- Client-side "resume workflow" command (nice-to-have; not blocking).
- Recovering `status="error"` journals (correctly excluded — errors include `WorkflowNonDeterministic` and are terminal).
- Cross-machine coordination or a shared restart lock (single-instance deployment).
- Change to how `WorkflowSuspended` is handled (already correct — that's the "waiting on user" path).

## Design

### Startup flow

`ConversationManager` gains a sibling method `startup_scan_workflows()`, called from `runner.py:59` right after the existing `startup_scan()`.

```python
# runner.py, alongside existing startup_scan
await manager.startup_scan()          # recovers status="suspended" confirmations
await manager.startup_scan_workflows()  # recovers status="running" workflows
```

Splitting to a separate method (rather than folding into `startup_scan`) keeps the two recovery surfaces independently testable and independently observable via log messages.

### Scan behavior

For each conversation directory:

1. `workflow_path(config, conv_id)` — if not exists, skip.
2. Load journal via `load_journal(path)`. If load fails, log warning and skip (fail-open).
3. If `journal.status != "running"`, skip.
4. If `journal.attempts >= config.workflow.max_resume_attempts` (default 3):
   - Set `journal.status = "error"`.
   - Persist.
   - Log warning: "Workflow {name} in {conv_id} exceeded resume attempts, marked as error."
   - Skip.
5. Increment `journal.attempts`, persist journal.
6. `enqueue_turn(conv_id, kind=TurnKind.WORKFLOW, prompt="", metadata={"workflow_name": journal.workflow_name, "resume": True})`.

The attempt increment happens **before** enqueue and is persisted immediately — so a crash inside the resumed turn still consumes an attempt. This is the correct behavior: a workflow that crashes the server every time it replays position `(3, 2, 0)` needs to eventually stop.

### Attempt counter reset

Successful completion (`status="done"`) leaves `attempts` untouched — the field is only relevant while status is "running". If a workflow eventually completes, the field is dead weight but harmless.

If a user-input suspension resets the counter: **no**. A workflow that alternates `running → suspended → running → suspended` and crashes at each `running` phase is still crashing repeatedly and should hit the cap. Only a full `done` should reset (and by then the journal is terminal).

Deferred: complete reset on `done` — not needed for v1 since done journals are terminal.

### Journal changes

- New field on `Journal` dataclass in `workflow/journal.py`: `attempts: int = 0`.
- `to_dict()` gains `"attempts": self.attempts`.
- `from_dict()` uses `d.get("attempts", 0)` — existing on-disk journals load with `attempts=0`, no migration.

### Config

- New field on `WorkflowConfig` dataclass in `config_types.py`: `max_resume_attempts: int = 3`.
- Env override: `WORKFLOW_MAX_RESUME_ATTEMPTS`.

### UX signal

The workflow engine already publishes `[workflow: {name}] running` via `ctx.publish("tool_status", ...)` on every resumed turn (`resume.py:62-63`). Once transports reconnect, that publish reaches the client subscriber and the resumed workflow becomes visible.

No new publish sites needed. The scan itself logs but does not publish (transports not yet up at scan time — no live subscribers).

### Error path (attempt cap reached)

When `attempts >= max_resume_attempts`, the journal is marked `status="error"` at scan time. This is symmetric with how the engine marks `status="error"` on `WorkflowSuspended`/exception (`engine.py:46-51`) — the terminal-state guarantee holds.

The user's next interaction with that conversation will see:
- No workflow in flight (`status="error"` excludes it from resume).
- The `/workflow-name` command can start a new one.

## Alternatives considered

- **Retry-in-memory only (no journal field).** Track attempts in a `dict[conv_id, int]` on the manager, reset each restart. Simpler, but doesn't survive successive restarts — a workflow that crashes on the same position every startup would re-attempt forever. Journal-persisted attempt counter is only ~3 lines more code for actual safety.
- **Time-based cooldown instead of attempt cap.** "Skip if attempted within X seconds." Handles rapid-restart edge cases but doesn't bound total attempts and adds a clock dependency. Attempt cap is cleaner.
- **Fold into `startup_scan` as a second pass.** Considered; keeping separate makes tests and logs cleaner. Two distinct concerns: confirmations vs workflows.

## Testing

Unit tests in `tests/test_workflow_resume.py` (extending the existing file if it makes sense, otherwise new file):

1. **`test_startup_scan_workflows_resumes_running`** — write a journal with `status="running"`, `attempts=0` to a fresh conv dir; call `startup_scan_workflows()`; assert `attempts=1` persisted and `enqueue_turn` was called with `kind=TurnKind.WORKFLOW`, `metadata={"workflow_name": ..., "resume": True}`.

2. **`test_startup_scan_workflows_skips_non_running`** — write journals with `status="done"`, `"error"`, `"suspended"`; call scan; assert no enqueues.

3. **`test_startup_scan_workflows_hits_attempt_cap`** — write a journal with `attempts >= max_resume_attempts`; call scan; assert journal is marked `status="error"`, persisted, and NO enqueue fires.

4. **`test_startup_scan_workflows_increments_and_persists_before_enqueue`** — verify order: journal on disk has `attempts=N+1` even if enqueue fires (regression guard so a subsequent crash sees the incremented value).

5. **`test_startup_scan_workflows_empty`** — no conversations; scan returns 0.

6. **`test_startup_scan_workflows_missing_journal_file`** — conversation dir exists but no `workflow.json`; scan skips silently.

7. **`test_startup_scan_workflows_corrupt_journal`** — write a malformed `workflow.json`; scan logs warning and skips (fail-open).

8. **`test_journal_backward_compatible`** (in `test_workflow_journal.py`) — load a journal dict without `attempts` field; assert loads with `attempts=0`.

E2E: extend `test_workflow_turn_integration.py:test_durable_resume_after_simulated_restart` (or add a sibling) to exercise the scan path end-to-end: suspend workflow mid-fan-out → simulate restart → call `startup_scan_workflows()` → assert workflow completes.

## Docs to update

- `docs/workflows.md` — new section under "Durability" covering the auto-resume flow, attempt cap, and config field.
- CLAUDE.md — no changes (convention unchanged).

## Rollout / risk

- Feature is on by default with `max_resume_attempts=3`. Config field lets users tune or effectively disable via `max_resume_attempts=0`.
- Fail-open behavior throughout: any error in the scan logs and continues — a broken journal in one conversation can't block startup.
- Existing `status="suspended"` recovery path unchanged.

## References

- Smoke transcript: `docs/dev-sessions/2026-06-10-1732-574-workflow-batch-primitives/smoke.md` (Finding 2)
- Replay engine: `src/decafclaw/workflow/engine.py`, `src/decafclaw/workflow/resume.py`
- Existing scan: `src/decafclaw/conversation_manager.py:1794-1830` (`startup_scan`)
- Journal: `src/decafclaw/workflow/journal.py`
- Paths: `src/decafclaw/workflow/paths.py`, `src/decafclaw/conversation_paths.py:59` (`iter_conversation_archives`)
- PR #573 (engine), PR #579 (primitives), PR #603, #610, #616 (smoke follow-ups)
