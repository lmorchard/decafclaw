"""Tests for workflow dataclass shapes and round-trips."""

import json

from decafclaw.workflow.types import (
    EdgeDef,
    GateDef,
    PhaseDef,
    PhaseKind,
    RunState,
    RunStatus,
    WorkflowDef,
)


def test_phase_kind_values():
    assert PhaseKind.INLINE.value == "inline"
    assert PhaseKind.SUBAGENT.value == "subagent"


def test_run_status_values():
    assert {s.value for s in RunStatus} == {
        "running", "paused-gate", "paused-subagent", "done", "error"
    }


def test_phase_def_minimal():
    phase = PhaseDef(
        id="draft",
        kind=PhaseKind.INLINE,
        prompt="Write the draft.",
        tools=["vault_write"],
        next_phases=[EdgeDef(id="review", when="ready", gate=None)],
        gate=None,  # legacy hook; phase-level gates not supported
        outputs=(),
        subagent_skill=None,
        context_profile={},
    )
    assert phase.id == "draft"
    assert phase.is_terminal is False


def test_phase_def_terminal():
    phase = PhaseDef(
        id="publish",
        kind=PhaseKind.INLINE,
        prompt="Publish.",
        tools=[],
        next_phases=[],
        gate=None,
        outputs=(),
        subagent_skill=None,
        context_profile={},
    )
    assert phase.is_terminal is True


def test_edge_def_with_gate():
    gate = GateDef(
        type="review",
        message="Approve?",
        approve_label="Yes",
        deny_label="No",
        on_deny="draft",
    )
    edge = EdgeDef(id="publish", when="approved", gate=gate)
    assert edge.gate is gate


def test_run_state_json_round_trip():
    state = RunState(
        workflow="weeknotes",
        slug="w20",
        run_id="2026-05-19-1402-weeknotes-w20",
        status=RunStatus.PAUSED_GATE,
        current_phase="draft",
        created_at="2026-05-19T14:02:00+00:00",
        updated_at="2026-05-19T14:35:12+00:00",
        history=[
            {"from": None, "to": "gather", "edge_index": None,
             "gate_response": None, "reason": "initial",
             "timestamp": "2026-05-19T14:02:00+00:00"}
        ],
        pending_gate={"edge_target": "review", "on_deny": "draft"},
        pending_subagent=None,
        error=None,
    )
    raw = state.to_json()
    parsed = json.loads(raw)
    assert parsed["workflow"] == "weeknotes"
    assert parsed["status"] == "paused-gate"
    back = RunState.from_json(raw)
    assert back == state


def test_workflow_def_lookup_phase():
    p1 = PhaseDef(
        id="a", kind=PhaseKind.INLINE, prompt="", tools=[],
        next_phases=[EdgeDef(id="b", when="", gate=None)],
        gate=None, outputs=(), subagent_skill=None, context_profile={},
    )
    p2 = PhaseDef(
        id="b", kind=PhaseKind.INLINE, prompt="", tools=[],
        next_phases=[], gate=None, outputs=(),
        subagent_skill=None, context_profile={},
    )
    wf = WorkflowDef(
        name="t", description="d", initial_phase="a",
        phases={"a": p1, "b": p2},
        user_invocable=True, argument_hint="",
    )
    assert wf.phase("a") is p1
    assert wf.phase("missing") is None
