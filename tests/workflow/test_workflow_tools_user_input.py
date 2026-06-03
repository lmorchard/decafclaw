"""Tests for the user_input suspension surface in workflow_tools.

Covers:
- tool_workflow_start returning the right widget ToolResult for text/choice modes.
- The on_response callback transforming widget data into workflow format.
- resume_user_input is awaited inline when the callback runs.
- resume_user_input failure is logged and the summary is still returned (fail-open).
- Unknown mode falls back to a plain status ToolResult.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from decafclaw.media import ToolResult, WidgetRequest
from decafclaw.tools.workflow_tools import (
    _build_paused_tool_result,
    _make_on_response,
    tool_workflow_start,
)
from decafclaw.workflow.registry import clear, register
from decafclaw.workflow.types import (
    RouteChoice,
    RunStatus,
    StepDef,
    StepKind,
    WorkflowDef,
    WorkflowState,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

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


def _make_state(*, mode: str, step_id: str = "ask_user",
                workflow: str = "test_wf",
                choices: list | None = None) -> WorkflowState:
    """Helper: build a WorkflowState paused at user_input."""
    pending: dict = {
        "step_id": step_id,
        "mode": mode,
        "prompt": "What is your name?",
    }
    if choices is not None:
        pending["choices"] = choices
    return WorkflowState(
        workflow=workflow,
        run_id="run-001",
        conv_id="test-conv",
        initial_step=step_id,
        current_step=step_id,
        status=RunStatus.PAUSED_USER_INPUT,
        state={},
        transitions=[],
        pending=pending,
    )


def _text_wf(name: str = "wf_text") -> WorkflowDef:
    """Workflow whose first step is a text user_input."""
    step = StepDef(
        id="ask_name",
        kind=StepKind.USER_INPUT,
        config={"prompt": "What is your name?", "input": "text"},
    )
    wf = WorkflowDef(
        name=name,
        description="text input workflow",
        initial_step="ask_name",
        steps=(step,),
        skill_dir=None,
    )
    register(wf)
    return wf


def _choice_wf(name: str = "wf_choice") -> WorkflowDef:
    """Workflow whose first step is a choice user_input."""
    step = StepDef(
        id="pick_path",
        kind=StepKind.USER_INPUT,
        config={"prompt": "Which path?"},
        choices=(
            RouteChoice(id="left", to="", label="Go left"),
            RouteChoice(id="right", to="", label="Go right"),
        ),
    )
    wf = WorkflowDef(
        name=name,
        description="choice input workflow",
        initial_step="pick_path",
        steps=(step,),
        skill_dir=None,
    )
    register(wf)
    return wf


# ---------------------------------------------------------------------------
# _build_paused_tool_result tests
# ---------------------------------------------------------------------------

def test_build_paused_text_returns_widget_request(ctx):
    state = _make_state(mode="text")
    result = _build_paused_tool_result(ctx, state)

    assert isinstance(result, ToolResult)
    assert result.widget is not None
    assert isinstance(result.widget, WidgetRequest)
    assert result.widget.widget_type == "text_input"


def test_build_paused_text_widget_has_prompt_and_field(ctx):
    state = _make_state(mode="text")
    result = _build_paused_tool_result(ctx, state)

    data = result.widget.data  # type: ignore[union-attr]
    assert data["prompt"] == "What is your name?"
    assert len(data["fields"]) == 1
    assert data["fields"][0]["key"] == "value"


def test_build_paused_text_has_end_turn(ctx):
    state = _make_state(mode="text")
    result = _build_paused_tool_result(ctx, state)

    # end_turn=True signals the agent loop to pause after the widget response.
    assert result.end_turn is True


def test_build_paused_text_on_response_callable(ctx):
    state = _make_state(mode="text")
    result = _build_paused_tool_result(ctx, state)

    assert callable(result.widget.on_response)  # type: ignore[union-attr]


def test_build_paused_choice_returns_multiple_choice_widget(ctx):
    state = _make_state(
        mode="choice",
        choices=[
            {"id": "yes", "label": "Yes"},
            {"id": "no", "label": "No"},
        ],
    )
    result = _build_paused_tool_result(ctx, state)

    assert isinstance(result, ToolResult)
    assert result.widget is not None
    assert result.widget.widget_type == "multiple_choice"


def test_build_paused_choice_widget_has_options(ctx):
    state = _make_state(
        mode="choice",
        choices=[
            {"id": "yes", "label": "Yes"},
            {"id": "no", "label": "No"},
        ],
    )
    result = _build_paused_tool_result(ctx, state)

    data = result.widget.data  # type: ignore[union-attr]
    assert data["prompt"] == "What is your name?"
    options = data["options"]
    assert len(options) == 2
    assert options[0] == {"value": "yes", "label": "Yes"}
    assert options[1] == {"value": "no", "label": "No"}


def test_build_paused_choice_on_response_callable(ctx):
    state = _make_state(mode="choice", choices=[{"id": "a", "label": "A"}])
    result = _build_paused_tool_result(ctx, state)

    assert callable(result.widget.on_response)  # type: ignore[union-attr]


def test_build_paused_unknown_mode_returns_status_text(ctx):
    state = _make_state(mode="unknown_mode")
    result = _build_paused_tool_result(ctx, state)

    # No widget — just a plain status message.
    assert result.widget is None
    assert "paused" in result.text.lower()
    assert "unknown_mode" in result.text


# ---------------------------------------------------------------------------
# _make_on_response callback tests
# ---------------------------------------------------------------------------

def _resume_sets_done(state):
    """Return a side_effect that sets state.status = DONE when called."""
    async def _impl(_ctx, _state, _user_input):
        _state.status = RunStatus.DONE
    return _impl


@pytest.mark.asyncio
async def test_on_response_text_returns_summary_string(ctx):
    """Callback returns a completion summary after one resume cycle."""
    state = _make_state(mode="text")
    callback = _make_on_response(ctx, state, "text")

    with patch("decafclaw.tools.workflow_tools.engine.resume_user_input",
               side_effect=_resume_sets_done(state)) as mock_resume:
        result = await callback({"value": "Alice"})

    assert isinstance(result, str)
    assert mock_resume.await_count == 1


@pytest.mark.asyncio
async def test_on_response_text_awaits_resume_before_returning(ctx):
    """resume_user_input is awaited inline — ordering is synchronous."""
    state = _make_state(mode="text")
    callback = _make_on_response(ctx, state, "text")

    call_order: list[str] = []

    async def mock_resume(_ctx, _state, _user_input):
        call_order.append("resume")
        _state.status = RunStatus.DONE

    with patch("decafclaw.tools.workflow_tools.engine.resume_user_input",
               side_effect=mock_resume):
        call_order.append("before")
        result = await callback({"value": "Bob"})
        call_order.append("after")

    # resume must complete before callback returns.
    assert call_order == ["before", "resume", "after"]
    assert isinstance(result, str)


@pytest.mark.asyncio
async def test_on_response_resume_failure_is_fail_open(ctx, caplog):
    """If resume_user_input raises, a summary is still returned (fail-open)."""
    state = _make_state(mode="text")
    callback = _make_on_response(ctx, state, "text")

    with patch("decafclaw.tools.workflow_tools.engine.resume_user_input",
               new_callable=AsyncMock) as mock_resume:
        mock_resume.side_effect = RuntimeError("engine exploded")
        result = await callback({"value": "Carol"})

    # Return value is still a string (fail-open: returns the partial summary).
    assert isinstance(result, str)
    assert "Carol" in result
    # Exception is logged.
    assert any("resume_user_input failed" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_on_response_choice_returns_summary_string(ctx):
    state = _make_state(mode="choice", choices=[{"id": "yes", "label": "Yes"}])
    callback = _make_on_response(ctx, state, "choice")

    with patch("decafclaw.tools.workflow_tools.engine.resume_user_input",
               side_effect=_resume_sets_done(state)):
        result = await callback({"selected": "yes"})

    assert isinstance(result, str)


@pytest.mark.asyncio
async def test_on_response_choice_list_takes_first(ctx):
    """When multiple_choice returns a list (allow_multiple=True), use the first element."""
    state = _make_state(mode="choice", choices=[])
    callback = _make_on_response(ctx, state, "choice")

    async def mock_resume(_ctx, _state, user_input):
        # Verify the first element was used.
        assert user_input["choice"] == "alpha"
        _state.status = RunStatus.DONE

    with patch("decafclaw.tools.workflow_tools.engine.resume_user_input",
               side_effect=mock_resume):
        result = await callback({"selected": ["alpha", "beta"]})

    assert isinstance(result, str)


@pytest.mark.asyncio
async def test_on_response_choice_empty_selection_handled(ctx):
    """Empty / missing selection doesn't crash — returns a valid string."""
    state = _make_state(mode="choice", choices=[])
    callback = _make_on_response(ctx, state, "choice")

    with patch("decafclaw.tools.workflow_tools.engine.resume_user_input",
               side_effect=_resume_sets_done(state)):
        result = await callback({})  # no "selected" key

    assert isinstance(result, str)


# ---------------------------------------------------------------------------
# Loop tests — multi-cycle user_input
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_on_response_returns_completion_summary_when_workflow_done(ctx):
    """After a single cycle that ends in DONE, callback returns completion summary."""
    state = _make_state(mode="text")
    callback = _make_on_response(ctx, state, "text")
    ctx.request_confirmation = AsyncMock()  # should not be called

    with patch("decafclaw.tools.workflow_tools.engine.resume_user_input",
               side_effect=_resume_sets_done(state)):
        result = await callback({"value": "Alice"})

    assert isinstance(result, str)
    assert "completed" in result.lower() or "status" in result.lower()
    ctx.request_confirmation.assert_not_awaited()


@pytest.mark.asyncio
async def test_on_response_loops_on_followup_user_input_pause(ctx):
    """Callback loops when resume lands at a second user_input pause, then DONE."""
    from decafclaw.confirmations import ConfirmationResponse
    state = _make_state(mode="text")
    callback = _make_on_response(ctx, state, "text")

    resume_call_count = 0

    async def mock_resume(_ctx, _state, _user_input):
        nonlocal resume_call_count
        resume_call_count += 1
        if resume_call_count == 1:
            # First resume: workflow pauses again at another user_input step.
            _state.pending = {
                "step_id": "ask_follow_up",
                "mode": "text",
                "prompt": "Follow-up question?",
            }
            # status stays PAUSED_USER_INPUT
        else:
            # Second resume: workflow completes.
            _state.status = RunStatus.DONE

    # ctx.request_confirmation returns an approved response for the second pause.
    fake_response = ConfirmationResponse(
        confirmation_id="fake-id",
        approved=True,
        data={"value": "my follow-up answer"},
    )
    ctx.request_confirmation = AsyncMock(return_value=fake_response)

    with patch("decafclaw.tools.workflow_tools.engine.resume_user_input",
               side_effect=mock_resume):
        result = await callback({"value": "first answer"})

    assert resume_call_count == 2
    ctx.request_confirmation.assert_awaited_once()
    assert isinstance(result, str)
    assert "done" in result.lower() or "status" in result.lower()


@pytest.mark.asyncio
async def test_on_response_handles_cancelled_followup(ctx):
    """If ctx.request_confirmation returns denied, callback returns a cancellation summary."""
    from decafclaw.confirmations import ConfirmationResponse
    state = _make_state(mode="text")
    callback = _make_on_response(ctx, state, "text")

    async def mock_resume_paused(_ctx, _state, _user_input):
        # Leave state in PAUSED_USER_INPUT to trigger the loop.
        _state.pending = {
            "step_id": "ask_follow_up",
            "mode": "text",
            "prompt": "Follow-up question?",
        }

    # Simulate the user cancelling (approved=False).
    denied_response = ConfirmationResponse(
        confirmation_id="fake-id",
        approved=False,
    )
    ctx.request_confirmation = AsyncMock(return_value=denied_response)

    with patch("decafclaw.tools.workflow_tools.engine.resume_user_input",
               side_effect=mock_resume_paused):
        result = await callback({"value": "first answer"})

    ctx.request_confirmation.assert_awaited_once()
    assert isinstance(result, str)
    assert "cancel" in result.lower()


# ---------------------------------------------------------------------------
# tool_workflow_start integration tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_workflow_start_text_user_input_returns_text_input_widget(workspace_ctx):
    _text_wf()
    result = await tool_workflow_start(workspace_ctx, name="wf_text")

    assert isinstance(result, ToolResult)
    assert result.widget is not None
    assert result.widget.widget_type == "text_input"
    assert result.end_turn is True


@pytest.mark.asyncio
async def test_workflow_start_choice_user_input_returns_multiple_choice_widget(workspace_ctx):
    _choice_wf()
    result = await tool_workflow_start(workspace_ctx, name="wf_choice")

    assert isinstance(result, ToolResult)
    assert result.widget is not None
    assert result.widget.widget_type == "multiple_choice"
    options = result.widget.data["options"]
    assert {"value": "left", "label": "Go left"} in options
    assert {"value": "right", "label": "Go right"} in options


@pytest.mark.asyncio
async def test_workflow_start_text_prompt_in_result(workspace_ctx):
    _text_wf()
    result = await tool_workflow_start(workspace_ctx, name="wf_text")

    assert "What is your name?" in result.text
    assert result.widget.data["prompt"] == "What is your name?"  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_workflow_start_choice_prompt_in_result(workspace_ctx):
    _choice_wf()
    result = await tool_workflow_start(workspace_ctx, name="wf_choice")

    assert "Which path?" in result.text
    assert result.widget.data["prompt"] == "Which path?"  # type: ignore[union-attr]
