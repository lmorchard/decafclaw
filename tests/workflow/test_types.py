"""Tests for workflow types — round-trip serialization, RunStatus, StepDef."""

import dataclasses
import json

import pytest

from decafclaw.workflow.types import (
    EdgeRef,
    RouteChoice,
    RunStatus,
    StepDef,
    StepKind,
    WorkflowDef,
    WorkflowState,
)


def test_run_status_values():
    assert RunStatus.RUNNING.value == "running"
    assert RunStatus.DONE.value == "done"
    assert RunStatus.ERROR.value == "error"
    assert RunStatus.PAUSED_USER_INPUT.value == "paused_user_input"
    assert RunStatus.PAUSED_SUBAGENT.value == "paused_subagent"
    # ABORTED must NOT exist in the new enum
    assert not hasattr(RunStatus, "ABORTED")
    # PAUSED_GATE must NOT exist (renamed)
    assert not hasattr(RunStatus, "PAUSED_GATE")


def test_step_kind_values():
    assert StepKind.LLM_CALL.value == "llm_call"
    assert StepKind.TOOL_CALL.value == "tool_call"
    assert StepKind.USER_INPUT.value == "user_input"
    assert StepKind.ROUTE.value == "route"
    assert StepKind.SUBAGENT.value == "subagent"
    assert StepKind.PYTHON.value == "python"


def test_step_def_construction():
    step = StepDef(
        id="greet",
        kind=StepKind.LLM_CALL,
        config={"prompt": "Say hello", "schema": {"type": "object"}},
    )
    assert step.id == "greet"
    assert step.kind == StepKind.LLM_CALL
    assert step.next_edges == ()
    assert step.choices == ()
    assert step.description == ""


def test_step_def_with_edges():
    edges = (
        EdgeRef(to="next_step", if_expr="state.x == 1"),
        EdgeRef(to=""),
    )
    step = StepDef(
        id="conditional",
        kind=StepKind.LLM_CALL,
        config={},
        next_edges=edges,
    )
    assert len(step.next_edges) == 2
    assert step.next_edges[0].to == "next_step"
    assert step.next_edges[0].if_expr == "state.x == 1"
    assert step.next_edges[1].to == ""
    assert step.next_edges[1].if_expr == ""


def test_step_def_frozen():
    """StepDef is frozen — mutation raises."""
    step = StepDef(id="s", kind=StepKind.LLM_CALL, config={})
    with pytest.raises((AttributeError, dataclasses.FrozenInstanceError)):
        step.id = "changed"  # type: ignore[misc]


def test_route_choice():
    choice = RouteChoice(id="approve", to="publish", when="draft is ready", label="Approve")
    assert choice.id == "approve"
    assert choice.to == "publish"
    assert choice.when == "draft is ready"
    assert choice.label == "Approve"


def test_workflow_state_construction():
    state = WorkflowState(
        workflow="test_wf",
        run_id="abc-123",
        conv_id="conv-1",
        initial_step="greet",
        current_step="greet",
        status=RunStatus.RUNNING,
        state={},
        transitions=[],
    )
    assert state.workflow == "test_wf"
    assert state.status == RunStatus.RUNNING
    assert state.state == {}
    assert state.transitions == []
    assert state.pending == {}


def test_workflow_state_json_round_trip():
    state = WorkflowState(
        workflow="hello",
        run_id="xyz",
        conv_id="conv-42",
        initial_step="step1",
        current_step="step1",
        status=RunStatus.DONE,
        state={"step1": {"greeting": "hi"}},
        transitions=[{"step": "step1", "ts": "2026-01-01T00:00:00+00:00"}],
        pending={"key": "value"},
    )
    serialized = state.to_json()
    data = json.loads(serialized)
    assert data["workflow"] == "hello"
    assert data["status"] == "done"
    assert data["state"]["step1"]["greeting"] == "hi"
    assert data["pending"]["key"] == "value"

    recovered = WorkflowState.from_json(serialized)
    assert recovered.workflow == state.workflow
    assert recovered.status == state.status
    assert recovered.state == state.state
    assert recovered.transitions == state.transitions
    assert recovered.pending == state.pending


def test_workflow_def_steps_by_id():
    steps = [
        StepDef(id="a", kind=StepKind.LLM_CALL, config={}),
        StepDef(id="b", kind=StepKind.LLM_CALL, config={}),
    ]
    wf = WorkflowDef(
        name="test",
        description="A test workflow",
        initial_step="a",
        steps=tuple(steps),
        skill_dir=None,
    )
    assert wf.steps_by_id["a"].id == "a"
    assert wf.steps_by_id["b"].id == "b"
