# Workflow ↔ Conversation Integration — Implementation Plan

**Goal:** Make workflows a peer to agent turns in `ConversationManager` — eliminate every surface where the LLM could drive workflow control flow.

**Approach:** Fresh branch off `main` with selective carry-forward from PR #572's branch (`feat/255-workflow-step-primitive`). Five vertical slices, each adding one end-to-end capability. Live integration smoke testing via `decafclaw-client` is a load-bearing gate, not a polish-phase concern.

**Tech stack:** Python 3.13, `asyncio`, Jinja2 (`SandboxedEnvironment`), existing `decafclaw` `ConversationManager` + `ConfirmationRequest` infrastructure + `EventBus` + `Context` types.

**Branch:** new branch `feat/255-workflow-conv-integration` off `origin/main`. PR #572's engine code carries forward; the integration is rebuilt.

**Module layout target (`src/decafclaw/workflow/`):**
```
__init__.py        # public exports
types.py           # carry forward unchanged from PR #572
loader.py          # carry forward unchanged
engine.py          # carry forward + rewrite resume_user_input (delete)
step_executors.py  # carry forward + rewrite _execute_user_input (uses ctx.request_confirmation)
                   #                 + delete _make_on_response, _resume_workflow
                   #                 + delete _build_paused_tool_result
jinja_env.py       # carry forward unchanged
conv_state.py      # carry forward (likely unchanged)
subagent.py        # carry forward unchanged
registry.py        # carry forward unchanged
emit.py            # NEW: workflow_message emission helper
```

LLM-facing tools at `src/decafclaw/tools/workflow_tools.py`: thin rewrite — only `workflow_status`, `workflow_abort`, `workflow_artifact_read`, `workflow_artifact_write`. **No `workflow_start`.**

Tests at `tests/workflow/`: carry-forward unit tests (~150 from PR #572) adapt to the new manager-driven dispatch where needed. New live integration smoke at `tests/integration/test_workflow_smoke.py` (or similar) using `python -m decafclaw.client`.

---

## Phase 1: Foundation — new branch, carry-forward, `WORKFLOW_RUN` dispatch, `workflow_hello` working

End-to-end deliverable: `/workflow_hello topic=foo` typed in web UI dispatches via the new command-mode path, runs through the engine (llm_call → tool_call), and reaches `RunStatus.DONE`. No `workflow_message` emission yet (Phase 2); no user_input (Phase 3); the workflow tool surface is empty for the LLM. The test of correctness is whether the workflow state on disk shows DONE with both steps' output populated.

**Files:**

- Create branch `feat/255-workflow-conv-integration` off `origin/main`; worktree at `.claude/worktrees/feat-255-workflow-conv-integration/`
- Carry forward from `feat/255-workflow-step-primitive`:
  - `src/decafclaw/workflow/{types,loader,engine,step_executors,jinja_env,conv_state,subagent,registry}.py`
  - `src/decafclaw/skills/{workflow_hello,research_brief,interview}/` (the three bundled workflows + their prompts + tools.py)
  - `tests/workflow/` (all ~150 tests; some will need fixture updates in later phases)
  - `evals/workflows.yaml` (the eval cases — adapt where they assert tool calls that no longer exist, e.g., `expect_tool: workflow_start` becomes a different assertion in Phase 5)
  - `pyproject.toml` jinja2 dep (verify it's added; uv add jinja2 if needed)
  - `Makefile` eval-workflows target
  - Carry forward dev-session docs: this session's full dir + the prior `2026-06-01-1055-workflow-step-primitive-design/` for reference
- Create: `src/decafclaw/workflow/emit.py` — stub for now; Phase 2 fills it in
- Modify: `src/decafclaw/conversation_manager.py` — add `TurnKind.WORKFLOW_RUN`, extend the manager's turn dataclass to carry `workflow_name` and `initial_state`, branch in `_start_turn`
- Modify: `src/decafclaw/commands.py` — add `mode="workflow"` branch in `execute_command` when matched skill is `kind: workflow`
- Modify: `src/decafclaw/web/websocket.py` — handle `mode="workflow"` in command dispatch handler
- Modify: `src/decafclaw/mattermost.py` — same as web for workflow mode
- Modify: `src/decafclaw/tools/workflow_tools.py` — carry forward from PR #572 AS-IS for now (all five tools including workflow_start still defined). Phase 4 trims to the thin shape. This keeps the registration intact so unrelated Phase-1 work doesn't have to handle missing tool registrations.
- Modify: `src/decafclaw/tools/__init__.py` — WORKFLOW_TOOLS / WORKFLOW_TOOL_DEFINITIONS registration stays as-is, but REMOVE just `workflow_start` from both. Other four tools still register. Phase 4 deletes the now-orphaned `workflow_start` function definition entirely.
- Modify: `src/decafclaw/workflow/step_executors.py` — make `_execute_user_input` raise `NotImplementedError("user_input requires Phase 3 wiring")` so Phase-1 tests for non-user-input steps still pass cleanly
- Modify: `src/decafclaw/workflow/engine.py` — delete `resume_user_input` and `resume_after_subagent` if present (Phase 3 doesn't need resume_user_input under the new model; subagent dispatch is sync per PR #572 Phase-3 design, no resume needed in production)
- Modify: `src/decafclaw/workflow/__init__.py` — drop `resume_user_input` from exports if present
- Tests:
  - Update `tests/workflow/test_engine.py` — drop tests for `resume_user_input` (no longer exists); keep `_run_to_suspension` tests; verify `start_workflow` accepts `initial_state` param
  - Update `tests/workflow/test_step_executors_llm_call.py`, `..._tool_call.py`, `..._route.py`, `..._python.py`, `..._subagent.py` — these should pass unchanged from PR #572 since the executors are unchanged
  - Delete `tests/workflow/test_step_executors_user_input.py` (Phase 3 rewrites with new implementation)
  - Delete `tests/workflow/test_cycles.py` (cycle tests pass through user_input; Phase 3 reintroduces relevant subset)
  - Delete `tests/workflow/test_workflow_tools.py` and `..._user_input.py` (Phase 5 introduces new minimal tool tests)
  - Create `tests/workflow/test_workflow_run_dispatch.py` — verifies the new `TurnKind.WORKFLOW_RUN` branching in `_start_turn`
  - Create `tests/workflow/test_command_workflow_mode.py` — verifies `commands.dispatch_command` returns `mode="workflow"` for `kind: workflow` skills

**Key changes:**

`conversation_manager.py` (additions):

```python
class TurnKind(str, Enum):
    USER = "user"
    HEARTBEAT_SECTION = "heartbeat_section"
    SCHEDULED_TASK = "scheduled_task"
    CHILD_AGENT = "child_agent"
    WAKE = "wake"
    WORKFLOW_RUN = "workflow_run"   # NEW

@dataclass
class _QueuedTurn:
    # existing fields: kind, prompt, history, ...
    workflow_name: str = ""        # NEW (used when kind == WORKFLOW_RUN)
    initial_state: dict = field(default_factory=dict)  # NEW

async def enqueue_turn(
    self,
    conv_id: str,
    *,
    kind: TurnKind,
    prompt: str = "",
    history: list | None = None,
    workflow_name: str = "",      # NEW
    initial_state: dict | None = None,  # NEW
    ...,
) -> asyncio.Future:
    # Existing signature plus the two new kwargs. Pass through to _QueuedTurn.
    ...

async def _start_turn(self, state, turn) -> None:
    """Dispatch the turn based on kind."""
    ctx = _build_ctx_for_turn(self.config, state, turn)
    if turn.kind == TurnKind.WORKFLOW_RUN:
        from .workflow import engine
        await engine.start_workflow(
            ctx, turn.workflow_name, initial_state=turn.initial_state or {}
        )
    else:
        await run_agent_turn(ctx, turn.prompt, list(turn.history or []))
```

`commands.py:execute_command` (new branch):

```python
def execute_command(ctx, command_name: str, arguments: str, *, dispatch_inline: bool) -> dict:
    skill = find_command(ctx.config, command_name)
    if skill is None:
        return {"mode": "unknown", ...}

    if skill.kind == "workflow":
        return {
            "mode": "workflow",
            "workflow_name": skill.name,
            "args": _parse_args(arguments),
        }

    # ... existing branches for inline / fork ...
```

`_parse_args` helper:

```python
def _parse_args(arg_string: str) -> dict:
    """Parse `key=value key2=value2` into a dict. Bare tokens land in `_positional`."""
    result = {}
    positional = []
    for token in arg_string.split():
        if "=" in token:
            key, _, value = token.partition("=")
            result[key.strip()] = value.strip()
        else:
            positional.append(token)
    if positional:
        result["_positional"] = positional
    return result
```

`web/websocket.py` (new branch in the existing command-dispatch handler around line 309-362):

```python
cmd_result = await dispatch_command(cmd_ctx, message_text)

if cmd_result["mode"] == "workflow":
    await manager.enqueue_turn(
        conv_id=conv_id,
        kind=TurnKind.WORKFLOW_RUN,
        workflow_name=cmd_result["workflow_name"],
        initial_state=cmd_result["args"],
    )
    return

# ... existing handling for inline / fork / help / etc. ...
```

`mattermost.py`: equivalent branch in its command dispatch handler.

`engine.py:start_workflow` (signature update):

```python
async def start_workflow(
    ctx, name: str, *, initial_state: dict | None = None,
) -> WorkflowState:
    """Initialize state with optional initial values, persist, and run to suspension."""
    wf = registry.get(name)
    if wf is None:
        raise RuntimeError(f"unknown workflow {name!r}")
    state = init_workflow_state(
        ctx, workflow=name, initial_step=wf.initial_step,
    )
    if initial_state:
        # Author-supplied initial state goes under a top-level key, NOT in the
        # step-keyed state.state dict. Templates reference as state.topic, etc.
        state.state.update(initial_state)
    save_workflow_state(ctx, state)
    return await _run_to_suspension(ctx, state, wf)
```

**Verification — automated:**
- [ ] `make lint` passes
- [ ] `make typecheck` passes
- [ ] `make test` passes
- [ ] `pytest tests/workflow/test_workflow_run_dispatch.py tests/workflow/test_command_workflow_mode.py -v` — new tests green
- [ ] `pytest tests/workflow/ -v` — carried-forward tests for engine/loader/types/jinja/non-user_input executors all pass; deleted-test files don't appear in collection

**Verification — manual:**
- [ ] Open web UI, type `/workflow_hello`, verify workflow_state.json shows status DONE with `state.greet` and `state.list_workspace` populated
- [ ] No errors in server logs; no agent loop turn invoked during the workflow

---

## Phase 2: `workflow_message` role + step emissions

End-to-end deliverable: `/workflow_hello` produces visible `workflow_message` records in the conversation archive, one per step. Web UI history load returns these records and renders them as workflow content (distinguishable from agent speech). LLM context remap embeds `[workflow:<name> step:<id>]` prefix. No user_input yet.

**Files:**

- Modify: `src/decafclaw/workflow/emit.py` — new helper `emit_workflow_message(ctx, workflow_name, step_id, content, widget=None, confirmation_id=None)` that calls `append_message` + `manager.emit`
- Modify: `src/decafclaw/workflow/engine.py` — `_apply_step_result` or per-kind hooks call `emit_workflow_message` with the step output. Final-step emission too.
- Modify: `src/decafclaw/workflow/step_executors.py` — pass enough info (content extracted from step output) to the emit helper. Decide per-kind:
  - `llm_call` → emit message with synthesized rendering of the structured output (default: `json.dumps(output, indent=2)` or, if `step.config.get("display")` provides a Jinja template, render that)
  - `tool_call` → emit message with `result.text`
  - `route` → emit message naming the chosen branch (e.g., `"Routed to: approve"`)
  - `python` → emit message with `json.dumps(return_value)` or a display template
  - `subagent` → emit message with child's final summary text
  - `user_input` → defer to Phase 3 (this phase doesn't emit user_input)
- Modify: `src/decafclaw/context_composer.py` — extend `ROLE_REMAP` (`context_composer.py:25-29`) with `workflow_message → user`. Add a content-transform helper that prepends `[workflow:<wf> step:<step>] ` to the message content before LLM context render.
- Modify: `src/decafclaw/archive.py` — verify `workflow_message` role is accepted by `append_message` (it should be — the function takes any role; just smoke-check with a test)
- Modify: `src/decafclaw/web/websocket.py` — verify `workflow_message` is NOT in `_HIDDEN_ROLES`. Add to history-emission projection if needed.
- Tests:
  - Create `tests/workflow/test_workflow_message_emit.py` — `emit_workflow_message` writes correct archive record + emits correct WebSocket event
  - Create `tests/workflow/test_workflow_message_role_remap.py` — `ROLE_REMAP` maps workflow_message → user with `[workflow:<name> step:<id>]` prefix in content
  - Update `tests/workflow/test_engine.py` — `_run_to_suspension` emits workflow_message per step
  - Update existing executor tests to verify each kind emits its workflow_message

**Key changes:**

`workflow/emit.py`:

```python
"""Server-side emission of workflow-driven conversation messages.

Mirrors the pattern used by `skills/background/tools.py:142-165` — append
to archive, emit to WebSocket subscribers. No agent turn, no LLM call.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from ..archive import append_message

log = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


async def emit_workflow_message(
    ctx,
    *,
    workflow_name: str,
    step_id: str,
    content: str,
    widget: dict | None = None,
    confirmation_id: str | None = None,
) -> dict:
    """Write a workflow_message record + emit to subscribers."""
    record: dict[str, Any] = {
        "role": "workflow_message",
        "workflow": workflow_name,
        "step": step_id,
        "content": content,
        "timestamp": _now_iso(),
    }
    if widget is not None:
        record["widget"] = widget
    if confirmation_id is not None:
        record["confirmation_id"] = confirmation_id

    append_message(ctx.config, ctx.conv_id, record)
    if ctx.manager is not None:
        await ctx.manager.emit(ctx.conv_id, {
            "type": "workflow_message",
            "record": record,
        })
    return record
```

`engine.py:_apply_step_result` (additions; merge with existing logic):

```python
def _apply_step_result(
    state: WorkflowState, step: StepDef, result: StepResult,
) -> None:
    if result.suspend_status is None:
        state.state[step.id] = result.output
    state.transitions.append({"step": step.id, "ts": _now_iso(), ...})
    # ... existing next_step / suspend handling ...

# AFTER applying state changes, call emit (only when step completes):
async def _maybe_emit_step_message(ctx, state, step, result) -> None:
    """Emit a workflow_message record for the completed step's user-visible
    content. Suspended steps emit their own message inside the executor
    (user_input does this in Phase 3); other kinds emit here."""
    if result.suspend_status is not None:
        return  # Suspended steps emit inside the executor
    content = _render_step_display(step, result.output)
    if content is None:
        return  # Step kind opted out of visible emission (rare)
    await emit_workflow_message(
        ctx, workflow_name=state.workflow, step_id=step.id, content=content,
    )

def _render_step_display(step: StepDef, output: Any) -> str | None:
    """Build human-visible content for the step's output. Per kind."""
    if step.kind == StepKind.LLM_CALL:
        # Default: serialize the structured output. Author can override
        # by including a `display` field in the step config that's a
        # Jinja template against the output dict.
        if "display" in step.config:
            return render_template(step.config["display"], {"output": output})
        return json.dumps(output, indent=2, ensure_ascii=False)
    if step.kind == StepKind.TOOL_CALL:
        return output.get("text") if isinstance(output, dict) else str(output)
    if step.kind == StepKind.ROUTE:
        return f"Routed: {output.get('choice', '<unknown>')}"
    if step.kind == StepKind.PYTHON:
        return json.dumps(output, indent=2, ensure_ascii=False, default=str)
    if step.kind == StepKind.SUBAGENT:
        return output.get("text", "")
    return None
```

`context_composer.py` (extend ROLE_REMAP):

```python
ROLE_REMAP: dict[str, str] = {
    "cancel_marker": "user",
    "turn_aborted": "user",
    "vault_retrieval": "user",
    "vault_references": "user",
    "conversation_notes": "user",
    "workflow_message": "user",  # NEW
}

# In the existing remap logic, when remapping workflow_message, transform content:
def _remap_message_content(msg: dict) -> dict:
    role = msg.get("role", "")
    if role == "workflow_message":
        wf = msg.get("workflow", "")
        step = msg.get("step", "")
        content = msg.get("content", "")
        return {
            **msg,
            "role": "user",
            "content": f"[workflow:{wf} step:{step}] {content}",
        }
    return msg
```

**Verification — automated:**
- [ ] `make lint` / `make typecheck` / `make test` pass
- [ ] `pytest tests/workflow/test_workflow_message_emit.py tests/workflow/test_workflow_message_role_remap.py -v` — new tests green
- [ ] Updated executor tests pass

**Verification — manual:**
- [ ] Run `/workflow_hello` in web UI; verify two workflow_message records appear in the conversation (one for `greet`, one for `list_workspace`)
- [ ] Open `workspace/conversations/{conv_id}.jsonl` and confirm role values + workflow + step metadata
- [ ] Confirm a follow-up regular user message triggers an agent turn that sees the prior workflow_messages in context (no LLM confusion)

---

## Phase 3: `user_input` step kind — `ctx.request_confirmation` based; delete `on_response` machinery

End-to-end deliverable: `/interview` walks through the full Q&A cycle on Flash — pick_question → ask_user (widget shown) → log_qa → assess → loop or summarize. Each user answer wakes the engine task directly via the existing `ConfirmationRequest` lifecycle. No on_response callback. No synthetic user-message injection. No race conditions.

**Files:**

- Modify: `src/decafclaw/confirmations.py` — add `ConfirmationAction.WORKFLOW_USER_INPUT` enum value (or repurpose `WIDGET_RESPONSE` — see Key changes)
- Modify: `src/decafclaw/workflow/step_executors.py` — rewrite `_execute_user_input` per Phase 3 design
- Modify: `src/decafclaw/workflow/engine.py` — verify the engine's `_run_to_suspension` correctly handles a step whose executor returns `StepResult` with non-None state changes vs awaits internally. Since the executor now AWAITS internally rather than returning a suspend signal, the executor returns a "completed" result (output = transformed user response) directly. The engine's loop continues.
- Modify: `src/decafclaw/widget_input.py` — verify widget attachment can carry over to workflow_message role (the existing widget infrastructure may not need changes if the engine emits widget as part of the workflow_message archive entry directly without going through resolve_widget). Document carefully.
- Modify: `src/decafclaw/web/websocket.py:_handle_widget_response` — currently calls `respond_to_confirmation` with the response. Verify this path works when the confirmation's action_type is `WORKFLOW_USER_INPUT`. Probably no changes needed (the manager doesn't switch on action_type for routing).
- Tests:
  - Create `tests/workflow/test_step_executors_user_input.py` — new tests for the rewritten `_execute_user_input`
  - Create `tests/workflow/test_cycles.py` — minimal: a workflow with a back-edge cycles correctly. Verify `_run_to_suspension` calls the same executor twice with different state. (Subset of PR #572's cycle tests, adapted.)
  - Update `tests/workflow/test_engine.py` — integration test running interview workflow with mocked confirmation responses

**Key changes:**

`confirmations.py` additions:

```python
class ConfirmationAction(str, Enum):
    # existing values
    WIDGET_RESPONSE = "widget_response"
    SKILL_PERMISSION = "skill_permission"
    SHELL_APPROVAL = "shell_approval"
    # NEW:
    WORKFLOW_USER_INPUT = "workflow_user_input"
```

We could reuse `WIDGET_RESPONSE` but the distinct enum value makes archive/log inspection clearer ("this confirmation was a workflow pause"). One-line addition.

`step_executors.py:_execute_user_input` (full rewrite):

```python
async def _execute_user_input(
    ctx, step: StepDef, state: WorkflowState,
) -> StepResult:
    """Suspend the workflow on a user_input step.

    Architecture: emit a workflow_message with the prompt + widget for
    visible display, AND create a ConfirmationRequest via
    ctx.request_confirmation for the routing wakeup. The engine awaits
    the response inline; when the user submits the widget, the existing
    respond_to_confirmation lifecycle wakes us up with response.data
    holding the widget values directly.

    No on_response callback. No synthetic user-message injection. The
    engine continues to the next step right here, with the user's
    response already in state.
    """
    cfg = step.config
    prompt = render_template(cfg["prompt"], state.state)

    # Build widget payload + confirmation_id.
    confirmation_id = secrets.token_hex(6)
    widget_payload = _build_user_input_widget(cfg, step)

    # Emit visible workflow_message with the widget attached.
    await emit_workflow_message(
        ctx,
        workflow_name=state.workflow,
        step_id=step.id,
        content=prompt,
        widget=widget_payload,
        confirmation_id=confirmation_id,
    )

    # Create + await routing record via the existing confirmation machinery.
    request = ConfirmationRequest(
        action_type=ConfirmationAction.WORKFLOW_USER_INPUT,
        action_data={
            "workflow": state.workflow,
            "step": step.id,
            "mode": cfg["input"] if "input" in cfg else "choice",
            "widget": widget_payload,
        },
        confirmation_id=confirmation_id,
    )
    response = await ctx.request_confirmation(request)

    if response is None or not response.approved:
        # Cancelled / denied — workflow goes to ERROR via the engine's
        # outer try/except.
        raise RuntimeError(
            f"workflow {state.workflow!r} user_input cancelled at step "
            f"{step.id!r}",
        )

    user_input = _transform_user_input(response.data, cfg.get("input", "choice"))
    next_step = _resolve_next_for_user_input(step, user_input, state)
    return StepResult(output=user_input, next_step=next_step)


def _build_user_input_widget(cfg: dict, step: StepDef) -> dict:
    """Translate the step config + choices into a widget_payload dict."""
    mode = cfg.get("input", "")
    prompt = cfg.get("prompt", "")
    if mode == "text":
        return {
            "widget_type": "text_input",
            "target": "inline",
            "data": {
                "prompt": prompt,
                "fields": [{
                    "key": "value", "label": "Your answer",
                    "multiline": True, "required": True,
                }],
            },
        }
    # Choice mode (no `input:` field, just `choices:`)
    if step.choices:
        options = [
            {"value": c.id, "label": c.label or c.id}
            for c in step.choices
        ]
        return {
            "widget_type": "multiple_choice",
            "target": "inline",
            "data": {"prompt": prompt, "options": options},
        }
    raise RuntimeError(
        f"user_input step {step.id!r}: must declare `input: text` or `choices:`"
    )


def _transform_user_input(response_data: dict, mode: str) -> dict:
    if mode == "text":
        return {"value": response_data.get("value", "")}
    selected = response_data.get("selected", "")
    if isinstance(selected, list):
        selected = selected[0] if selected else ""
    return {"choice": selected}


def _resolve_next_for_user_input(
    step: StepDef, user_input: dict, state: WorkflowState,
) -> str | None:
    """For choice-mode, choice's `to` is the next step; for text-mode,
    use the standard resolve_next over edge conditions."""
    if "choice" in user_input:
        choice_id = user_input["choice"]
        target = next((c.to for c in step.choices if c.id == choice_id), None)
        if target is None:
            raise RuntimeError(
                f"user_input step {step.id!r} got unknown choice {choice_id!r}"
            )
        return target or None
    # Text mode: standard edge resolution
    augmented = {**state.state, step.id: user_input}
    for edge in step.next_edges:
        if eval_condition(edge.if_expr, augmented):
            return edge.to or None
    return None
```

**Deletions** in this phase (clean cuts):

- `src/decafclaw/workflow/step_executors.py:_make_on_response` — delete
- `src/decafclaw/workflow/step_executors.py:_transform_response` — moved to `_transform_user_input` (inlined; same logic)
- `src/decafclaw/workflow/step_executors.py:_summary_for_response`, `_summary_for_completion`, `_build_confirmation_for_pause` — all dead under new model; delete
- `src/decafclaw/tools/workflow_tools.py:_build_paused_tool_result` — file is already deleted in Phase 1; double-check this stays gone
- Any `_resume_workflow` async helper — delete

**Verification — automated:**
- [ ] `make lint` / `make typecheck` / `make test` pass
- [ ] `pytest tests/workflow/test_step_executors_user_input.py tests/workflow/test_cycles.py -v` — new tests green
- [ ] `pytest tests/workflow/test_engine.py::test_interview_workflow_walks_full_cycle -v` — integration with mocked LLM + mocked confirmations
- [ ] `grep -r "on_response\|_make_on_response\|_resume_workflow\|_build_paused_tool_result" src/` returns nothing (deletions confirmed)

**Verification — manual:**
- [ ] Run `/interview` in web UI; first question appears as widget; respond → next question appears; respond → after enough rounds, summary appears; workflow status DONE
- [ ] Force the clarify path: deliberately vague answer → same question appears again (latest-wins state); `state.log_qa.qa_log` accumulates entries across cycles
- [ ] Type a regular message mid-interview; verify it queues until interview completes (busy-flag behavior)
- [ ] Inspect archive — verify workflow_message records appear with widget metadata and confirmation_ids matching the ConfirmationRequest records

---

## Phase 4: Thin `workflow_tools.py` + scheduled-task dispatch + heartbeat rejection

End-to-end deliverable: agent can invoke `workflow_status`, `workflow_abort`, `workflow_artifact_read`, `workflow_artifact_write` from regular turns. Scheduled tasks whose skill is `kind: workflow` dispatch as `TurnKind.WORKFLOW_RUN`. Heartbeat skills with `kind: workflow` are rejected at load time with a clear error.

**Files:**

- Create: `src/decafclaw/tools/workflow_tools.py` — thin rewrite. Only the four tools, no `workflow_start`.
- Modify: `src/decafclaw/tools/__init__.py` — re-add `WORKFLOW_TOOLS` / `WORKFLOW_TOOL_DEFINITIONS` registration (this time without workflow_start)
- Modify: `src/decafclaw/schedules.py` — `run_schedule_task` checks `skill.kind`; if "workflow", dispatch `TurnKind.WORKFLOW_RUN` with `workflow_name=skill.name` and `initial_state` from the task's args (if any). Else fall through to existing `SCHEDULED_TASK` path.
- Modify: `src/decafclaw/heartbeat.py` (or wherever heartbeat skills are loaded) — reject heartbeat skills with `kind: workflow`. Either at heartbeat-config-load time or at execution time with a clear error.
- Modify: `src/decafclaw/skills/__init__.py` — if it's the central registration point for `kind: workflow`, ensure heartbeat-context loads don't include them (or surface validation error)
- Tests:
  - Create `tests/workflow/test_workflow_tools.py` — verifies the four tools exist, workflow_start is NOT registered
  - Create `tests/workflow/test_schedule_workflow_dispatch.py` — schedule task with `kind: workflow` skill dispatches WORKFLOW_RUN
  - Create `tests/workflow/test_heartbeat_workflow_rejected.py` — heartbeat with workflow skill raises a clear loader/dispatcher error

**Key changes:**

`tools/workflow_tools.py` (new minimal version):

```python
"""LLM-callable workflow inspection / control tools.

Workflow initiation is NOT in this module — workflows start via /command
dispatch, scheduled tasks (kind: workflow), or subagent step. The LLM
should not initiate workflows mid-turn; that surface was the largest
control-flow risk in PR #572 and is removed by design.

Tools here:
- workflow_status: read-only inspection of active workflow state
- workflow_abort: kill switch for an active workflow (rare; user-driven)
- workflow_artifact_read/write: file I/O against the workflow's artifact dir
"""

from __future__ import annotations

import logging
from pathlib import Path

from ..media import ToolResult
from ..workflow.conv_state import (
    artifacts_dir, load_workflow_state, save_workflow_state, archive_workflow_state,
)
from ..workflow.types import RunStatus

log = logging.getLogger(__name__)


async def tool_workflow_status(ctx) -> ToolResult:
    """Inspect the active workflow's state, if any."""
    state = load_workflow_state(ctx)
    if state is None:
        return ToolResult(text="[no workflow active in this conversation]")
    return ToolResult(
        text=(
            f"Workflow {state.workflow!r} status: {state.status.value}\n"
            f"Current step: {state.current_step}\n"
            f"State keys: {list(state.state.keys())}"
        ),
        data={"workflow": state.workflow, "status": state.status.value,
              "current_step": state.current_step, "state": state.state},
    )


async def tool_workflow_abort(ctx, reason: str = "") -> ToolResult:
    """Abort the active workflow."""
    state = load_workflow_state(ctx)
    if state is None:
        return ToolResult(text="[no workflow to abort]")
    state.status = RunStatus.ERROR
    state.transitions.append({
        "step": state.current_step,
        "ts": _now_iso(),
        "aborted": True,
        "reason": reason or "user-initiated",
    })
    save_workflow_state(ctx, state)
    archive_workflow_state(ctx, state)
    # Also cancel the running workflow task if active.
    if ctx.manager and ctx.conv_id:
        await ctx.manager.cancel_active_turn(ctx.conv_id)
    return ToolResult(
        text=f"Workflow {state.workflow!r} aborted: {reason or '(no reason)'}",
    )


async def tool_workflow_artifact_read(ctx, path: str) -> ToolResult:
    """Read a file from the active workflow's artifact directory."""
    artifacts = artifacts_dir(ctx)
    full_path = (artifacts / path).resolve()
    try:
        full_path.relative_to(artifacts.resolve())
    except ValueError:
        return ToolResult(text=f"[error: path {path!r} escapes artifacts dir]")
    if not full_path.exists():
        return ToolResult(text=f"[error: artifact {path!r} not found]")
    try:
        content = full_path.read_text()
    except OSError as exc:
        return ToolResult(text=f"[error: could not read {path!r}: {exc}]")
    return ToolResult(text=content, data={"path": path, "size": len(content)})


async def tool_workflow_artifact_write(ctx, path: str, content: str) -> ToolResult:
    """Write content to a workflow artifact file."""
    artifacts = artifacts_dir(ctx)
    full_path = (artifacts / path).resolve()
    try:
        full_path.relative_to(artifacts.resolve())
    except ValueError:
        return ToolResult(text=f"[error: path {path!r} escapes artifacts dir]")
    full_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        full_path.write_text(content)
    except OSError as exc:
        return ToolResult(text=f"[error: could not write {path!r}: {exc}]")
    return ToolResult(text=f"Written {len(content)} bytes to {path!r}.")


WORKFLOW_TOOLS = {
    "workflow_status": tool_workflow_status,
    "workflow_abort": tool_workflow_abort,
    "workflow_artifact_read": tool_workflow_artifact_read,
    "workflow_artifact_write": tool_workflow_artifact_write,
}

WORKFLOW_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "workflow_status",
            "description": "Inspect the active workflow's state in this conversation.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
        "priority": "normal",
    },
    # ... similar for abort/read/write ...
]
```

`schedules.py:run_schedule_task` (kind-aware dispatch):

```python
async def run_schedule_task(config, manager, skill, task) -> None:
    # ... existing setup ...
    if skill.kind == "workflow":
        await manager.enqueue_turn(
            conv_id=conv_id,
            kind=TurnKind.WORKFLOW_RUN,
            workflow_name=skill.name,
            initial_state=_parse_args(task.arguments or ""),
            user_id=f"schedule-{task.source}",
        )
        return
    # ... existing SCHEDULED_TASK path ...
```

`heartbeat.py` (or skills loader for heartbeat context): reject workflow kind:

```python
def _validate_heartbeat_skill(skill: SkillInfo) -> None:
    if skill.kind == "workflow":
        raise ValueError(
            f"heartbeat skill {skill.name!r} cannot be kind: workflow; "
            f"heartbeats run as agent turns, not workflow runs"
        )
```

**Verification — automated:**
- [ ] `make lint` / `make typecheck` / `make test` pass
- [ ] `pytest tests/workflow/test_workflow_tools.py tests/workflow/test_schedule_workflow_dispatch.py tests/workflow/test_heartbeat_workflow_rejected.py -v` — new tests green
- [ ] `grep -rn "workflow_start" src/ tests/ evals/ 2>&1 | grep -v __pycache__` — returns NO matches in production code (eval YAML may still mention workflow_start as `expect_no_tool`, that's fine)

**Verification — manual:**
- [ ] Have agent call `workflow_status` outside an active workflow — returns "no workflow active"
- [ ] Run `/workflow_hello`, wait for completion, then have agent call `workflow_status` — returns DONE status
- [ ] Create a scheduled task pointing at `workflow_hello`; wait for it to fire; verify a WORKFLOW_RUN turn fires (not SCHEDULED_TASK)
- [ ] Attempt to register a heartbeat skill with `kind: workflow` — error surfaces at load time with clear message

---

## Phase 5: Live integration smoke + docs

End-to-end deliverable: `make smoke-workflows-live` runs the three bundled workflows end-to-end via `python -m decafclaw.client` against a real decafclaw server. CI can run this. Docs reflect the peer architecture.

**Files:**

- Create: `tests/integration/test_workflow_smoke.py` (or directly under `tests/integration/`) — pytest-driven smoke tests using `decafclaw-client`
- Create: `scripts/smoke_workflows.sh` (or Python module) — spins up a server, runs the client commands, asserts on output
- Modify: `Makefile` — add `smoke-workflows-live` target
- Modify: `docs/workflows.md` — rewrite to reflect the peer architecture. Specifically:
  - `kind: workflow` skills run as `WORKFLOW_RUN` turns, NOT agent turns
  - `/command` dispatch goes directly to the engine without an LLM call
  - workflow_messages are first-class conversation content with workflow-specific role
  - LLM tools: status/abort/artifact only; no workflow_start
  - Live smoke testing is required to merge
- Modify: `CLAUDE.md` (project root) — update the workflow-skills bullet to reflect the new architecture. Mention peer-turn-kind, workflow_message role, no workflow_start LLM tool.
- Modify: `docs/index.md` — verify workflow link is still correct
- Delete: any leftover SKILL.md imperative bodies in the three bundled workflows that referenced `workflow_start` as a tool (they were band-aids for the prior LLM-driven dispatch model)

**Key changes:**

`Makefile` target:

```makefile
smoke-workflows-live:
	@echo "Starting decafclaw server for smoke test..."
	bash scripts/smoke_workflows.sh
```

`scripts/smoke_workflows.sh` (sketch):

```bash
#!/usr/bin/env bash
set -euo pipefail

# Start decafclaw server on a free port; capture PID for cleanup
SMOKE_PORT=$(python -c "import socket; s=socket.socket(); s.bind(('', 0)); print(s.getsockname()[1]); s.close()")
DATA_HOME=$(mktemp -d) HTTP_PORT=$SMOKE_PORT MATTERMOST_ENABLED=false uv run python -m decafclaw &
SERVER_PID=$!
trap "kill $SERVER_PID 2>/dev/null || true; rm -rf $DATA_HOME" EXIT

# Wait for server to be ready
for i in {1..30}; do
  curl -s "http://localhost:$SMOKE_PORT/healthz" >/dev/null && break
  sleep 1
done

export DECAFCLAW_HOST="http://localhost:$SMOKE_PORT"
export DECAFCLAW_TOKEN="$(cat $DATA_HOME/*/token 2>/dev/null || echo dev-token)"

# 1) workflow_hello smoke
python -m decafclaw.client send --prompts "/workflow_hello" --fmt summary

# 2) interview smoke (multi-turn)
python -m decafclaw.client send --prompts "/interview" --fmt summary --conv interview-smoke
# Get the latest confirmation_id from the server (helper script TBD)
CONF_ID=$(...)
python -m decafclaw.client respond --confirmation-id $CONF_ID --conv interview-smoke \
    --fmt summary
# Loop: respond, get next confirmation, respond, until status complete

# 3) research_brief smoke
python -m decafclaw.client send --prompts "/research_brief topic=sleep hygiene" --fmt summary

echo "All workflow smoke tests passed"
```

The `decafclaw-client` already emits jsonl events when run with `--fmt jsonl`, and the existing `send` action's recorder captures `widget_request` / `confirmation_request` events. The smoke script switches to `--fmt jsonl`, pipes through `jq` to extract the most recent `confirmation_id` from a `widget_request` or `confirmation_request` event, and passes it to the next `respond` call. No new client subcommand needed.

`docs/workflows.md` (revised key passages):

```markdown
# Workflows

Workflows are a peer to agent turns in decafclaw. When you type `/workflow_name`,
the command dispatches directly to the workflow engine — the LLM is NOT invoked
to start the workflow.

## Architecture

The conversation manager tracks workflow runs as a new TurnKind:

- `TurnKind.WORKFLOW_RUN` runs the workflow engine instead of the agent loop
- A workflow run holds the conversation's busy flag for its entire lifetime
- Workflow-emitted messages use the `workflow_message` role (distinct from agent's `assistant`)
- User responses to workflow widgets route directly to the engine via `ConfirmationRequest`

The agent loop does NOT run during a workflow. After the workflow completes, queued
user turns process via the normal agent loop, with the workflow's Q&A visible in
context (remapped to user-role with `[workflow:<name> step:<id>]` prefix).

## Authoring

[... bundled workflows shown ...]

## LLM tool surface

Workflows expose four tools to the LLM (for agent turns to inspect/control state):

- `workflow_status`: read-only inspection
- `workflow_abort`: kill switch
- `workflow_artifact_read`: file read from artifacts dir
- `workflow_artifact_write`: file write

**`workflow_start` is not an LLM tool.** Workflows initiate via:
- `/command` dispatch (the primary path)
- `kind: workflow` scheduled tasks
- `subagent` step inside another workflow (referencing a regular skill, not another workflow — recursive workflow-as-subagent is future work)
```

**Verification — automated:**
- [ ] `make lint` / `make check` / `make test` pass
- [ ] `make smoke-workflows-live` passes (server starts, three workflows complete, exit code 0)
- [ ] `grep -rn "workflow_start" docs/ CLAUDE.md` shows only references that explicitly say it's REMOVED / not a tool

**Verification — manual:**
- [ ] Read through `docs/workflows.md` end-to-end — accurate and complete description of the peer architecture
- [ ] `make smoke-workflows-live` output is informative (so a CI failure tells you which workflow + which step broke)

---

## Spec coverage check

| Spec requirement | Phase |
|---|---|
| `TurnKind.WORKFLOW_RUN` branching dispatch in `_start_turn` | 1 |
| New command mode `workflow` in `dispatch_command` | 1 |
| Transport handlers route `mode=workflow` → `enqueue_turn(WORKFLOW_RUN)` | 1 |
| Carry-forward engine modules (types, loader, jinja, conv_state, registry, subagent) | 1 |
| Carry-forward bundled workflows | 1 |
| `workflow_message` role + widget attachment | 2 |
| `ROLE_REMAP: workflow_message → user` with metadata-tagged content | 2 |
| Server-side `emit_workflow_message` helper | 2 |
| `_execute_user_input` via `ctx.request_confirmation`; no callback / synthetic msg | 3 |
| Two records per pause sharing `confirmation_id` (display + routing) | 3 |
| `ConfirmationAction.WORKFLOW_USER_INPUT` enum value | 3 |
| Delete `on_response`, `_resume_workflow`, `_build_paused_tool_result` machinery | 3 |
| Thin LLM tool surface (status/abort/artifact only; no workflow_start) | 4 |
| Scheduled task dispatch via WORKFLOW_RUN for kind:workflow skills | 4 |
| Heartbeat workflows rejected | 4 |
| Live integration tests via `decafclaw-client` required to merge | 5 |
| Docs reflect peer architecture | 5 |
| Subagent step kind unchanged | 1 (carry-forward) |
| Off-workflow inputs use existing busy-flag queueing | 1 (no change needed) |
| Step error semantics enumerated | inherited from PR #572 engine (Phase 1 carry-forward) |
| Open question defaults applied | Inline across phases |

All spec requirements covered.

## Self-review notes

- **No placeholders.** Every "deferred to Phase N" reference is followed up in that phase. No "TBD" remains after the self-review pass — confirmation_id fetching is committed to the `--fmt jsonl` + `jq` parse approach.
- **Type consistency.** `TurnKind`, `WORKFLOW_RUN`, `ConfirmationAction.WORKFLOW_USER_INPUT`, `workflow_message`, `WorkflowState`, `StepResult`, `_execute_user_input` — all used consistently across phases.
- **Scope respected.** No step-primitive redesign. No UI rendering implementation beyond minimal differentiation. No workflow-as-subagent. No retroactive fixes to checklist/project/delegate_task.
- **Carry-forward strategy.** Phase 1 explicitly enumerates the files coming over from `feat/255-workflow-step-primitive`. Phase 1's deletion of `workflow_tools.py` is intentional and inverted in Phase 4 with the thin rewrite — separating "what gets removed" from "what gets added back" makes both phases focused.
