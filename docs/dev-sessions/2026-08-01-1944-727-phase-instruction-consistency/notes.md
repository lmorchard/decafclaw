# Session notes — #727 phase-consistent project-skill instructions

**Status: PARKED at 2c/execute, pending one decision from Les.** No PR opened. The branch and
worktree are intact and either resolution resumes cheaply from here.

## What happened

Phases 0 (freeze) and the plan completed cleanly. Implementation fixed 2 of the 4 sites outright.
The other 2 hit a **frozen check that fails an implementation which satisfies its own criterion** —
the amendment trigger in `frozen-checks.md`. That is a mandatory stop at either tier.

## The defect in the frozen check

The check-author subagent was briefed (by me) with this sentence about `tool_project_switch`:

> "The text is invariant over the switched-to status, so you can produce it once and evaluate it
> against every `ProjectState`."

That is a true statement about the **buggy** code, and I put it in the brief. The check-author
faithfully encoded it: `_sites()` produces switch's text **once** (from a freshly-created,
therefore `BRAINSTORMING`, project) and grades that single sample against all six phases. Same for
`tool_project_advance`, produced once with `target_status="planning"`.

So the oracle inherited an implementation detail of the pre-fix code. It now asserts something
stronger than, and different from, the criterion: *one fixed string must be safe in every reading
phase*, rather than *the string emitted for phase P must be safe in P*.

This is the failure mode `frozen-checks.md` warns about from the other direction — not a weakened
oracle, but one that was over-fitted to current behaviour at authoring time. Worth remembering: a
check-author brief should describe the *criterion*, never the shape of the code being replaced.

## Consequence — the check permits only one implementation

Measured (`/tmp/probe727b.py`), the intersection of tool sets across every reading phase:

- **switch** (readable in all 6 phases) — the frozen check permits only **`project_status`**
- **advance** (readable in `{brainstorming, done, planning}`) — permits only
  `project_note` / `project_status`

So the frozen check mandates a phase-*independent* hint at both sites, and forbids a phase-*aware*
one, even though the phase-aware one is strictly more accurate.

## Amendment test — run, not reasoned

`/tmp/probe727c.py` implements both wordings and was run against both trees (freeze tree reached
via `git stash push -- src/decafclaw/skills/project/`, restored with `git stash pop`).

- **OLD wording** = produce the text once, grade against every reading phase
- **NEW wording** = produce the text for each reading phase, grade each against its own phase

| tree | OLD | NEW |
|---|---|---|
| freeze `eb32b8a` | FAIL — 4 pairs | FAIL — identical 4 pairs |
| current implementation | FAIL — 4 pairs | **PASS** — 0 |

Verdict **same at freeze**, **differs against the implementation** → the bottom-left cell of
`frozen-checks.md`'s four-cell table → **amendment**, not clarification. Amendments require human
confirmation regardless of tier and downgrade the run to `needs-review`.

## The decision Les needs to make

Two honest implementations exist. Neither weakens anything; they differ in quality and in cost.

**Option A — phase-independent hint. No amendment. Stays `auto-ok`.**
Make switch and advance both emit `Call project_status …`. Passes the frozen check as written,
ships today, tier unchanged. Cost: an agent switching into a `BRAINSTORMING` / `PLANNING` /
`EXECUTING` project is told to call `project_status` where `project_next_task` was both correct
*and* the more useful instruction. It trades a mild regression in the three common paths for
correctness in the three broken ones. Not degenerate — `project_status` is real, dispatchable, and
gives a next action.

**Option B — phase-aware hint. Needs the amendment; downgrades this run to `needs-review`.**
Already implemented and in the working tree. A `_next_action_hint(phase)` helper reads
`_PHASE_TOOLS` and returns the best tool that phase can actually dispatch, so every phase gets the
most useful accurate instruction (`project_next_task` where available, `project_status` +
`project_task_done` in review phases, `project_status` in `DONE`). Verified: all six phases emit a
hint dispatchable in their own phase. Cost: `_sites()` in the frozen test must be amended to
produce each phase's own text, plus Les's confirmation and the tier downgrade.

My read, for what it's worth: **B is the better code and A is the cheaper path.** I did not pick,
because the spec pinned only "fix the messages, don't widen the gates" and said "exact wording is
the implementer's" solely about the `_next_execution_step` site — it never delegated whether
switch/advance become phase-aware, and choosing B unattended would mean editing the oracle to fit
my own implementation, which is the exact thing the freeze exists to prevent.

## Current state of the tree

Committed: freeze (`eb32b8a`), freeze-sha + plan (`f847d56`). The implementation is committed on
the branch as work-in-progress so nothing is lost under either option.

- `_next_execution_step` empty-plan branch → now routes to `project_advance` (dispatchable in
  `EXECUTING`). **Fixed, graded correctly by the frozen check, passing.**
- `prompts/plan_no_steps.md` → now names `project_update_plan` + `project_task_done`, both
  dispatchable in `PLANNING` *and* `PLAN_REVIEW`. **Fixed, graded correctly, passing.**
- `tool_project_switch`, `tool_project_advance` → phase-aware via `_next_action_hint` (Option B).
  Correct per the criterion; rejected by the frozen check as written.

Verification at the parked state:

- C1 `test_no_instruction_names_undispatchable_tool` — **fail**, 4 pairs (down from 6 at freeze).
  All 4 are the switch/advance mis-grading described above.
- C2 `test_every_instruction_names_a_dispatchable_tool` — **fail**, same 4.
- Guards G2/G3/G4 — `3 passed`.
- G1 `uv run pytest tests/test_project_tools.py -q` — `2 failed, 39 passed`; no pre-existing test
  lost, skipped, or newly failing (the 2 failures are the frozen criteria themselves).
- `make check` — green (0 errors, 0 warnings; JS clean).
- Phase 2 (docs) — **not started.** `docs/project-skill.md:52-67` lists all twelve tools flatly
  with no mention that the set is phase-gated; the planned subsection is still owed and its wording
  depends on which option ships.

## Resuming

- **If A:** replace `_next_action_hint` calls at the two sites with fixed `project_status` text (or
  drop the helper), run the two criteria nodes, do Phase 2, then `pr`. Tier stays `auto-ok`.
- **If B:** amend `_sites()` in `tests/test_project_tools.py` so switch/advance text is produced
  per reading phase; log the amendment in `checks.md`; re-freeze that file; set tier
  `needs-review (downgraded: C1/C2 check amended)`; do Phase 2, then `pr`.

## Carried forward (not acted on — out of scope)

- The spec recorded `34 passed` for `tests/test_project_tools.py` at triage (2026-07-29); the file
  was at **36** at session start. Drift in the file, not a guard violation, but the guard's recorded
  baseline is worth re-reading rather than trusting.
- The spec's C1 says "all seven `ProjectState` values"; there are **six**. Logged as a clarification
  in `checks.md` (no verdict change at either tree, so no tier cost). `_PHASE_TOOLS` has seven keys
  — the six states plus `None` — which is the likely origin of the miscount.
