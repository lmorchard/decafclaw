# project skill instructs the agent to call tools its phase does not expose

**Goal:** Stop the project skill telling the agent to call tools that its current phase does not expose.
Six instructions across four sites name a tool that is not dispatchable on the turn that reads them,
leaving the agent at a dead end.

**Source:** https://github.com/lmorchard/decafclaw/issues/727 — surfaced by agent-session:triage while
scanning #525 (2026-07-29). #525 was closed as stale — its own failure signatures no longer reproduce —
but this defect is real, independent of it, and deterministic. Filed separately so it isn't lost on a
closed issue. Plausibly a contributor to the `project_next_task` thrash that #525's eval was actually
catching.

## Current state

The project skill exposes a different tool set per phase. `_PHASE_TOOLS`
(`src/decafclaw/skills/project/tools.py:815-838`) maps each `ProjectState` to its allowed tool names;
`_tools_for_phase` (`:841`) filters, and `get_tools` (`:849`) is the dynamic provider that recomputes the
set each turn from the project's saved status.

Separately, several tools return **instruction text naming the next tool to call.** Nothing checks those
names against the phase that will read them, and **six do not match.** Verified by probe, 2026-07-29:

| Site | Phase(s) reading it | Names | Dispatchable there? |
|---|---|---|---|
| `tools.py:352` `_next_execution_step` | `EXECUTING` | `project_update_plan` | **no** |
| `tools.py:424` `tool_project_switch` | `SPEC_REVIEW`, `PLAN_REVIEW`, `DONE` | `project_next_task` | **no** (3 phases) |
| `tools.py:620` `tool_project_advance` | `DONE` | `project_next_task` | **no** |
| `prompts/plan_no_steps.md` | `PLAN_REVIEW` | `project_next_task` | **no** |

Probe output for the first one, which is the clearest:

```
returned: 'No plan found. Use project_update_plan to write one.'
phase=executing  names=['project_update_plan']  NOT-DISPATCHABLE=['project_update_plan']
```

Why each is reachable:

- **`_next_execution_step`** — a project in `EXECUTING` whose plan file is empty or missing. `EXECUTING`
  is normally entered from `PLAN_REVIEW` approval, which requires a non-empty plan with ≥1 step, so this
  is reached when the plan file is edited or removed on disk afterwards. These are user-facing files, so
  that is an ordinary situation, not a contrived one. The agent is then told to write a plan with a tool
  it cannot call, and `EXECUTING` offers no plan-writing tool at all.
- **`tool_project_switch`** — returns `"Switched to project 'x' (<status>). Call project_next_task."`
  **unconditionally**, while the switched-to project's own phase governs the next turn.
  `SPEC_REVIEW`, `PLAN_REVIEW` and `DONE` all lack `project_next_task`.
- **`tool_project_advance`** — returns `"Project reverted to <target>. Call project_next_task."`
  unconditionally. `project_advance` is `EXECUTING`-only, and `TRANSITIONS`
  (`skills/project/state.py`) permits `EXECUTING → {DONE, PLANNING, BRAINSTORMING}`. `PLANNING` and
  `BRAINSTORMING` are fine; **`DONE` is not.**
- **`prompts/plan_no_steps.md`** — returned from the `PLANNING`/`PLAN_REVIEW` branch of
  `project_task_done` (`tools.py:302`) *before* any status change, so the phase on the next turn is
  whatever it already was. In `PLANNING` it is fine; in `PLAN_REVIEW` it is not.

Related but out of scope: `SKILL.md:37` names `project_update_plan` in always-loaded context, which is
phase-independent by nature.

## Design decisions

- **Decision: fix the instructions, do not widen the phase gates.**
  - **Why:** the gating is deliberate — review phases intentionally restrict to approve / revise / status,
    and `project_next_task` in `DONE` is meaningless. The defect is that the *messages* are wrong, not
    that the gates are.
  - **Rejected:** adding `project_next_task` to `SPEC_REVIEW`/`PLAN_REVIEW`/`DONE` and
    `project_update_plan` to `EXECUTING`. That would also satisfy the criteria below, which is why this
    is pinned here rather than left open — it would let the agent skip review phases, a change to the
    workflow's strictness rather than a bug fix.
  - The guards below enforce this decision mechanically, so the choice cannot silently drift.
- **Decision: `EXECUTING` with an empty plan should route the agent somewhere real** rather than name a
  plan-writing tool. `project_advance` back to `PLANNING` is dispatchable in `EXECUTING` and is the
  natural recovery. Exact wording is the implementer's.

## Verifiable acceptance criteria

- CRITERION: The project skill SHALL NOT emit instruction text naming a `project_*` tool that is not
  dispatchable in the phase which reads that text.
  CHECK: a pytest node — e.g.
  `uv run pytest tests/test_project_tools.py::TestPhaseInstructionConsistency::test_no_instruction_names_undispatchable_tool`
  — that, for each of the four sites above, extracts every `\bproject_[a-z_]+\b` token from the emitted
  text and asserts it is a member of `_PHASE_TOOLS[phase]` for every phase that can read it (including,
  for `project_switch`, all seven `ProjectState` values, and for `project_advance`, each valid
  `TRANSITIONS[EXECUTING]` target).
  VERIFIED DISCRIMINATING: **6 mismatches today**, enumerated in the table above, produced by a probe run
  at triage. Fails now; passes only when every instruction is accurate.

- CRITERION: Each of the four sites SHALL still name at least one `project_*` tool that **is**
  dispatchable in every phase that reads it.
  CHECK: the same node, asserting a non-empty intersection between the tokens found and
  `_PHASE_TOOLS[phase]`.
  VERIFIED DISCRIMINATING: fails today at the same four sites (each currently names exactly one tool, and
  that tool is the non-dispatchable one — so the intersection is empty).
  **Why this second criterion exists:** without it, the cheapest way to make the first one green is to
  **delete the tool name from the message**, which removes the misdirection but strands the agent with no
  next action. The pair forces an accurate instruction rather than a silent one.

## Regression guards

Pass today; must keep passing.

- GUARD: `uv run pytest tests/test_project_tools.py -q` — invariant: no test lost, newly skipped, or
  newly failing. Observed at triage: `34 passed in 1.69s`.
- GUARD (**enforces the design decision above**): the deliberate exclusions in `_PHASE_TOOLS` are
  preserved — `SPEC_REVIEW`, `PLAN_REVIEW` and `DONE` still exclude `project_next_task`, and `EXECUTING`
  still excludes `project_update_plan`. This is the guard that matters: **it blocks the degenerate fix of
  widening the gates until the mismatch disappears.** All four exclusions hold today (that is precisely
  why the six mismatches exist).
- GUARD: phase gating remains real — no `ProjectState` exposes the entire `TOOLS` registry. Blocks the
  other degenerate fix, making `_tools_for_phase` return everything.
- GUARD: `TRANSITIONS` (`skills/project/state.py`) is unchanged — the criterion's phase-reachability
  reasoning for `project_advance` depends on `EXECUTING → {DONE, PLANNING, BRAINSTORMING}`. Altering the
  state machine to dodge the `DONE` case would satisfy the criteria for the wrong reason.
- GUARD (invariant): full suite green. **UNRUN (needs a serial run)** — not verified here.

## Tier: needs-review

**Downgraded — C1/C2 check amended.** Filed as `auto-ok`; downgraded during execution on
2026-08-02 when amendment A1 (see `checks.md`) was approved and applied. An amended oracle was not
authored independently before implementation, so per `frozen-checks.md` it no longer supports an
autonomous merge, however green the checks are. The original derivation is preserved below, and
neither of its two triggers fired — the downgrade comes from the amendment alone, not from a
reassessment of the work's risk.

### Original derivation at intake (`auto-ok`)

Neither trigger fires.

**Trigger 1:** both criteria are concrete-example assertions over pure, in-process data — `_PHASE_TOOLS`,
`TRANSITIONS`, a prompt file, and three string literals. The oracle exists today
(`tests/test_project_tools.py`, 34 passing, with the `ctx`/`SimpleNamespace` fixture pattern at `:37`), the
checks were **run at triage and produced 6 concrete failures**, and neither is satisfiable without the
work — the first rules out inaccurate instructions, the second rules out deleting them. No human judgment
is involved: what counts as "dispatchable" is `_PHASE_TOOLS` membership, not taste. **Not
harness-dependent** — nothing reads `load_config()`, a live registry, the network, or the developer's
`data/` directory; the probe that produced the six failures used only imports and a tmp path.

**Trigger 2:** no risk-gated path — `src/decafclaw/skills/project/` application code plus one prompt
template. No auth/authorization, secrets, data migration or deletion, deploy/infra/CI config, or
dependency change.

The one open implementation choice (fix messages vs. widen gates) is **pinned by the design decision
above and enforced by a guard**, so it is not a withheld decision that changes which criteria apply.

## What we're NOT doing

- **Widening `_PHASE_TOOLS`** — see the design decision; a guard blocks it.
- **`SKILL.md:37`'s mention of `project_update_plan`** — always-loaded context is phase-independent, so it
  is not part of this invariant.
- **Making the check fully automatic via control-flow analysis.** The prompt-site → phase mapping is
  derived by reading the code and is encoded as a table in the test. A general analysis would be fragile
  and is not worth it for four sites; if a fifth is added, the test should be extended along with it.
- Re-litigating #525's eval flakiness. That issue is closed; this is the deterministic defect that was
  found underneath it.
