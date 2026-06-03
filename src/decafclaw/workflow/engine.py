"""Workflow step-graph executor.

The engine drives workflow execution deterministically. It bypasses the
agent loop entirely — the LLM appears only inside specific step executors
(llm_call, route), never as an orchestrator deciding what to do next.

Public API:
  start_workflow(ctx, name) -> WorkflowState
  resume_user_input(ctx, state, response) -> WorkflowState  (Phase 5)
  resume_after_subagent(ctx, state) -> WorkflowState
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from . import step_executors
from .conv_state import init_workflow_state, save_workflow_state
from .registry import get as registry_get
from .subagent import SubagentResult
from .types import RunStatus, StepDef, WorkflowDef, WorkflowState

log = logging.getLogger(__name__)

# Safety cap: prevent runaway execution from misconfigured back-edges.
# Cycles are not supported until Phase 4/5; this guard catches regressions early.
_MAX_STEPS = 500


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


async def start_workflow(ctx, name: str) -> WorkflowState:
    """Initialize workflow state and begin execution at initial_step.

    Raises ValueError if the workflow name is not registered.
    """
    wf = registry_get(name)
    if wf is None:
        raise ValueError(
            f"Workflow {name!r} not found in registry. "
            "Ensure the workflow skill is loaded."
        )
    state = init_workflow_state(ctx, workflow=name, initial_step=wf.initial_step)
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


async def resume_user_input(ctx, state: WorkflowState,
                            response: dict) -> WorkflowState:
    """Resume a workflow paused at a PAUSED_USER_INPUT step.

    Called when the user responds to a user_input confirmation prompt.

    ``response`` is:
      - ``{"value": "<text>"}`` for text-mode steps, or
      - ``{"choice": "<id>"}`` for choice-mode steps.

    Steps:
    1. Validate the state is PAUSED_USER_INPUT.
    2. Write the response to state[step_id].
    3. Resolve the next step:
       - choice mode: look up the RouteChoice.to for the chosen id.
       - text mode: walk step.next_edges as normal.
    4. Clear state.pending, set status = RUNNING (or DONE if terminal).
    5. Persist and continue via _run_to_suspension if still RUNNING.
    """
    if state.status != RunStatus.PAUSED_USER_INPUT:
        raise RuntimeError(
            f"resume_user_input called on workflow with status "
            f"{state.status.value!r}; expected PAUSED_USER_INPUT"
        )

    wf = registry_get(state.workflow)
    if wf is None:
        raise ValueError(
            f"Workflow {state.workflow!r} not found in registry during "
            "user_input resumption"
        )

    step_id = state.pending.get("step_id", "")
    step = wf.steps_by_id.get(step_id)
    if step is None:
        raise RuntimeError(
            f"resume_user_input: pending step_id={step_id!r} not found "
            f"in workflow {state.workflow!r}"
        )

    try:
        state.state[step_id] = response
        state.transitions.append({
            "step": step_id,
            "ts": _now_iso(),
            "resumed": True,
        })

        if "choice" in response:
            choice_id = response["choice"]
            target = next(
                (c.to for c in step.choices if c.id == choice_id),
                None,
            )
            if target is None:
                raise RuntimeError(
                    f"resume_user_input: unknown choice {choice_id!r} "
                    f"for step {step_id!r}"
                )
            next_step: str | None = target if target else None
        else:
            # Text input: use resolve_next with the response as output.
            next_step = step_executors.resolve_next(step, response, state)

        state.pending = {}
        if next_step is not None:
            state.current_step = next_step
            state.status = RunStatus.RUNNING
        else:
            state.status = RunStatus.DONE
            log.info(
                "[engine] workflow %r reached terminal state at step %r "
                "(after user_input resumption)",
                state.workflow, step_id,
            )
            save_workflow_state(ctx, state)
            return state

        save_workflow_state(ctx, state)
        return await _run_to_suspension(ctx, state, wf)

    except Exception as exc:
        log.error(
            "[engine] resume_user_input: failed for step %r of workflow %r: %s",
            step_id, state.workflow, exc,
        )
        state.status = RunStatus.ERROR
        state.transitions.append({
            "step": step_id,
            "ts": _now_iso(),
            "error": str(exc),
        })
        save_workflow_state(ctx, state)
        raise


def get_completed_subagent_result(ctx, state: WorkflowState) -> SubagentResult:
    """Retrieve the completed subagent result for resumption.

    This is a thin hook that the test suite can monkeypatch — in
    production, the result is fetched from the child conversation's
    archive or artifacts. Since _run_child awaits completion
    synchronously, the result is available immediately after the
    child turn completes; this function is called by resume_after_subagent
    to retrieve it.

    In Phase 3 the synchronous execution model means resume_after_subagent
    is only exercised when a PAUSED_SUBAGENT state is recovered from disk
    (e.g. after a process restart during a long-running subagent). The
    default implementation here raises NotImplementedError to signal that
    proper out-of-process result recovery is a future concern.

    Tests monkeypatch this with a lambda returning a SubagentResult.
    """
    raise NotImplementedError(
        "get_completed_subagent_result is not implemented for production use "
        "in Phase 3 — it exists as a seam for testing resume_after_subagent. "
        "In normal operation the subagent completes synchronously before "
        "the engine suspends."
    )


async def resume_after_subagent(ctx, state: WorkflowState) -> WorkflowState:
    """Resume a workflow paused at a PAUSED_SUBAGENT step.

    Called when the child agent turn has completed (either through the
    synchronous await path or, in future, via a manager callback).

    Steps:
    1. Validate the state is PAUSED_SUBAGENT.
    2. Retrieve the completed child result via get_completed_subagent_result.
    3. Populate state[step_id] with the output dict.
    4. Clear state.pending and set status = RUNNING.
    5. Resolve next step and continue execution via _run_to_suspension.
    """
    if state.status != RunStatus.PAUSED_SUBAGENT:
        raise RuntimeError(
            f"resume_after_subagent called on workflow with status "
            f"{state.status.value!r}; expected PAUSED_SUBAGENT"
        )

    wf = registry_get(state.workflow)
    if wf is None:
        raise ValueError(
            f"Workflow {state.workflow!r} not found in registry during resumption"
        )

    step_id = state.pending.get("step_id", "")
    step = wf.steps_by_id.get(step_id)
    if step is None:
        raise RuntimeError(
            f"resume_after_subagent: pending step_id={step_id!r} not found "
            f"in workflow {state.workflow!r}"
        )

    try:
        result = get_completed_subagent_result(ctx, state)
    except Exception as exc:
        log.error(
            "[engine] resume_after_subagent: failed to retrieve result "
            "for step %r of workflow %r: %s",
            step_id, state.workflow, exc,
        )
        state.status = RunStatus.ERROR
        state.transitions.append({
            "step": step_id,
            "ts": _now_iso(),
            "error": str(exc),
        })
        save_workflow_state(ctx, state)
        raise

    output = {
        "text": result.text,
        "outputs": result.output_paths,
    }

    # Write the output and resolve the next step before clearing pending.
    next_step = step_executors.resolve_next(step, output, state)

    state.state[step_id] = output
    state.transitions.append({"step": step_id, "ts": _now_iso(), "resumed": True})
    state.pending = {}

    if next_step is not None:
        state.current_step = next_step
        state.status = RunStatus.RUNNING
    else:
        state.status = RunStatus.DONE
        log.info(
            "[engine] workflow %r reached terminal state at step %r "
            "(after subagent resumption)",
            state.workflow, step_id,
        )
        save_workflow_state(ctx, state)
        return state

    save_workflow_state(ctx, state)
    return await _run_to_suspension(ctx, state, wf)


def _apply_step_result(state: WorkflowState, step: StepDef,
                        result: step_executors.StepResult) -> None:
    """Write step output to state; advance current_step or set status.

    Output is only written to state[step.id] when the step completes
    (suspend_status is None). For suspended steps the entry is left absent;
    resume_user_input / resume_after_subagent populate it on resumption.
    This avoids a transient None slot that templates could read between the
    save-on-suspend and the eventual resume.
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
