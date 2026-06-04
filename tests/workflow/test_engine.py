"""Tests for workflow engine — start_workflow, _run_to_suspension, _apply_step_result."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from decafclaw.workflow import engine
from decafclaw.workflow.conv_state import init_workflow_state
from decafclaw.workflow.loader import load_workflow
from decafclaw.workflow.registry import clear, register
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
async def test_start_workflow_accepts_initial_state(ctx, hello_wf):
    """initial_state values are merged into state.state before execution.

    Templates can reference them as {{ state.topic }}, etc.
    """
    mock_resp = make_llm_response("submit_greet", {"greeting": "Hello from topic"})
    with patch("decafclaw.workflow.step_executors.call_llm", new=AsyncMock(return_value=mock_resp)):
        state = await engine.start_workflow(
            ctx, "workflow_hello", initial_state={"topic": "test topic"}
        )

    assert state.status == RunStatus.DONE
    # initial_state value should be in state
    assert state.state["topic"] == "test topic"


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
# Tests for _apply_step_result suspend behaviour (C2) + user_input stub
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_user_input_step_raises_not_implemented(ctx):
    """user_input step kind raises NotImplementedError in Phase 1.

    Phase 3 rewrites this with ctx.request_confirmation. Until then,
    workflows containing user_input steps should surface a clear error.
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

    with pytest.raises((NotImplementedError, RuntimeError)):
        await engine.start_workflow(ctx, "suspend_wf")
