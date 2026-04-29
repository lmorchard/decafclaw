# Session notes: eval-expect-tool-assertions

**Issue:** [#349](https://github.com/lmorchard/decafclaw/issues/349) — Eval harness: expect_tool / expect_no_tool / expect_tool_count_by_name assertions
**Branch:** `eval-expect-tool-assertions`
**Worktree:** `.claude/worktrees/eval-expect-tool-assertions/`
**Started:** 2026-04-29 15:01

## Context

Split from #240. Audit doc in #338 (`docs/dev-sessions/2026-04-24-0941-eval-coverage/evals-audit.md`) flagged that `docs/eval-loop.md` originally claimed `expect_tool` support but the runner never implemented it. Row was removed from doc table. Real feature still needed — load-bearing for tool-deferral evals.

## Scope (from issue)

Implement three assertions in `src/decafclaw/eval/runner.py::_check_assertions`:
- `expect.expect_tool: <name>` — fail if agent did NOT call named tool
- `expect.expect_no_tool: <name>` — fail if agent DID call named tool
- `expect.expect_tool_count_by_name: {name: count}` — exact match (consider min/max later)

Update `docs/eval-loop.md` to re-add to field table.

## Open questions / observations

(filled during brainstorm)
