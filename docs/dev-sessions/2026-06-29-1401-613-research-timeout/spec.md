# /research timeout fix Spec

**Goal:** Let `/research` complete a real research session end-to-end on `vertex-gemini-flash`. Bump `tabstack_research`'s per-tool timeout to a defensive 600-second ceiling (3-5× typical 2-3-minute research duration; leaves headroom for the iteration depth tabstack can need on broad topics without preemptively cutting off real work) and lower `/research`'s parallel-query count from 3-5 to 2-3 so each call has its full budget without crowding.

**Source:** [Issue #613](https://github.com/lmorchard/decafclaw/issues/613). Surfaced during the #582 smoke ([PR #610](https://github.com/lmorchard/decafclaw/pull/610) — workflow primitive hang fix). With the #582 hang fixed, `/research` reaches `tabstack_research` cleanly, but all parallel calls hit the 180s `TOOL_TIMEOUT_SEC` default before iterative research finishes.

## Current state

Two coupled bottlenecks (see `research.md` for grounding):

1. **`tabstack_research` inherits the default 180s per-tool timeout.** Its definition at `src/decafclaw/skills/tabstack/tools.py:525-546` has no `timeout` key, so the per-tool wrapper at `src/decafclaw/tools/__init__.py:53-107` caps every call at 180s. The smoke evidence (`docs/dev-sessions/2026-06-25-1723-582-pipeline-hang/smoke.md`) shows tabstack making forward progress through 3 iterations of multi-query search + page analysis — the work is just deep, not stuck. All 3 parallel calls timed out at 180s while in their final "Writing report" step.

2. **`/research` plans 3-5 parallel tabstack queries.** Schema bounds at `src/decafclaw/workflow/workflows/research.py:43-44` (`minItems: 3`, `maxItems: 5`); reinforced by the system prompt (line 22-26) and user prompt (line 78-79) both saying "3-5 search queries." Since tabstack itself expands each query into ~7 internal searches per iteration, 3-5 parallel calls = 21-35+ internal searches. That's overkill — tabstack handles breadth internally, the parallel fan-out is for diversity, not coverage volume.

The two fix points compose: bumping the per-tool budget alone wouldn't help much if 5 parallel calls all run for the full budget, and reducing query count alone wouldn't help if each still hits 180s.

## Desired end state

**`tabstack_research`'s definition declares `"timeout": 600`** (10-minute defensive ceiling). Matches the existing pattern at `src/decafclaw/tools/delegate.py:546` etc. — per-tool override carried through to `_resolve_tool_timeout` (`src/decafclaw/tools/__init__.py:113-149`) without further wiring, because the activation path (`activate_skill_internal` at `src/decafclaw/tools/skill_tools.py:323-327`) extends `ctx.tools.extra_definitions` with the complete dict preserving all keys including `timeout`.

**`/research`'s parallel-query count drops from 3-5 to 2-3.** Three coordinated edits in `src/decafclaw/workflow/workflows/research.py`:
- `_PLAN_SCHEMA["properties"]["queries"]["minItems"]`: 3 → 2
- `_PLAN_SCHEMA["properties"]["queries"]["maxItems"]`: 5 → 3
- `_SYS_PLAN` (system prompt): "3-5 search queries" → "2-3 search queries"
- User prompt in `_research_plan_prompt`: "3-5 search queries" → "2-3 search queries"

**Acceptance smoke.** Live walk `/research kelp forest restoration` on `vertex-gemini-flash` from the worktree server. Expected end state in the post-run journal: outer parallel entry at seq `(3,)` with 2-3 NON-error result dicts (real tabstack markdown), pipeline summarize entries at seq `(4, i, 0)`, outer pipeline at seq `(4,)`, subagent entry at seq `(5,)` (or whichever seq corresponds), journal status `"done"`, client receives a final report dict.

## Design decisions

- **Decision:** `timeout: 600` (finite ceiling) for `tabstack_research`, not `None` (opt-out).
  - **Why:** Defensive bound. Tabstack's typical research duration is ~3 minutes (per the smoke's near-completion at 180s); 600s gives ~3-5× headroom. A finite ceiling caps the worst case — if tabstack ever has a runaway iteration bug, the workflow fails in 10 minutes, not indefinitely. Matches the user's preference for bounded over open-ended timeouts.
  - **Rejected:** `timeout: None` (opt-out like `delegate_task`) — cleaner pattern match but loses the worst-case bound. `timeout: 900` — only slightly more generous, no obvious tradeoff. Configurable env var — premature for a single-tool concern.

- **Decision:** Lower `/research`'s query count to 2-3 (not 1-2, not 4-5).
  - **Why:** 2-3 preserves the parallel-fan-out demo (still exercises `wf.parallel` with N>1) while cutting overlap with tabstack's internal multi-query search. Tabstack expands each query into ~7 internal searches per iteration, so 2-3 parallel calls produce ~14-21 internal searches — enough breadth without redundancy. The system prompt's "without overlap" guidance still applies; the schema's `minItems: 2` keeps at least one fan-out partner alongside.
  - **Rejected:** Drop to 1 query (kills the parallel demo); keep at 5 with longer timeout (still produces 35 internal searches and risks hitting even the 600s ceiling on broad topics).

- **Decision:** Update BOTH the schema and the prompts to match.
  - **Why:** The schema enforces the cap at the structured-output layer; the prompts steer the LLM's intent. Leaving prompts at "3-5" while clamping the schema to 2-3 means the LLM might try to bin 5 ideas into 3 slots and produce weaker queries. Consistency across schema + system prompt + user prompt is the cheap insurance against that.
  - **Rejected:** Schema-only (prompts misalign with output budget); prompt-only (LLM might still emit 5 and fail schema validation).

- **Decision:** Add a regression test in `tests/test_tool_timeout.py` for `tabstack_research`'s configured timeout.
  - **Why:** The existing test suite (per `research.md` §4) has `test_per_tool_long_override_survives` covering the override-survives case generically. Adding a tabstack-specific test pinned to 600s guards against accidental removal of the override during future tabstack refactors. Small marginal cost.
  - **Rejected:** Skip the test (existing generic coverage is "good enough" — but the failure mode is "someone deletes `timeout: 600` from the tabstack definition and the regression isn't caught for 3 weeks until someone tries `/research` again").

- **Decision:** Don't change `tabstack_research`'s docstring or argument shape.
  - **Why:** This is a config tweak, not a behavior change. The tool still does iterative research; only the per-tool clock changes.
  - **Rejected:** Document the timeout in the tool's docstring (already implicit in the `timeout` key; would clutter the docstring with implementation detail).

## Patterns to follow

- **`timeout` key placement:** Mirror the existing entries — `src/decafclaw/tools/delegate.py:546` (`"timeout": None,`), `src/decafclaw/tools/conversation_tools.py:107` (same), `src/decafclaw/skills/claude_code/tools.py:1016` (same). Add `"timeout": 600,` as a sibling key alongside `"name"`, `"description"`, `"parameters"` in the tabstack_research dict at `src/decafclaw/skills/tabstack/tools.py:525-546`.
- **Schema/prompt consistency:** mirror the `_PLAN_SCHEMA` + `_SYS_PLAN` + `_research_plan_prompt` triad at `src/decafclaw/workflow/workflows/research.py:37-49`, `22-26`, `73-80`. Touch all three with consistent "2-3" wording.
- **Regression test pattern:** mirror `tests/test_tool_timeout.py::test_per_tool_long_override_survives` (lines 93-103) — confirms the configured value wins over the global default. The new test stubs the tabstack registry entry and asserts `_resolve_tool_timeout(ctx, "tabstack_research") == 600`.
- **Existing `/research` test compatibility:** the unit tests in `tests/test_workflow_research.py` set up mock LLM responses. If any test's mock `queries` list has more than 3 items, it would now fail schema validation. Audit and adjust during execute. The smoke evidence used 3 queries (within the new bounds), so the happy-path test is probably already compatible.

## What we're NOT doing

- **Returning partial results from `tabstack_research` on timeout (option 4 in the issue body).** Out of scope — affects the skill's contract for all callers, not just `/research`. Worth filing as a follow-up if the 600s ceiling proves insufficient.
- **Adding a lighter search tool for the parallel fan-out (option 3 in the issue body).** Bigger design change; reshapes what `/research` demos. Not needed if 600s + 2-3 queries lets the workflow complete.
- **Touching the global `TOOL_TIMEOUT_SEC` default (180s).** This is a per-tool override; other tools' 180s default stays.
- **Changing `/research`'s pipeline or subagent stages.** Only the plan stage's query count changes. The downstream extract/summarize/synthesize stages remain identical.
- **Updating evals.** The `/research` workflow isn't in the `evals/` suite; the unit tests + live smoke are the verification surface.
- **Adding a runtime `tool_timeout_sec` env var override per tool.** Configurable env per tool is premature (rejected above).

## Open questions

- **Q: Does `tests/test_workflow_research.py`'s happy-path test use more than 3 queries in its mock LLM response?**
  - **Default:** Check during execute; adjust the mock to ≤3 if needed. The smoke evidence used 3 queries and the existing test patterns predate `/research`'s rollout, so the most likely state is "fine without changes."

- **Q: Is there a tabstack-specific `mode` arg (e.g. `mode="quick"` or `mode="light"`) that would naturally fit the parallel fan-out better than the default?**
  - **Default:** Out of scope for this session — option 3 territory. Note in research.md (already done implicitly via the §2 mention of `mode: str = "balanced"`); revisit if 600s proves insufficient or if `/research` needs a deeper rework.

- **Q: Should the 600s ceiling be applied even to non-`/research` uses of `tabstack_research` (e.g., a user manually calling it via the agent loop)?**
  - **Default:** Yes — the `timeout` key is per-tool-definition, not per-caller. The agent loop's `tabstack_research` calls get the same 600s budget. That's the right semantics: the tool's work doesn't change based on who called it. If a non-`/research` caller has a different timeout need, that's a follow-up.
