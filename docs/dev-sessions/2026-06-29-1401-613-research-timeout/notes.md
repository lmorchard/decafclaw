# Notes — /research tabstack timeout (#613)

## Session start — 2026-06-29 14:01

- Branch: `fix/613-research-timeout` (from `origin/main` @ `ea21c02`)
- Worktree: `/Users/lorchard/devel/decafclaw/.claude/worktrees/fix-613-research-timeout`
- Session dir: `docs/dev-sessions/2026-06-29-1401-613-research-timeout/`
- HTTP_PORT: 18895; `TABSTACK_API_KEY` enabled per #580 learning
- Baseline: `make test` — 2981 passing in 20.93s

## Origin

Surfaced by #582 smoke (PR #610). With the #582 primitive hang fixed, `/research` now reaches `tabstack_research` cleanly — but all 3-5 parallel calls hit the 180s `TOOL_TIMEOUT_SEC` default before iterative research can finish. The orchestrator's fail-fast guard correctly catches the all-error case and exits cleanly, but `/research` can't actually complete a real research session.

Four candidate directions in the issue body (longer timeout / partial results / lighter tool / fewer queries). Brainstorm to pick.

## Execution complete (2026-07-06)

| Phase | Commit | Notes |
| --- | --- | --- |
| 1: tabstack_research timeout=600 + regression test | `f7b63c4` | TDD clean. Implementer caught the `TOOL_DEFINITIONS` shape mismatch (OpenAI schema nesting under `"function"`; `"timeout"` is a top-level sibling) and adjusted both fix and test to match the resolver at `tools/__init__.py:130-149`. |
| 2: Lower /research query count to 2-3 | `eba7b36` | Three coordinated edits (schema bounds + system prompt + user prompt). Implementer correctly identified the unrelated "3-5" refs in `_SYS_SUMMARIZE` and `_summarize_prompt` (about summary bullet counts) and left them alone. |
| 3: Live smoke + session artifacts | (this commit) | End-to-end success on Flash: `status="done"`, 2 queries × ~4-5KB real markdown, final report at seq `(5,)` titled "Restoring Our Underwater Forests." ~3 min wall-clock; well under the 600s ceiling. |

### Execute-phase highlights

- **Both spec-anticipated shape checks fired.** The schema mismatch in Phase 1 (`name` nested under `"function"`) was anticipated as "check during implementation"; the implementer caught and adjusted. The `/research` test compatibility in Phase 2 was checked during plan-phase (existing mocks use 2/3 queries, both fit the new 2-3 cap).
- **The 600s ceiling is generous, and that's the right posture.** Actual wall-clock for both tabstack calls was ~180s each (finishing right around where the OLD default cut them off — hence the failure mode). 600s gives ~3× headroom for typical topics and 10x for cache-cold cases; catches runaway iteration bugs without preemptively cutting off real work.

### Smoke findings

See [smoke.md](smoke.md). Headlines:

- ✅ **`/research` reaches subagent synthesis with a real report** — the first end-to-end demo of #574's fan-out primitives.
- ✅ **No `[error: timed out]` entries** in the journal (contrasts with the #613 pre-fix smoke).
- ✅ **Journal shape is textbook**: user_input × 2 → llm_call (plan) → parallel → tool_call × 2 → pipeline → summarize llm_call × 2 → subagent → done.
- 📋 No new follow-ups from this smoke — the fix landed the hero workflow cleanly.

### Acceptance check

All #613 acceptance criteria met. `/research` now works as a usable demo of the fan-out primitives.
