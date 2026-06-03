"""Workflow step executors — one per StepKind.

Each executor receives (ctx, step: StepDef, state: WorkflowState) and
returns a StepResult. The engine drives execution; this module has no
knowledge of the agent loop.

Implements: ``llm_call``, ``tool_call``, ``subagent``, ``route``,
``python``, ``user_input``.

The llm_call executor uses a forced-tool structured-output call: a single
tool schema the model MUST call, with one retry on narrate. See
``_call_structured`` below.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from decafclaw.llm import call_llm

from .jinja_env import eval_condition, render_template
from .subagent import run_subagent_step
from .types import RunStatus, StepDef, StepKind, WorkflowState

log = logging.getLogger(__name__)


@dataclass
class StepResult:
    """Return value from a step executor.

    - output: written to state[step.id] by the engine.
    - next_step: the step id to run next; None = engine terminates or
      looks at suspend_status.
    - suspend_status: set when the step wants to pause (user_input,
      subagent); the engine persists this and stops looping.
    - pending: data the engine stores in state.pending for resumption.
    """
    output: Any
    next_step: str | None = None
    suspend_status: RunStatus | None = None
    pending: dict = field(default_factory=dict)


# All step kinds are implemented; _KIND_PHASE is now empty.
_KIND_PHASE: dict = {}


async def execute(ctx, step: StepDef, state: WorkflowState) -> StepResult:
    """Dispatch to the appropriate executor for step.kind."""
    if step.kind == StepKind.LLM_CALL:
        return await _execute_llm_call(ctx, step, state)
    if step.kind == StepKind.TOOL_CALL:
        return await _execute_tool_call(ctx, step, state)
    if step.kind == StepKind.SUBAGENT:
        return await _execute_subagent(ctx, step, state)
    if step.kind == StepKind.ROUTE:
        return await _execute_route(ctx, step, state)
    if step.kind == StepKind.PYTHON:
        return await _execute_python(ctx, step, state)
    if step.kind == StepKind.USER_INPUT:
        return await _execute_user_input(ctx, step, state)
    raise NotImplementedError(
        f"Step kind {step.kind.value!r} (step {step.id!r}) is not implemented."
    )


async def _execute_route(ctx, step: StepDef, state: WorkflowState) -> StepResult:
    """Forced-tool LLM call returning an enum choice; maps choice → outgoing edge.

    This is the only surface where the LLM influences control flow in the
    engine. The model picks from a fixed enum declared in step.choices; the
    target step for each choice is determined by RouteChoice.to, not by the
    model.

    The schema exposed to the model has a single ``choice`` property with an
    enum of the declared choice ids. The property description concatenates each
    choice's ``when`` hint so the model understands what each id means.

    Tool name: ``choose_<step.id>``.
    Output written to state: ``{"choice": "<chosen_id>"}``.
    """
    cfg = step.config
    prompt = render_template(cfg.get("prompt", ""), state.state)
    system = cfg.get("system", "")

    enum_values = [c.id for c in step.choices]
    choice_description = "; ".join(
        f"{c.id}: {c.when}" for c in step.choices if c.when
    ) or "; ".join(enum_values)

    schema = {
        "type": "object",
        "properties": {
            "choice": {
                "type": "string",
                "enum": enum_values,
                "description": choice_description,
            },
        },
        "required": ["choice"],
    }
    tool_name = f"choose_{step.id}"

    output = await _call_structured(
        ctx,
        system=system,
        user_msg=prompt,
        schema=schema,
        tool_name=tool_name,
    )

    choice_id = output.get("choice", "")
    target = next((c.to for c in step.choices if c.id == choice_id), None)
    if target is None:
        raise RuntimeError(
            f"route step {step.id!r} returned unknown choice {choice_id!r}; "
            f"declared choices: {enum_values}"
        )

    return StepResult(output=output, next_step=target if target else None)


async def _execute_user_input(ctx, step: StepDef,
                               state: WorkflowState) -> StepResult:
    """Suspend the workflow and prompt the user for input.

    Two modes:
    - ``input: text`` — free-text capture via WidgetInputPause.
    - choices list (no ``input:`` key, or choices present) — button
      picker via the confirmation infrastructure.

    The engine stores the pending dict in ``state.pending`` and sets
    ``status = PAUSED_USER_INPUT``. Resumption is triggered by
    ``engine.resume_user_input`` when the user responds.
    """
    cfg = step.config
    prompt = render_template(cfg.get("prompt", ""), state.state)

    if cfg.get("input") == "text":
        pending = {
            "step_id": step.id,
            "mode": "text",
            "prompt": prompt,
        }
        return StepResult(
            output=None,
            suspend_status=RunStatus.PAUSED_USER_INPUT,
            pending=pending,
        )

    if step.choices:
        pending = {
            "step_id": step.id,
            "mode": "choice",
            "prompt": prompt,
            "choices": [
                {"id": c.id, "label": c.label or c.id}
                for c in step.choices
            ],
        }
        return StepResult(
            output=None,
            suspend_status=RunStatus.PAUSED_USER_INPUT,
            pending=pending,
        )

    raise RuntimeError(
        f"user_input step {step.id!r}: must have `input: text` or a "
        f"`choices:` list"
    )


async def _execute_python(ctx, step: StepDef, state: WorkflowState) -> StepResult:
    """Call a registered Python function from the workflow's tools.py.

    The function is resolved from the workflow skill's ``tools.py`` module
    via importlib. It receives the full workflow state dict and must return
    a dict (or a scalar that gets wrapped as ``{"value": <scalar>}``).

    Both sync and async functions are supported. Sync functions run directly
    via asyncio.to_thread to avoid blocking the event loop.

    Output shape: the dict returned by the function (or wrapped scalar).
    """
    cfg = step.config
    fn_name = cfg["fn"]
    workflow_dir = state.workflow  # workflow name → used to import tools module

    fn = _resolve_python_fn(workflow_dir, fn_name)

    if asyncio.iscoroutinefunction(fn):
        raw_result = await fn(state.state)
    else:
        raw_result = await asyncio.to_thread(fn, state.state)

    output = raw_result if isinstance(raw_result, dict) else {"value": raw_result}
    next_step = resolve_next(step, output, state)
    return StepResult(output=output, next_step=next_step)


def _resolve_python_fn(workflow_name: str, fn_name: str):
    """Import the workflow's tools.py and return the named callable.

    Imports ``decafclaw.skills.<workflow_name>.tools`` via importlib.
    Raises RuntimeError if the function is not found or not callable.
    """
    module_path = f"decafclaw.skills.{workflow_name}.tools"
    mod = importlib.import_module(module_path)
    fn = getattr(mod, fn_name, None)
    if fn is None or not callable(fn):
        raise RuntimeError(
            f"python step references unknown or non-callable function "
            f"{fn_name!r} in module {module_path!r}"
        )
    return fn


async def _execute_tool_call(ctx, step: StepDef,
                              state: WorkflowState) -> StepResult:
    """Invoke a decafclaw tool by name with args computed from state.

    Each string arg value is rendered through Jinja against the workflow
    state. Non-string values are passed through as-is. The tool is
    invoked via a forked ctx so ``current_tool_call_id`` is set for
    status events — tool_start/tool_end events publish through the
    existing event bus so the web UI sees per-step progress.

    Note: Jinja rendering applies only to top-level string values in
    ``args``. Nested dicts/lists pass through unrendered. If you need a
    nested string interpolated, hoist it to a top-level arg or compute
    it in a ``python`` step (Phase 4).

    Output shape: ``{"text": result.text, "data": result.data}``.
    """
    cfg = step.config
    tool_name = cfg["tool"]

    # Render each string arg through Jinja; pass non-strings as-is.
    rendered_args = {
        k: render_template(v, state.state) if isinstance(v, str) else v
        for k, v in (cfg.get("args") or {}).items()
    }

    # Function-level import to break the circular import cycle:
    # tools.__init__ → workflow_tools → engine → step_executors → tools.__init__
    from decafclaw.tool_execution import resolve_widget  # noqa: PLC0415
    from decafclaw.tools import execute_tool  # noqa: PLC0415

    # Fork ctx so current_tool_call_id is set — this wires tool_start/
    # tool_end events through the standard event bus path.
    tool_call_id = f"wf-{step.id}-{uuid.uuid4().hex[:8]}"
    call_ctx = ctx.fork_for_tool_call(tool_call_id)

    await call_ctx.publish("tool_start", tool=tool_name, args=rendered_args,
                           tool_call_id=tool_call_id)
    try:
        result = await execute_tool(call_ctx, tool_name, rendered_args)
    except Exception:
        await call_ctx.publish("tool_end", tool=tool_name, result_text="[error]",
                               display_text=None, display_short_text=None,
                               media=[], tool_call_id=tool_call_id)
        raise

    # Mirror canonical tool_end shape from tool_execution.execute_single_tool
    # so the web UI receives display_text / display_short_text / media / widget.
    widget_payload = resolve_widget(tool_name, result, tool_call_id)
    publish_kwargs = {
        "tool": tool_name,
        "result_text": result.text,
        "display_text": getattr(result, "display_text", None),
        "display_short_text": getattr(result, "display_short_text", None),
        "media": result.media or [],
        "tool_call_id": tool_call_id,
    }
    if widget_payload is not None:
        publish_kwargs["widget"] = widget_payload
    await call_ctx.publish("tool_end", **publish_kwargs)

    output = {"text": result.text, "data": result.data}
    next_step = resolve_next(step, output, state)
    return StepResult(output=output, next_step=next_step)


async def _execute_subagent(ctx, step: StepDef,
                             state: WorkflowState) -> StepResult:
    """Spawn a child agent loop for the subagent step.

    The child runs to completion synchronously (awaited with timeout).
    If run_subagent_step returns suspended=True, the engine receives
    PAUSED_SUBAGENT and stores pending for future resumption.

    Output shape (when not suspended):
        {"text": <child final message>, "outputs": {filename: path}}
    """
    cfg = step.config
    prompt = render_template(cfg.get("prompt", ""), state.state)

    result = await run_subagent_step(
        ctx,
        state=state,
        step_id=step.id,
        skill=cfg.get("skill"),
        tools=cfg.get("tools") or [],
        outputs=cfg.get("outputs") or [],
        context_profile=cfg.get("context-profile") or {},
        prompt=prompt,
    )

    if result.suspended:
        return StepResult(
            output=None,
            suspend_status=RunStatus.PAUSED_SUBAGENT,
            pending={"step_id": step.id, "child_conv_id": result.child_conv_id},
        )

    output = {
        "text": result.text,
        "outputs": result.output_paths,
    }
    next_step = resolve_next(step, output, state)
    return StepResult(output=output, next_step=next_step)


async def _execute_llm_call(ctx, step: StepDef,
                             state: WorkflowState) -> StepResult:
    """Forced-tool structured LLM call.

    Builds a single-function tool the model must call, sends it along
    with the rendered prompt, and returns the parsed arguments as
    output. Retries once with a stricter nudge if the model narrates
    instead of calling.
    """
    cfg = step.config
    prompt = render_template(cfg.get("prompt", ""), state.state)
    schema = cfg.get("schema") or {"type": "object"}
    system = cfg.get("system", "")
    tool_name = f"submit_{step.id}"

    output = await _call_structured(
        ctx,
        system=system,
        user_msg=prompt,
        schema=schema,
        tool_name=tool_name,
    )
    next_step = resolve_next(step, output, state)
    return StepResult(output=output, next_step=next_step)


async def _call_structured(
    ctx,
    *,
    system: str,
    user_msg: str,
    schema: dict,
    tool_name: str,
    description: str = "",
    retries: int = 1,
) -> dict:
    """Force a structured response by exposing a single tool the model must call.

    Returns the parsed tool arguments. Retries once with a stricter
    nudge if the model narrates instead of calling. After retries are
    exhausted, raises RuntimeError.

    Uses a forced-tool schema: the model MUST call the named tool.
    """
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_msg},
    ]
    tools = [{
        "type": "function",
        "function": {
            "name": tool_name,
            "description": description or (
                "Submit the structured result for this step. "
                "You MUST call this — do not respond with prose."
            ),
            "parameters": schema,
        },
    }]
    # Use active_model from ctx if set; fall back to None (uses default)
    model_name = ctx.active_model or None
    config = ctx.config

    last_error: str | None = None
    for attempt in range(retries + 1):
        result = await call_llm(config, messages, tools=tools, model_name=model_name)
        tool_calls = result.get("tool_calls") or []
        if tool_calls:
            args_raw = tool_calls[0].get("function", {}).get("arguments") or "{}"
            try:
                return json.loads(args_raw)
            except json.JSONDecodeError as exc:
                last_error = (
                    f"invalid JSON in tool args: {exc}; raw={args_raw[:200]!r}"
                )
        else:
            last_error = (
                f"model emitted text instead of calling {tool_name!r}: "
                f"{(result.get('content') or '')[:200]!r}"
            )
        log.debug(
            "[step_executors] attempt %d/%d failed for %s: %s",
            attempt + 1, retries + 1, tool_name, last_error,
        )
        # Stricter retry nudge
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": (
                user_msg
                + f"\n\nIMPORTANT: You MUST call the tool `{tool_name}` "
                f"now. Do not narrate. Emit only the function call."
            )},
        ]
    raise RuntimeError(
        f"structured call to {tool_name!r} failed after {retries + 1} "
        f"attempts: {last_error}"
    )


def resolve_next(step: StepDef, output: dict,
                  state: WorkflowState) -> str | None:
    """Walk step.next_edges; first matching if_expr wins.

    An empty to="" edge is terminal (returns None). Steps with no
    next_edges are also terminal.

    The step's own output is temporarily available as ``state.<step_id>``
    so edge conditions can reference it without waiting for the engine
    to write it.
    """
    augmented = {**state.state, step.id: output}
    for edge in step.next_edges:
        if eval_condition(edge.if_expr, augmented):
            return edge.to if edge.to else None
    return None  # no matching edge = terminal
