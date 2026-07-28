# progress_tracker: stale sticky tracker when leaving EXECUTING via project_switch/project_create

**Source:** https://github.com/lmorchard/decafclaw/issues/656

Follow-up from #414 (PR #653).

The project skill clears its `progress_tracker` from the sticky slot on transition to DONE and on `project_advance` out of EXECUTING, but **not** when the active project changes without a status transition — i.e. `project_switch` or `project_create` while a project is EXECUTING. Since projects and the sticky slot are both per-conversation, switching to another project mid-execution leaves project A's tracker pinned above the chat until project B next enters EXECUTING and emits.

Display-only (no functional harm), and outside #414's enumerated transitions, so it was deliberately deferred. Fix: call `_clear_project_progress(ctx)` (or re-emit for the newly active project) when `project_switch`/`project_create` moves away from an EXECUTING project.

Surfaced by the #414 whole-branch review.

---

## Design decisions (resolved at intake, 2026-07-27)

**D1 — clear the sticky slot; do not re-emit for the newly active project.**
`project_switch` / `project_create` that moves away from an EXECUTING project calls
`_clear_project_progress(ctx)` (`skills/project/tools.py:151`), matching what the skill already does
on transition to DONE (line 335) and on `project_advance` out of EXECUTING (line 615). One rule for
every way of leaving EXECUTING.

Rejected: re-emitting the newly active project's tracker. It is only defined when the *incoming*
project is itself EXECUTING, so it still needs the clear as a fallback — making it "clear, then emit
if applicable", a superset that needs its own criterion for what the incoming project displays. That
is a UX enhancement, not this bug.

## Acceptance criteria

**C1 — switching away from an EXECUTING project clears its tracker.**
WHEN `project_switch` changes the active project while the outgoing project is EXECUTING,
THE SYSTEM SHALL clear the `progress_tracker` sticky slot.
- CHECK: `uv run pytest tests/test_project_tools.py::TestProgressTrackerEmit::test_switch_away_from_executing_clears`
- Demonstrated absent at intake: a throwaway test mirroring the existing
  `test_advance_out_of_executing_clears` failed with `clear_sticky.await_count == 0`.

**C2 — creating a project while another is EXECUTING clears the outgoing tracker.**
WHEN `project_create` makes a new project active while the outgoing project is EXECUTING,
THE SYSTEM SHALL clear the `progress_tracker` sticky slot.
- CHECK: `uv run pytest tests/test_project_tools.py::TestProgressTrackerEmit::test_create_while_executing_clears`
- Demonstrated absent at intake: same throwaway, `clear_sticky.await_count == 0`.
- Kept separate from C1 because these are two distinct call sites; a fix that threads the clear into
  only one of them satisfies the other criterion and leaves half the bug.

### Regression guards (pass today; must keep passing — not criteria)

- **G1:** `tests/test_project_tools.py::TestProgressTrackerEmit::test_done_clears_sticky` and
  `::test_advance_out_of_executing_clears` — the two existing clear paths must keep working.
- **G2 (negative control):** `tests/test_project_tools.py::TestProgressTrackerEmit::test_update_step_emits_during_executing`
  — the tracker must still be *set* while a project is EXECUTING. Blocks an over-broad fix that
  clears the slot on every project-tool call.
- Observed at intake: `2 passed in 1.65s` for the two G1 tests
  (`-k "clears_sticky or advance_out_of_executing_clears"`).

## What we're NOT doing

- **Not re-emitting the incoming project's tracker** — see D1. If that is wanted, it is its own issue.
- **Not revisiting #414's enumerated transitions.** DONE and `project_advance` already clear
  correctly; G1 pins them.
- **Not touching the sticky mechanism itself** (`decafclaw.sticky`). The fix is call sites in
  `skills/project/tools.py`.

## Tier: `auto-ok`

Both criteria reduce to concrete assertions using the mock pattern already established in
`tests/test_project_tools.py`, and both were demonstrated failing today. The oracle exists — the test
file, the `ctx` fixture, and the `_advance_to_executing` helper are all in place, so no harness needs
building. No risk-gated path: display-only, no auth/authorization, secrets, data migration or
deletion, deploy/infra/CI config, or dependency change. The switch-vs-re-emit choice was the only
withheld decision, and D1 resolves it.

---
*Decisions resolved and criteria added via `agent-session intake`. Every check was run at intake
time, not inferred; original issue text preserved verbatim above.*
