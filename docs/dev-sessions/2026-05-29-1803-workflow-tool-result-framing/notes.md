# Cheap Experiment — Findings

**Date:** 2026-05-29
**Branch:** `feat/255-workflow-engine` (PR #557)
**Outcome:** Prompt-only fix insufficient. Escalating to phase-turn rework.

## What we tried

Three rounds of code change, four real-LLM smoke runs against the bundled
`research_brief` workflow via the web UI (Playwright MCP), model
`vertex-gemini-flash` (default).

### Round 1 — strong tool-result framing on workflow_start / phase_advance

Replaced the bland `"Started workflow X. Use phase_advance to move forward."`
return with a long structured handoff containing:

- Active phase identity
- Phase body verbatim
- Tool whitelist
- Next-phase options + `when:` annotations
- Imperative "do not stop, do not narrate" directive

Committed as `ab47755`.

### Round 2 — strong framing on subagent dispatch + topic-passing

Modified `subagent.py:_run_child` to wrap the child's user-role kickoff
prompt with similar strong framing, plus an injection of the parent's
most-recent user message (so the gather subagent learns the actual
research topic — Bug 1 of the prior smoke). Uncommitted (decision pending).

### Round 3 — output path notation fix

The first version of the subagent framing said
`REQUIRED OUTPUTS: artifacts/gather/sources.md`. The LLM passed
`"artifacts/gather/sources.md"` verbatim to `workflow_artifact_write`,
which prepends `artifacts/`, producing `artifacts/artifacts/gather/sources.md`.
Engine couldn't find the expected path → error. Fixed framing to use the
tool's actual relative-path semantics.

## Smoke transcript summary

| Smoke | Subagent behavior | Engine outcome |
|---|---|---|
| #1 (pre-fix baseline) | Narrated "I will research" / asked for topic → no tool call | ERROR — missing output |
| #2 (env-only, tabstack discovery bug) | Tabstack tools missing (filed [#566](https://github.com/lmorchard/decafclaw/issues/566)) | ERROR — missing output |
| #3 (subagent framing v1) | Wrote `sources.md` ✓ but to wrong path (`artifacts/artifacts/...`) | ERROR — verify path mismatch |
| #4 (path-notation fix) | Called `tabstack_research` ✓, then **inlined the markdown content directly in its text response and ended turn — no `workflow_artifact_write` call** | ERROR — missing output |

In **none** of the four runs did we reach the parent-side strong-handoff
code path on the success branch — the gather subagent always errored
before `RunStatus.RUNNING` in an inline phase.

## Failure-mode taxonomy

We observed three distinct narrate/inline patterns that all defeat
prompt-only fixes:

1. **Parent post-tool-call stall.** Calls `workflow_start`, receives
   a tool result that mentions "use phase_advance," responds with a
   1-line acknowledgement ("Okay, started"), ends turn. This was the
   original failure mode from the spec era. Theoretically addressed
   by the strong-handoff text, but we never got to test it because
   gather subagent always failed first.

2. **Subagent narrate-only stall.** Receives the phase prompt as a
   user-role message, responds with "I understand my role, I will
   research" — ends turn with zero tool calls. The strong-framing +
   topic-injection fix addressed THIS variant (smoke #4 confirms the
   subagent now proceeds to `tabstack_research`).

3. **Subagent inline-content stall.** Calls the research tool, gets
   the data back, then **writes the final structured content directly
   into its text response** instead of routing through
   `workflow_artifact_write`. Technically not "narrating a plan" —
   it's just producing the final answer in the wrong channel.
   Our `"do not narrate"` directive missed this case.

Patterns 2 and 3 are both stochastic outcomes from the same root
issue: Gemini Flash (and similar models) treats "produce output X"
as "write text in your response" by default. Tool calls are a
secondary affordance the model has to be explicitly bullied into
using. Each prompt iteration nails one variant and the next round
surfaces another.

## What the cheap experiment did prove

Useful findings even though we didn't reach the parent-side handoff
test:

- **Subagent dispatch needs framing too**, not just parent
  transitions. The phase-turn spec already calls for phase-as-system-
  prompt at every WORKFLOW_PHASE turn, including child agents. This
  is now confirmed by evidence rather than just architectural
  intuition.
- **Topic-passing is a real, separate problem** ([Bug 1 of the original
  smoke](../2026-05-29-1729-workflow-engine-phase-turn-model/spec.md#open-questions-for-review)).
  Pulling the parent's most-recent user message is a working
  workaround. The phase-turn spec's `params:` arg on `workflow_start`
  (open question #3) is the cleaner long-term path.
- **Prompt engineering on output-format expectations is fragile.**
  Even when the LLM intends to follow tool-use conventions, a small
  context cue ("here's structured content") can flip it back into
  text-response mode. A structural fix (phase prompt as **system
  prompt** rather than user-role message + tool result hint) gives
  the LLM a much stronger contextual signal that it's in
  worker-not-narrator mode.
- **The `artifacts/` path semantics are a sharp edge.** The
  `workflow_artifact_write` tool prepends `artifacts/`; documenting
  output paths needs to use the relative-to-artifacts notation. The
  phase-turn rework should be careful here too — and the demo
  workflow's gather.md prompt says
  `"write sources.md with a top-level heading"` without specifying the
  relative path explicitly, which leaves the LLM to guess.

## What we did NOT prove

- **The strong-handoff text fix on `workflow_start` works.** We
  never reached the success branch where that text would matter.
  Could still be the right fix for parent-side transitions in
  isolation, but we can't tell from this evidence.
- **The cheap experiment is generally invalid.** It clearly helps
  for some failure modes (smoke #4 showed clear progression vs
  smoke #1). But the bottleneck for `research_brief` walks deeper
  than the parent-side handoff, and chasing each new failure with
  prompts is yielding diminishing returns.

## Pre-existing bugs discovered along the way

- [#566](https://github.com/lmorchard/decafclaw/issues/566) — Skill
  discovery's `requires.env` check ignores `config.skills` resolution.
  Caused tabstack tools to be silently unavailable when the API key
  was in config.json instead of env. Out of scope for #557.
- **`workflow_artifact_write` path doubling foot-gun.** No tool error
  when LLM passes `artifacts/foo.md` — it just writes to
  `artifacts/artifacts/foo.md`. Worth a small fix (strip leading
  `artifacts/` from `relative_path`, or document it more loudly).
  Not filed yet.

## Code state

- **Committed (ab47755):** `_render_phase_handoff` helper in
  `workflow_tools.py` + strong-handoff text on `tool_workflow_start`
  and `tool_phase_advance` returns. Unit tests added.
- **Uncommitted:** `subagent.py` strong-framing kickoff prompt +
  topic-passing via `_latest_parent_user_message`. Decision pending.

Both pieces have transferable value to the phase-turn rework:

- `_render_phase_handoff` becomes the `WORKFLOW_PHASE` mode's
  system-prompt builder.
- `_latest_parent_user_message` (or the cleaner `params:` arg
  approach) survives as the topic-passing mechanism for the
  CHILD_AGENT turn enqueue.

## Next steps

1. Decide what to do with the uncommitted `subagent.py` change
   (commit / revert / stash).
2. Review the phase-turn spec's 7 open questions and settle them
   ([../2026-05-29-1729-workflow-engine-phase-turn-model/notes.md](../2026-05-29-1729-workflow-engine-phase-turn-model/notes.md)).
3. Write the implementation plan.
4. Execute via subagent-driven-development.
