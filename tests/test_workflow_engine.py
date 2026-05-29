"""Tests for workflow engine transitions and dispatch."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from decafclaw.media import EndTurnConfirm
from decafclaw.workflow import registry
from decafclaw.workflow.conv_state import (
    init_workflow_state,
    load_workflow_state,
)
from decafclaw.workflow.engine import (
    AdvanceResult,
    advance,
    finalize_gate_response,
    verify_subagent_outputs,
)
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


def _ctx_for(tmp_path: Path,
             conv_id: str = "conv-engine-test") -> SimpleNamespace:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = SimpleNamespace(workspace_path=workspace)
    return SimpleNamespace(config=config, conv_id=conv_id,
                           manager=None)


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
    ctx = _ctx_for(tmp_path)
    state = init_workflow_state(ctx, workflow="demo", initial_phase="a")

    result = await advance(ctx, state, target="b", reason="done")
    assert isinstance(result, AdvanceResult)
    assert result.new_phase == "b"
    assert result.end_turn_signal is None
    reloaded = load_workflow_state(ctx)
    assert reloaded is not None
    assert reloaded.current_phase == "b"
    assert reloaded.status == RunStatus.DONE  # b is terminal
    assert reloaded.history[-1]["from"] == "a"
    assert reloaded.history[-1]["to"] == "b"
    assert reloaded.history[-1]["reason"] == "done"


@pytest.mark.asyncio
async def test_advance_rejects_invalid_target(tmp_path: Path):
    wf = _simple_workflow()
    registry.register(wf)
    ctx = _ctx_for(tmp_path)
    state = init_workflow_state(ctx, workflow="demo", initial_phase="a")

    with pytest.raises(ValueError, match="not a valid next phase"):
        await advance(ctx, state, target="ghost", reason="")
    # State unchanged
    reloaded = load_workflow_state(ctx)
    assert reloaded is not None
    assert reloaded.current_phase == "a"


@pytest.mark.asyncio
async def test_advance_with_gate_returns_end_turn_confirm(tmp_path: Path):
    wf = _gated_workflow()
    registry.register(wf)
    ctx = _ctx_for(tmp_path)
    state = init_workflow_state(ctx, workflow="gated", initial_phase="a")

    result = await advance(ctx, state, target="b", reason="ok")
    assert isinstance(result.end_turn_signal, EndTurnConfirm)
    # State should be paused-gate, current phase still 'a'
    reloaded = load_workflow_state(ctx)
    assert reloaded is not None
    assert reloaded.status == RunStatus.PAUSED_GATE
    assert reloaded.current_phase == "a"
    assert reloaded.pending_gate == {"edge_target": "b", "on_deny": "a"}


@pytest.mark.asyncio
async def test_finalize_gate_approve(tmp_path: Path):
    wf = _gated_workflow()
    registry.register(wf)
    ctx = _ctx_for(tmp_path)
    state = init_workflow_state(ctx, workflow="gated", initial_phase="a")
    await advance(ctx, state, target="b", reason="ok")
    state = load_workflow_state(ctx)
    assert state is not None

    await finalize_gate_response(ctx, state, approved=True)
    reloaded = load_workflow_state(ctx)
    assert reloaded is not None
    assert reloaded.current_phase == "b"
    assert reloaded.status == RunStatus.DONE
    assert reloaded.pending_gate is None
    assert reloaded.history[-1]["gate_response"] == "approved"


@pytest.mark.asyncio
async def test_finalize_gate_deny(tmp_path: Path):
    wf = _gated_workflow()
    registry.register(wf)
    ctx = _ctx_for(tmp_path)
    state = init_workflow_state(ctx, workflow="gated", initial_phase="a")
    await advance(ctx, state, target="b", reason="ok")
    state = load_workflow_state(ctx)
    assert state is not None

    await finalize_gate_response(ctx, state, approved=False)
    reloaded = load_workflow_state(ctx)
    assert reloaded is not None
    # on_deny was "a" — stayed in phase a (but transitioned through gate)
    assert reloaded.current_phase == "a"
    assert reloaded.status == RunStatus.RUNNING
    assert reloaded.history[-1]["gate_response"] == "denied"


@pytest.mark.asyncio
async def test_verify_subagent_outputs_present(tmp_path: Path):
    wf = _subagent_workflow()
    registry.register(wf)
    ctx = _ctx_for(tmp_path)
    state = init_workflow_state(ctx, workflow="sub", initial_phase="g")
    artifacts = (ctx.config.workspace_path / "conversations"
                 / ctx.conv_id / "artifacts" / "g")
    artifacts.mkdir(parents=True)
    (artifacts / "sources.md").write_text("data")

    missing = verify_subagent_outputs(ctx, state, phase_id="g")
    assert missing == []


@pytest.mark.asyncio
async def test_verify_subagent_outputs_missing_returns_list(tmp_path: Path):
    wf = _subagent_workflow()
    registry.register(wf)
    ctx = _ctx_for(tmp_path)
    state = init_workflow_state(ctx, workflow="sub", initial_phase="g")

    missing = verify_subagent_outputs(ctx, state, phase_id="g")
    assert missing == ["sources.md"]


@pytest.mark.asyncio
async def test_finalize_gate_response_uses_fresh_state(tmp_path: Path):
    """If state has been mutated on disk since the caller loaded it,
    finalize_gate_response should use the fresh state from inside the
    lock rather than the stale passed-in state."""
    wf = _gated_workflow()
    registry.register(wf)
    ctx = _ctx_for(tmp_path)
    state = init_workflow_state(ctx, workflow="gated", initial_phase="a")
    # Caller advance triggers the gate; state is now PAUSED_GATE on disk.
    await advance(ctx, state, target="b", reason="ok")
    captured = load_workflow_state(ctx)
    assert captured is not None

    # Simulate: another process finalized the gate first
    await finalize_gate_response(ctx, captured, approved=True)

    # Now a second finalize call with the SAME (now-stale) `captured`
    # state should raise — because the on-disk state is no longer
    # PAUSED_GATE.
    with pytest.raises(ValueError, match="not paused on a gate"):
        await finalize_gate_response(ctx, captured, approved=True)


@pytest.mark.asyncio
async def test_subagent_dispatch_happy_path(tmp_path: Path, monkeypatch):
    """Dispatching a subagent phase writes the artifact and advances
    to the next phase."""
    from decafclaw.workflow import subagent as wf_subagent
    from decafclaw.workflow.engine import dispatch_and_finalize_subagent

    wf = _subagent_workflow()
    registry.register(wf)
    ctx = _ctx_for(tmp_path)
    state = init_workflow_state(ctx, workflow="sub", initial_phase="g")

    # Stub the child-agent runner to "produce" the output file
    async def fake_run_child(*, ctx, state, phase):
        artifacts = (ctx.config.workspace_path / "conversations"
                     / ctx.conv_id / "artifacts" / phase.id)
        artifacts.mkdir(parents=True, exist_ok=True)
        (artifacts / "sources.md").write_text("fetched")
        return "done"

    monkeypatch.setattr(wf_subagent, "_run_child", fake_run_child)

    await dispatch_and_finalize_subagent(ctx, state, phase_id="g")
    reloaded = load_workflow_state(ctx)
    assert reloaded is not None
    assert reloaded.current_phase == "d"
    assert reloaded.status == RunStatus.DONE
    assert reloaded.history[-1]["from"] == "g"
    assert reloaded.history[-1]["to"] == "d"


@pytest.mark.asyncio
async def test_subagent_dispatch_missing_output_sets_error(
        tmp_path: Path, monkeypatch):
    from decafclaw.workflow import subagent as wf_subagent
    from decafclaw.workflow.engine import dispatch_and_finalize_subagent

    wf = _subagent_workflow()
    registry.register(wf)
    ctx = _ctx_for(tmp_path)
    state = init_workflow_state(ctx, workflow="sub", initial_phase="g")

    async def fake_run_child(*, ctx, state, phase):
        # Subagent "completes" but doesn't write the output
        return "incomplete"

    monkeypatch.setattr(wf_subagent, "_run_child", fake_run_child)

    await dispatch_and_finalize_subagent(ctx, state, phase_id="g")
    reloaded = load_workflow_state(ctx)
    assert reloaded is not None
    assert reloaded.status == RunStatus.ERROR
    assert "sources.md" in (reloaded.error or "")
    assert reloaded.current_phase == "g"  # didn't advance


def test_blocked_for_children_includes_phase_advance():
    """Children must not be able to call phase_advance — it would let
    them advance the parent's workflow state machine."""
    from decafclaw.workflow.subagent import _BLOCKED_FOR_CHILDREN
    assert "phase_advance" in _BLOCKED_FOR_CHILDREN
    # Sanity: also confirm the workflow_* admin tools are blocked
    for t in ("workflow_start", "workflow_abort", "workflow_status"):
        assert t in _BLOCKED_FOR_CHILDREN, f"{t} should be blocked"


@pytest.mark.asyncio
async def test_subagent_dispatch_child_crash_sets_error(
        tmp_path: Path, monkeypatch):
    """If _run_child raises, dispatch_and_finalize_subagent should set
    RunStatus.ERROR with the exception text rather than propagating."""
    from decafclaw.workflow import subagent as wf_subagent
    from decafclaw.workflow.engine import dispatch_and_finalize_subagent

    wf = _subagent_workflow()
    registry.register(wf)
    ctx = _ctx_for(tmp_path)
    state = init_workflow_state(ctx, workflow="sub", initial_phase="g")

    async def boom(*, ctx, state, phase):
        raise RuntimeError("LLM exploded")

    monkeypatch.setattr(wf_subagent, "_run_child", boom)

    await dispatch_and_finalize_subagent(ctx, state, phase_id="g")
    reloaded = load_workflow_state(ctx)
    assert reloaded is not None
    assert reloaded.status == RunStatus.ERROR
    assert "LLM exploded" in (reloaded.error or "")
    assert reloaded.current_phase == "g"


@pytest.mark.asyncio
async def test_run_child_setup_overrides_conv_id_to_parent(
        tmp_path: Path):
    """The child's setup callback must set conv_id to the parent's so
    workflow_artifact_write resolves to the parent's artifacts/ dir."""
    from decafclaw.config import Config
    from decafclaw.config_types import AgentConfig
    from decafclaw.workflow import subagent as wf_subagent

    captured: dict = {}

    # enqueue_turn returns an asyncio.Future in production; returning a
    # coroutine here lets the `await manager.enqueue_turn(...)` call
    # complete and capture setup before asyncio.wait_for raises (it
    # expects a Future, not a coroutine result).  We catch that below.
    class FakeManager:
        async def enqueue_turn(self, child_conv_id, *, kind, prompt,
                               history, context_setup, user_id, **kwargs):
            captured["setup"] = context_setup
            captured["child_conv_id"] = child_conv_id
            return "fake-child-output"

    # _run_child calls dataclasses.replace(config, ...) and
    # replace(config.agent, ...), so both must be real dataclass instances.
    # Use Config with data_home pointing at tmp_path so workspace_path
    # resolves to a real directory without extra mkdir calls.
    cfg = Config(
        agent=AgentConfig(
            data_home=str(tmp_path),
            id="test-agent",
            child_max_tool_iterations=8,
            child_timeout_sec=60,
        ),
    )
    (tmp_path / "test-agent" / "workspace").mkdir(parents=True, exist_ok=True)

    parent_ctx = SimpleNamespace(
        config=cfg,
        conv_id="parent-conv-id",
        channel_id="parent-channel",
        tools=SimpleNamespace(extra={}, extra_definitions=[], allowed=None),
        skills=SimpleNamespace(activated=set(), data={}),
        manager=FakeManager(),
        event_context_id="evt",
        context_id="ctx-id",
        cancelled=None,
        request_confirmation=None,
        active_model="",
        user_id="",
    )

    phase = PhaseDef(
        id="gather", kind=PhaseKind.SUBAGENT,
        prompt="do research", tools=[],
        next_phases=[], gate=None,
        outputs=("sources.md",),
        subagent_skill=None, context_profile={},
    )

    wf = _subagent_workflow()
    registry.register(wf)
    state = init_workflow_state(parent_ctx, workflow="sub",
                                initial_phase="g")

    try:
        await wf_subagent._run_child(
            ctx=parent_ctx, state=state, phase=phase)
    except Exception:
        # asyncio.wait_for receives a plain string from FakeManager (not a
        # real Future) and raises TypeError.  That's fine — we only need
        # setup to be captured before that point.
        pass

    assert "setup" in captured, (
        "_run_child should call manager.enqueue_turn with a "
        "context_setup callback")

    child_ctx = SimpleNamespace(
        config=parent_ctx.config,
        conv_id="some-child-conv-id",
        tools=SimpleNamespace(extra={}, extra_definitions=[], allowed=None),
        skills=SimpleNamespace(activated=set(), data={}),
        cancelled=None,
        request_confirmation=None,
        event_context_id="",
        on_stream_chunk=None,
        is_child=False,
        skip_reflection=False,
        skip_vault_retrieval=False,
        active_model="",
    )
    captured["setup"](child_ctx)
    assert child_ctx.conv_id == "parent-conv-id", (
        f"setup should override child conv_id to parent's, got "
        f"{child_ctx.conv_id!r}"
    )
