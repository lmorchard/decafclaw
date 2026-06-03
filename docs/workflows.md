# Workflow Engine

Workflows are a first-class skill kind that let code drive multi-step processes while keeping the LLM as a constrained worker on focused sub-problems. The engine walks a typed step graph deterministically; the LLM appears only inside specific step kinds (`llm_call`, `route`). Everything else — tool invocation, state updates, edge resolution — is code-driven.

**Design thesis:** Three iterations of the earlier phase-based engine (PR #557) failed because Flash couldn't reliably emit a `phase_advance` tool call to crank the state machine. The step-primitive engine dissolves that failure mode: the engine, not the model, decides what runs next.

See also: `CLAUDE.md` Skills section, `src/decafclaw/workflow/`.

---

## Authoring layout

```
src/decafclaw/skills/<workflow>/
  SKILL.md           # kind: workflow + name / description / required-skills / user-invocable
  workflow.yaml      # initial-step + full step list + graph edges
  prompts/           # optional — prompt-from: refs
    *.md
  tools.py           # optional — kind: python registered functions
```

`SKILL.md` uses the same frontmatter shape as all other skills; the only new field is `kind: workflow`. The loader reads `workflow.yaml` from the same directory.

---

## Step kinds

Six step kinds cover all MVP needs. Each step writes output to `state[step_id]`.

### `llm_call`

One forced-tool structured LLM call. The engine builds a single-tool schema from the step's declared `schema:` (JSON Schema), calls the LLM with `tool_choice=required`, and writes the parsed tool arguments to `state[step_id]`. The model MUST call the tool; the engine retries once with a stricter prompt if it narrates instead.

```yaml
- id: outline
  kind: llm_call
  prompt-from: outline.md      # or inline prompt:
  schema:
    type: object
    properties:
      title: {type: string}
      bullets: {type: array, items: {type: string}}
    required: [title, bullets]
  next: draft
```

`prompt-from:` resolves to `prompts/<name>.md` in the skill directory. Inline `prompt:` is a Jinja2 template rendered against `state`.

### `tool_call`

Invoke a decafclaw tool by name. Args are Jinja2 templates rendered against `state`. The tool runs via the standard tool-execution machinery (timeout, `tool_status` events). `{text, data}` from the `ToolResult` is written to `state[step_id]`.

```yaml
- id: read_sources
  kind: tool_call
  tool: workspace_read
  args:
    path: "{{ state.gather.outputs['sources.md'] }}"
  next: outline
```

### `user_input`

Suspend the workflow, present a prompt to the user, and resume with their response written to `state[step_id]`. Two input modes:

- `input: text` — free-text entry via `WidgetInputPause`; response lands as `{value: "<text>"}`.
- `input: choice` — button choices via `EndTurnConfirm`; response lands as `{choice: "<id>"}`.

```yaml
- id: ask_user
  kind: user_input
  prompt: "{{ state.pick_question.question }}"
  input: text
  next: log_qa
```

The suspension is persistent: state is saved, and the workflow resumes when the user responds — surviving page reloads and server restarts (backed by the existing confirmation-message persistence infrastructure).

### `route`

The LLM picks from a declared enum of choices; the choice maps to an outgoing edge. **This is the only mechanism by which the LLM influences control flow.** The engine builds the schema and calls the LLM with `tool_choice=required`; the returned `choice` id maps to the step's `choices:` list.

```yaml
- id: critique
  kind: route
  prompt-from: critique.md
  choices:
    - {id: approve, to: publish,  when: "draft satisfies the brief"}
    - {id: revise,  to: outline,  when: "structural rework needed"}
    - {id: abort,   to: "",       when: "fundamentally broken"}
```

`to: ""` is a terminal edge. `when:` is the LLM-facing description injected into the schema's enum description.

### `subagent`

Spawn a child agent loop using the existing child-agent dispatch infrastructure (`workflow/subagent.py`, carried forward from PR #557). The child has its own conversation, skill, and tool set. File outputs are declared via `outputs:` and appear as paths in `state[step_id].outputs[<filename>]`; reading their content requires an explicit `tool_call: workspace_read` or `vault_read` step.

```yaml
- id: gather
  kind: subagent
  prompt-from: gather.md
  skill: vault
  tools: [workflow_artifact_write]
  outputs: [sources.md]
  context-profile:
    memory-retrieval: off
  next: read_sources
```

The engine suspends with `PAUSED_SUBAGENT` until the child agent completes, then resumes automatically.

### `python`

Escape hatch: call a registered Python function from the workflow's `tools.py`. The function receives `state` (the full state dict) and returns a dict written to `state[step_id]`. Use this for non-trivial computation that Jinja2 can't handle inline.

```yaml
- id: word_count
  kind: python
  fn: count_draft_words
  next:
    - if: "state.word_count.count > 800"
      to: shorten
    - to: critique
```

```python
# skills/research_brief/tools.py
def count_draft_words(state: dict) -> dict:
    body = state.get("shorten", state.get("draft", {})).get("body", "")
    return {"count": len(body.split())}
```

---

## State model

State is a flat dict keyed by step id: `state[step_id]` = that step's output dict.

**Latest-wins on re-execution.** When a back-edge revisits a step (e.g., `critique → revise → outline → draft → critique` cycle), the step's entry in `state` is overwritten. Authors needing history accumulate it explicitly — via a `python` step (see `interview`'s `log_qa`) or via `notes_append`.

**Subagent outputs are paths, not content.** `state.gather.outputs["sources.md"]` is a workspace-relative path. Content requires an explicit read step.

---

## Templates and edge conditions

All template strings and edge `if:` expressions use **Jinja2 `SandboxedEnvironment`**. Templates are evaluated at runtime against the current state dict, accessible via `state.<step_id>.<field>`. The LLM does not emit templates at runtime — they are author-static.

```yaml
prompt: "Summarise this brief: {{ state.draft.body | truncate(2000) }}"
```

Edge conditions:

```yaml
next:
  - if: "state.word_count.count > 800"
    to: shorten
  - to: critique          # no if: → default fallback
```

First-matching-wins; an entry without `if:` is the unconditional default.

---

## Graph model

Per-step `next:` field, three forms:

- `next: step_id` — linear advance
- `next: [{if: expr, to: id}, ...]` — conditional, first-match-wins
- Omitted — terminal (workflow finishes after this step)

`route` steps use `choices:` instead of `next:` — each choice declares its own `to:` target inline.

Constraints:
- Single entry via `initial-step:` (default: first declared step).
- Multiple terminals — `next` omitted or `to: ""`.
- **Cycles are first-class.** Back-edges revisit steps with latest-wins state semantics.
- Load-time validation: every `to:` reference resolves to a known step id or `""`; `initial-step` exists; unreachable steps are logged as warnings but do not fail loading.

---

## Engine design

The engine (`workflow/engine.py`) drives step execution directly — it does **not** route through the agent loop. For `llm_call`, `tool_call`, `python`, `route`, and `user_input` steps, no agent turn is involved. The LLM sees no tool catalog; the engine calls LLM APIs directly. This eliminates the "model decides what to do next" surface that broke the prior phase-based engine.

`subagent` is the sole exception: it dispatches a full child agent loop via `conversation_manager.enqueue_turn(kind=CHILD_AGENT)` and suspends until the child completes.

State is persisted to `workspace/workflows/{conv_id}/state.json` after every step.

---

## Bundled workflows

### `workflow_hello` — minimal

Exercises `llm_call` + `tool_call`. Two steps: generate a greeting, list the workspace. Used as a smoke test for the engine.

```yaml
initial-step: greet
steps:
  - id: greet
    kind: llm_call
    prompt: "Generate a 3-word greeting for the topic: {{ state.topic | default('agent testbed') }}"
    schema:
      type: object
      properties:
        greeting: {type: string}
      required: [greeting]
    next: list_workspace

  - id: list_workspace
    kind: tool_call
    tool: workspace_list
    args: {path: ""}
    # terminal
```

### `research_brief` — full graph with cycle

Exercises all six step kinds. Gather sources via subagent → read → outline → draft → word-count (python) → optional shorten → critique (route: approve/revise/abort) → publish. The `revise` choice creates a back-edge to `outline`, demonstrating cycle execution with latest-wins state.

```yaml
initial-step: gather
steps:
  - id: gather          # subagent: writes sources.md to artifacts
    ...
    next: read_sources
  - id: read_sources    # tool_call: vault_read or workspace_read
    ...
    next: outline
  - id: outline         # llm_call: {title, bullets}
    ...
    next: draft
  - id: draft           # llm_call: {body}
    ...
    next: word_count
  - id: word_count      # python: count_draft_words → {count}
    next:
      - {if: "state.word_count.count > 800", to: shorten}
      - {to: critique}
  - id: shorten         # llm_call: shorten body
    ...
    next: critique
  - id: critique        # route: approve → publish | revise → outline | abort → ""
    kind: route
    choices:
      - {id: approve, to: publish,  when: "draft satisfies the brief"}
      - {id: revise,  to: outline,  when: "structural rework needed"}
      - {id: abort,   to: "",       when: "fundamentally broken"}
  - id: publish         # tool_call: workflow_artifact_write
    # terminal
```

### `interview` — suspension and explicit accumulation

Exercises `user_input` (text), `python` (log accumulation), `route`, and cycles. The `log_qa` python step explicitly appends each Q&A pair to a running list, demonstrating the pattern for accumulating history under latest-wins semantics.

```yaml
initial-step: pick_question
steps:
  - id: pick_question   # llm_call: {question, remaining_topics}
    next: ask_user
  - id: ask_user        # user_input text — suspends; resume writes {value}
    next: log_qa
  - id: log_qa          # python: log_qa → {qa_log: [...]}
    next: assess
  - id: assess          # route: clarify → ask_user | next_question → pick_question | summarize → final_summary
    kind: route
    choices:
      - {id: clarify,       to: ask_user,      when: "answer too vague"}
      - {id: next_question, to: pick_question,  when: "good answer; more topics remain"}
      - {id: summarize,     to: final_summary,  when: "all topics covered"}
  - id: final_summary   # llm_call: {summary} — terminal
```

---

## Known limitations

**Paused user_input does not survive process restart.** Workflow state (`PAUSED_USER_INPUT`) persists to disk via `conv_state`, so the pause is durable. However, if the process restarts while a `user_input` step is pending, the in-memory closure that resumes the workflow is gone. The widget response would be injected into the agent loop as a synthetic message rather than routed back to the workflow engine. Surviving process restart for paused workflows requires wiring a restart-recovery handler in `confirmations.py` (the `WORKFLOW_USER_INPUT` confirmation action and handler were removed as dead code in PR #572 — deferred until restart recovery is a priority).

---

## Eval surface

`evals/workflows.yaml` — three cases covering `workflow_hello`, `research_brief`, and `interview`. Run via `make eval-workflows`. Assertions use standard eval primitives (`expect_tool`, `expect_tool_count_by_name`, `final_status`).

```
make eval-workflows    # run all three cases against the default model
make eval-history      # print trend table from evals/history.jsonl
```
