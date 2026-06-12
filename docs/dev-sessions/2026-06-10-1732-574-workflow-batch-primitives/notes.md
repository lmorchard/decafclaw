# Notes — Workflow batch primitives (#574)

## Session start — 2026-06-10 17:32

- Branch: `feat/574-workflow-batch-primitives` (from `origin/main` @ `024175d`)
- Worktree: `/Users/lorchard/devel/decafclaw/.claude/worktrees/feat-574-workflow-batch-primitives`
- Session dir: `docs/dev-sessions/2026-06-10-1732-574-workflow-batch-primitives/`
- HTTP_PORT: 18892 (main uses default 18880, conversation-sidecar-dirs worktree uses 18891)
- Baseline: `make test` — 2820 passing in 52s

## Pre-flight reading queue (per user)

1. `gh issue view 574` — done; sketch in spec.md
2. `docs/workflows.md` — engine substrate (research.md §5 captures the "What is not in v1" verbatim)
3. `docs/dev-sessions/2026-06-05-1455-workflow-replay-engine/` — prior session's spec/plan/notes (research.md §5)
4. PR #573 — what just shipped + smoke checklist

## Brainstorm — design decisions

### Round 1 (locked)

- **Sub-keying:** hierarchical sub-handles. `wf.parallel` allocates outer seq N, each thunk gets a sub-handle keyed by `(N, thunk_idx)` with its own `_cursor` starting at 0. Journal entry `seq` becomes a tuple-path. Outer entry caches the aggregate result for fast-path full-replay; child entries enable mid-parallel resume. Nests naturally for parallels-of-parallels and pipelines-of-parallels.
- **Subagent dispatch:** wrap `delegate._run_child_turn` directly (not via tool). Fingerprint includes prompt + schema + allowed_tools. Journal stores text + optional structured data. Reuses the mature child-dispatch path.
- **Error policy in `wf.parallel`:** propagate (raise out) by default. Callers wrap individual thunks in try/except for tolerant collection. Matches decafclaw's "fail loud" posture.
- **Tool visibility for `wf.tool_call`:** parent ctx's `allowed_tools` — same set the orchestrator's parent agent could invoke at this turn. Uses existing `tools.execute_tool` plus current allowed_tools gate.

### Round 2 (locked)

- **Cancellation propagation:** cooperative cancel via `ctx.cancelled`. Mirrors `tool_execution.py:307-311`'s pattern — outer cancellation cancels in-flight sub-thunks; completed journal entries are kept; resume continues from where it stopped.
- **Pipeline stage signature:** `async def stage(prev, item, idx, sub)`. Originally locked as `(prev, item, idx)` in round 2 to match Claude Code's Workflow tool, but plan self-review surfaced that durable replay requires stages to access a per-item sub-handle for journaled calls. Departed from Claude Code's signature deliberately — their workflows aren't journaled, ours are.
- **Hero workflow:** `/research <topic>` — exercises all four primitives (user_input → parallel fetches → pipeline extract/summarize → subagent synthesis).
- **Subagent schema:** yes — `wf.subagent(prompt, schema=...)` forces structured output via `_run_child_turn`'s existing `return_schema` mechanic. Symmetric with `wf.llm_call`.

### Plan complete

`plan.md` has 8 phases. Foundation first (tuple-path journal + sub-handles), then primitives in increasing complexity (`tool_call` → `subagent` → `parallel` → `pipeline`), then hero workflow, then live smoke + docs.

## Execution complete

All 8 phases landed as separate commits on `feat/574-workflow-batch-primitives`:

| Phase | Commit | Test delta |
| --- | --- | --- |
| 1: Tuple-path journal | `ea31413` | 2820 → 2824 (+4) |
| 2: Sub-handle factory | `66314a0` | 2824 → 2831 (+7) |
| 3: `wf.tool_call` | `da3be9a` | 2831 → 2838 (+7) |
| 4: `wf.subagent` | `8907ec3` | 2838 → 2845 (+7) |
| 5: `wf.parallel` | `e32b742` | 2845 → 2854 (+9, incl. exception-unmasking regression test) |
| 6: `wf.pipeline` | `ae25dee` | 2854 → 2866 (+12) |
| 7: `/research` hero workflow | `3cc4de3` | 2866 → 2870 (+4) |
| 8a: `docs/workflows.md` contracts | `93166ef` | no test change |
| 8b: live smoke + findings | (this commit) | no test change |

### Execute-phase highlights

- **Phase 5 bug-find:** the spec's reference code for `wf.parallel` had `asyncio.wait(..., FIRST_EXCEPTION)` watching a cancel-watcher that returned normally on `event.wait()`. `FIRST_EXCEPTION` only triggers on raising tasks, so the watcher needed to raise a sentinel — fixed with an inner `_CancelSignal` class. Spec-compliance reviewer reproduced the bug from a 2-line script before approving the fix.
- **Phase 5 exception-masking bug:** the code-quality reviewer caught that `[t.result() for t in tasks]` in `wf.parallel` would surface a cleanup-induced `CancelledError` from a lower-index pending task instead of the higher-index task that actually raised. Fixed by iterating in index order and skipping cancelled tasks before re-raising the first real exception. Regression test added; Phase 6 mirrored the fix.
- **Phase 6 stage signature:** initially locked as `(prev, item, idx)` in brainstorm to match Claude Code's Workflow tool. Plan self-review caught that durable-replay stages need a per-item sub-handle for journaled calls; spec + plan updated to `(prev, item, idx, sub)` before any code touched.

### Live smoke (Phase 8b)

See [smoke.md](smoke.md) for the full transcript. Headlines:

- **Proven:** tuple-path on-disk serialization, sub-handle key composition under live load (`(3, idx, 0)` per parallel child), mid-run SIGINT preserves journal state faithfully, all four primitives wire through the transport, `/research` is user-invokable via existing `/<name>` slash dispatch.
- **Three follow-up findings** (filed for future work, not blockers for this PR):
  1. Skill-bundled tools (e.g., `tabstack_research`) aren't reachable from workflow contexts — `execute_tool` returns "unknown tool" because skills aren't activated for `TurnKind.WORKFLOW`. Surfaces correctly through `wf.tool_call` as an error-shaped result dict.
  2. No auto-resume on server restart for `status=running` workflows. Replay machinery is correct; the wiring gap is just "scan for in-flight journals at startup and re-enqueue."
  3. A 3-minute hang during pipeline summarize against error-text input — needs more instrumentation to diagnose (could be Vertex throttling on degenerate prompts, could be a primitive issue we missed).
