# Notes — Workflow status=running auto-resume (#581)

## Session start — 2026-07-07 15:48

- Branch: `fix/581-workflow-autoresume` (from `origin/main` @ `293b624`)
- Worktree: `/Users/lorchard/devel/decafclaw/.claude/worktrees/fix-581-workflow-autoresume`
- Session dir: `docs/dev-sessions/2026-07-07-1548-581-workflow-autoresume/`
- HTTP_PORT: 18896; `TABSTACK_API_KEY` enabled per prior sessions' learnings
- Baseline: `make test` — 2983 passing in 19.59s

## Origin

Filed as follow-up to #574's smoke (PR #579 Finding 2). If a server crash interrupts a workflow turn mid-LLM, `workflow.json` persists at `status="running"` but nothing re-enqueues it on server restart. The replay machinery itself is correct (unit tests already cover full-cache + mid-fan-out resume against synthetic journals) — the wiring gap is at the harness layer.

This is the LAST remaining follow-up from #574's smoke trio. After this, the workflow surface should be stable:
- #580 (skill activation from workflows) — closed
- #582 (parallel/pipeline hang) — closed
- #613 (/research timeout) — closed
- #581 (auto-resume on restart) — this session
