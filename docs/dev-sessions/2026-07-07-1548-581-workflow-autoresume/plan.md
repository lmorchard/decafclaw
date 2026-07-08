# Plan — Auto-resume `status="running"` workflows (#581)

Spec: `spec.md`. Research: `research.md`.

Execution mode: TDD per phase where practical (test-first for behavior changes, straight edits for pure data-model additions). Each phase is a single logical commit and independently reviewable.

## Phase 1 — Journal `attempts` field

**Files:**
- `src/decafclaw/workflow/journal.py`
- `tests/test_workflow_journal.py` (extend if exists; create otherwise)

**Changes:**
1. Add `attempts: int = 0` to `Journal` dataclass (line ~45).
2. Add `"attempts": self.attempts` to `to_dict()` output.
3. In `from_dict()`, read via `d.get("attempts", 0)`.

**Tests (write first):**
- `test_journal_attempts_defaults_to_zero` — construct a `Journal`, assert `attempts == 0`.
- `test_journal_attempts_round_trip` — set `attempts=2`, `to_dict` → `from_dict`, assert preserved.
- `test_journal_backward_compatible_missing_attempts` — dict without `attempts` key → loads with `attempts=0`.

**Acceptance:** All three tests pass. `make test` clean.

## Phase 2 — `WorkflowConfig.max_resume_attempts`

**Files:**
- `src/decafclaw/config_types.py` — new `WorkflowConfig` dataclass.
- `src/decafclaw/config.py` — import + wire into top-level `Config`.
- `tests/test_config.py` (or wherever existing dataclass tests live).

**Changes:**
1. In `config_types.py`, add:
   ```python
   @dataclass
   class WorkflowConfig:
       max_resume_attempts: int = 3
   ```
2. In `config.py`:
   - Import `WorkflowConfig`.
   - Add `workflow: WorkflowConfig = field(default_factory=WorkflowConfig)` on `Config`.
   - Add `workflow = load_sub_config(WorkflowConfig, file_data.get("workflow", {}), "WORKFLOW")` and pass to `Config(...)`.
   - Verify env override: `WORKFLOW_MAX_RESUME_ATTEMPTS=5` → `config.workflow.max_resume_attempts == 5`.

**Tests (write first):**
- `test_workflow_config_defaults` — default is 3.
- `test_workflow_config_env_override` — set env, load config, verify.
- `test_workflow_config_json_override` — file `{"workflow": {"max_resume_attempts": 7}}`, verify.

**Acceptance:** All three tests pass. `make config` shows the new section. No regressions in `make test`.

## Phase 3 — `startup_scan_workflows` on `ConversationManager`

**Files:**
- `src/decafclaw/conversation_manager.py` — new method next to `startup_scan()`.
- `src/decafclaw/runner.py` — call the new method after existing `startup_scan()`.
- `tests/test_conversation_manager.py` — unit tests mirroring the existing scan tests.

**Method skeleton:**
```python
async def startup_scan_workflows(self) -> int:
    """Recover workflows in status='running' by re-enqueueing them.

    Returns the number of workflows re-enqueued (not counting cap-exceeded).
    """
    from decafclaw.workflow.journal import load_journal, save_journal
    from decafclaw.workflow.paths import workflow_path

    resumed = 0
    for conv_id, _archive in iter_conversation_archives(self.config):
        path = workflow_path(self.config, conv_id)
        if not path.exists():
            continue
        try:
            journal = load_journal(path)
        except Exception as exc:
            log.warning("Failed to load workflow journal at %s: %s", path, exc)
            continue

        if journal.status != "running":
            continue

        cap = self.config.workflow.max_resume_attempts
        if journal.attempts >= cap:
            log.warning(
                "Workflow %r in %s exceeded resume attempts (%d), marked as error.",
                journal.workflow_name, conv_id, cap)
            journal.status = "error"
            save_journal(path, journal)
            continue

        journal.attempts += 1
        save_journal(path, journal)

        log.info(
            "Resuming workflow %r in %s (attempt %d/%d)",
            journal.workflow_name, conv_id, journal.attempts, cap)

        await self.enqueue_turn(
            conv_id,
            kind=TurnKind.WORKFLOW,
            prompt="",
            metadata={"workflow_name": journal.workflow_name, "resume": True},
        )
        resumed += 1

    return resumed
```

**Wire into runner:**
```python
# runner.py line ~59
await manager.startup_scan()
await manager.startup_scan_workflows()
```

**Tests (write first):**
- `test_startup_scan_workflows_resumes_running` — write journal `status="running"`, `attempts=0`; call scan; assert (a) `attempts=1` persisted on disk, (b) `enqueue_turn` called once with matching metadata.
- `test_startup_scan_workflows_skips_non_running` — arrange three journals (`done`, `error`, `suspended`); assert zero enqueues.
- `test_startup_scan_workflows_hits_attempt_cap` — journal at `attempts=3` (cap default); assert `status="error"` on disk, no enqueue.
- `test_startup_scan_workflows_increments_before_enqueue` — patch `enqueue_turn` to raise; verify persisted `attempts` still reflects increment (so a crash inside enqueue still consumes an attempt).
- `test_startup_scan_workflows_empty` — no conversations dir; scan returns 0.
- `test_startup_scan_workflows_missing_journal_file` — conv dir exists but no `workflow.json`; skip silently.
- `test_startup_scan_workflows_corrupt_journal` — write malformed JSON; scan logs warning, continues to next conv.
- `test_startup_scan_workflows_multiple_conversations` — mix of resumable + non-resumable across conv dirs; assert only the running ones get enqueued.

**Fixture pattern:** copy the arrangement from `test_startup_scan_finds_pending_confirmation` (`test_conversation_manager.py:869-889`). Use the `manager` fixture; write journal files via `save_journal(workflow_path(config, conv_id), journal)`.

**Enqueue mock:** patch `manager.enqueue_turn` with an `AsyncMock` or a lightweight recorder — asserting the call was made, not that the turn actually ran (that's Phase 4's job).

**Acceptance:** all eight tests pass. `make check` clean. `make test` shows no regressions.

## Phase 4 — E2E integration test

**File:** `tests/test_workflow_turn_integration.py` (extend).

**New test:** `test_startup_scan_resumes_running_workflow_end_to_end`
- Arrange: use the same setup as `test_durable_resume_after_simulated_restart` (108-173).
- Suspend a workflow mid-fan-out via existing helpers.
- Manually flip journal `status` from `"suspended"` back to `"running"` (simulating a crash mid-`_persist`).
- Discard the old manager, construct a new one against the same config (simulating restart).
- Call `startup_scan_workflows()` on the fresh manager.
- Await the returned turn's completion signal (through the existing test infra).
- Assert: workflow reaches `status="done"`, no double-execution of cached primitives (checked via the same call-count assertions the existing test uses).

**Acceptance:** test passes. Existing `test_durable_resume_after_simulated_restart` still passes.

## Phase 5 — Docs

**Files:**
- `docs/workflows.md` — new section, e.g. under "Durability".

**Content:**
- What auto-resume does (one paragraph).
- The attempt cap and why it exists (one paragraph).
- Config field: `workflow.max_resume_attempts` (default 3), env `WORKFLOW_MAX_RESUME_ATTEMPTS`.
- Terminal states: `done` (success), `error` (failed, exceeded cap, or non-determinism).

**Acceptance:** doc renders, links check out. No code changes.

## Verification commands

Between phases:
- `make lint` — compile-check
- `make check` — lint + typecheck (Python + JS + message types)
- `make test` — full pytest run

Before opening PR:
- `make check && make test` clean
- Check `pytest --durations=25` for any new slow tests (should not appear — all tests should be fixture-based)
- Manual smoke: no evals needed (this is a startup-time recovery mechanism, not LLM-visible)

## Not doing (deferred / non-goals)

- Client-side "resume workflow" command — separate issue if we want it.
- Attempt-counter reset on `suspended` transitions — see spec §"Attempt counter reset".
- Cross-machine coordination — single-instance deployment.
- Migration for existing on-disk journals — backward-compatible via `d.get("attempts", 0)`.

## Risk register

- **Replay-storm** — bounded by attempt cap.
- **Broken journal blocks startup** — mitigated by fail-open (log + continue).
- **Attempt cap too aggressive** — default 3 is conservative; config field lets users tune.
- **Attempt cap too permissive** — user can set `max_resume_attempts=0` to effectively disable auto-resume.
- **Race with a live transport enqueue** — impossible; scan runs before transports connect (verified in research §1).
