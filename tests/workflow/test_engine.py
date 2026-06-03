"""Tests for workflow engine — start_workflow, _run_to_suspension, _apply_step_result,
resume_after_subagent."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from decafclaw.workflow import engine
from decafclaw.workflow.conv_state import init_workflow_state
from decafclaw.workflow.loader import load_workflow
from decafclaw.workflow.registry import clear, register
from decafclaw.workflow.subagent import SubagentResult
from decafclaw.workflow.types import EdgeRef, RunStatus, StepDef, StepKind, WorkflowDef


def make_llm_response(tool_name: str, args: dict) -> dict:
    return {
        "tool_calls": [
            {"function": {"name": tool_name, "arguments": json.dumps(args)}}
        ]
    }


@pytest.fixture(autouse=True)
def clear_registry():
    """Clear the workflow registry before/after each test."""
    clear()
    yield
    clear()


@pytest.fixture
def hello_wf():
    """A minimal hello workflow with one terminal llm_call step."""
    steps = (
        StepDef(
            id="greet",
            kind=StepKind.LLM_CALL,
            config={
                "prompt": "Generate a 3-word greeting for: {{ state.topic | default('agent testbed') }}",
                "schema": {
                    "type": "object",
                    "properties": {"greeting": {"type": "string"}},
                    "required": ["greeting"],
                },
            },
        ),
    )
    wf = WorkflowDef(
        name="workflow_hello",
        description="Minimal hello workflow",
        initial_step="greet",
        steps=steps,
        skill_dir=None,
    )
    register(wf)
    return wf


@pytest.mark.asyncio
async def test_start_workflow_reaches_done(ctx, hello_wf):
    mock_resp = make_llm_response("submit_greet", {"greeting": "Hello agent testbed"})
    with patch("decafclaw.workflow.step_executors.call_llm", new=AsyncMock(return_value=mock_resp)):
        state = await engine.start_workflow(ctx, "workflow_hello")

    assert state.status == RunStatus.DONE
    assert "greet" in state.state
    assert state.state["greet"]["greeting"] == "Hello agent testbed"


@pytest.mark.asyncio
async def test_start_workflow_logs_transition(ctx, hello_wf):
    mock_resp = make_llm_response("submit_greet", {"greeting": "Hi there"})
    with patch("decafclaw.workflow.step_executors.call_llm", new=AsyncMock(return_value=mock_resp)):
        state = await engine.start_workflow(ctx, "workflow_hello")

    assert len(state.transitions) == 1
    t = state.transitions[0]
    assert t["step"] == "greet"
    assert "ts" in t


@pytest.mark.asyncio
async def test_start_workflow_persists_state(ctx, hello_wf, tmp_path):
    """State file should be written after completion."""
    # Set data_home so workspace_path = tmp_path/test-agent/workspace
    ctx.config.agent.data_home = str(tmp_path)
    workspace = ctx.config.workspace_path
    workspace.mkdir(parents=True, exist_ok=True)

    mock_resp = make_llm_response("submit_greet", {"greeting": "Test greeting"})
    with patch("decafclaw.workflow.step_executors.call_llm", new=AsyncMock(return_value=mock_resp)):
        await engine.start_workflow(ctx, "workflow_hello")

    # Check file was written
    wf_path = workspace / "conversations" / ctx.conv_id / "workflow.json"
    assert wf_path.is_file()
    data = json.loads(wf_path.read_text())
    assert data["status"] == "done"
    assert data["state"]["greet"]["greeting"] == "Test greeting"


@pytest.mark.asyncio
async def test_start_workflow_two_step(ctx):
    """Two-step linear workflow: first → second → done."""
    steps = (
        StepDef(
            id="first",
            kind=StepKind.LLM_CALL,
            config={"prompt": "First step", "schema": {"type": "object", "properties": {"x": {"type": "integer"}}}},
            next_edges=(EdgeRef(to="second"),),
        ),
        StepDef(
            id="second",
            kind=StepKind.LLM_CALL,
            config={"prompt": "Second step: {{ state.first.x }}", "schema": {"type": "object", "properties": {"y": {"type": "integer"}}}},
        ),
    )
    wf = WorkflowDef(
        name="two_step_wf",
        description="Two steps",
        initial_step="first",
        steps=steps,
        skill_dir=None,
    )
    register(wf)

    call_count = 0
    responses = [
        make_llm_response("submit_first", {"x": 42}),
        make_llm_response("submit_second", {"y": 84}),
    ]

    async def fake_llm(*args, **kwargs):
        nonlocal call_count
        resp = responses[call_count]
        call_count += 1
        return resp

    with patch("decafclaw.workflow.step_executors.call_llm", new=fake_llm):
        state = await engine.start_workflow(ctx, "two_step_wf")

    assert state.status == RunStatus.DONE
    assert state.state["first"]["x"] == 42
    assert state.state["second"]["y"] == 84
    assert len(state.transitions) == 2


@pytest.mark.asyncio
async def test_start_workflow_errors_on_unknown_name(ctx):
    """start_workflow raises ValueError for unregistered workflow name."""
    with pytest.raises(ValueError, match="not found"):
        await engine.start_workflow(ctx, "no_such_workflow")


@pytest.mark.asyncio
async def test_run_to_suspension_caps_runaway_cycle(ctx):
    """Engine raises RuntimeError and sets ERROR status if _MAX_STEPS is exceeded.

    Simulates a two-step workflow where step_a always points back to step_a
    (unconditional self-edge), which would loop forever without the cap.
    """
    steps = (
        StepDef(
            id="step_a",
            kind=StepKind.LLM_CALL,
            config={"prompt": "Loop forever", "schema": {"type": "object"}},
            # Unconditional back-edge to itself — simulates a misconfigured cycle
            next_edges=(EdgeRef(to="step_a"),),
        ),
    )
    wf = WorkflowDef(
        name="runaway_wf",
        description="Runaway cycle test",
        initial_step="step_a",
        steps=steps,
        skill_dir=None,
    )
    register(wf)

    # LLM always "succeeds" (returns empty object) so the executor doesn't raise
    mock_resp = make_llm_response("submit_step_a", {})
    with patch("decafclaw.workflow.step_executors.call_llm", new=AsyncMock(return_value=mock_resp)):
        with pytest.raises(RuntimeError, match="step limit"):
            await engine.start_workflow(ctx, "runaway_wf")


# ---------------------------------------------------------------------------
# Tests for resume_after_subagent
# ---------------------------------------------------------------------------

@pytest.fixture
def subagent_wf():
    """Workflow with a subagent step followed by a terminal llm_call step."""
    steps = (
        StepDef(
            id="gather",
            kind=StepKind.SUBAGENT,
            config={
                "prompt": "Gather sources",
                "tools": [],
                "outputs": ["sources.md"],
            },
            next_edges=(EdgeRef(to="summarize"),),
        ),
        StepDef(
            id="summarize",
            kind=StepKind.LLM_CALL,
            config={
                "prompt": "Summarize: {{ state.gather.text }}",
                "schema": {"type": "object", "properties": {"summary": {"type": "string"}}},
            },
        ),
    )
    wf = WorkflowDef(
        name="subagent_wf",
        description="Subagent + summarize",
        initial_step="gather",
        steps=steps,
        skill_dir=None,
    )
    register(wf)
    return wf


@pytest.mark.asyncio
async def test_resume_after_subagent_continues_to_done(ctx, subagent_wf, tmp_path):
    """resume_after_subagent populates output, clears pending, and continues execution."""
    ctx.config.agent.data_home = str(tmp_path)
    workspace = ctx.config.workspace_path
    workspace.mkdir(parents=True, exist_ok=True)

    # Create a PAUSED_SUBAGENT state directly
    state = init_workflow_state(ctx, workflow="subagent_wf", initial_step="gather")
    state.status = RunStatus.PAUSED_SUBAGENT
    state.current_step = "gather"
    state.pending = {
        "step_id": "gather",
        "child_conv_id": "child-abc-123",
    }

    finished_result = SubagentResult(
        suspended=False,
        child_conv_id="child-abc-123",
        text="Found 4 sources.",
        output_paths={"sources.md": "conversations/test-conv/artifacts/gather/sources.md"},
    )

    mock_llm_resp = make_llm_response("submit_summarize", {"summary": "Key themes found."})

    with patch(
        "decafclaw.workflow.engine.get_completed_subagent_result",
        return_value=finished_result,
    ), patch(
        "decafclaw.workflow.step_executors.call_llm",
        new=AsyncMock(return_value=mock_llm_resp),
    ):
        result_state = await engine.resume_after_subagent(ctx, state)

    assert result_state.status == RunStatus.DONE
    assert result_state.pending == {}
    assert result_state.state["gather"]["text"] == "Found 4 sources."
    assert result_state.state["gather"]["outputs"] == {
        "sources.md": "conversations/test-conv/artifacts/gather/sources.md",
    }
    assert result_state.state["summarize"]["summary"] == "Key themes found."


@pytest.mark.asyncio
async def test_resume_after_subagent_clears_pending(ctx, subagent_wf, tmp_path):
    """After resume, state.pending is cleared regardless of downstream success."""
    ctx.config.agent.data_home = str(tmp_path)
    workspace = ctx.config.workspace_path
    workspace.mkdir(parents=True, exist_ok=True)

    state = init_workflow_state(ctx, workflow="subagent_wf", initial_step="gather")
    state.status = RunStatus.PAUSED_SUBAGENT
    state.current_step = "gather"
    state.pending = {"step_id": "gather", "child_conv_id": "child-xxx"}

    finished_result = SubagentResult(
        suspended=False,
        child_conv_id="child-xxx",
        text="done",
        output_paths={},
    )

    mock_llm_resp = make_llm_response("submit_summarize", {"summary": "Brief."})

    with patch(
        "decafclaw.workflow.engine.get_completed_subagent_result",
        return_value=finished_result,
    ), patch(
        "decafclaw.workflow.step_executors.call_llm",
        new=AsyncMock(return_value=mock_llm_resp),
    ):
        result_state = await engine.resume_after_subagent(ctx, state)

    assert result_state.pending == {}


@pytest.mark.asyncio
async def test_resume_after_subagent_errors_on_wrong_status(ctx, subagent_wf, tmp_path):
    """resume_after_subagent raises if state is not PAUSED_SUBAGENT."""
    ctx.config.agent.data_home = str(tmp_path)
    workspace = ctx.config.workspace_path
    workspace.mkdir(parents=True, exist_ok=True)

    state = init_workflow_state(ctx, workflow="subagent_wf", initial_step="gather")
    state.status = RunStatus.RUNNING  # wrong status

    with pytest.raises(RuntimeError, match="PAUSED_SUBAGENT"):
        await engine.resume_after_subagent(ctx, state)


# ---------------------------------------------------------------------------
# Tests for _apply_step_result suspend behaviour (C2)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_apply_step_result_does_not_write_none_on_suspend(ctx):
    """When a step suspends, state[step_id] must remain absent — not written as None.

    Regression guard for Copilot finding C2: the old code wrote
    ``state.state[step.id] = result.output`` unconditionally, so a suspended
    step (output=None) left a None slot that templates could read between
    save-on-suspend and eventual resume.
    """
    steps = (
        StepDef(
            id="ask_user",
            kind=StepKind.USER_INPUT,
            config={"prompt": "What colour?", "input": "text"},
        ),
    )
    wf = WorkflowDef(
        name="suspend_wf",
        description="Suspend test",
        initial_step="ask_user",
        steps=steps,
        skill_dir=None,
    )
    register(wf)

    state = await engine.start_workflow(ctx, "suspend_wf")

    assert state.status == RunStatus.PAUSED_USER_INPUT, (
        f"Expected PAUSED_USER_INPUT, got {state.status}"
    )
    # The key must be absent, not written as None.
    assert "ask_user" not in state.state, (
        f"state['ask_user'] should be absent on suspend, got {state.state.get('ask_user')!r}"
    )


@pytest.mark.asyncio
async def test_resume_after_subagent_persists_error_on_failure(
    ctx, subagent_wf, tmp_path
):
    """resume_after_subagent persists RunStatus.ERROR when get_completed_subagent_result raises.

    Ensures the workflow does not stay in PAUSED_SUBAGENT forever on failure.
    """
    ctx.config.agent.data_home = str(tmp_path)
    workspace = ctx.config.workspace_path
    workspace.mkdir(parents=True, exist_ok=True)

    state = init_workflow_state(ctx, workflow="subagent_wf", initial_step="gather")
    state.status = RunStatus.PAUSED_SUBAGENT
    state.current_step = "gather"
    state.pending = {"step_id": "gather", "child_conv_id": "child-fail-123"}

    boom = RuntimeError("result fetch failed")

    with patch(
        "decafclaw.workflow.engine.get_completed_subagent_result",
        side_effect=boom,
    ):
        with pytest.raises(RuntimeError, match="result fetch failed"):
            await engine.resume_after_subagent(ctx, state)

    # State on disk must reflect ERROR
    wf_path = workspace / "conversations" / ctx.conv_id / "workflow.json"
    assert wf_path.is_file(), "workflow state file was not written on error"
    import json
    data = json.loads(wf_path.read_text())
    assert data["status"] == "error"
    # Transition should record the error
    assert any("error" in t for t in data.get("transitions", []))
