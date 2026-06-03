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

## Spike retro (2026-05-31)

**The spike walked end-to-end first try.** That bar — `gather → draft → review → publish` against `vertex-gemini-flash`, no stall — is what no iteration had cleared. This one cleared it in ~18s wall clock, five Vertex API calls (one per phase + a routing call after `draft`), zero retries on any structured-output call.

### What it does

Bundled skill at `src/decafclaw/skills/spike_research_brief/` (SKILL.md + tools.py, ~409 lines together):

- `/spike_brief <topic>` is a user-invokable command. Body is a one-tool directive ("call `spike_brief_run(topic=...)` immediately, do not narrate"). Gemini Flash complies — the LLM kickoff is one constrained decision with one tool in scope.
- `spike_brief_run` is a single tool whose implementation IS the orchestrator. Inside:
  - `_call_structured(...)` — helper that exposes ONE tool with the phase's output schema and a "you MUST call this" description. Parses the tool-call args. Retries once with a stricter nudge if the model narrates. Provider-agnostic — no vertex.py changes.
  - 4 phase functions: `_gather`, `_draft`, `_draft_route`, `_review`. Each is a single `_call_structured` call.
  - `_publish` is pure Python — writes a file to `workspace/spike_briefs/<slug>.md`. No LLM call.
  - `tool_spike_brief_run` sequences phases with a bounded draft → back-to-gather loop (`MAX_GATHER_REVISITS = 1`), publishes `tool_status` events per phase, threads structured results forward as return values, returns `ToolResult(text=..., end_turn=True)` with the full transcript and rendered brief.

### Smoke

`make dev` in the worktree → web UI → new conv → `/spike_brief tide pools along the oregon coast` → Playwright drove the chat. Sequence in logs:

```
12:46:02  starting orchestrator (topic=…, model=vertex-gemini-flash)
12:46:02  [phase: gather] researching sources...
12:46:09  [phase: draft] writing brief (attempt 1)...    ← 7s
12:46:16  [phase: draft → route] choosing next step...   ← 7s
12:46:18  [phase: review] critiquing draft...            ← 2s (route → review)
12:46:20  [phase: publish] writing to workspace...       ← 2s
12:46:20  complete — published to spike_briefs/tide-pools-along-the-oregon-coast.md
```

Final artifact: `data/decafclaw/workspace/spike_briefs/tide-pools-along-the-oregon-coast.md` — a real 400-word brief, structured framing + 2 themed sections + open questions + review summary. Coherent, plausible. Plenty good enough for "the mechanism works."

Web UI rendered the full transcript inline (tool-result block) plus a final assistant message with the brief markdown-formatted. The expected `end_turn=True` extra LLM hop fired (one more Vertex call) — it produced a wrap-up assistant message echoing the brief. Six LLM calls total, not five.

### What this confirmed (vs. the prior three iterations)

- **Forced-tool structured output is reliable on Flash.** Every phase's `_call_structured` got a clean tool call with valid JSON on first attempt. The "you MUST call this" framing on a single tool with one schema is structurally equivalent to Sophie's zod-schema mode — the model has nowhere else to go.
- **Code-driven sequencing eliminated every stall mode the prior iterations hit.** No `phase_advance` to forget. No `workflow_artifact_write` to mis-route through prose. The orchestrator threads state forward as return values; there is no "did the model remember to fire X" failure surface.
- **The routing decision at `draft` (review vs. gather) worked as a structured call.** Model returned `{target: "review", reason: "..."}`. Code consumed it deterministically.
- **The bounded `MAX_GATHER_REVISITS` loop didn't trigger this run** — Flash routed straight to review, which is the realistic case for a thin topic. The branch is exercised in code but not in this smoke; a topic that forces a gap-driven revisit would prove that path.

### Residual / nice-to-haves (not blockers)

- **Interruptibility was implemented (via `_check_cancel`) but not smoked.** The orchestrator polls `ctx.cancelled` between phases. The default `agent.turn_on_new_message` is `"queue"`, not `"cancel"` — so a new user message *queues* instead of cancelling. The mechanism is correct, but exercising it requires either flipping that config or sending an explicit cancel.
- **The `end_turn=True` final-no-tools LLM call is a wart.** It echoes the brief once more (15,285 in / 14 out tokens this run). For a long-running orchestrator that already returned a fully-formed result, an "end the turn without one more LLM hop" signal would be cleaner — see CLAUDE.md note on `end_turn` semantics. Worth raising as a follow-up; not blocking.
- **The spike's `_gather` is "hallucinate plausible sources from training data," not real research.** Faithful enough for proving the orchestrator pattern. A real engine integration would swap in `tabstack_research` via direct call (NOT via LLM tool call) inside the gather phase.
- **No review gate** in the spike (skipped to keep plumbing minimal). The existing engine's `EndTurnConfirm` pattern is the right home for that — would slot in cleanly after `_review`.

### What to fold back into PR #557

The whole point of this throwaway was to validate the mechanism before generalizing. The Sophie-style pieces to lift back into the existing engine (per the salvage triage above):

1. **Generalize the `dispatch_and_finalize_subagent → verify_outputs → auto-advance` pattern from `engine.py` to all single-edge phases.** Inline phases stop emitting `phase_advance`; the engine auto-advances after each phase function returns.
2. **Replace multi-edge `phase_advance` with a structured routing call** — `_call_structured(target: Enum[<next_phase_ids>], reason: str)`, modeled on `_draft_route` in this spike. The existing `EdgeDef.when` annotations are already the perfect enum-option descriptions.
3. **Phase output as return value, captured by the engine** — not "model remembers `workflow_artifact_write`." Inline phases return structured output; the engine writes the artifact file from the returned object.
4. **Don't build the `max_phase_continuations` nudge loop** from the phase-turn spec. The spike confirms the crank is removable — no patch needed.

The declarative authoring layer (loader/types/registry/conv_state) doesn't have to change.

### Files added on this branch

- `src/decafclaw/skills/spike_research_brief/SKILL.md`
- `src/decafclaw/skills/spike_research_brief/tools.py`

Both are throwaway. When the engine is reworked, drop the skill (or keep it as a worked-example test fixture for the new pattern).
