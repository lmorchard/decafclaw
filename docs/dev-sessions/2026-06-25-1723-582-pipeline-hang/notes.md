# Notes — Pipeline hang investigation (#582)

## Session start — 2026-06-25 17:23

- Branch: `fix/582-pipeline-hang` (from `origin/main` @ `9deab85`)
- Worktree: `/Users/lorchard/devel/decafclaw/.claude/worktrees/fix-582-pipeline-hang`
- Session dir: `docs/dev-sessions/2026-06-25-1723-582-pipeline-hang/`
- HTTP_PORT: 18894 (main 18880; other worktrees 18891/18892/18893 either active or recently used)
- `TABSTACK_API_KEY` uncommented in worktree `.env` per #580 learning
- Baseline: `make test` — 2943 passing in 22.76s

## Origin

Twin smoke evidence from #574 and #580. The pre-existing primitive bug between "all `wf.parallel` thunks completed" and "`wf.parallel` records its outer entry" reproduces in two settings:

1. #574 smoke (PR #579): degenerate `[error: unknown tool 'tabstack_research']` content fed into the summarize stage. Vertex-throttling hypothesis was plausible.
2. #580 smoke (PR #603): real 4-6KB tabstack markdown — same hang. Vertex-throttling hypothesis ruled out.

The bug is in the primitives themselves (parallel/pipeline), not in Vertex's handling of degenerate prompts.

## Investigation framing

Likely candidates (from the #582 comment + investigation suggestions posted in PR #603's cleanup):

- **Outer-entry write hang.** `journal.append(seq, "parallel", fp, results)` or `save_journal(...)` silently failing/hanging. Cheap probe: add `log.debug` immediately before and after each.
- **Async cleanup deadlock.** The `_CancelSignal` watcher's `finally` cleanup, or the `pending` cancellation gather, might wedge when ALL tasks complete normally (so only the cancel_watcher is left "pending").
- **Per-task `t.result()` raising silently.** Phase 5's exception-unmasking iteration. If a `tasks[i]` somehow ended up cancelled (not by us) during the cleanup gather, `tasks[i].result()` would raise CancelledError but our scan would skip it (since we filter cancelled tasks) — and we'd silently fall through to a `results = [...]` line that... well, let me read the code carefully.

Goal of this session: a minimum repro (unit-test scale, no real LLM) that exhibits the hang, then a targeted fix. Investigation, not architectural change.

## Execution complete (2026-06-26)

| Phase | Commit | Notes |
| --- | --- | --- |
| 1: Verify asyncio semantics | `1dd024e` | Caught a real spec bug (`create_task(gather(...))` fails on 3.11+ since gather returns a Future, not a coroutine). Standalone repro validates the corrected shape. |
| 2: Fix wf.parallel | `0300561` | Implementer caught a SECOND spec bug — `return_exceptions=True` would have created a different hang (slow + fast_raiser deadlock). Shipped with default `return_exceptions=False` + explicit straggler cancel before `.result()`. Two regression-guarding tests pass (#579 index-vs-time-order, mid-run-cancel). |
| 3: Fix wf.pipeline | `ba4a8fd` | Verbatim mirror of Phase 2. Mirror was trivial — same body structure. Stale `_CancelSignal` comment in pipeline also updated. |
| 4: Live smoke + docs | (this commit) | Proved the outer parallel entry at seq `(3,)` now lands (was missing in #574/#580 smokes — the smoking gun of the bug). |

### Execute-phase highlights

- **Two spec bugs caught in execute, not in plan-phase verification.** Phase 1's verification was supposed to catch them; it caught the first (`create_task(gather)`) but the second (`return_exceptions=True`) only surfaced when the implementer ran the existing `test_parallel_surfaces_real_exception_over_cleanup_cancel` test against the new code shape. Lesson: the plan-phase verification script needs to exercise the FULL fix shape against an EXISTING regression-guarding test, not just isolated asyncio claims. Worth a journal note for future debug-session plans.
- **The fix is one line of code in each primitive: `asyncio.gather(*tasks)` instead of `list(tasks)` in the wait_set construction.** Everything around it (the cancel_fired check, the straggler-cancel-then-`.result()` flow, the `_CancelSignal` cleanup) is reshaped, but the actual bug was the one wait_set construction. Two-line fix masked by ~50 lines of reshape per primitive. Worth noting that the reshape was justified by clarity (the alternative — loop-with-FIRST_COMPLETED — was rejected as harder to follow).

### Smoke findings

See [smoke.md](smoke.md). Headlines:

- ✅ **The bug is fixed.** Outer parallel entry at seq `(3,)` lands in the live smoke (vs missing in #574 and #580 smokes — that was the smoking gun).
- ✅ **Workflow exits cleanly** via the orchestrator's fail-fast guard rather than the primitive hang. The system is now working as designed.
- 📋 **Surfaced incidental issue: `tabstack_research` exceeds the 180s per-tool TOOL_TIMEOUT_SEC.** The tool's iterative research takes longer than the default per-tool timeout. Worth a follow-up issue — either bump `/research`'s per-tool timeout, return partial results on timeout, or pick a faster fan-out target.

### Acceptance check

#582's acceptance criteria are all met. Unit tests directly exercise the bug at the primitive level; live smoke confirms the production code path behaves correctly under realistic load (parallel completes, journal lands the outer entry, orchestrator can react to the result list).
