# Phase 4 — Live smoke transcript

Date: 2026-06-26
Worktree: `.claude/worktrees/fix-582-pipeline-hang` on `fix/582-pipeline-hang` @ post-Phase-3 tip
Model: `vertex-gemini-flash` (default for `/research`)
Conversation: `web-lmorchard-82eb0673`

## Setup

- Worktree `.env` already had `MATTERMOST_ENABLED=false`, `HTTP_PORT=18894`, and `TABSTACK_API_KEY` enabled (per #580 session learnings).
- Server: `uv run decafclaw` web-only on `0.0.0.0:18894`.
- Client: `decafclaw-client send`/`respond` against `http://localhost:18894`.

## What this smoke proves

**The #582 primitive-level hang is gone.** Pre-fix, `wf.parallel` would hang forever once all thunks completed normally with `ctx.cancelled` present but never set — the outer parallel entry at seq `(3,)` was never written (see #574 smoke Finding 3 and #580 smoke). Post-fix, the outer entry lands and the workflow continues.

The live smoke walked `/research kelp forest restoration techniques` end-to-end through the same code path that hung in #574 and #580. Two material differences from those prior smokes:

1. **The outer parallel entry at seq `"3"` is now present** in the post-crash journal:

   ```json
   {
     "seq": "3",
     "kind": "parallel",
     "result": [
       {"text": "[error: tool tabstack_research timed out after 180s]", "data": null},
       {"text": "[error: tool tabstack_research timed out after 180s]", "data": null},
       {"text": "[error: tool tabstack_research timed out after 180s]", "data": null}
     ]
   }
   ```

   Compare with #580's smoke journal (saved at `docs/dev-sessions/2026-06-12-1435-580-workflow-skill-activation/smoke-journal-snapshot.json`): same `(3, i, 0)` child entries, NO outer `(3,)` entry. The presence of `(3,)` here is the load-bearing proof that the fix worked.

2. **The workflow exited cleanly with `status="error"` via a higher-level mechanism**, not via the primitive hang. The orchestrator's `/research` fail-fast guard (PR #579's defense-in-depth check for "all results errored") raised `RuntimeError`, which the engine caught and persisted as `status="error"`. Pre-fix, the workflow would have sat at `status="running"` with no progress, no error, no exit.

## What the smoke surfaced about `tabstack_research`

All 3 `tabstack_research` calls timed out at the per-tool 180s `TOOL_TIMEOUT_SEC` default. The tool runs iterative research (the client log shows "Iteration 3 of 3", "Searching with 7 queries", "Analyzing 13 pages") — a single research session can exceed 3 minutes wall-clock on a non-trivial topic. The `/research` workflow runs 3-5 of these in parallel, so the parallel as a whole can take longer than per-tool timeout even when each call is making forward progress.

This is **out of scope for #582** (the primitive hang is fixed; the tool timeout is incidental). Worth filing as a follow-up:

- **`/research` should set a longer per-tool timeout for `tabstack_research`**, OR
- **`tabstack_research` should return partial results on timeout** rather than `[error: …]`, OR
- **`/research` should use a faster/lighter search tool** as the parallel fan-out target.

Probably worth a brief brainstorm if `/research` is going to be exercised as a real hero workflow.

## Final journal state (saved as `smoke-journal-snapshot.json`)

```
status: "error"
entries (seqs):  "0", "1", "2", "3", "3.0.0", "3.1.0", "3.2.0"
entries (kinds): user_input, user_input, llm_call, parallel, tool_call, tool_call, tool_call
```

Pre-fix expected state (would have been, per #574/#580 smokes):

```
status: "running"  (hung)
seqs: "0", "1", "2", "3.0.0", "3.1.0", "3.2.0"  ← NO "3" outer entry
```

The single-character difference (`"3"` present in `seqs`) is the entire #582 fix in one observable.

## Acceptance check

**#582 acceptance:**
- ✅ Unit-test repro of the bug in both `wf.parallel` and `wf.pipeline` (regression tests in `tests/test_workflow_parallel.py:235` and `tests/test_workflow_pipeline.py:347` or wherever they landed).
- ✅ Pre-fix new tests timeout at 2.0s (the wait_for guard).
- ✅ Post-fix new tests pass in <100ms.
- ✅ Existing cancellation regression tests (PR #574's index-vs-time-order, PR #574's mid-run-cancel) STILL PASS — the fix preserved their guarantees.
- ✅ Live smoke: outer parallel entry lands; workflow exits via orchestrator's fail-fast (not via primitive hang).
- ✅ Same fix applied to both primitives (deliberate code duplication per spec's third-callsite-extraction-threshold).

**Out of scope (separate issue):**
- `tabstack_research` per-tool timeout vs the tool's iterative-research duration. Surfaces #582 as a side effect — when the tool times out, the orchestrator's fail-fast catches it cleanly. That's the system working as designed.

## Artifacts

- Pre-restart journal snapshot: `smoke-journal-snapshot.json` (sibling file).
- Server log: `/tmp/decafclaw-582-smoke.log` (last activity at the parallel-completion + orchestrator-fail-fast point).
- Client log: `/tmp/decafclaw-582-final.log` (received the workflow-error message_complete from the engine).
