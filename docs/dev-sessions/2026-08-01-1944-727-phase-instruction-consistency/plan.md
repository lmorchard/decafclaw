# Phase-consistent project-skill instructions — Implementation Plan

**Goal:** Make every instruction the project skill emits name only tools the reading phase can
actually dispatch, while still naming a real next action.

**Source issue:** https://github.com/lmorchard/decafclaw/issues/727 — **Tier:** `needs-review`
(filed `auto-ok`; downgraded 2026-08-02 when amendment A1 to the C1/C2 check was approved and
applied — see `checks.md`. The work's risk profile is unchanged; the downgrade is the amendment's
cost.)

**Approach:** Fix the four instruction sites, do **not** widen `_PHASE_TOOLS`. Two sites
(`tool_project_switch`, `tool_project_advance`) currently emit an unconditional
`Call project_next_task.` while the *reading* phase varies — make each one phase-aware. One site
(`_next_execution_step`) names a tool `EXECUTING` does not have — route the agent to
`project_advance` back to `PLANNING`, which is dispatchable there. One prompt file
(`plan_no_steps.md`) is read in both `PLANNING` and `PLAN_REVIEW` — name a tool dispatchable in both.

**Criteria:** C1 no instruction names an undispatchable `project_*` tool · C2 every instruction still
names at least one dispatchable one.

Full text + checks live in `checks.md`. Ids are assigned there.

---

## Phase 0: Freeze the acceptance checks — DONE

Written and committed as `eb32b8a`. No implementation in it.

**Files:**
- Created: `docs/dev-sessions/2026-08-01-1944-727-phase-instruction-consistency/checks.md`
- Modified: `tests/test_project_tools.py` — added `TestPhaseInstructionConsistency`
  (2 criteria nodes + 3 guard nodes)

**Verification — automated:**
- [x] C1 fails for the expected reason — 6 mismatched (site, phase) pairs, pytest exit 1
      (not 5), from a real assertion over invoked code
- [x] C2 fails for the expected reason — same 6 pairs, empty intersection, exit 1
- [x] Guards pass: `uv run pytest tests/test_project_tools.py -q -k "guard_"` → 3 passed
- [x] G1 pre-freeze baseline: `uv run pytest tests/test_project_tools.py -q` → 36 passed
- [x] G5: `make test` → 3675 passed, 2 skipped
- [x] Freeze commit made (`eb32b8a`); sha recorded in `checks.md` in this follow-up commit

---

## Phase 1: Make the four instruction sites phase-accurate

One vertical slice — the four sites are the same defect and share one oracle, and splitting them
would leave C1 and C2 red at every intermediate commit without buying independent value.

**Advances:** C1, C2 (both fully)

**Files:**
- Modify: `src/decafclaw/skills/project/tools.py` — `_next_execution_step`,
  `tool_project_switch`, `tool_project_advance`
- Modify: `src/decafclaw/skills/project/prompts/plan_no_steps.md` — the one prompt template
- **Read-only (frozen):** `tests/test_project_tools.py` — a failing node here means the
  implementation is wrong, never the check

**Key changes:**

A shared helper, so the four sites derive the instruction from `_PHASE_TOOLS` rather than
each hardcoding a guess. Placed next to `_PHASE_TOOLS` (below it, since it reads it):

```python
def _next_action_hint(phase: ProjectState) -> str:
    """Name a tool the given phase can actually dispatch.

    Instruction text is read on the *next* turn, when the phase's tool set is what
    `get_tools` returns for `phase` — so a hardcoded tool name goes stale silently (#727).
    """
    names = _PHASE_TOOLS.get(phase, [])
    if "project_next_task" in names:
        return "Call project_next_task."
    if "project_task_done" in names:
        return "Call project_status to review, then project_task_done when it looks right."
    if "project_status" in names:
        return "Call project_status to see where the project stands."
    return "Call project_list to see available projects."
```

The ladder is ordered by usefulness, and every rung is checked against `_PHASE_TOOLS` at call
time, so the helper cannot name a tool the phase lacks. `DONE` has no `project_next_task` and no
`project_task_done`, so it lands on `project_status` — accurate, and meaningful for a finished
project.

Site-by-site:

1. **`tool_project_switch`** — the switched-to project's own status governs the next turn, and
   `info.status` is in hand:

   ```python
   return (
       f"Switched to project '{info.slug}' ({info.status.value}). "
       f"{_next_action_hint(info.status)}"
   )
   ```

2. **`tool_project_advance`** — `target` is the phase that reads the message:

   ```python
   return f"Project reverted to {target.value}. {_next_action_hint(target)}"
   ```

   Note `EXECUTING → DONE` is a permitted (forward) transition, which is the case that fails
   today. `_next_action_hint(DONE)` resolves to `project_status`.

3. **`_next_execution_step`**, empty/missing-plan branch — the reading phase is always
   `EXECUTING`, which has no plan-writing tool at all. Route to the recovery that *is*
   dispatchable there, per the spec's second design decision:

   ```python
   if not content.strip():
       return (
           "No plan found — the plan file is empty or missing. "
           "Call project_advance with target_status='planning' to go back and rewrite it."
       )
   ```

   `project_advance` is in `_PHASE_TOOLS[EXECUTING]`, so C1 and C2 both clear here.

4. **`prompts/plan_no_steps.md`** — read in both `PLANNING` and `PLAN_REVIEW`. `project_next_task`
   is in `PLANNING` only; `project_update_plan` and `project_task_done` are in **both**. The
   template is static text with no phase in scope, so it must name a tool dispatchable in both:

   ```markdown
   The plan was written but has no parseable steps.

   Rewrite it with project_update_plan using checkbox format:
   ```
   - [ ] 1. First step
   - [ ] 2. Second step
   ```

   Then call project_task_done to submit it for review.
   ```

   Both named tools are in `PLANNING` and `PLAN_REVIEW`, so C1 holds and C2's intersection is
   non-empty in both.

**Verification — automated:**
- [x] C1's check passes: `uv run pytest tests/test_project_tools.py::TestPhaseInstructionConsistency::test_no_instruction_names_undispatchable_tool`
- [x] C2's check passes: `uv run pytest tests/test_project_tools.py::TestPhaseInstructionConsistency::test_every_instruction_names_a_dispatchable_tool`
- [x] G1: `uv run pytest tests/test_project_tools.py -q` — no test lost, newly skipped, or newly
      failing (41 nodes: 36 pre-existing + 5 frozen)
- [x] G2/G3/G4: `uv run pytest tests/test_project_tools.py -q -k "guard_"` → 3 passed
- [x] G5: `make test` — full suite green, no regression
- [x] `make check` passes (lint + pyright + JS)

**Verification — manual:**
- None. Both criteria are mechanical — no human judgment is involved in grading them. (The
  `needs-review` tier comes from amendment A1, not from any criterion needing a human eye.)

---

## Phase 2: Document the phase-gated tool set

`docs/project-skill.md:52-67` lists all twelve tools in one flat table with no indication that the
set is **per-phase** — which is the fact this whole issue turns on. Close that gap and record the
invariant the frozen test now enforces.

**Advances:** neither C1 nor C2 — resolved deliberately rather than left ambiguous. The plan
template asks whether a phase advancing no criterion is scope creep or a missing criterion; it is
neither here. It is the repo's own docs convention (`CLAUDE.md`: "When changing a feature: update
its `docs/` page **as part of the same PR**, not a follow-up"), which applies to every PR
regardless of criteria. Scope is bounded to the phase-gating fact and the instruction invariant —
no rewrite of the page.

**Files:**
- Modify: `docs/project-skill.md` — add a short "Phase-gated tool exposure" subsection after the
  Tools table

**Key changes:** a subsection stating that `get_tools` recomputes the dispatchable set each turn
from the project's saved status via `_PHASE_TOOLS`, that review phases deliberately withhold
`project_next_task` and `EXECUTING` deliberately withholds `project_update_plan`, and that
instruction text must therefore name only tools the *reading* phase can dispatch — with a pointer
to `TestPhaseInstructionConsistency` as the enforcing test and a note that a new instruction site
must be added to that test's table.

**Verification — automated:**
- [x] `make check` passes
- [x] No claim in the subsection contradicts `_PHASE_TOOLS` — cross-read against
      `tools.py:825-848`

---

## Notes on scope

- **No widening of `_PHASE_TOOLS`.** Pinned by the spec's design decision and enforced by guard G2.
- **`SKILL.md:37`'s mention of `project_update_plan`** stays as-is — always-loaded context is
  phase-independent, explicitly out of scope.
- **No control-flow analysis.** The site→phase mapping is a hand-maintained table in the test, the
  sanctioned approach per the issue.
