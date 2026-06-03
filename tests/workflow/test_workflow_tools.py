"""Tests for workflow_tools — thin tool surface (start/status/abort/read/write)."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from decafclaw.media import WidgetRequest
from decafclaw.tools.workflow_tools import (
    WORKFLOW_TOOL_DEFINITIONS,
    WORKFLOW_TOOLS,
    tool_workflow_abort,
    tool_workflow_artifact_read,
    tool_workflow_artifact_write,
    tool_workflow_start,
    tool_workflow_status,
)
from decafclaw.workflow.conv_state import init_workflow_state, save_workflow_state
from decafclaw.workflow.registry import clear, register
from decafclaw.workflow.types import RunStatus, StepDef, StepKind, WorkflowDef, WorkflowState


@pytest.fixture(autouse=True)
def clear_registry():
    clear()
    yield
    clear()


@pytest.fixture
def workspace_ctx(ctx, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    ctx.config.__class__ = type(
        "PatchedConfig",
        (type(ctx.config),),
        {"workspace_path": property(lambda self: workspace)},
    )
    return ctx


@pytest.fixture
def hello_wf():
    steps = (
        StepDef(
            id="greet",
            kind=StepKind.LLM_CALL,
            config={
                "prompt": "Say hi",
                "schema": {"type": "object", "properties": {"greeting": {"type": "string"}}},
            },
        ),
    )
    wf = WorkflowDef(
        name="workflow_hello",
        description="Hello",
        initial_step="greet",
        steps=steps,
        skill_dir=None,
    )
    register(wf)
    return wf


def test_tools_dict_has_expected_keys():
    assert "workflow_start" in WORKFLOW_TOOLS
    assert "workflow_status" in WORKFLOW_TOOLS
    assert "workflow_abort" in WORKFLOW_TOOLS
    assert "workflow_artifact_read" in WORKFLOW_TOOLS
    assert "workflow_artifact_write" in WORKFLOW_TOOLS
    # phase_advance must NOT be present
    assert "phase_advance" not in WORKFLOW_TOOLS
    # refresh_workflow_tools must NOT be present
    assert "refresh_workflow_tools" not in WORKFLOW_TOOLS


def test_tool_definitions_have_names():
    names = {td["function"]["name"] for td in WORKFLOW_TOOL_DEFINITIONS}
    assert "workflow_start" in names
    assert "workflow_status" in names
    assert "workflow_abort" in names
    assert "workflow_artifact_read" in names
    assert "workflow_artifact_write" in names


@pytest.mark.asyncio
async def test_workflow_start_runs_and_returns_status(workspace_ctx, hello_wf):
    ctx = workspace_ctx
    mock_resp = {
        "tool_calls": [
            {"function": {"name": "submit_greet", "arguments": json.dumps({"greeting": "Hello!"})}}
        ]
    }
    with patch("decafclaw.workflow.step_executors.call_llm", new=AsyncMock(return_value=mock_resp)):
        result = await tool_workflow_start(ctx, name="workflow_hello")

    assert "done" in result.text.lower() or "complete" in result.text.lower() or "workflow_hello" in result.text


@pytest.mark.asyncio
async def test_workflow_start_errors_on_unknown(workspace_ctx):
    ctx = workspace_ctx
    result = await tool_workflow_start(ctx, name="no_such_workflow")
    assert "[error" in result.text


@pytest.mark.asyncio
async def test_workflow_status_no_active(workspace_ctx):
    ctx = workspace_ctx
    result = await tool_workflow_status(ctx)
    assert "no active" in result.text.lower() or "no workflow" in result.text.lower()


@pytest.mark.asyncio
async def test_workflow_abort_no_active(workspace_ctx):
    ctx = workspace_ctx
    result = await tool_workflow_abort(ctx)
    assert "no active" in result.text.lower() or "no workflow" in result.text.lower()


@pytest.mark.asyncio
async def test_workflow_artifact_write_and_read(workspace_ctx):
    ctx = workspace_ctx
    # Initialize workflow state so artifacts dir exists
    from decafclaw.workflow.conv_state import init_workflow_state
    init_workflow_state(ctx, workflow="wf", initial_step="s1")

    write_result = await tool_workflow_artifact_write(
        ctx, path="test/output.txt", content="hello artifact"
    )
    assert "[error" not in write_result.text

    read_result = await tool_workflow_artifact_read(ctx, path="test/output.txt")
    assert "hello artifact" in read_result.text


@pytest.mark.asyncio
async def test_workflow_artifact_read_missing(workspace_ctx):
    ctx = workspace_ctx
    from decafclaw.workflow.conv_state import init_workflow_state
    init_workflow_state(ctx, workflow="wf", initial_step="s1")

    result = await tool_workflow_artifact_read(ctx, path="nonexistent.txt")
    assert "[error" in result.text


@pytest.mark.asyncio
async def test_workflow_abort_adds_aborted_transition_entry(workspace_ctx, hello_wf):
    """Aborting a workflow appends a transition with aborted=True.

    Regression guard for Copilot finding C3: before this fix, abort set
    status=ERROR without any distinguishing marker, making it impossible for
    downstream consumers to tell a user-abort from an execution failure.
    Now the last transition has ``"aborted": True`` so callers can check
    ``transitions[-1].get("aborted")``.
    """
    ctx = workspace_ctx
    # Start the workflow so state exists
    mock_resp = {
        "tool_calls": [
            {"function": {"name": "submit_greet", "arguments": json.dumps({"greeting": "Hi!"})}}
        ]
    }
    with patch("decafclaw.workflow.step_executors.call_llm", new=AsyncMock(return_value=mock_resp)):
        await tool_workflow_start(ctx, name="workflow_hello")

    # Restart state as RUNNING so abort has something to act on.
    from decafclaw.workflow.conv_state import init_workflow_state, save_workflow_state
    state = init_workflow_state(ctx, workflow="workflow_hello", initial_step="greet")
    state.status = RunStatus.RUNNING
    save_workflow_state(ctx, state)

    result = await tool_workflow_abort(ctx)
    assert "aborted" in result.text.lower() or "workflow_hello" in result.text

    # Re-load state from disk and check the transition
    from decafclaw.workflow.conv_state import load_workflow_state
    # State is archived after abort, so load from archive is not possible;
    # instead check that the transition was appended before archiving by
    # inspecting what was passed to save_workflow_state via the saved state.
    # The abort tool archives then clears, so we verify via the in-memory
    # state before archive — re-run save check via a fresh abort on new state.
    state2 = init_workflow_state(ctx, workflow="workflow_hello", initial_step="greet")
    state2.status = RunStatus.RUNNING
    save_workflow_state(ctx, state2)

    with patch("decafclaw.tools.workflow_tools.archive_workflow_state"):
        with patch("decafclaw.tools.workflow_tools.save_workflow_state") as mock_save:
            await tool_workflow_abort(ctx, reason="test abort")
            assert mock_save.called
            saved_state = mock_save.call_args[0][1]
            aborted_transitions = [
                t for t in saved_state.transitions if t.get("aborted")
            ]
            assert aborted_transitions, (
                f"Expected an aborted transition; got transitions: {saved_state.transitions}"
            )
            assert aborted_transitions[-1]["reason"] == "test abort"
            assert saved_state.status == RunStatus.ERROR


# ---------------------------------------------------------------------------
# Idempotent workflow_start tests (same-name + paused_user_input)
# ---------------------------------------------------------------------------

def _paused_state(workspace_ctx, *, workflow: str = "interview",
                  mode: str = "text") -> WorkflowState:
    """Save and return a WorkflowState paused at user_input for workspace_ctx."""
    state = init_workflow_state(workspace_ctx, workflow=workflow,
                                initial_step="ask_q1")
    state.status = RunStatus.PAUSED_USER_INPUT
    state.pending = {
        "step_id": "ask_q1",
        "mode": mode,
        "prompt": "Tell me about yourself.",
    }
    if mode == "choice":
        state.pending["choices"] = [
            {"id": "yes", "label": "Yes"},
            {"id": "no", "label": "No"},
        ]
    save_workflow_state(workspace_ctx, state)
    return state


@pytest.mark.asyncio
async def test_workflow_start_idempotent_returns_widget_for_paused_user_input(workspace_ctx):
    """workflow_start re-renders the pause widget when the same workflow is
    already paused at a user_input step (text mode).

    This is the primary fix: after inline-resume advances to a new pause,
    the agent's natural 'call workflow_start again' recovery correctly
    surfaces the next question's widget instead of erroring.
    """
    ctx = workspace_ctx
    _paused_state(ctx, workflow="interview", mode="text")

    result = await tool_workflow_start(ctx, name="interview")

    assert "[error" not in result.text
    assert result.widget is not None
    assert isinstance(result.widget, WidgetRequest)
    assert result.widget.widget_type == "text_input"
    assert result.end_turn is True
    assert callable(result.widget.on_response)


@pytest.mark.asyncio
async def test_workflow_start_idempotent_choice_returns_widget_for_paused_user_input(workspace_ctx):
    """workflow_start re-renders the pause widget when paused at a choice step."""
    ctx = workspace_ctx
    _paused_state(ctx, workflow="interview", mode="choice")

    result = await tool_workflow_start(ctx, name="interview")

    assert "[error" not in result.text
    assert result.widget is not None
    assert result.widget.widget_type == "multiple_choice"
    assert result.end_turn is True


@pytest.mark.asyncio
async def test_workflow_start_errors_for_different_workflow_active(workspace_ctx):
    """workflow_start errors when a *different* workflow is already active,
    preserving the existing error path.
    """
    ctx = workspace_ctx
    _paused_state(ctx, workflow="interview", mode="text")

    result = await tool_workflow_start(ctx, name="research_brief")

    assert "[error" in result.text
    assert "already active" in result.text


@pytest.mark.asyncio
async def test_workflow_start_errors_for_running_workflow(workspace_ctx):
    """workflow_start errors when the same workflow is RUNNING (not paused).
    Idempotency only applies to the paused-user-input case.
    """
    ctx = workspace_ctx
    state = init_workflow_state(ctx, workflow="interview", initial_step="ask_q1")
    state.status = RunStatus.RUNNING
    save_workflow_state(ctx, state)

    result = await tool_workflow_start(ctx, name="interview")

    assert "[error" in result.text
    assert "already active" in result.text
