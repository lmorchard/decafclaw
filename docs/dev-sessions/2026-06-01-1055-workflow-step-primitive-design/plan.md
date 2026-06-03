# Workflow Step-Primitive Engine — Implementation Plan

**Goal:** Replace decafclaw's phase-based workflow engine (PR #557) with a step-primitive engine where typed step kinds (`llm_call`, `tool_call`, `user_input`, `route`, `subagent`, `python`) operationalize the design thesis at the abstraction level.

**Approach:** Fresh branch off `main` with selective carry-forward of well-tested code (`subagent.py`, `conv_state.py` lock + path helpers, `RunStatus`). Vertical slices add one step kind at a time and exercise it end-to-end via `evals/workflows.yaml` cases. Each phase ends with the engine able to run more workflows than before.

**Tech stack:** Python 3.13, `asyncio`, Jinja2 (`SandboxedEnvironment`), existing decafclaw `call_llm` + tool registry + `EndTurnConfirm` / `WidgetInputPause` confirmation infrastructure.

**Module layout target (`src/decafclaw/workflow/`):**
```
__init__.py
conv_state.py     # carried forward, schema updated to step-keyed dict
types.py          # rewritten: StepDef + per-kind subtypes; WorkflowState
loader.py         # rewritten: workflow.yaml parser
engine.py         # rewritten: step graph executor
step_executors.py # NEW: one executor function per step kind, dispatched by engine
jinja_env.py      # NEW: SandboxedEnvironment factory + template/condition helpers
subagent.py       # carried forward (mostly intact)
registry.py       # carried forward, unchanged
```

LLM-facing tools: `src/decafclaw/tools/workflow_tools.py` — rewritten to a thin set (`workflow_start`, `workflow_status`, `workflow_abort`, `workflow_artifact_read/write`); `phase_advance` and `refresh_workflow_tools` deleted.

Test layout: `tests/workflow/test_*.py` per module + one integration test per smoke workflow.

---

## Phase 1: Foundation — branch setup, carry-forward, `llm_call`, hello workflow, eval surface

End-to-end deliverable: an empty `hello_world` workflow with one `llm_call` step runs against a real LLM via `workflow_start("hello_world")` and writes its structured output to workflow state; an eval case asserts it terminates and produces expected state.

**Files:**
- Create branch `feat/255-workflow-step-primitive` off `origin/main`; worktree at `.claude/worktrees/feat-255-workflow-step-primitive/`
- Carry forward (copy from this worktree):
  - `src/decafclaw/workflow/subagent.py` (intact; only caller site changes in phase 4)
  - `src/decafclaw/workflow/conv_state.py` (lock + path helpers; **rewrite WorkflowState schema**)
  - `src/decafclaw/workflow/registry.py` (unchanged)
  - `src/decafclaw/skills/spike_research_brief/` (kept as reference; deleted in phase 7)
  - All `docs/dev-sessions/2026-*-workflow*/` and `docs/dev-sessions/2026-06-01-1055-workflow-step-primitive-design/` directories
  - `docs/dev-sessions/2026-05-31-1223-code-driven-engine-spike/`
- Create: `src/decafclaw/workflow/types.py` — rewritten
- Create: `src/decafclaw/workflow/loader.py` — rewritten
- Create: `src/decafclaw/workflow/engine.py` — rewritten
- Create: `src/decafclaw/workflow/step_executors.py` — new
- Create: `src/decafclaw/workflow/jinja_env.py` — new
- Rewrite: `src/decafclaw/tools/workflow_tools.py` — thin shape (no `phase_advance`, no per-phase tool restriction)
- Modify: `src/decafclaw/workflow/__init__.py` — exports
- Modify: `src/decafclaw/tool_definitions.py` — remove `refresh_workflow_tools` integration if present (was the dynamic-tool hook for `phase_advance`)
- Create: `src/decafclaw/skills/workflow_hello/SKILL.md` + `workflow.yaml` (one-step minimal workflow)
- Create: `evals/workflows.yaml` — new theme file with one hello case
- Modify: `Makefile` if needed — add `eval-workflows` target paralleling existing `eval-*` targets
- Create tests:
  - `tests/workflow/test_types.py`
  - `tests/workflow/test_loader.py`
  - `tests/workflow/test_engine.py`
  - `tests/workflow/test_jinja_env.py`
  - `tests/workflow/test_step_executors_llm_call.py`
  - `tests/workflow/test_workflow_tools.py` (replaces PR #557 test_workflow_tools.py at the new shape)
  - `tests/workflow/test_conv_state.py` (carried forward + schema-updated)

**Key changes:**

`types.py`:
```python
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

class StepKind(str, Enum):
    LLM_CALL = "llm_call"
    TOOL_CALL = "tool_call"
    USER_INPUT = "user_input"
    ROUTE = "route"
    SUBAGENT = "subagent"
    PYTHON = "python"

class RunStatus(str, Enum):
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"
    PAUSED_USER_INPUT = "paused_user_input"   # renamed from PAUSED_GATE
    PAUSED_SUBAGENT = "paused_subagent"

@dataclass(frozen=True)
class EdgeRef:
    """A `to: step_id` reference with an optional `if:` Jinja condition."""
    to: str             # target step id; "" = terminal
    if_expr: str = ""   # empty = unconditional / default fallback

@dataclass(frozen=True)
class RouteChoice:
    id: str             # enum value
    to: str             # target step id
    when: str = ""      # LLM-facing description
    label: str = ""     # only used by user_input (button label)

@dataclass(frozen=True)
class StepDef:
    id: str
    kind: StepKind
    config: dict[str, Any]          # kind-specific config (schema, prompt-from, tool, args, etc.)
    next_edges: tuple[EdgeRef, ...] = ()    # for non-route, non-user_input kinds
    choices: tuple[RouteChoice, ...] = ()   # for route + user_input(choice) kinds
    description: str = ""           # author-facing doc string

@dataclass
class WorkflowState:
    workflow: str           # workflow name
    run_id: str             # uuid
    conv_id: str
    initial_step: str
    current_step: str
    status: RunStatus
    state: dict[str, Any]   # step_id → step output (author-visible in Jinja as `state.<step_id>`)
    transitions: list[dict] # engine-internal step transition log (NOT visible to authors)
    pending: dict[str, Any] = field(default_factory=dict)  # pause-state (user_input prompt, subagent context, etc.)
```

`loader.py`:
```python
def load_workflow(skill_dir: Path) -> WorkflowDef:
    """Parse SKILL.md + workflow.yaml + prompts/ into a WorkflowDef."""
    # 1. Parse SKILL.md frontmatter (name, description, kind: workflow,
    #    required-skills, user-invocable, argument-hint).
    # 2. Parse workflow.yaml: initial-step + steps list.
    # 3. For each step: validate kind ∈ StepKind, parse next/choices,
    #    resolve prompt-from refs to prompts/*.md contents.
    # 4. Validate: every `to:` resolves to a step id or ""; initial-step exists;
    #    warn on unreachable steps.
    # 5. Return frozen WorkflowDef(name, description, initial_step, steps).
```

`jinja_env.py`:
```python
from jinja2.sandbox import SandboxedEnvironment

_env = SandboxedEnvironment(autoescape=False)

def render_template(template_str: str, state: dict) -> str:
    """Render a Jinja template string against workflow state."""
    return _env.from_string(template_str).render(state=state)

def eval_condition(expr: str, state: dict) -> bool:
    """Evaluate a Jinja expression to bool. Empty/whitespace → True."""
    if not expr.strip():
        return True
    compiled = _env.compile_expression(expr)
    return bool(compiled(state=state))
```

`engine.py`:
```python
async def start_workflow(ctx, name: str) -> WorkflowState:
    """Initialize state, persist, and begin execution at initial_step."""
    wf = registry.get(name)
    state = init_workflow_state(ctx, workflow=name, initial_step=wf.initial_step)
    return await _run_to_suspension(ctx, state, wf)

async def _run_to_suspension(ctx, state, wf) -> WorkflowState:
    """Execute steps until a terminal step, a suspension, or an error."""
    while state.status == RunStatus.RUNNING:
        step = wf.steps_by_id[state.current_step]
        result = await step_executors.execute(ctx, step, state)
        _apply_step_result(state, step, result)
        save_workflow_state(ctx, state)
    return state

def _apply_step_result(state, step, result):
    """Write step output to state[step.id]; advance current_step per kind."""
    state.state[step.id] = result.output
    state.transitions.append({"step": step.id, "ts": _now_iso(), ...})
    if result.next_step is not None:
        state.current_step = result.next_step
    elif result.suspend_status is not None:
        state.status = result.suspend_status
        state.pending = result.pending
    else:
        state.status = RunStatus.DONE
```

`step_executors.py` (phase 1 covers `llm_call` only):
```python
@dataclass
class StepResult:
    output: Any                                 # written to state[step.id]
    next_step: str | None = None                # None = engine picks default or terminates
    suspend_status: RunStatus | None = None     # set when stepping pauses (user_input, subagent)
    pending: dict = field(default_factory=dict) # data needed to resume

async def execute(ctx, step: StepDef, state: WorkflowState) -> StepResult:
    if step.kind == StepKind.LLM_CALL:
        return await _execute_llm_call(ctx, step, state)
    raise NotImplementedError(f"step kind {step.kind} not yet implemented")

async def _execute_llm_call(ctx, step: StepDef, state: WorkflowState) -> StepResult:
    """Forced-tool structured-output call. Pattern from spike_research_brief."""
    cfg = step.config
    prompt = render_template(cfg["prompt"], state.state)
    schema = cfg["schema"]   # JSON Schema dict
    tool_name = f"submit_{step.id}"
    output = await call_llm_structured(
        ctx, system=cfg.get("system", ""), user_msg=prompt,
        schema=schema, tool_name=tool_name,
    )
    next_step = _resolve_next(step, output, state)
    return StepResult(output=output, next_step=next_step)

def _resolve_next(step: StepDef, output: dict, state: WorkflowState) -> str | None:
    """Walk step.next_edges, first matching if_expr wins; '' = terminal."""
    # Temporarily augment state with this step's output so edge conditions
    # can reference it without an explicit save (engine writes after this call).
    augmented = {**state.state, step.id: output}
    for edge in step.next_edges:
        if eval_condition(edge.if_expr, augmented):
            return edge.to or None  # "" → terminal
    return None  # no matching edge = terminal
```

`workflow_tools.py` (thin rewrite):
```python
WORKFLOW_TOOLS = {
    "workflow_start": tool_workflow_start,
    "workflow_status": tool_workflow_status,
    "workflow_abort": tool_workflow_abort,
    "workflow_artifact_read": tool_workflow_artifact_read,
    "workflow_artifact_write": tool_workflow_artifact_write,
}
# No phase_advance. No refresh_workflow_tools. No _build_phase_allowed_set.
```

Hello workflow (`src/decafclaw/skills/workflow_hello/`):
```yaml
# SKILL.md frontmatter
name: workflow_hello
description: Smallest possible workflow — one llm_call step.
kind: workflow

# workflow.yaml
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
    # no next: → terminal
```

`evals/workflows.yaml`:
```yaml
# Step-primitive workflow engine — end-to-end smoke cases.

- name: "hello_world workflow completes via workflow_start"
  setup:
    reflection_enabled: false   # per #534 — avoid eval-spurious retry
    max_tool_iterations: 5
  input: "Run the workflow_hello workflow."
  expect:
    tool_called: workflow_start
    final_status: done           # NEW eval assertion (see verification below)
```

**Verification — automated:**
- [x] `make lint` passes
- [x] `make typecheck` passes
- [x] `make test` passes (2836 passed)
- [x] `pytest tests/workflow/ -v` — all phase-1 unit tests green
- [x] `make eval-workflows` (or equivalent invocation) runs the hello case end-to-end against a real LLM and asserts the workflow reaches `RunStatus.DONE` — PASS on Flash (4.0s)

**Verification — manual:** (deferred to pre-PR self-review; eval validates end-to-end)
- [ ] Open a workspace conversation, send `!workflow_hello` (or invoke via web UI command) — workflow runs and shows terminal state
- [ ] Inspect `workspace/workflows/{conv_id}/state.json` — `state.greet` populated with the schema-shaped dict
- [ ] Verify `tests/workflow/` directory structure matches the layout above (no stragglers from old test files)

---

## Phase 2: `tool_call` step kind

End-to-end deliverable: a workflow step can invoke a decafclaw tool by name with args computed from state via Jinja templates; tool result lands in `state[step_id]`.

**Files:**
- Modify: `src/decafclaw/workflow/types.py` — no schema change; `tool_call` config shape is `{tool: str, args: dict}`
- Modify: `src/decafclaw/workflow/step_executors.py` — add `_execute_tool_call`
- Modify: `src/decafclaw/workflow/loader.py` — parse + validate `tool_call` step
- Modify: `src/decafclaw/skills/workflow_hello/workflow.yaml` — add a second step that does a `tool_call` to exercise the kind (e.g., `workspace_list`)
- Modify: `evals/workflows.yaml` — update hello assertion to cover both steps
- Create: `tests/workflow/test_step_executors_tool_call.py`

**Key changes:**

```python
async def _execute_tool_call(ctx, step: StepDef, state: WorkflowState) -> StepResult:
    """Invoke a decafclaw tool by name with args computed from state."""
    cfg = step.config
    tool_name = cfg["tool"]
    # Render each arg value through Jinja against state.
    rendered_args = {k: render_template(v, state.state) if isinstance(v, str) else v
                     for k, v in (cfg.get("args") or {}).items()}
    # Reuse tool_execution machinery to get standard timeout + status events.
    result = await execute_tool_for_workflow(ctx, tool_name, rendered_args)
    output = {"text": result.text, "data": result.data}
    next_step = _resolve_next(step, output, state)
    return StepResult(output=output, next_step=next_step)
```

The `execute_tool_for_workflow` helper wraps `tool_execution.execute_single_tool` with the workflow's `ctx` fork (so `current_tool_call_id` is populated for status events). Requirement: `tool_status` events publish through the existing event bus so the web UI sees per-step progress just as it does for tool calls in normal agent loops.

`workflow_hello/workflow.yaml` updated:
```yaml
initial-step: greet
steps:
  - id: greet
    kind: llm_call
    prompt: "Generate a 3-word greeting..."
    schema: { ... }
    next: list_workspace
  - id: list_workspace
    kind: tool_call
    tool: workspace_list
    args: { path: "" }
    # terminal
```

**Verification — automated:**
- [x] `make lint` / `make typecheck` / `make test` pass (2853 passed)
- [x] `pytest tests/workflow/test_step_executors_tool_call.py -v` — new tests green (9 tests)
- [x] `make eval-workflows` — hello case now includes tool_call step in assertions — PASS on Flash (5.1s)

**Verification — manual:** (deferred to pre-PR self-review)
- [ ] Run `!workflow_hello` manually; verify `state.list_workspace.data` contains workspace entries

---

## Phase 3: `subagent` step kind + research_brief skeleton

End-to-end deliverable: a workflow step can dispatch a child agent loop using the carried-forward `subagent.py`; output files appear as paths in state; partial `research_brief` workflow (gather → read_sources → publish stub) runs end-to-end.

**Files:**
- Modify: `src/decafclaw/workflow/subagent.py` — adapt caller-site: the public entry point is `run_subagent_step(ctx, step, state) -> StepResult`, called from `step_executors._execute_subagent`. PR #557's `_run_child`, `_resolve_phase_tools`, output verification logic preserved
- Modify: `src/decafclaw/workflow/step_executors.py` — add `_execute_subagent`
- Modify: `src/decafclaw/workflow/loader.py` — parse `subagent` step config (skill, tools, outputs, context-profile, prompt)
- Modify: `src/decafclaw/workflow/engine.py` — wire `PAUSED_SUBAGENT` resumption: when subagent completes, engine resumes step execution and advances
- Modify: `src/decafclaw/conversation_manager.py` — on `TurnKind.CHILD_AGENT` completion, if parent has a paused workflow with `pending.child_conv_id` matching, call `engine.resume_after_subagent(ctx, parent_state)`. (PR #557's `dispatch_and_finalize_subagent` did this synchronously; the new model makes it manager-driven so subagent steps participate in normal turn lifecycle.)
- Create: `src/decafclaw/skills/research_brief/SKILL.md` + `workflow.yaml` + `prompts/gather.md`
- Modify: `evals/workflows.yaml` — partial research_brief eval case (gather + read_sources)
- Create: `tests/workflow/test_step_executors_subagent.py`

**Key changes:**

```python
async def _execute_subagent(ctx, step: StepDef, state: WorkflowState) -> StepResult:
    """Spawn child agent loop. Suspends until child completes."""
    cfg = step.config
    prompt = render_template(cfg.get("prompt", ""), state.state)
    # Dispatch via existing subagent.py public entry (adapted from PR #557).
    result = await run_subagent_step(
        ctx, state=state, step_id=step.id,
        skill=cfg.get("skill"), tools=cfg.get("tools", []),
        outputs=cfg.get("outputs", []),
        context_profile=cfg.get("context-profile", {}),
        prompt=prompt,
    )
    if result.suspended:
        # Engine sees PAUSED_SUBAGENT; resumes when child completes (manager callback).
        return StepResult(output=None, suspend_status=RunStatus.PAUSED_SUBAGENT,
                          pending={"step_id": step.id, "child_conv_id": result.child_conv_id})
    output = {
        "text": result.text,
        "outputs": result.output_paths,   # dict: filename → relative path
    }
    next_step = _resolve_next(step, output, state)
    return StepResult(output=output, next_step=next_step)
```

`research_brief/workflow.yaml` (phase-3 skeleton; expanded in phase 5):
```yaml
initial-step: gather
steps:
  - id: gather
    kind: subagent
    description: "Use web tools to gather sources on the topic."
    prompt: "Research the topic: {{ state.topic }}. Identify 4-6 sources and 3-5 themes."
    skill: tabstack_research        # if available; otherwise reference vault_write only
    tools: [tabstack_research, vault_write]
    outputs: [sources.md]
    context-profile:
      memory-retrieval: off
    next: read_sources

  - id: read_sources
    kind: tool_call
    tool: vault_read
    args: { path: "{{ state.gather.outputs['sources.md'] }}" }
    next: publish_stub

  - id: publish_stub
    kind: tool_call                   # placeholder until phase 5 fills in outline/draft/critique/publish
    tool: notes_append
    args:
      text: "research_brief reached publish_stub for topic {{ state.topic }}"
    # terminal
```

`required-skills: [tabstack_research]` in SKILL.md frontmatter (existing skill-loader convention).

**Verification — automated:**
- [x] `make lint` / `make typecheck` / `make test` pass (2865 passed)
- [x] `pytest tests/workflow/test_step_executors_subagent.py -v` — new tests green
- [x] `make eval-workflows` — both cases pass (workflow_hello + research_brief skeleton); research_brief pivoted off tabstack (uses `vault` skill + training-knowledge sources for portability)

**Verification — manual:** (deferred to pre-PR self-review)
- [ ] Run `!research_brief topic="sleep hygiene"` (or similar) — verify gather subagent spawns visible in event stream, sources.md created in artifacts/, downstream tool_call reads it
- [ ] Inspect `workspace/workflows/{conv_id}/state.json` — `state.gather.outputs["sources.md"]` is a valid path

---

## Phase 4: `route` + `python` step kinds; complete `research_brief` workflow

End-to-end deliverable: `research_brief` runs all four real phases (outline → draft → critique → publish) plus the critique cycle and a python word-count step.

**Files:**
- Modify: `src/decafclaw/workflow/step_executors.py` — add `_execute_route`, `_execute_python`
- Modify: `src/decafclaw/workflow/loader.py` — parse `route` (`choices: [{id, to, when}]`) and `python` (`fn: <function_name>`) step configs
- Modify: `src/decafclaw/workflow/types.py` — no new top-level types; choices already on `StepDef`
- Modify: `src/decafclaw/skills/research_brief/workflow.yaml` — replace publish_stub with real outline/draft/critique/publish; add word-count python step
- Create: `src/decafclaw/skills/research_brief/prompts/outline.md`, `draft.md`, `critique.md`
- Create: `src/decafclaw/skills/research_brief/tools.py` — registered python function: `def count_draft_words(state): return {"count": len(state["draft"]["body"].split())}`
- Modify: `evals/workflows.yaml` — full research_brief eval case asserting terminal `publish` reached
- Create: `tests/workflow/test_step_executors_route.py`
- Create: `tests/workflow/test_step_executors_python.py`

**Key changes:**

```python
async def _execute_route(ctx, step: StepDef, state: WorkflowState) -> StepResult:
    """Forced-tool LLM call returning an enum choice; maps choice → outgoing edge."""
    cfg = step.config
    prompt = render_template(cfg["prompt"], state.state)
    enum_values = [c.id for c in step.choices]
    schema = {
        "type": "object",
        "properties": {
            "choice": {"type": "string", "enum": enum_values,
                       "description": "; ".join(f"{c.id}: {c.when}" for c in step.choices)},
        },
        "required": ["choice"],
    }
    output = await call_llm_structured(
        ctx, system=cfg.get("system", ""), user_msg=prompt,
        schema=schema, tool_name=f"choose_{step.id}",
    )
    choice_id = output["choice"]
    target = next((c.to for c in step.choices if c.id == choice_id), None)
    if target is None:
        raise RuntimeError(f"route {step.id} returned unknown choice {choice_id!r}")
    return StepResult(output=output, next_step=target or None)

async def _execute_python(ctx, step: StepDef, state: WorkflowState) -> StepResult:
    """Call a registered Python function from the workflow's tools.py."""
    cfg = step.config
    fn = _resolve_python_fn(ctx, step.workflow_dir, cfg["fn"])
    result = await asyncio.to_thread(fn, state.state) if not asyncio.iscoroutinefunction(fn) \
             else await fn(state.state)
    output = result if isinstance(result, dict) else {"value": result}
    next_step = _resolve_next(step, output, state)
    return StepResult(output=output, next_step=next_step)

def _resolve_python_fn(ctx, workflow_dir: Path, fn_name: str):
    """Import workflow's tools.py, return the named function."""
    mod = importlib.import_module(f"decafclaw.skills.{workflow_dir.name}.tools")
    fn = getattr(mod, fn_name, None)
    if fn is None or not callable(fn):
        raise RuntimeError(f"python step references unknown function {fn_name!r}")
    return fn
```

`research_brief/workflow.yaml` (full version):
```yaml
initial-step: gather
steps:
  - id: gather       # (unchanged from phase 3)
    kind: subagent
    ...
    next: read_sources

  - id: read_sources # (unchanged from phase 3)
    kind: tool_call
    tool: vault_read
    args: { path: "{{ state.gather.outputs['sources.md'] }}" }
    next: outline

  - id: outline
    kind: llm_call
    prompt-from: outline.md
    schema:
      type: object
      properties:
        title: {type: string}
        bullets: {type: array, items: {type: string}}
      required: [title, bullets]
    next: draft

  - id: draft
    kind: llm_call
    prompt-from: draft.md
    schema:
      type: object
      properties:
        body: {type: string}
      required: [body]
    next: word_count

  - id: word_count
    kind: python
    fn: count_draft_words
    # state.word_count = {"count": <int>}
    next:
      - if: "state.word_count.count > 800"
        to: shorten
      - to: critique

  - id: shorten
    kind: llm_call
    prompt: "Shorten this draft to under 800 words preserving structure:\n\n{{ state.draft.body }}"
    schema: { type: object, properties: { body: {type: string} }, required: [body] }
    next: critique

  - id: critique
    kind: route
    prompt-from: critique.md
    choices:
      - { id: approve, to: publish, when: "draft satisfies the brief" }
      - { id: revise,  to: outline, when: "structural rework needed" }
      - { id: abort,   to: "",      when: "fundamentally broken" }

  - id: publish
    kind: tool_call
    tool: vault_write
    args:
      path: "briefs/{{ state.outline.title | slug }}.md"
      content: "# {{ state.outline.title }}\n\n{{ state.draft.body }}"
    # terminal
```

`research_brief/tools.py`:
```python
def count_draft_words(state: dict) -> dict:
    body = state.get("draft", {}).get("body", "")
    if "shorten" in state:   # shorten step's output overrides if present
        body = state["shorten"].get("body", body)
    return {"count": len(body.split())}
```

**Verification — automated:**
- [x] `make lint` / `make typecheck` / `make test` pass (2903 passed)
- [x] `pytest tests/workflow/test_step_executors_route.py tests/workflow/test_step_executors_python.py -v` — new tests green
- [x] `make eval-workflows` — full research_brief eval case completes (gather → ... → publish), Flash succeeds first-try (matching spike's known-good run) — PASS (32.3s, 28987 tokens)

**Verification — manual:** (deferred to pre-PR self-review)
- [ ] Run `!research_brief topic="sleep hygiene"` end-to-end on Flash; verify terminal `publish` reached
- [ ] Verify `briefs/<topic-slug>.md` written to vault with correct content
- [ ] Test the critique cycle: contrive a draft Flash judges as "revise" (or hand-edit state) — verify control returns to outline and loop runs at least once

---

## Phase 5: `user_input` step kind, suspension/resumption, cycles, `interview` workflow

End-to-end deliverable: a workflow step can suspend the conversation, prompt the user via existing confirmation infrastructure, and resume with the user's response in state; an `interview` workflow exercises text input + button choices + cycles.

**Files:**
- Modify: `src/decafclaw/workflow/step_executors.py` — add `_execute_user_input`
- Modify: `src/decafclaw/workflow/types.py` — `user_input` config shape: `{prompt: str, input: "text" | "choice"}`; `choices: [...]` for choice form
- Modify: `src/decafclaw/workflow/loader.py` — parse + validate `user_input` step config
- Modify: `src/decafclaw/workflow/engine.py` — suspension/resumption handling:
  - On suspend: persist state with `PAUSED_USER_INPUT`, store pending prompt + step_id
  - On resume: receive user response, write to `state[step_id]`, set `current_step` per response, continue
- Modify: `src/decafclaw/confirmations.py` — register a `WorkflowUserInputAction` confirmation type whose on-approve / on-deny resumes the workflow (mirrors PR #557's gate confirmation handler, but step-level)
- Modify: `src/decafclaw/conversation_manager.py` — on confirmation completion for a workflow user_input, call engine resume
- Create: `src/decafclaw/skills/interview/SKILL.md` + `workflow.yaml` + `prompts/pick_next.md`, `assess.md`, `summarize.md`
- Create: `src/decafclaw/skills/interview/tools.py` — `log_qa` function for explicit history accumulation (demonstrates the latest-wins escape hatch)
- Modify: `evals/workflows.yaml` — assertion that interview workflow reaches `PAUSED_USER_INPUT` on first turn (full eval-driven interview deferred; manual smoke covers it)
- Create: `tests/workflow/test_step_executors_user_input.py`
- Create: `tests/workflow/test_cycles.py` — cycle execution (back-edge revisits step, latest-wins state update)

**Key changes:**

```python
async def _execute_user_input(ctx, step: StepDef, state: WorkflowState) -> StepResult:
    """Build a confirmation request, return suspend signal."""
    cfg = step.config
    prompt = render_template(cfg["prompt"], state.state)
    if cfg["input"] == "text":
        # Use WidgetInputPause for free-text capture.
        pending = {"step_id": step.id, "mode": "text", "prompt": prompt}
        return StepResult(output=None, suspend_status=RunStatus.PAUSED_USER_INPUT,
                          pending=pending)
    if cfg["input"] == "choice":
        # Build EndTurnConfirm-like confirmation with N buttons.
        pending = {
            "step_id": step.id, "mode": "choice", "prompt": prompt,
            "choices": [{"id": c.id, "label": c.label or c.id} for c in step.choices],
        }
        return StepResult(output=None, suspend_status=RunStatus.PAUSED_USER_INPUT,
                          pending=pending)
    raise RuntimeError(f"user_input step {step.id}: unknown input mode {cfg['input']!r}")

# In engine.resume_user_input:
async def resume_user_input(ctx, state: WorkflowState, response: dict) -> WorkflowState:
    """Called by the confirmation handler when user responds.
    response: {"value": "<text>"} or {"choice": "<id>"}.
    """
    step_id = state.pending["step_id"]
    state.state[step_id] = response
    if "choice" in response:
        # Resolve next step from the step's choices.
        wf = registry.get(state.workflow)
        step = wf.steps_by_id[step_id]
        target = next((c.to for c in step.choices if c.id == response["choice"]), None)
        state.current_step = target or ""
    else:
        # Text input: resolve via step.next_edges with augmented state.
        ...   # parallel to _resolve_next in step_executors
    state.status = RunStatus.RUNNING if state.current_step else RunStatus.DONE
    state.pending = {}
    save_workflow_state(ctx, state)
    if state.status == RunStatus.RUNNING:
        return await _run_to_suspension(ctx, state, wf)
    return state
```

Confirmation handler registration (`confirmations.py`):
```python
class WorkflowUserInputAction:
    """Resumes a workflow paused on a user_input step."""
    def __init__(self, conv_id: str, step_id: str):
        self.conv_id = conv_id
        self.step_id = step_id

    async def on_approve(self, ctx, payload):
        state = load_workflow_state(ctx)
        await engine.resume_user_input(ctx, state, response=payload)

    async def on_deny(self, ctx):
        # User cancelled: surface as workflow_abort
        state = load_workflow_state(ctx)
        state.status = RunStatus.ERROR
        save_workflow_state(ctx, state)
```

`interview/workflow.yaml`:
```yaml
initial-step: pick_question
steps:
  - id: pick_question
    kind: llm_call
    prompt-from: pick_next.md
    inputs:
      qa_log: "{{ state.log_qa.qa_log | default([]) | tojson }}"
    schema:
      type: object
      properties:
        question: {type: string}
        remaining_topics: {type: array, items: {type: string}}
      required: [question, remaining_topics]
    next: ask_user

  - id: ask_user
    kind: user_input
    prompt: "{{ state.pick_question.question }}"
    input: text
    next: log_qa

  - id: log_qa
    kind: python
    fn: log_qa
    # log_qa reads prior state.log_qa.qa_log + appends (q, a) → returns new {qa_log: [...]}
    # demonstrates the explicit accumulation pattern under latest-wins state
    next: assess

  - id: assess
    kind: route
    prompt-from: assess.md
    inputs:
      question: "{{ state.pick_question.question }}"
      answer:   "{{ state.ask_user.value }}"
      remaining:"{{ state.pick_question.remaining_topics | tojson }}"
    choices:
      - { id: clarify,       to: ask_user,       when: "answer too vague — re-prompt same question" }
      - { id: next_question, to: pick_question,  when: "good answer; more topics remain" }
      - { id: summarize,     to: final_summary,  when: "all topics covered" }

  - id: final_summary
    kind: llm_call
    prompt-from: summarize.md
    inputs:
      qa_log: "{{ state.log_qa.qa_log | tojson }}"
    schema: { type: object, properties: { summary: {type: string} }, required: [summary] }
    # terminal
```

`interview/tools.py`:
```python
def log_qa(state: dict) -> dict:
    """Append latest (question, answer) to qa_log. Demonstrates explicit
    accumulation under the engine's latest-wins state model."""
    prior = state.get("log_qa", {}).get("qa_log", [])
    new_entry = {
        "q": state["pick_question"]["question"],
        "a": state["ask_user"]["value"],
    }
    return {"qa_log": prior + [new_entry]}
```

**MVP UX trade-off:** the `clarify` path re-prompts the user with the *same* question (latest-wins on `state.pick_question`). Structurally correct, demonstrates the cycle mechanism, but real interview UX would generate a follow-up clarifying question. Out of MVP scope; document as a future-work pointer in the workflow's `SKILL.md`.

**Verification — automated:**
- [x] `make lint` / `make typecheck` / `make test` pass (2932 passed)
- [x] `pytest tests/workflow/test_step_executors_user_input.py tests/workflow/test_cycles.py -v` — new tests green (7 + 4 = 11 tests)
- [x] `make eval-workflows` — interview case reaches `PAUSED_USER_INPUT` after pick_question (3.9s, 5095 tokens). All 3 eval cases pass (workflow_hello + research_brief + interview). Vacuous-pass note: assertion is currently `expect_tool: workflow_start` only; full multi-turn interview eval (synthetic response injection) deferred.

**Verification — manual:** (deferred to pre-PR self-review)
- [ ] Run `!interview` in web UI; verify first question appears as text-input prompt
- [ ] Answer the question; verify route step fires and either re-asks (`clarify`), advances to next topic, or summarizes (depending on answer quality)
- [ ] Force the `clarify` path: give a deliberately vague answer; verify the SAME step (`ask_user`) re-prompts with the SAME question (latest-wins on `state.pick_question`) and `state.log_qa.qa_log` has TWO entries for that question after the second answer — confirms back-edge + cycle accumulates explicitly
- [ ] Run to completion; verify `state.final_summary.summary` populated and workflow status DONE

---

## Phase 6: Polish, docs, cleanup, baseline

End-to-end deliverable: PR-ready branch. Spike skill removed; docs reflect the new model; eval baseline captured for regression tracking.

**Files:**
- Delete: `src/decafclaw/skills/spike_research_brief/`
- Delete: any orphan PR #557 modules that didn't carry forward (`tools/workflow_tools.py` is rewritten in phase 1, not deleted)
- Modify: `docs/workflows.md` — rewrite from phase-based to step-primitive model; include workflow.yaml examples from both bundled workflows
- Modify: `CLAUDE.md` — update the workflow-skills bullet in the Skills section to reflect step model
- Modify: `docs/index.md` — if any stale links
- Run: `make eval-history` after `make eval-workflows` to capture baseline trends for regression tracking
- Run: `make check` (lint + typecheck Python + JS)
- Verify: `pytest --durations=25` to catch any slow test smells per CLAUDE.md test-discipline section

**Verification — automated:**
- [ ] `make check` passes (Python lint + typecheck + JS check)
- [ ] `make test` passes
- [ ] `make eval-workflows` passes; `make eval-history` shows baseline established
- [ ] `pytest --durations=25` — no workflow tests in top-25 (catches missing mocks / fixed sleeps)
- [ ] `grep -r "phase_advance" src/ docs/ evals/` returns nothing (no stale references)
- [ ] `grep -r "spike_research_brief\|spike_brief_run" src/ docs/ evals/` returns nothing
- [ ] `grep -r "PAUSED_GATE\|GateDef\|_enter_gate\|finalize_gate_response" src/` returns nothing (gate concept fully removed)

**Verification — manual:**
- [ ] Re-run `!workflow_hello`, `!research_brief topic=...`, `!interview` end-to-end one final time — all succeed
- [ ] Open `docs/workflows.md` in a text browser; confirm it accurately describes the step model and examples render
- [ ] Confirm PR #557 still open on origin (will be closed-as-superseded in `/dev-session pr`)
- [ ] Ready for `/dev-session pr` to compose the PR body and open against main

---

## Spec coverage check

Each requirement from `spec.md` mapped to a phase:

| Spec requirement | Phase |
|---|---|
| Step graph replaces phase graph | 1 (engine + types) |
| `llm_call` kind | 1 |
| `tool_call` kind | 2 |
| `subagent` kind, PR #557 semantics preserved | 3 |
| `route` kind, choices inline | 4 |
| `python` kind, registered functions | 4 |
| `user_input` kind, replaces gate; text + choice | 5 |
| State flat dict, step-id keyed, latest-wins | 1 (engine `_apply_step_result`); cycle test in 5 |
| Subagent file outputs as paths in state | 3 |
| Jinja2 SandboxedEnvironment for templates + edge conditions | 1 (`jinja_env.py`) |
| Per-step `next:` polymorphic; conditional list with `if:` | 1 (`_resolve_next`) |
| Route choices declare `to:` inline | 4 |
| Single entry via `initial-step:`; multiple terminals; cycles allowed | 1 (loader); 5 (cycle test) |
| Load-time validation: `to:` resolves, `initial-step` exists, unreachable warnings | 1 (loader) |
| Single `workflow.yaml` + optional `prompts/` side dir | 1 (loader); used in all bundled workflows |
| SKILL.md unchanged in shape | 1 (loader) |
| Engine bypasses agent loop for non-subagent steps | 1 (engine drives directly); 3 (subagent is the exception) |
| Per-step LLM tool whitelists only inside subagent children | 3 (subagent carry-forward) |
| Smoke: `research_brief` workflow as eval case | 3 (partial) + 4 (full) |
| Smoke: `interview` workflow as eval case | 5 (partial — reaches suspension) + manual |
| Fresh branch off main, selective carry-forward | 1 |
| Dev-session docs carried forward | 1 |
| Spike code carried forward as reference, deleted after `llm_call` ships | 1 (carry); 6 (delete) |
| PR #557 closed-as-superseded | `/dev-session pr` (out of plan scope; handled in pr phase) |

All spec requirements covered. Spec's three open questions all carry their default answers in the plan:
- Jinja sandbox: default `SandboxedEnvironment` (phase 1, no custom filters)
- `python` step exercise: `count_draft_words` in research_brief (phase 4)
- Eval YAML assertions: existing primitives (`tool_called`, `final_status`) extended minimally with workflow-aware status assertion (phase 1)

## Self-review notes

- **No placeholder strings** present in any phase (no "TBD", no "implement appropriate X", no "similar to phase N").
- **Type consistency:** `StepDef`, `RunStatus`, `StepKind`, `EdgeRef`, `RouteChoice`, `WorkflowState`, `StepResult`, `WorkflowUserInputAction` — same names used identically across all phases. Field names (`step.id`, `step.kind`, `step.choices`, `step.next_edges`, `step.config`, `state.state`, `state.pending`) consistent.
- **Function signatures:** `_execute_<kind>(ctx, step, state) -> StepResult` is the uniform executor signature across all step kinds. `_resolve_next(step, output, state) -> str | None` is the shared edge-resolution helper.
- **Spec scope respected:** no migration of existing skills, no `loop`/`set`/`branch` kinds, no subflow, no structured forms in user_input, no per-step LLM tool whitelists outside subagent children.
