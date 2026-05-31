# Code-Driven Engine — Spike (fourth iteration)

**Issue:** [#255](https://github.com/lmorchard/decafclaw/issues/255)
**Branch:** `feat/255-workflow-engine` (PR #557, **NOT** merged)
**Status:** Spike pending. Spec + plan not yet written — the spike's job is to validate the mechanism *before* writing more design docs.

## Why a fourth iteration

PR #557 comment dated 2026-05-31 (Sophie's analysis):
<https://github.com/lmorchard/decafclaw/pull/557#issuecomment-4587813874>

TL;DR of Sophie's diagnosis: every iteration so far (cross-conv → conv-scoped → phase-turn) kept the LLM responsible for emitting `phase_advance` (the crank that moves the state machine). Every smoke stalled because the LLM doesn't reliably emit it. The fix isn't to nudge harder — it's to **stop asking the LLM to drive transitions at all.** Engine owns control flow; LLM is called as a structured-output worker for each step.

Three drivers exist for multi-phase transitions:

| Driver | Example in tree | Works? |
|---|---|---|
| Human | `project` skill | ✅ |
| Code | Sophie research orchestrator | ✅ |
| LLM | This PR's engine (all three iterations) | ❌ |

The two working examples are *in our own tree*. We've been building the only variant that doesn't work.

## Build order (Sophie's recommendation)

1. **Prove the kernel before generalizing it.** Build `research_brief` as a plain imperative async function — `gather()`, `draft()`, `review()`, `publish()` — where:
   - The engine calls the LLM per step with **structured output** (no tool calls the model may forget to emit).
   - Code captures each phase's result as a return value, not a side effect.
   - Code sequences phase transitions in an explicit loop.
   - The only LLM decision is `draft`'s one branch (review vs. back-to-gather), via a structured routing call (`{target: Enum[next_phase_ids], reason: str}`).
   - No declarative loader, no graph traversal, no `phase_advance` tool, no dynamic schemas in the path.

2. Run it against `vertex-gemini-flash` in the conversation timeline, interruptible by a user message. Walk gather → draft → review → publish end-to-end. **That bar is what no iteration has cleared.**

3. *Then* lift the existing declarative format (`kind: workflow`, `phases/*.md`, `next-phases:`, `when:`, gates) back on top of the proven kernel. Loader / types / registry / conv_state are sound and worth keeping.

## Sophie reference

Source code: `/Users/lorchard/devel/tabs-project/sophie/packages/core/src/`

Particularly relevant patterns:

- **Plain `while` loop as the orchestrator** — LLM is never in the control-flow path.
- **Schema-constrained structured generation for every LLM call** — planning, analysis, gap-eval, outline, judgment. No "did it remember to call the tool" failure mode.
- **Gap-evaluation pattern** — model returns `shouldContinueResearch: boolean` plus next queries; the *orchestrator* reads that field and decides whether to loop. If model says "continue" but supplies no queries, code **overrides it to stop.** Model as sensor, code as driver.
- **Phase output as return value** — captured directly, not via tool side-effect.
- **Validate-and-fallback at boundaries** — failure-rate thresholds, retries with tighter instructions, graceful degradation. Never an open-ended nudge loop.

Browse the orchestrator file first, then the per-phase function files. The spike should mirror this shape: imperative phases, structured output, captured results.

## Salvage from PR #557 (Sophie's analysis)

**Keep as-is** (sound, even exemplary):
- Authoring format: `kind: workflow`, `phases/*.md`, frontmatter.
- `loader.py`, `types.py`, `registry.py`.
- `conv_state.py` — conv-scoped persistence + `conv_lock`.
- Engine transition mechanics: `advance()`, `_apply_transition`, `_enter_gate` / `finalize_gate_response`. What changes is *who calls them*, not the functions.
- The existing `dispatch_and_finalize_subagent` → `verify_subagent_outputs` → auto-advance pattern for subagent phases. **This is the model for the redesign** — generalize it to all phases.
- `required-skills:`, per-phase tool gating, edge gates via `EndTurnConfirm`, the phase-turn model's turn-scheduling instinct (`parent_conv_id`, `WORKFLOW_PHASE` compose mode).

**Change (narrow):**
- Inline phases stop advancing via LLM-emitted `phase_advance`. Engine auto-advances single-edge phases (subagent pattern generalized); a structured routing call decides multi-edge branches.
- Phase output moves from "model remembers `workflow_artifact_write`" to "engine captures the turn's result as the artifact."

**Drop:**
- The `max_phase_continuations` nudge loop from the phase-turn spec. Patching the crank we're removing. **Don't build it.**

## Where the prior iterations stand

- [`../2026-05-19-2121-workflow-engine/`](../2026-05-19-2121-workflow-engine/) — cross-conv (first iteration). Historical.
- [`../2026-05-21-1732-workflow-engine-rework/`](../2026-05-21-1732-workflow-engine-rework/) — conv-scoped (second iteration). Code on disk, 79 tests pass. Engine plumbing solid; LLM stalls.
- [`../2026-05-29-1729-workflow-engine-phase-turn-model/`](../2026-05-29-1729-workflow-engine-phase-turn-model/) — phase-turn rework (third iteration). Spec + 1945-line plan. **Plan superseded** — has a banner pointing here. Still useful as documentation of where the design was reaching.
- [`../2026-05-29-1803-workflow-tool-result-framing/`](../2026-05-29-1803-workflow-tool-result-framing/) — cheap-experiment notes (four real-LLM smokes documenting the narrate-stall taxonomy that motivated Sophie's reframing).

PR #557 commits stay as-is — the cheap-experiment commits (`ab47755`, `50a528b`) and the phase-turn artifacts are kept on the branch as history of what didn't work. We don't squash or revert.

## Next-session checklist

When starting the fresh session for this work:

1. Read this `notes.md` first, then the PR comment, then skim Sophie's `packages/core/src/`.
2. Write the spike at `scripts/spike_research_brief.py` (or similar one-off location — it's throwaway).
3. The spike must:
   - Use the existing `ConversationManager.enqueue_turn` so phases live in the conversation timeline.
   - Call the LLM via the existing client with structured-output schemas (check `llm/` for the API).
   - Implement `research_brief` as four imperative phase functions: `gather()`, `draft()`, `review()`, `publish()`.
   - Sequence phases in code; the only LLM decision is `draft`'s `{target: "review" | "gather", reason}` routing.
   - Capture each phase's product as a return value; write artifacts via the engine, not via a tool the LLM emits.
   - Use `vertex-gemini-flash` (the model that has stalled every prior iteration).
   - Be interruptible by a user message.
4. Run it against a real conversation. Assert: workflow walks end-to-end, all four phases visible in the timeline.
5. **Do not generalize prematurely.** Get this one workflow working reliably before touching the declarative engine.

## Open questions for the next session

- Does the existing LLM client expose structured-output generation? (`llm/providers/*.py` — check.)
- How does the conversation manager surface a "phase turn" that the user can interrupt? (`enqueue_turn(kind=...)` — check whether a synthetic kind is needed or whether we can piggyback on USER/WAKE.)
- How are turn boundaries surfaced to the web UI so each phase reads as a separate event? (`web/websocket.py` — check message types.)
- What schema should the routing call use, exactly? `Enum` from `next_phases` IDs + free-form `reason` string is the obvious shape — verify the LLM client supports JSON-Schema enums in structured output.

These questions don't block the spike — they just shape its implementation. Answer them by reading code, not by speculation.
