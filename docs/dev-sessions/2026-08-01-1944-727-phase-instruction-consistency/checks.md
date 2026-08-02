# Frozen acceptance checks

**Source:** https://github.com/lmorchard/decafclaw/issues/727
**Frozen at:** `eb32b8a` (2026-08-01)
**Check files — read-only from Phase 1 onward:**
- `tests/test_project_tools.py`

Note: this file is *both* the check file and guard G1's target. The frozen check is the new
`TestPhaseInstructionConsistency` class added in the freeze commit; G1 covers the 36 pre-existing
tests in the same file. The tamper diff runs from the freeze commit forward, so it must be empty —
the freeze commit already contains the added class.

## C1

CRITERION: The project skill SHALL NOT emit instruction text naming a `project_*` tool that is not
dispatchable in the phase which reads that text.

CHECK: `uv run pytest tests/test_project_tools.py::TestPhaseInstructionConsistency::test_no_instruction_names_undispatchable_tool`

AT FREEZE: fails — 6 mismatched (site, phase) pairs, exactly the set the issue enumerates:

```
_next_execution_step (empty plan)  phase=executing    not-dispatchable=['project_update_plan']
tool_project_switch                phase=spec_review  not-dispatchable=['project_next_task']
tool_project_switch                phase=plan_review  not-dispatchable=['project_next_task']
tool_project_switch                phase=done         not-dispatchable=['project_next_task']
tool_project_advance               phase=done         not-dispatchable=['project_next_task']
prompts/plan_no_steps.md           phase=plan_review  not-dispatchable=['project_next_task']
```

Collected 1 test, 1 failed. Correct reason: the behaviour is genuinely absent, not a setup error.

## C2

CRITERION: Each of the four sites SHALL still name at least one `project_*` tool that **is**
dispatchable in every phase that reads it.

CHECK: `uv run pytest tests/test_project_tools.py::TestPhaseInstructionConsistency::test_every_instruction_names_a_dispatchable_tool`

AT FREEZE: fails — the same 6 (site, phase) pairs have an *empty* intersection between the tokens
found and `_PHASE_TOOLS[phase]` (each site currently names exactly one tool, and that tool is the
non-dispatchable one). Collected 1 test, 1 failed. Correct reason.

## Guards

(Pass today; must keep passing. Not criteria — they can't fail at freeze.)

- G1: `uv run pytest tests/test_project_tools.py -q` — invariant: no test lost, newly skipped, or
  newly failing. **Passed at freeze: 36 passed** (pre-freeze baseline, before the frozen class was
  added). The issue recorded `34 passed` at triage on 2026-07-29; the file has gained two tests
  since, which is drift in the file, not a violation of the invariant.
- G2 (**enforces the spec's design decision**): the deliberate exclusions in `_PHASE_TOOLS` are
  preserved — `SPEC_REVIEW`, `PLAN_REVIEW` and `DONE` still exclude `project_next_task`, and
  `EXECUTING` still excludes `project_update_plan`. Blocks the degenerate fix of widening the gates.
  Passed at freeze (all four exclusions hold — that is why the six mismatches exist).
- G3: phase gating remains real — no `ProjectState` exposes the entire `TOOLS` registry. Blocks the
  other degenerate fix, making `_tools_for_phase` return everything. Passed at freeze.
- G4: `TRANSITIONS` (`skills/project/state.py`) is unchanged —
  `EXECUTING → {DONE, PLANNING, BRAINSTORMING}`. Altering the state machine to dodge the `DONE`
  case would satisfy the criteria for the wrong reason. Passed at freeze.
- G5 (invariant): full suite green. **Passed at freeze: 3675 passed, 2 skipped** — the issue left
  this UNRUN; it was run at session setup on the worktree baseline and again at the freeze.

G2, G3 and G4 are executable as pytest nodes in the same frozen class
(`test_guard_phase_tools_exclusions_preserved`, `test_guard_phase_gating_remains_real`,
`test_guard_transitions_unchanged`) so the verifier can run each by name rather than eyeballing a
diff.

## Amendments

(Append-only.)

**None.** One **clarification** was logged — it changes no criterion's verdict at either tree, so
per `frozen-checks.md` it costs no tier downgrade:

- **C1 prose said "all seven `ProjectState` values"; `ProjectState` has six members**
  (`BRAINSTORMING`, `SPEC_REVIEW`, `PLANNING`, `PLAN_REVIEW`, `EXECUTING`, `DONE`). The runnable
  reading is "every `ProjectState` value", which is what the check does. The "seven" reading is not
  runnable at all — there is no seventh member to iterate — so no verdict can differ at the freeze
  tree or the implementation tree. Verified: iterating all six values reproduces exactly the 6
  mismatches the issue's own table predicts, so the criterion's intent and its arithmetic disagree
  only in the count, not in the result. (`_PHASE_TOOLS` does have seven keys — the six states plus
  `None` for "no current project" — which is the likely origin of the miscount. `None` is correctly
  excluded from `project_switch`'s reader set: a switch always leaves a current project selected,
  so the next turn is never the `None` phase.)

## Tamper verdict

(Recorded at `pr.md` step 5.)
