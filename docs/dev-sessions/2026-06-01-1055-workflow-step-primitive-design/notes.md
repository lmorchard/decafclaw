# Workflow engine: step-primitive redesign (cold-restart handoff)

**Status:** Brainstorm complete (2026-06-01). See [spec.md](./spec.md) for the finalized design. Original cold-restart handoff content preserved below for historical context.

**Issue:** [#255](https://github.com/lmorchard/decafclaw/issues/255) · **Branch:** `feat/255-workflow-engine` (PR #557 still open, not merged)

---

## Where this session sits in the #557 timeline

1. **Three failed iterations** of an LLM-driven phase engine ([cross-conv](../2026-05-19-2121-workflow-engine/), [conv-scoped](../2026-05-21-1732-workflow-engine-rework/), [phase-turn](../2026-05-29-1729-workflow-engine-phase-turn-model/)) — all stalled on Flash because the LLM was responsible for emitting `phase_advance` to crank the state machine. Cheap experiment ([2026-05-29-1803](../2026-05-29-1803-workflow-tool-result-framing/)) confirmed prompt-only fixes were structurally insufficient.
2. **Sophie analysis** in [PR #557 comment 4587813874](https://github.com/lmorchard/decafclaw/pull/557#issuecomment-4587813874): reframed the failure as "we made the LLM responsible for turning the crank — make code drive instead, LLM is a structured-output worker." Salvage triage retained most of #557 (loader, types, state machine kernel); only the LLM-driven crank had to go.
3. **Code-driven spike** ([2026-05-31-1223](../2026-05-31-1223-code-driven-engine-spike/)) — built a freestanding `research_brief` orchestrator as a single tool (no engine integration). Walked gather → draft → review → publish end-to-end on `vertex-gemini-flash` first try, ~18s wall, five Vertex calls, zero retries. Committed as `1cd6924`. **Proved the mechanism but proved it at "phase = one LLM call" granularity.**
4. **First rework attempt** ([2026-05-31-1720](../2026-05-31-1720-code-driven-engine-rework/) — *shelved*, see banner on that session's spec.md) — wrote research.md + spec.md to generalize the spike's pattern back into PR #557's existing engine. Mid-brainstorm, Les surfaced the question that pivoted this session: **what about phases that need multiple LLM calls? Or user interaction? Or iterative steps with branching between them?**
5. **The pivot.** Les's reframe (saved as [project-workflow-design-thesis](../../../../../.claude/projects/-Users-lorchard-devel-decafclaw/memory/project_workflow_design_thesis.md)): this is a learning project, no shipping pressure, and the load-bearing architectural commitment is **code drives the process, LLM is a constrained worker on focused problems**. Operationalize the commitment at the abstraction level — don't rely on author convention. That means decomposing phases into smaller **step primitives**, not just removing the LLM from the transition layer.

## The design direction (post-pivot)

**From** (the shelved rework spec):
- Workflow = graph of *phases*
- Phase = single structured-output LLM call (or subagent)
- Edges between phases driven by code-issued routing calls
- Authoring: phase body = LLM prompt, frontmatter = schema/edges/gates

**To** (this session's direction):
- Workflow = graph of *steps* (phase may survive as a coarse grouping label, or dissolve entirely)
- Step = one focused primitive operation: ONE LLM call with declared schema, OR a tool call, OR a user-input prompt, OR a routing decision, OR a Python op
- Edges between **steps** drive transitions
- Authoring: step list with typed `kind` + per-step config; LLM steps carry their own prompt template

The vocabulary forces discipline at the type level: an `llm_call` step is *structurally* "one focused call with a schema." It cannot grow into "do everything for this phase" — the abstraction won't let it. Authors compose multi-step work by chaining steps, not by overloading one LLM call.

## What the next brainstorm needs to work through

These are entangled — picking one informs the others. The brainstorm should resolve them in roughly this order:

1. **Phases: keep, dissolve, or repurpose?**
   - Option A: phases stay as transition nodes, steps run sequentially inside a phase (linear)
   - Option B: phases become labels/groups only — steps are the real graph nodes, edges can cross phase boundaries
   - Option C: phases dissolved entirely — workflow is just a graph of steps
   - The interview example Les described maps most naturally to (B) or (C) since loop-back-with-question pops out as a step-level cycle.

2. **The primitive vocabulary.** What's the irreducible set of step kinds? Sketch from the conversation so far:
   - `llm_call` — structured-output LLM call with declared schema
   - `tool_call` — invoke a deterministic decafclaw tool with args computed from state
   - `user_input` — suspend, emit prompt (free-form text input or button choices), capture response to state
   - `route` — structured LLM decision returning enum, chooses the next step
   - `subagent` — spawn a child agent loop with a prompt, capture result
   - `python` — invoke a registered Python function with state, get result back (escape hatch)
   - `set` (maybe) — pure-Python: compute a state field from other state fields without an LLM
   - `loop` (maybe) — iterate sub-steps over a list
   - `branch` (maybe) — conditional based on state (could fold into `route` or graph edges)
   - **Open:** which are MVP, which are syntactic sugar, which are escape-hatch
   - **Open:** does `tool_call` exist as its own kind or is it just `python` with a tool-resolver?

3. **The state model.** How do steps address each other's outputs?
   - Each step has an `id`; its output goes into accumulated workflow state under `state[step_id]`
   - Later steps reference via templating: `{{state.plan_questions.questions}}`
   - **Open:** template language (Jinja? Mustache? something simpler?)
   - **Open:** is state a flat dict or nested? mutable or append-only?
   - **Open:** does the existing `workflow/artifacts/<phase_id>/...` directory model survive, or is it state-only?

4. **The graph model.** Edges between steps:
   - Explicit `next` field per step? Or a graph-level edge list?
   - Routing call's enum is auto-derived from the step's outgoing edges
   - Loops (back-edges) are just edges that point to an earlier step
   - **Open:** are there single-entry / single-exit constraints, or fully arbitrary directed graph?
   - **Open:** how does the interview pattern's "ask, capture, check, loop or exit" look in the graph — is it 3-4 steps with a back-edge, or a `loop` primitive wrapping sub-steps?

5. **The authoring surface.** Today: phase markdown body + frontmatter. With steps:
   - Option A: workflow declared in a single YAML file (`workflow.yaml`) listing all steps + edges; per-step `prompt` fields can be inline or `prompt-from: file.md`
   - Option B: per-step markdown files with frontmatter (one file per step, like phases today but smaller)
   - Option C: workflow YAML for graph structure + a side directory of prompt template files for LLM steps
   - **Open:** how does this interact with the existing `kind: workflow` skill format?

6. **Subagent steps vs. inline steps.** The existing `subagent` phase kind runs a full child agent loop. In the step model:
   - A `subagent` step kind keeps that semantic (spawn child, capture text or structured result)
   - But the child *itself* could be a workflow of steps — composition / sub-workflows
   - **Open:** is `subflow` a separate step kind? Is it the same as `subagent` with a different child type? Is there a useful distinction between "run a workflow" and "spawn an agent that runs a workflow"?

7. **Gate / user-interaction semantics.** Today: gate = approve/deny button at edge level. With steps:
   - `user_input` step suspends the workflow, emits a question + input affordance, captures response into state
   - Approve/deny is just a `user_input` with a button-list constraint
   - Free-form text input is a `user_input` with a text-field constraint
   - **Open:** does this swallow the existing `EndTurnConfirm` gate kernel entirely, or does that stay as the implementation primitive `user_input` is built on?
   - **Open:** what about multi-choice text? structured input? attachments?

8. **What survives from PR #557.**
   - Likely keep: `conv_state.py` (per-conversation persistence + lock), the `RunStatus` lifecycle, the registry, the bundled-skill loader for the `kind: workflow` SKILL.md format
   - Likely rewrite: `loader.py` (parsing changes substantially), `types.py` (PhaseDef → StepDef + new step kinds), `engine.py` (step graph executor), `subagent.py` (mostly intact — subagent is still a child agent loop, just now invoked as a step kind), `workflow_tools.py` (mostly delete — `phase_advance` is gone, `workflow_start/status/abort/artifact_read/write` may need adjustment to step semantics)
   - **Open:** how much actually survives — the answer changes the scope of the PR substantially

9. **Smoke / validation target.** What workflow should the new engine walk end-to-end as proof?
   - Migrate `research_brief` (the spike's topic) — well-known, comparable to the spike's run
   - Build an *interview* workflow — exercises `user_input`, loops, state accumulation. This is the case the pivot was triggered by.
   - Both, in sequence — research_brief proves the basic shape, interview proves the harder semantics.

## Pointers

- **Memory (load-bearing):** [project-workflow-design-thesis](../../../../../.claude/projects/-Users-lorchard-devel-decafclaw/memory/project_workflow_design_thesis.md), [project-spike-research-brief-walked](../../../../../.claude/projects/-Users-lorchard-devel-decafclaw/memory/project_spike_research_brief_walked.md), [reference-structured-output-pattern](../../../../../.claude/projects/-Users-lorchard-devel-decafclaw/memory/reference_structured_output_pattern.md).
- **Spike code (in tree, committed `1cd6924`):** `src/decafclaw/skills/spike_research_brief/{SKILL.md,tools.py}`. Throwaway, but `_call_structured` is the reference pattern for `llm_call` step implementation.
- **Sophie (external reference):** `/Users/lorchard/devel/tabs-project/sophie/packages/core/src/orchestrator.ts` + `phases/*.ts`. The orchestrator is "Sophie at phase granularity"; each phase function is essentially a sequence of typed steps in TS form — useful pattern reference even though TS-native.
- **Shelved rework session:** [`2026-05-31-1720-code-driven-engine-rework/`](../2026-05-31-1720-code-driven-engine-rework/) — spec.md banner explains why it's shelved. research.md there is still factually accurate about the current engine shape (file:line refs), reusable when assessing what to keep / rewrite / delete in PR #557.
- **Sophie pattern PR comment** ([4587813874](https://github.com/lmorchard/decafclaw/pull/557#issuecomment-4587813874)) — full salvage triage of #557, still relevant; the step-model is an *extension* of Sophie's approach, not a replacement.
- **Spike retro** ([2026-05-31-1223](../2026-05-31-1223-code-driven-engine-spike/notes.md)) — what worked, what's a wart, what to fold back. The "what to fold back" section's four items still apply but at step granularity instead of phase granularity.

## How to resume cold

1. Read this notes.md.
2. Read [project-workflow-design-thesis](../../../../../.claude/projects/-Users-lorchard-devel-decafclaw/memory/project_workflow_design_thesis.md) and the spike retro for the lesson + the existence-proof.
3. Skim Sophie's `orchestrator.ts` once for the shape (don't go deep on TS).
4. Run `/dev-session brainstorm` from this directory — work through the 9 entangled questions above. Recommend resolving in order (phases first, then vocabulary, then state, then graph, then authoring), since downstream decisions depend on upstream ones.
5. Produce a `spec.md` once the design space is pinned down. Probably ~150 lines per spec-template.
6. Plan + execute lives in subsequent sessions.

## Things this session is NOT trying to do

- Not writing code yet — design only.
- Not committing to scope size for the eventual PR. The redesign is potentially large; we'll know after the brainstorm closes whether it's one PR or staged across several.
- Not migrating PR #557's history — the prior commits stay on the branch as record of what didn't work, same convention as the cheap-experiment commits.
- Not yet deciding whether to land this as a follow-up to #557 or as a fresh PR. Decide after the brainstorm.

---

## Brainstorm session outcome (2026-06-01)

Worked through the 9 entangled questions in the recommended order. Full design in [spec.md](./spec.md).

Headline decisions:

1. **Phases dissolved entirely.** Workflow = graph of steps. No phase concept at the abstraction layer.
2. **Six step kinds in MVP:** `llm_call`, `tool_call`, `user_input`, `route`, `subagent`, `python`. Defer `loop`, `set`, `branch`.
3. **State** = flat dict keyed by step id; **latest-wins** on re-execution; subagent file outputs stored as **paths**, content read via explicit `tool_call: vault_read`.
4. **Graph** uses per-step `next:` with polymorphic forms (string for linear, `[{if, to}, ...]` for conditional, `choices: [{id, to, when}]` inline on `route`). Single entry (`initial-step:`), multiple exits, cycles allowed.
5. **Authoring** = single `workflow.yaml` + optional `prompts/` side dir; SKILL.md interface unchanged.
6. **Subagent in MVP**; subflow composition deferred.
7. **`user_input`** (text + choice) **fully replaces** edge-level `gate:`; implementation builds on existing `EndTurnConfirm` / `WidgetInputPause`.
8. **Fresh branch off `main`** with selective carry-forward (`subagent.py`, `conv_state.py` helpers, `RunStatus`, spike code as reference, dev-session docs). PR #557 closed-as-superseded when new PR lands.
9. **Two smoke targets** as `evals/workflows.yaml` cases: `research_brief` (migrates the spike) + `interview` (proves the pivot case — cycles, user_input, state accumulation).

Key principle clarified mid-brainstorm: **the thesis is about runtime LLM behavior, not author-time YAML expressiveness.** Templates and edge conditions can use real Jinja2 expressions; authors aren't agents and don't need to be constrained at the syntax level.

Key architectural shift: **the engine bypasses the agent loop for non-subagent steps.** LLM sees no tool catalog during `llm_call` / `route` / `tool_call` / `python` / `user_input` execution — the engine drives directly. This eliminates the surface that broke iterations 1–3 and drastically simplifies tool-restriction logic.

Next phase: `/dev-session plan`.
