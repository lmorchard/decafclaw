"""Workflow step-graph executor.

The engine drives workflow execution deterministically. It bypasses the
agent loop entirely — the LLM appears only inside specific step executors
(llm_call, route), never as an orchestrator deciding what to do next.

Public API:
  start_workflow(ctx, name, *, initial_state=None) -> WorkflowState
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from . import step_executors
from .conv_state import init_workflow_state, save_workflow_state
from .registry import get as registry_get
from .types import RunStatus, StepDef, WorkflowDef, WorkflowState

log = logging.getLogger(__name__)

# Safety cap: prevent runaway execution from misconfigured back-edges.
# Cycles are not supported until Phase 4/5; this guard catches regressions early.
_MAX_STEPS = 500


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


async def start_workflow(
    ctx, name: str, *, initial_state: dict | None = None,
) -> WorkflowState:
    """Initialize workflow state and begin execution at initial_step.

    ``initial_state`` values are merged into the top-level state dict so
    templates can reference them as ``state.topic``, ``state.user_response``,
    etc.  These are NOT step-keyed — they sit at the same level as step
    outputs.

    Raises ValueError if the workflow name is not registered.
    """
    wf = registry_get(name)
    if wf is None:
        raise ValueError(
            f"Workflow {name!r} not found in registry. "
            "Ensure the workflow skill is loaded."
        )
    state = init_workflow_state(ctx, workflow=name, initial_step=wf.initial_step)
    if initial_state:
        state.state.update(initial_state)
    save_workflow_state(ctx, state)
    log.info("[engine] starting workflow %r, initial_step=%r", name, wf.initial_step)
    return await _run_to_suspension(ctx, state, wf)


async def _run_to_suspension(ctx, state: WorkflowState,
                              wf: WorkflowDef) -> WorkflowState:
    """Execute steps until a terminal state, suspension, or error.

    Each iteration:
      1. Resolve the current step from the graph.
      2. Execute it via the step_executors dispatcher.
      3. Apply the result (write output to state, advance pointer).
      4. Persist state.
    """
    steps_taken = 0
    while state.status == RunStatus.RUNNING:
        if steps_taken >= _MAX_STEPS:
            log.error(
                "[engine] workflow %r exceeded _MAX_STEPS=%d — "
                "terminating with error (possible runaway cycle)",
                wf.name, _MAX_STEPS,
            )
            state.status = RunStatus.ERROR
            save_workflow_state(ctx, state)
            raise RuntimeError(
                f"Workflow {wf.name!r} exceeded the step limit of {_MAX_STEPS}. "
                "Check for runaway cycles in the workflow graph."
            )
        steps_taken += 1
        step_id = state.current_step
        step = wf.steps_by_id.get(step_id)
        if step is None:
            log.error(
                "[engine] workflow %r has no step %r — terminating with error",
                wf.name, step_id,
            )
            state.status = RunStatus.ERROR
            save_workflow_state(ctx, state)
            break

        log.debug("[engine] executing step %r (kind=%s)", step_id, step.kind.value)
        try:
            result = await step_executors.execute(ctx, step, state)
        except Exception as exc:
            log.error(
                "[engine] step %r of workflow %r raised: %s",
                step_id, wf.name, exc,
            )
            state.status = RunStatus.ERROR
            state.transitions.append({
                "step": step_id,
                "ts": _now_iso(),
                "error": str(exc),
            })
            save_workflow_state(ctx, state)
            raise

        _apply_step_result(state, step, result)
        save_workflow_state(ctx, state)

    return state


def _apply_step_result(state: WorkflowState, step: StepDef,
                        result: step_executors.StepResult) -> None:
    """Write step output to state; advance current_step or set status.

    Output is only written to state[step.id] when the step completes
    (suspend_status is None). For suspended steps the entry is left absent
    until the suspending executor itself populates it on completion
    (per Phase 3 onward, user_input awaits ctx.request_confirmation inline
    and returns a completed StepResult directly).
    """
    state.transitions.append({
        "step": step.id,
        "ts": _now_iso(),
    })

    if result.suspend_status is not None:
        # Step wants to pause (user_input, subagent); output is not yet
        # available — leave state[step.id] absent until resume.
        state.status = result.suspend_status
        state.pending = result.pending
    else:
        # Step completed: write output now.
        state.state[step.id] = result.output
        if result.next_step is not None:
            # Advance to the next step
            state.current_step = result.next_step
        else:
            # No next step → terminal
            state.status = RunStatus.DONE
            log.info(
                "[engine] workflow %r reached terminal state at step %r",
                state.workflow, step.id,
            )
