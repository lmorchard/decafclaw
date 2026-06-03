# Workflow Tool-Result Framing — Cheap Experiment

**Issue:** [#255](https://github.com/lmorchard/decafclaw/issues/255)
**Branch:** `feat/255-workflow-engine` (PR #557)
**Parent spec:** [`../2026-05-29-1729-workflow-engine-phase-turn-model/spec.md`](../2026-05-29-1729-workflow-engine-phase-turn-model/spec.md) — phase-turn rework
**Date:** 2026-05-29

## Why this exists

The phase-turn rework spec is sizable (new `TurnKind`, manager self-enqueue,
new composer mode, phase-internal nudge loop, `parent_conv_id` field). Before
committing to that architectural reach, run a cheap experiment to see if the
underlying behavior gap can be closed with a much smaller change.

The live smoke that triggered the rework discussion (conversation
`web-lmorchard-1b7180ab`) showed the LLM calling `workflow_start`, receiving
the tool result `"Started workflow 'research_brief'. Current phase: draft.
Status: running. Use phase_advance to move forward."`, narrating "Okay, I've
started the workflow. The first phase is draft." in iteration 2, and ending
the turn. The engine's state had already advanced to `draft` (via the gather
subagent completing). What the LLM was missing was a strong cue that **it
should now act as the draft-phase agent** — which phase body to follow,
which tools, what target to advance to next, and an explicit "don't stop"
directive.

The current tool result is weak. The cheap experiment is to make it loud.

## Hypothesis

Replacing the bland `"Started workflow ..."` / `"Advanced to phase ..."`
text with a strongly-framed handoff that includes:

1. **Phase identity** — "YOU ARE NOW WORKING IN PHASE: 'draft'"
2. **Phase body verbatim** — the LLM-author's instructions for what to do
3. **Tools available** — the phase's tool whitelist, listed explicitly
4. **Next-phase options** — each target with its `when:` annotation
5. **Imperative directive** — "DO NOT STOP. Begin executing the phase task
   immediately. End the turn only when the phase task is complete (call
   `phase_advance`) or you need user input."

…will get the LLM to drive workflow phases forward in the existing
conv-scoped architecture, without a structural rework.

## What changes

In `src/decafclaw/tools/workflow_tools.py`:

- Add `_render_phase_handoff(state, wf, transition_note)` helper that
  produces the strongly-framed tool result text.
- `tool_workflow_start` calls `_render_phase_handoff` for its return value
  (instead of the bare "Started workflow ..." string), once the engine has
  settled the workflow into an inline phase (subagent dispatch already
  completed if applicable).
- `tool_phase_advance` calls `_render_phase_handoff` for its return value
  (instead of the bare "Advanced to phase ..." string), when the new phase
  is inline (not a gate, not done).
- Terminal-phase, gate, and error paths keep their current short results.
- `end_turn=False` stays on `tool_phase_advance` — the goal is for the LLM
  to keep iterating in the same turn with the new framing.

No other files change. No `Context`, `TurnRunner`, `ContextComposer`, or
`ConversationManager` changes. No new turn kinds, no `parent_conv_id` field.

## Success criteria

Run a real-LLM smoke against the demo `research_brief` workflow. Success
means the LLM:

1. After `workflow_start`, performs draft-phase work in iteration 2 — reads
   `artifacts/gather/sources.md`, writes `artifacts/draft/brief.md`, and
   calls `phase_advance(review)`.
2. After landing in `review`, presents the draft and calls
   `phase_advance(publish)` — triggering the gate.
3. After gate approval, performs publish-phase work and reaches `done`.

If steps 1–3 happen reliably (let's say 2 out of 3 fresh runs), the cheap
experiment succeeded and the phase-turn architectural rework is
unnecessary for v1. We can ship PR #557 with this fix and revisit the
phase-turn model if real-world workflows expose new failure modes.

If step 1 fails consistently (LLM still stalls after `workflow_start`),
the cheap experiment failed. We then have evidence that the structural
change (phase-as-system-prompt + engine-driven turn scheduling) is
required. We move to the phase-turn rework with confidence the small
fix wasn't enough.

## What doesn't change (carried over)

- Conv-scoped state at `conversations/{conv_id}/workflow.json` + `artifacts/`
- `required-skills:` auto-activation on `workflow_start`
- Synchronous subagent dispatch via `engine.dispatch_subagent_if_needed`
- Per-phase tool catalog hard-gate via `ctx.tools.allowed`
- Dynamic `phase_advance` schema + `priority: critical`
- Edge gates via `EndTurnConfirm`
- Phase prompt format + loader + registry + types
- 79 existing workflow tests (text assertions are loose enough to survive)

## Open questions

1. **Should the phase body be rendered verbatim, or summarized?** Verbatim
   bloats the tool result. Summary loses signal. Lean: verbatim — phases
   are typically short (50-200 lines), and the body is exactly the
   information the LLM needs to do the phase work.

2. **Should we also inject a user-role synthetic message after the tool
   result?** Per memory `feedback_user_role_for_midturn_directives.md`,
   user-role + imperative beats tool-role for mid-turn directives. If a
   strong tool result alone fails the smoke, this is the escalation: have
   `tool_workflow_start` / `tool_phase_advance` somehow inject a
   user-role nudge. (Mechanism TBD — tools don't normally append
   non-tool-role messages.)

3. **What about the very first phase if it's inline (no subagent
   dispatch)?** `workflow_start` returns immediately with state in the
   initial inline phase. The new handoff text covers it — no special case
   needed.

4. **What if the subagent dispatch advanced the workflow to `done` or
   `error`?** Terminal/error paths take the short-result branch.

## Smoke approach (TBD)

Decide between:
- **Eval-framework YAML case** (durable, ~6-10 min wall time, real LLM).
- **Standalone Python smoke script** (faster iteration, no archive).
- **Web UI manual via Playwright MCP** (requires stopping `make dev`).

Lean: eval-framework, with the smoke YAML committed alongside the change.

## References

- [Phase-turn spec](../2026-05-29-1729-workflow-engine-phase-turn-model/spec.md)
- Conv-scoped rework spec: [`../2026-05-21-1732-workflow-engine-rework/spec.md`](../2026-05-21-1732-workflow-engine-rework/spec.md)
- Failing smoke conversation: `data/decafclaw/workspace/conversations/web-lmorchard-1b7180ab.jsonl`
