# Code-Driven Workflow Engine Rework Spec

> **⚠️ SHELVED 2026-06-01.** Mid-brainstorm, Les surfaced a question (what about phases that need multiple LLM calls, or user interaction, or step-level branching?) that exposed a deeper reframe: phases at "single LLM call" granularity are still too coarse. The right operationalization of the [code-drives-process thesis](../../../../../.claude/projects/-Users-lorchard-devel-decafclaw/memory/project_workflow_design_thesis.md) is to decompose phases into typed **step primitives** — `llm_call`, `route`, `user_input`, `tool_call`, `python`, etc. — so the discipline lives at the abstraction level, not at author convention. This spec's "phase as single structured-output call" model becomes the *trivial case* of a one-step phase in the step-based design.
>
> **Current direction is captured in [`../2026-06-01-1055-workflow-step-primitive-design/notes.md`](../2026-06-01-1055-workflow-step-primitive-design/notes.md)**, which has the design questions queued for the next brainstorm.
>
> The research.md in this directory ([research.md](research.md)) is still factually accurate about PR #557's current engine shape and is reusable when the step-based brainstorm needs to decide what survives vs. rewrites in the existing tree.

---

**Goal:** Convert decafclaw's workflow engine (PR #557) from LLM-driven phase transitions to code-driven, validated by the [2026-05-31 spike](../2026-05-31-1223-code-driven-engine-spike/notes.md), so that `vertex-gemini-flash` can walk a declared workflow end-to-end without the narrate-stall failure mode that has blocked three prior iterations.

**Source:** [Issue #255](https://github.com/lmorchard/decafclaw/issues/255) + [PR #557 comment 4587813874](https://github.com/lmorchard/decafclaw/pull/557#issuecomment-4587813874) (Sophie analysis) + spike retro.

## Current state

The engine is structurally correct but its crank is wrong: the LLM drives every phase transition by emitting a `phase_advance` tool call ([workflow_tools.py:411](../../../src/decafclaw/workflow/workflow_tools.py)). Every smoke against Flash stalled because the model narrated instead of firing the tool. See [research.md](research.md) for current dispatch flow, frontmatter contract, gate firing, and test footprint.

Already-correct primitives (keep as-is): `loader.py`, `types.py`, `registry.py`, `conv_state.py`, the `advance()` / `_apply_transition()` / `_enter_gate()` / `finalize_gate_response()` engine kernel ([research.md §1, §4](research.md)), and the `dispatch_and_finalize_subagent → verify_subagent_outputs → auto-advance` pattern for subagent phases ([engine.py:187-294](../../../src/decafclaw/workflow/engine.py)). What changes is *who calls them* — code, not the LLM.

The spike at `src/decafclaw/skills/spike_research_brief/tools.py` proves the target pattern: forced-tool structured-output calls per phase, code sequences phases, the only LLM "decision" is a separate structured routing call at multi-edge phases. Five Vertex calls, no retries, no nudges.

## Desired end state

- **Inline phases run as engine-driven, single-shot LLM calls.** The engine constructs a forced-tool schema from the phase's `output-schema` frontmatter (defaulting to `{body: string}`), calls the LLM via `call_llm`, parses the tool args, and threads the result forward as a return value. No agent loop, no tool iteration inside a phase.
- **Single outgoing edge → engine auto-advances.** Same pattern that already exists for subagent phases ([engine.py:261-267](../../../src/decafclaw/workflow/engine.py)), generalized to inline.
- **Multi outgoing edge → engine makes a structured routing call.** Schema is `{target: enum[next_phase_ids], reason: string}`. The enum's per-option descriptions are auto-derived from each edge's `when:` annotation.
- **Gated edge → engine fires the gate** ([engine.py:92-109](../../../src/decafclaw/workflow/engine.py)) as it does today, with the prior phase's structured output rendered for the gate message.
- **`phase_advance` tool is deleted.** No escape hatch. `workflow_abort` covers bail-out.
- **Phase output is captured by the engine.** Inline phases produce a structured object; the engine writes `artifacts/<phase_id>/output.json` and (if the schema has a `body` field) `artifacts/<phase_id>/body.md`. Subagent phases continue to write artifacts via `workflow_artifact_write` (unchanged).
- **The migrated `research_brief` workflow_demo walks Flash end-to-end** via the new engine (same four phases: gather subagent + draft multi-edge + review gate + publish terminal). The `spike_research_brief` skill is deleted once this demo passes the same smoke.
- **Workflow runs entirely within a single user turn,** suspending at gates exactly as today (button callback resumes via `finalize_gate_response`). The orchestrator publishes `tool_status` events per phase so the conversation timeline shows live progress.

## Design decisions

- **Decision:** Inline phases declare `output-schema` in frontmatter; default to `{body: string}` when omitted.
  - **Why:** Every LLM call is structured (no narrate-stall), but authors don't have to write a schema for every prose-y phase. Forces deliberation only when downstream code consumes specific fields.
  - **Rejected:** Required schema on every phase (boilerplate for prose). Sidecar JSON file (two-file dance). Python-only schemas (abandons the declarative authoring model the loader was built for).

- **Decision:** Migrate `workflow_demo/research_brief` to the new engine; delete `spike_research_brief` after the migrated demo passes end-to-end smoke.
  - **Why:** The demo IS how authors learn the format — it has to use the engine. Migrating forces the engine to cover the realistic shape (subagent + multi-edge + gate + terminal).
  - **Rejected:** Side-by-side old + spike (old will rot since `phase_advance` is being removed). Rename spike to demo (spike doesn't use the engine — porting it is the same work).

- **Decision:** Delete `phase_advance` tool, `build_phase_advance_definition()`, and the dynamic-enum injection ([workflow_tools.py:72-123, 411](../../../src/decafclaw/workflow/workflow_tools.py)). Code drives all transitions.
  - **Why:** No escape hatch means no LLM regression path. Subagents already don't use it. The whole point of the rework.
  - **Rejected:** Hidden escape hatch (preserves the unreliability we're removing). Opt-in for subagents (no real use case).

- **Decision:** Per-area test rewrite. Delete most of `test_workflow_tools.py` (617 lines, exercises `phase_advance` mechanics). Rewrite `test_workflow_engine.py` (440 lines) TDD-first to express the new contract. Keep `test_workflow_loader.py`, `test_workflow_conv_state.py`, `test_workflow_skill_loader.py`, `test_workflow_context.py`, `test_workflow_types.py` mostly intact, with small additions for `output-schema` frontmatter parsing + the new types.
  - **Why:** The change surface is tightly bounded — loader/state/types are unaffected; engine + tools change fundamentally.
  - **Rejected:** Delete all (loses deliberate edge-case coverage in unaffected files). Keep all passing (impossible — old tests express the LLM-driven contract).

- **Decision:** Engine exposes a single entry point `run_workflow(ctx, state)` that runs until completion, a gate, an error, or `ctx.cancelled` is set. Re-entered after gate approval from `finalize_gate_response`.
  - **Why:** Matches the spike's mental model and Sophie's orchestrator shape. Gates are the natural suspension points — the existing button-callback flow already re-enters engine code synchronously.
  - **Rejected:** Per-phase `step()` driven by an external loop in the tool layer (re-creates the LLM-driven crank surface).

- **Decision:** Lift the spike's `_call_structured` helper into a new `src/decafclaw/workflow/llm.py` module. Single forced tool, "you MUST call this" framing, one retry with a stricter nudge on narrate-stall.
  - **Why:** Provider-agnostic. No changes to `vertex.py`. The spike already proved this is reliable on Flash.
  - **Rejected:** Extend the LLM provider with native `responseSchema` plumbing (premature generalization; the forced-tool pattern is sufficient).

## Patterns to follow

- **Spike orchestrator structure** at [`src/decafclaw/skills/spike_research_brief/tools.py`](../../../src/decafclaw/skills/spike_research_brief/tools.py) (current file, will be deleted): `_call_structured` helper, phase functions returning structured dicts, bounded routing loop. This is the template for the new engine.
- **Sophie's orchestrator** at `/Users/lorchard/devel/tabs-project/sophie/packages/core/src/orchestrator.ts:152-440` (external reference): plain `while` loop, phases as classes with `execute(...)` returning typed structured objects, `shouldContinueResearch` boolean read by code (not by the LLM).
- **Existing subagent dispatch** at [engine.py:187-294](../../../src/decafclaw/workflow/engine.py): the working code-driven precedent in the current engine. Generalize this shape — `dispatch → verify_outputs → auto-advance` — to inline phases too. Inline's `verify_outputs` step is "parsed the structured output successfully," not "files appeared on disk."
- **Gate firing kernel** at [engine.py:92-156](../../../src/decafclaw/workflow/engine.py): unchanged. Trigger point shifts from "LLM called `phase_advance` on a gated edge" to "engine resolved a single gated outgoing edge after the phase's structured output was captured."

## What we're NOT doing

- **Not building the `max_phase_continuations` nudge loop** from the phase-turn spec (the third-iteration proposal). The crank it patches is gone.
- **Not extending the LLM provider with native `responseSchema` / JSON-mode** kwargs. Forced-tool schema is sufficient.
- **Not unifying inline and subagent phases** into one "phase is a function" model. Subagent phases stay subagent-shaped (full child agent loop, multi-turn, file-based outputs). Inline phases become single-shot structured-output calls.
- **Not introducing per-phase Python handlers** registered in code. Phases stay declarative markdown with frontmatter. Authoring stays the same surface.
- **Not adding output rendering templates** beyond the `body.md` convention for `{body: string}` schemas. Phases with richer schemas get `output.json` only; downstream phases read fields directly.
- **Not preserving in-flight workflow state across the upgrade.** No live users. Any persisted state from old runs gets deleted; PR notes the breakage.
- **Not changing the `workflow/` package's external API surface** (`workflow_start`, `workflow_status`, `workflow_abort`, `workflow_artifact_read/write` tools remain). User experience is identical except for the absence of LLM `phase_advance` chatter in the transcript.
- **Not touching the `phase_turn_model` and `workflow_tool_result_framing` historical session docs.** They stay as record of what didn't work.

## Open questions

- **How does the engine emit per-phase progress to the conversation timeline?**
  - *Default answer:* `tool_status` events per phase (start/end), matching the spike's pattern. Web UI already renders these as live progress under the tool-call block. Plan can reconsider during execute if a richer event type proves needed.
- **What's the engine's behavior when a phase's structured-output LLM call exhausts its retry?**
  - *Default answer:* Same as a subagent verify failure today — `state.status = RunStatus.ERROR`, error logged, workflow archived, user surfaced via tool result text. No silent fall-through to the next phase.
- **Should the engine's `run_workflow` entry point be invoked from `workflow_start` directly, or via a new "workflow runner" tool that wraps both start and resume?**
  - *Default answer:* `workflow_start` invokes `run_workflow` directly. No new wrapper tool — keep the user-facing surface identical to today.
