# Workflow Step-Primitive Engine

**Goal:** Replace decafclaw's phase-based workflow engine (PR #557) with a step-primitive engine that operationalizes the thesis "code drives the process, LLM is a constrained worker" at the abstraction level — typed step kinds make discipline structural rather than aspirational.

**Source:** Issue [#255](https://github.com/lmorchard/decafclaw/issues/255) · brainstorm session 2026-06-01-1055 · supersedes PR [#557](https://github.com/lmorchard/decafclaw/pull/557)

## Current state

Reference: [`../2026-05-31-1720-code-driven-engine-rework/research.md`](../2026-05-31-1720-code-driven-engine-rework/research.md) (still factually accurate for PR #557 module shape).

PR #557 (`src/decafclaw/workflows/`) implements a phase-graph engine where each phase is either:
- **INLINE**: LLM-driven agent loop with per-phase tool whitelist + prompt; emits `phase_advance(target_id)` to crank the state machine.
- **SUBAGENT**: child agent loop dispatched by the engine, captures declared output files.

Three iterations failed because Flash couldn't reliably emit `phase_advance` to drive the state machine ([project-workflow-design-thesis](../../../../../.claude/projects/-Users-lorchard-devel-decafclaw/memory/project_workflow_design_thesis.md)). The 2026-05-31 spike (`src/decafclaw/skills/spike_research_brief/`, commit `1cd6924`) proved code-driven orchestration with forced-tool structured outputs walks end-to-end on Flash first-try.

## Desired end state

A workflow is a graph of typed **steps**. The engine walks the graph deterministically; the LLM appears only as a constrained worker inside specific step kinds.

### Step kinds (MVP, six)

1. **`llm_call`** — one forced-tool structured LLM call. Output (matching declared schema) → `state[step_id]`.
2. **`tool_call`** — invoke a decafclaw tool by name with args computed from state. `{text, data}` from `ToolResult` → state.
3. **`user_input`** — suspend; emit text input or button choices via existing `EndTurnConfirm` / `WidgetInputPause`; resume with `{value}` (text) or `{choice}` (button) in state. Fully replaces PR #557's edge-level `gate:`.
4. **`route`** — LLM picks from declared enum; choice maps to outgoing edge inline. **The only LLM control-flow influence in the entire engine.**
5. **`subagent`** — spawn child agent loop (PR #557 `subagent.py` semantics: `skill`, `tools`, `outputs`, `context-profile`).
6. **`python`** — escape hatch: registered function in workflow's `tools.py`, takes state, returns dict written to state.

Deferred: `loop`, `set`, `branch`. Cycles + edge `if:` + Jinja inline expressions + `python` cover MVP needs.

### State model

- Flat dict keyed by step id: `state[step_id]` = that step's output.
- **Latest-wins** on re-execution: a back-edge revisiting a step overwrites its prior output. Authors needing history accumulate explicitly (`python` step, `notes_append`).
- Subagent file outputs appear as **paths** in `state[step_id].outputs[<filename>]`; reading content requires an explicit `tool_call: vault_read` step.

### Templates and edge conditions

**Jinja2 `SandboxedEnvironment`**, used uniformly for:
- Template strings (prompt inputs, tool args, user prompts)
- Edge `if:` expressions (evaluated to bool)

Authors use filters, comparisons, idioms (`state.x | length > 5000`, `state.choice == "approve"`). `python` step remains the escape hatch for non-trivial computation. Templates and edge conditions are author-static; the LLM does not emit them at runtime.

### Graph model

Per-step `next:` field, polymorphic:
- `next: step_id` — linear
- `next: [{if: cond, to: id}, ...]` — conditional, first-match-wins, entry without `if` is the default
- `route` steps omit `next:` — their `choices:` declare targets inline:
  ```yaml
  - id: critique
    kind: route
    prompt-from: critique.md
    choices:
      - { id: approve, to: publish,  when: "draft satisfies the brief" }
      - { id: revise,  to: outline,  when: "structural rework needed" }
      - { id: abort,   to: "",       when: "fundamentally broken" }
  ```

Constraints:
- Single entry via `initial-step:` (default: first declared step).
- Multiple terminals — `next` omitted or `to: ""`.
- Cycles allowed; back-edges are first-class.
- Load-time validation: every `to:` reference resolves; `initial-step` exists; unreachable steps are warnings, not errors.

### Authoring layout

```
src/decafclaw/skills/<workflow>/
  SKILL.md           # kind: workflow + name/description/required-skills (existing shape)
  workflow.yaml      # initial-step + full step list + graph
  prompts/           # optional, for prompt-from: refs
    *.md
  tools.py           # optional, for kind: python registered functions
```

SKILL.md keeps existing fields; `workflow.initial-phase` removed (entrypoint is `workflow.yaml` by convention).

### Smoke validation

Two bundled workflows, both as `evals/workflows.yaml` cases (new theme, per [feedback-eval-framework-over-smoke-scripts](../../../../../.claude/projects/-Users-lorchard-devel-decafclaw/memory/feedback_eval_framework_over_smoke_scripts.md)):

- **`research_brief`** — migrates the 2026-05-31 spike. `gather` (subagent) → `read_sources` (tool_call) → `outline` (llm_call) → `draft` (llm_call) → `critique` (route: approve/revise/abort) → `publish` (tool_call). Cycle: `critique.revise → outline`.
- **`interview`** — the case that triggered the pivot. `pick_question` (llm_call) → `ask_user` (user_input) → `assess` (route: clarify/next_question/summarize) with back-edges to `ask_user` and `pick_question`; terminates at `final_summary` (llm_call).

Together exercise 5 of 6 step kinds + cycles + user suspension + subagent dispatch. `python` validated by dedicated unit test, plus a small inline use in one workflow (e.g., `research_brief` word-count check) to exercise the kind in a real eval run.

## Design decisions

- **Phases dissolved; workflow = step graph.**
  - Why: phases-as-graph-nodes constrain loops to phase boundaries (broken for interview); phases-as-tags is purely cosmetic and recoverable if observability later wants breadcrumbs.
  - Rejected: phases as transition nodes (interview loop can't target a single question); phases as labels-only (adds concept with no semantic role).

- **LLM control-flow influence is bounded to exactly two surfaces.** `llm_call` produces structured output → state; `route` picks an enum that maps to an outgoing edge. No LLM emits a "next step" command outside `route`.
  - Why: the thesis. Three iterations of LLM-driven `phase_advance` proved structurally insufficient.
  - Rejected: any pattern where the LLM implicitly decides what to do next; unconstrained schemas with routing fields mixed in.

- **`tool_call` and `subagent` are distinct kinds, not folded into `python`.**
  - Why: declarative visibility — workflow YAML reads as "this step calls `tabstack_research`" / "this step spawns a subagent" without reading Python. `tool_call` inherits the tool registry's status-event flow.
  - Rejected: 4-kind minimalist vocabulary folding both into `python` wrappers.

- **No `loop` / `set` / `branch` kinds in MVP.** Cycles handle conditional loops; edge `if:` handles deterministic branching; Jinja inline expressions handle most computation; `python` is the escape hatch for serious work.
  - Why: YAGNI. Neither smoke target needs them; design questions (parallel? error handling? expression DSL?) lack grounded cases.
  - Rejected: ship `loop` upfront — risks guessing wrong on semantics with no real workflow to ground the decisions.

- **Templates and edge conditions use Jinja2 `SandboxedEnvironment`.**
  - Why: workflow YAML is authored statically (human-reviewed), not emitted dynamically by an agent. The thesis is about runtime LLM control, not author-time expressiveness. A real expression language reads better than a custom mini-DSL.
  - Rejected: strict dotted-path-only templates (over-constrains author); arbitrary `eval()` (Jinja2 sandbox is the appropriate primitive).

- **State is flat, keyed by step id, latest-wins on re-execution.**
  - Why: matches normal-code mental model (variables hold latest value). Linear references stay simple; loops accumulate explicitly.
  - Rejected: auto-array on revisit (complicates linear references just for the loop case).

- **Subagent file outputs are paths in state; content read via explicit `tool_call: vault_read`.**
  - Why: uniformity (state = step outputs, no magic), explicit I/O traceable in the graph, scales to large/binary files. Aligns with "code drives everything."
  - Rejected: auto-read into state (special-cases subagent, hides I/O, breaks for large files); size-cap hybrid (magic threshold, two code paths).

- **Per-step `next:` field with polymorphic forms; `route` choices declare targets inline.**
  - Why: locality — edges next to their source step; `route`'s choice-to-target binding is naturally inline.
  - Rejected: global edge list (splits route's tight coupling across two sections); hybrid linear+global (two ways to express same routing).

- **Single `workflow.yaml` for the graph + optional `prompts/` side dir.**
  - Why: graph topology readable in one file; prompts externalized when long; refactoring (rename step, change route target) is single-file.
  - Rejected: per-step `.md` files (loses whole-graph view; numbered-prefix smell for non-linear graphs); inline in SKILL.md frontmatter (bloats SKILL.md).

- **Subagent in MVP; subflow deferred.**
  - Why: research_brief needs subagent; nothing yet needs subflow composition. Adding subflow later is non-breaking.
  - Rejected: both in MVP (no grounded constraints for state passing / error mapping / suspension propagation).

- **`user_input` (text + choice) fully replaces edge-level `gate:`.**
  - Why: one concept for human interaction. Two-choice user_input IS a gate. Reduces vocabulary by deleting `GateDef`, `_enter_gate`, `finalize_gate_response`.
  - Rejected: `gate:` shortcut alongside user_input (two ways to express same thing); text-only user_input (forces extra LLM call per approve/deny).

- **Engine bypasses the agent loop for non-subagent steps.** The LLM sees no tool catalog during `llm_call` / `route` / `tool_call` / `python` / `user_input` execution; the engine drives directly.
  - Why: this IS the thesis-implementation. Eliminates the "LLM decides what to do next" surface that broke iterations 1-3. Drastically simplifies the tool-restriction model — per-step whitelists matter only inside subagent step children.
  - Rejected: dispatch steps through the agent loop with restricted tools (the model that failed three times).

- **Fresh branch off `main`; selective carry-forward.** Carry: `subagent.py`, `conv_state.py` lock + path helpers, `RunStatus`, spike code (delete after `llm_call` ships), all dev-session docs. PR #557 closed-as-superseded when new PR lands.
  - Why: ~70% of PR #557 workflow code is best rewritten clean; editing existing files would force mid-rewrite intermediate states fighting the old structure. Squashing on landing means PR-level commit history isn't preserved on main anyway; the pedagogy lives in `docs/dev-sessions/`.
  - Rejected: continue on PR #557 (intermediate broken-CI commits; harder final diff); total clean slate without carry-forward (rewriting well-tested `subagent.py` is wasted work).

## Patterns to follow

- **Subagent child-agent dispatch:** `subagent.py:_run_child`, `subagent.py:_resolve_phase_tools`, `subagent.py:200-215` (skill-as-prompt). Carry forward with caller-site adaptation — invoked from the step executor for `kind: subagent`, not from `engine.dispatch_subagent_if_needed`.
- **Per-conversation state persistence:** `conv_state.py` `conv_lock` async-lock pattern; `workspace/workflows/{conv_id}/...` file path convention. State schema rewritten (step-keyed dict, no phase history) but plumbing intact.
- **`user_input` backing infrastructure:** `EndTurnConfirm` (`confirmations.py`), `WidgetInputPause` (`widget_input.py`). Persistence as `confirmation_request` conversation messages — already reload-survivable and tested. Workflow engine consumes; does not reimplement.
- **Forced-tool structured-output pattern:** `src/decafclaw/skills/spike_research_brief/tools.py:_call_structured` — reference implementation for the `llm_call` step kind. Delete the spike skill once `llm_call` ships.
- **Eval framework:** `evals/<theme>.yaml` + `setup.max_tool_iterations` per the existing pattern. New `evals/workflows.yaml` theme. Per [reference-structured-output-pattern](../../../../../.claude/projects/-Users-lorchard-devel-decafclaw/memory/reference_structured_output_pattern.md), forced-tool + "you MUST call this" framing is Flash-reliable.
- **Skill loader integration:** `kind: workflow` recognition + bundled SKILL.md dispatch — interface unchanged; only the downstream loader changes.

## What we're NOT doing

- **No `loop`, `set`, or `branch` step kinds.** Reopen when a grounded use case demands.
- **No subflow composition.** Workflow-as-step deferred until ≥2 workflows share structure.
- **No structured-form input or attachments in `user_input`.** Text + choice only.
- **No per-step LLM tool whitelists.** Tool restrictions apply only inside subagent step children.
- **No mid-step interruption protocol.** Step runs to completion or suspension; user interrupts via existing turn cancellation, not workflow-aware logic.
- **No migration of existing decafclaw skills to workflows.** This ships the engine + two smoke workflows only.
- **No commit-level history of PR #557's iterations preserved on `main`.** Lessons live in `docs/dev-sessions/`; squashing the merge is fine.
- **No effort to keep PR #557 mergeable.** It's superseded; closed when new PR lands.

## Open questions

- **Exact Jinja2 sandbox configuration.** Default `SandboxedEnvironment` is the starting point.
  - Default answer: ship Jinja2 defaults; tighten only if a security or footgun concern surfaces during execute or PR review.

- **Where to add `python` step exercise in MVP smokes.** Both workflows can technically run without it.
  - Default answer: add a small `python` step to `research_brief` (word-count check feeding an edge condition) so an end-to-end eval run touches the kind; unit test covers it standalone regardless.

- **Eval YAML assertion shape for workflow runs.** Existing eval framework asserts tool calls; workflows want step-completion assertions.
  - Default answer: start with existing assertion primitives (`workflow_start` + `workflow_status` calls, count via `expect_tool_count_by_name`); extend the eval framework with workflow-specific assertions only if those prove insufficient during execute.
