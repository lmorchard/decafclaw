"""Tests for workflow engine transitions and dispatch."""

from pathlib import Path

import pytest

from decafclaw.media import EndTurnConfirm
from decafclaw.workflow import registry
from decafclaw.workflow.engine import (
    AdvanceResult,
    advance,
    finalize_gate_response,
    verify_subagent_outputs,
)
from decafclaw.workflow.runs import create_run, load_run
from decafclaw.workflow.types import (
    EdgeDef,
    GateDef,
    PhaseDef,
    PhaseKind,
    RunStatus,
    WorkflowDef,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    registry.clear()
    yield
    registry.clear()


def _simple_workflow(name: str = "demo") -> WorkflowDef:
    return WorkflowDef(
        name=name,
        description="",
        initial_phase="a",
        phases={
            "a": PhaseDef(
                id="a", kind=PhaseKind.INLINE, prompt="A",
                tools=[],
                next_phases=[EdgeDef(id="b", when="", gate=None)],
                gate=None, outputs=(), subagent_skill=None,
                context_profile={},
            ),
            "b": PhaseDef(
                id="b", kind=PhaseKind.INLINE, prompt="B",
                tools=[], next_phases=[], gate=None, outputs=(),
                subagent_skill=None, context_profile={},
            ),
        },
        user_invocable=False, argument_hint="",
    )


def _gated_workflow() -> WorkflowDef:
    gate = GateDef(type="review", message="?", on_deny="a")
    return WorkflowDef(
        name="gated", description="", initial_phase="a",
        phases={
            "a": PhaseDef(
                id="a", kind=PhaseKind.INLINE, prompt="A",
                tools=[],
                next_phases=[EdgeDef(id="b", when="ok", gate=gate)],
                gate=None, outputs=(), subagent_skill=None,
                context_profile={},
            ),
            "b": PhaseDef(
                id="b", kind=PhaseKind.INLINE, prompt="B",
                tools=[], next_phases=[], gate=None, outputs=(),
                subagent_skill=None, context_profile={},
            ),
        },
        user_invocable=False, argument_hint="",
    )


def _subagent_workflow() -> WorkflowDef:
    return WorkflowDef(
        name="sub", description="", initial_phase="g",
        phases={
            "g": PhaseDef(
                id="g", kind=PhaseKind.SUBAGENT,
                prompt="gather",
                tools=["vault_read"],
                next_phases=[EdgeDef(id="d", when="", gate=None)],
                gate=None, outputs=("sources.md",),
                subagent_skill=None, context_profile={},
            ),
            "d": PhaseDef(
                id="d", kind=PhaseKind.INLINE, prompt="draft",
                tools=[], next_phases=[], gate=None, outputs=(),
                subagent_skill=None, context_profile={},
            ),
        },
        user_invocable=False, argument_hint="",
    )


@pytest.mark.asyncio
async def test_advance_simple_no_gate(tmp_path: Path):
    wf = _simple_workflow()
    registry.register(wf)
    ws = tmp_path / "workspace"
    ws.mkdir()
    state = create_run(ws, workflow="demo", slug="x",
                       initial_phase="a")

    result = await advance(ws, state, target="b", reason="done")
    assert isinstance(result, AdvanceResult)
    assert result.new_phase == "b"
    assert result.end_turn_signal is None
    reloaded = load_run(ws, state.run_id)
    assert reloaded.current_phase == "b"
    assert reloaded.status == RunStatus.DONE  # b is terminal
    assert reloaded.history[-1]["from"] == "a"
    assert reloaded.history[-1]["to"] == "b"
    assert reloaded.history[-1]["reason"] == "done"


@pytest.mark.asyncio
async def test_advance_rejects_invalid_target(tmp_path: Path):
    wf = _simple_workflow()
    registry.register(wf)
    ws = tmp_path / "workspace"
    ws.mkdir()
    state = create_run(ws, workflow="demo", slug="x",
                       initial_phase="a")

    with pytest.raises(ValueError, match="not a valid next phase"):
        await advance(ws, state, target="ghost", reason="")
    # State unchanged
    reloaded = load_run(ws, state.run_id)
    assert reloaded.current_phase == "a"


@pytest.mark.asyncio
async def test_advance_with_gate_returns_end_turn_confirm(tmp_path: Path):
    wf = _gated_workflow()
    registry.register(wf)
    ws = tmp_path / "workspace"
    ws.mkdir()
    state = create_run(ws, workflow="gated", slug="x",
                       initial_phase="a")

    result = await advance(ws, state, target="b", reason="ok")
    assert isinstance(result.end_turn_signal, EndTurnConfirm)
    # State should be paused-gate, current phase still 'a'
    reloaded = load_run(ws, state.run_id)
    assert reloaded.status == RunStatus.PAUSED_GATE
    assert reloaded.current_phase == "a"
    assert reloaded.pending_gate == {"edge_target": "b", "on_deny": "a"}


@pytest.mark.asyncio
async def test_finalize_gate_approve(tmp_path: Path):
    wf = _gated_workflow()
    registry.register(wf)
    ws = tmp_path / "workspace"
    ws.mkdir()
    state = create_run(ws, workflow="gated", slug="x",
                       initial_phase="a")
    await advance(ws, state, target="b", reason="ok")
    state = load_run(ws, state.run_id)

    await finalize_gate_response(ws, state, approved=True)
    reloaded = load_run(ws, state.run_id)
    assert reloaded.current_phase == "b"
    assert reloaded.status == RunStatus.DONE
    assert reloaded.pending_gate is None
    assert reloaded.history[-1]["gate_response"] == "approved"


@pytest.mark.asyncio
async def test_finalize_gate_deny(tmp_path: Path):
    wf = _gated_workflow()
    registry.register(wf)
    ws = tmp_path / "workspace"
    ws.mkdir()
    state = create_run(ws, workflow="gated", slug="x",
                       initial_phase="a")
    await advance(ws, state, target="b", reason="ok")
    state = load_run(ws, state.run_id)

    await finalize_gate_response(ws, state, approved=False)
    reloaded = load_run(ws, state.run_id)
    # on_deny was "a" — stayed in phase a (but transitioned through gate)
    assert reloaded.current_phase == "a"
    assert reloaded.status == RunStatus.RUNNING
    assert reloaded.history[-1]["gate_response"] == "denied"


@pytest.mark.asyncio
async def test_verify_subagent_outputs_present(tmp_path: Path):
    wf = _subagent_workflow()
    registry.register(wf)
    ws = tmp_path / "workspace"
    ws.mkdir()
    state = create_run(ws, workflow="sub", slug="x",
                       initial_phase="g")
    artifacts = ws / "workflows" / "sub" / "runs" / state.run_id \
        / "artifacts" / "g"
    artifacts.mkdir(parents=True)
    (artifacts / "sources.md").write_text("data")

    missing = verify_subagent_outputs(ws, state, phase_id="g")
    assert missing == []


@pytest.mark.asyncio
async def test_verify_subagent_outputs_missing_returns_list(tmp_path: Path):
    wf = _subagent_workflow()
    registry.register(wf)
    ws = tmp_path / "workspace"
    ws.mkdir()
    state = create_run(ws, workflow="sub", slug="x",
                       initial_phase="g")

    missing = verify_subagent_outputs(ws, state, phase_id="g")
    assert missing == ["sources.md"]


@pytest.mark.asyncio
async def test_finalize_gate_response_uses_fresh_state(tmp_path: Path):
    """If state has been mutated on disk since the caller loaded it,
    finalize_gate_response should use the fresh state from inside the
    lock rather than the stale passed-in state."""
    wf = _gated_workflow()
    registry.register(wf)
    ws = tmp_path / "workspace"
    ws.mkdir()
    state = create_run(ws, workflow="gated", slug="x",
                       initial_phase="a")
    # Caller advance triggers the gate; state is now PAUSED_GATE on disk.
    await advance(ws, state, target="b", reason="ok")
    captured = load_run(ws, state.run_id)

    # Simulate: another process finalized the gate first
    await finalize_gate_response(ws, captured, approved=True)

    # Now a second finalize call with the SAME (now-stale) `captured`
    # state should raise — because the on-disk state is no longer
    # PAUSED_GATE.
    with pytest.raises(ValueError, match="not paused on a gate"):
        await finalize_gate_response(ws, captured, approved=True)


@pytest.mark.asyncio
async def test_subagent_dispatch_happy_path(tmp_path: Path, monkeypatch):
    """Dispatching a subagent phase writes the artifact and advances
    to the next phase."""
    from types import SimpleNamespace

    from decafclaw.workflow import subagent as wf_subagent
    from decafclaw.workflow.engine import dispatch_and_finalize_subagent

    wf = _subagent_workflow()
    registry.register(wf)
    ws = tmp_path / "workspace"
    ws.mkdir()
    state = create_run(ws, workflow="sub", slug="x",
                       initial_phase="g")

    # Stub the child-agent runner to "produce" the output file
    async def fake_run_child(*, ctx, workspace, state, phase):
        artifacts_dir = (workspace / "workflows" / state.workflow
                         / "runs" / state.run_id / "artifacts"
                         / phase.id)
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        (artifacts_dir / "sources.md").write_text("fetched")
        return "done"

    monkeypatch.setattr(wf_subagent, "_run_child", fake_run_child)

    ctx = SimpleNamespace(config=SimpleNamespace(workspace_path=ws))
    await dispatch_and_finalize_subagent(ctx, ws, state, phase_id="g")
    reloaded = load_run(ws, state.run_id)
    assert reloaded.current_phase == "d"
    assert reloaded.status == RunStatus.DONE
    assert reloaded.history[-1]["from"] == "g"
    assert reloaded.history[-1]["to"] == "d"


@pytest.mark.asyncio
async def test_subagent_dispatch_missing_output_sets_error(
        tmp_path: Path, monkeypatch):
    from types import SimpleNamespace

    from decafclaw.workflow import subagent as wf_subagent
    from decafclaw.workflow.engine import dispatch_and_finalize_subagent

    wf = _subagent_workflow()
    registry.register(wf)
    ws = tmp_path / "workspace"
    ws.mkdir()
    state = create_run(ws, workflow="sub", slug="x",
                       initial_phase="g")

    async def fake_run_child(*, ctx, workspace, state, phase):
        # Subagent "completes" but doesn't write the output
        return "incomplete"

    monkeypatch.setattr(wf_subagent, "_run_child", fake_run_child)

    ctx = SimpleNamespace(config=SimpleNamespace(workspace_path=ws))
    await dispatch_and_finalize_subagent(ctx, ws, state, phase_id="g")
    reloaded = load_run(ws, state.run_id)
    assert reloaded.status == RunStatus.ERROR
    assert "sources.md" in (reloaded.error or "")
    assert reloaded.current_phase == "g"  # didn't advance


def test_blocked_for_children_includes_phase_advance():
    """Children must not be able to call phase_advance — it would let
    them advance the parent's workflow state machine."""
    from decafclaw.workflow.subagent import _BLOCKED_FOR_CHILDREN
    assert "phase_advance" in _BLOCKED_FOR_CHILDREN
    # Sanity: also confirm the workflow_* admin tools are blocked
    for t in ("workflow_start", "workflow_switch", "workflow_list",
              "workflow_status"):
        assert t in _BLOCKED_FOR_CHILDREN, f"{t} should be blocked"


@pytest.mark.asyncio
async def test_subagent_dispatch_child_crash_sets_error(
        tmp_path: Path, monkeypatch):
    """If _run_child raises, dispatch_and_finalize_subagent should set
    RunStatus.ERROR with the exception text rather than propagating."""
    from types import SimpleNamespace

    from decafclaw.workflow import subagent as wf_subagent
    from decafclaw.workflow.engine import dispatch_and_finalize_subagent

    wf = _subagent_workflow()
    registry.register(wf)
    ws = tmp_path / "workspace"
    ws.mkdir()
    state = create_run(ws, workflow="sub", slug="x",
                       initial_phase="g")

    async def boom(*, ctx, workspace, state, phase):
        raise RuntimeError("LLM exploded")

    monkeypatch.setattr(wf_subagent, "_run_child", boom)

    ctx = SimpleNamespace(config=SimpleNamespace(workspace_path=ws))
    await dispatch_and_finalize_subagent(ctx, ws, state, phase_id="g")
    reloaded = load_run(ws, state.run_id)
    assert reloaded.status == RunStatus.ERROR
    assert "LLM exploded" in (reloaded.error or "")
    assert reloaded.current_phase == "g"
