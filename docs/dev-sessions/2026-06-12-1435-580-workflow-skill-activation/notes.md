# Notes — Workflow skill activation (#580)

## Session start — 2026-06-12 14:35

- Branch: `feat/580-workflow-skill-activation` (from `origin/main` @ `11e0691`)
- Worktree: `/Users/lorchard/devel/decafclaw/.claude/worktrees/feat-580-workflow-skill-activation`
- Session dir: `docs/dev-sessions/2026-06-12-1435-580-workflow-skill-activation/`
- HTTP_PORT: 18893 (main 18880, conversation-sidecar-dirs 18891, feat-574 used 18892)
- Baseline: `make test` — 2914 passing in 22.72s

## Origin

Follow-up from #574 (PR #579) live smoke Finding 1. The `/research` workflow can't reach `tabstack_research` because the tabstack skill isn't activated for `TurnKind.WORKFLOW` turns; only the agent loop activates skills via `activate_skill`. Spec sketch is the issue body — needs brainstorm to pick between the three sketched directions.

## Execution complete (2026-06-24)

| Phase | Commit | Test delta |
| --- | --- | --- |
| 1: Extract `activate_always_loaded` | `cb6964b` | 2931 → 2935 (+4) |
| 2: `activate_skills_for_workflow` + `WorkflowSkillActivationFailed` | `88bef0a` | 2935 → 2941 (+6, including silent-ToolResult-failure regression test) |
| 3: `WorkflowSpec.requires_skills` + decorator | `d8e0866` | 2941 → 2944 (+3) |
| 4: `run_workflow_turn` activation block | `735ba49` | 2944 → 2948 (+4) |
| 5: `/research` declares `requires_skills=("tabstack",)` | `4d43c10` | 2948 → 2949 (+1) |
| 6: docs + live smoke | (this commit) | no test change |

### Execute-phase highlights

- **Phase 1 bug-find: `activate_skill_internal` silent ToolResult failure path.** The code-quality reviewer caught that `activate_skill_internal` (`tools/skill_tools.py:228-230`) catches its own exception and returns a `ToolResult` rather than raising. The original Phase 2 helper's `try/except Exception` missed this branch entirely — a `requires_skills` declaration on a skill with a broken `tools.py` would silently succeed (activation function returned cleanly, but the skill wasn't actually added to `ctx.skills.activated`). Fixed by adding a post-check on `ctx.skills.activated` membership and extracting the `ToolResult.text` for the raised exception message. New regression test (`test_activate_skills_for_workflow_tool_load_failure_raises`) faked a `ToolResult` return value to lock in the fix.

- **Phase 4 — fail-soft + fail-loud single try/except shape.** `activate_always_loaded` (fail-soft) and `activate_skills_for_workflow` (fail-loud) are wrapped in one try/except in `run_workflow_turn`. The reviewer noted this is mildly misleading (the always-loaded helper can't raise `WorkflowSkillActivationFailed`), but the inline comment carries the load. Acceptable.

### Smoke findings

See [smoke.md](smoke.md). Headlines:

- ✅ **Setup gotcha discovered.** Tabstack's `requires.env: TABSTACK_API_KEY` declaration gates discovery on the actual env var, not the resolved value in `data/decafclaw/config.json`. Worktree `.env` had the var commented; uncommenting it made the skill discoverable. Worth a memory note for future smoke setups.
- ✅ **Run 1 (pre-fix) — fail-loud activation confirmed.** With tabstack not in discovered skills, the workflow returned `"[error: skill activation failed: requires_skills entry 'tabstack' is not a discovered skill]"` BEFORE running. Journal status persisted as `"error"`.
- ✅ **Run 2 (post-fix) — real tabstack output landed in the journal.** Four `wf.parallel` thunks each completed a real `tabstack_research` invocation, with 4-6KB of legitimate markdown per child entry. This is the load-bearing proof that the activation block actually makes skill tools reachable from workflows.
- ❌ **Workflow hangs at the same point as #574 smoke Finding 3.** After the 4 child `tool_call` entries land, `wf.parallel`'s outer entry never lands and pipeline never starts. This time the input is REAL markdown, not error text — ruling out the "Vertex throttling on degenerate input" hypothesis from #574/#582. The bug is in the parallel/pipeline primitives themselves. **#582 needs updating with this evidence.**

### Acceptance check

#580's acceptance criteria are all met (always-loaded auto-activates, `requires_skills` declarations work, fail-loud verified, `/research` reaches real tabstack). The pre-existing #582 primitive bug prevents the full hero workflow from running end-to-end, but that's a separate issue with its own ticket.

## Brainstorm — decisions

### Round 1 (locked)

- **Reframe surfaced by research:** the gap is broader than tabstack — workflows can't reach ANY skill tool, including the always-loaded set (vault/background/mcp). Workflow turns bypass `_setup_turn_state` AND the workflow engine bypasses ContextComposer entirely.
- **Direction:** Hybrid. Workflow turns auto-activate the always-loaded set (matching USER turns), plus `@workflow(..., requires_skills=[...])` declarations for additional skills. Composes the two existing skill-availability models.
- **Failure policy:** fail-loud — activation errors raise before `run_workflow` runs the orchestrator. Matches decafclaw's zero-tolerance-for-silent-skips posture.

### Spec ready

`spec.md` is finalized. Headlines:
- Extract `activate_always_loaded(ctx, *, extra=())` helper in `skills/__init__.py`. Used by both `_setup_turn_state` and `run_workflow_turn`.
- `WorkflowSpec.requires_skills: tuple[str, ...] = ()`. Decorator: `@workflow(name, *, model=..., requires_skills=())`.
- `/research` declares `requires_skills=["tabstack"]`.
- Open questions all have defaults.
