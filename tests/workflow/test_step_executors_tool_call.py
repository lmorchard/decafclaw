"""Tests for the tool_call step executor.

Covers:
  - Success path: tool result lands in output as {text, data}.
  - Args rendering: Jinja templates in args resolve against state.
  - Tool error: exception from execute_single_tool → StepResult with error output.
  - tool_status event: ctx.publish called with current_tool_call_id set.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from decafclaw.media import ToolResult
from decafclaw.workflow.step_executors import StepResult, execute
from decafclaw.workflow.types import EdgeRef, RunStatus, StepDef, StepKind, WorkflowState


@pytest.fixture
def workflow_state():
    return WorkflowState(
        workflow="test_wf",
        run_id="run-001",
        conv_id="conv-1",
        initial_step="list_ws",
        current_step="list_ws",
        status=RunStatus.RUNNING,
        state={},
        transitions=[],
    )


@pytest.fixture
def tool_call_step():
    return StepDef(
        id="list_ws",
        kind=StepKind.TOOL_CALL,
        config={"tool": "workspace_list", "args": {"path": ""}},
    )


@pytest.mark.asyncio
async def test_tool_call_success_path(ctx, tool_call_step, workflow_state):
    """Success path: mock execute_single_tool returns ToolResult; output matches {text, data}."""
    fake_result = ToolResult(text="dir listing", data={"files": ["a.txt", "b.md"]})

    with patch(
        "decafclaw.tools.execute_tool",
        new=AsyncMock(return_value=fake_result),
    ):
        result = await execute(ctx, tool_call_step, workflow_state)

    assert isinstance(result, StepResult)
    assert result.output == {"text": "dir listing", "data": {"files": ["a.txt", "b.md"]}}
    assert result.next_step is None  # no next edges → terminal
    assert result.suspend_status is None


@pytest.mark.asyncio
async def test_tool_call_output_text_only(ctx, tool_call_step, workflow_state):
    """When tool returns no data, output.data is None."""
    fake_result = ToolResult(text="some output")

    with patch(
        "decafclaw.tools.execute_tool",
        new=AsyncMock(return_value=fake_result),
    ):
        result = await execute(ctx, tool_call_step, workflow_state)

    assert result.output["text"] == "some output"
    assert result.output["data"] is None


@pytest.mark.asyncio
async def test_tool_call_args_jinja_rendering(ctx, workflow_state):
    """Jinja templates in args resolve against state."""
    # Prior step output in state
    workflow_state.state["prior"] = {"name": "foo"}

    step = StepDef(
        id="read_file",
        kind=StepKind.TOOL_CALL,
        config={"tool": "vault_read", "args": {"path": "{{ state.prior.name }}"}},
    )

    fake_result = ToolResult(text="file contents")
    captured_args: dict = {}

    async def fake_execute_tool(call_ctx, name, args):
        captured_args.update(args)
        return fake_result

    with patch(
        "decafclaw.tools.execute_tool",
        new=fake_execute_tool,
    ):
        await execute(ctx, step, workflow_state)

    # Jinja rendered "{{ state.prior.name }}" → "foo"
    assert captured_args["path"] == "foo"


@pytest.mark.asyncio
async def test_tool_call_args_non_string_passthrough(ctx, workflow_state):
    """Non-string arg values are passed through without Jinja rendering."""
    step = StepDef(
        id="some_step",
        kind=StepKind.TOOL_CALL,
        config={"tool": "some_tool", "args": {"limit": 42, "enabled": True}},
    )

    fake_result = ToolResult(text="ok")
    captured_args: dict = {}

    async def fake_execute_tool(call_ctx, name, args):
        captured_args.update(args)
        return fake_result

    with patch(
        "decafclaw.tools.execute_tool",
        new=fake_execute_tool,
    ):
        await execute(ctx, step, workflow_state)

    assert captured_args["limit"] == 42
    assert captured_args["enabled"] is True


@pytest.mark.asyncio
async def test_tool_call_no_args(ctx, workflow_state):
    """Tool call with no args (omitted or empty) works without error."""
    step = StepDef(
        id="no_args_step",
        kind=StepKind.TOOL_CALL,
        config={"tool": "some_tool"},
    )

    fake_result = ToolResult(text="ok")
    captured_args: dict = {}

    async def fake_execute_tool(call_ctx, name, args):
        captured_args.update(args)
        return fake_result

    with patch(
        "decafclaw.tools.execute_tool",
        new=fake_execute_tool,
    ):
        result = await execute(ctx, step, workflow_state)

    assert result.output["text"] == "ok"
    assert captured_args == {}


@pytest.mark.asyncio
async def test_tool_call_error_propagates(ctx, tool_call_step, workflow_state):
    """Exception from execute_tool propagates out of _execute_tool_call."""
    async def fail(*args, **kwargs):
        raise RuntimeError("tool exploded")

    with patch(
        "decafclaw.tools.execute_tool",
        new=fail,
    ), pytest.raises(RuntimeError, match="tool exploded"):
        await execute(ctx, tool_call_step, workflow_state)


@pytest.mark.asyncio
async def test_tool_call_resolves_next_step(ctx, workflow_state):
    """next_edge is resolved after tool_call succeeds."""
    step = StepDef(
        id="list_ws",
        kind=StepKind.TOOL_CALL,
        config={"tool": "workspace_list", "args": {"path": ""}},
        next_edges=(EdgeRef(to="step2"),),
    )

    fake_result = ToolResult(text="listing")

    with patch(
        "decafclaw.tools.execute_tool",
        new=AsyncMock(return_value=fake_result),
    ):
        result = await execute(ctx, step, workflow_state)

    assert result.next_step == "step2"


@pytest.mark.asyncio
async def test_tool_call_publishes_tool_end_with_canonical_fields(
    ctx, tool_call_step, workflow_state
):
    """tool_end event includes all five canonical fields to match agent-loop shape.

    Canonical shape (tool_execution.execute_single_tool): result_text,
    display_text, display_short_text, media, widget (conditional), tool_call_id.
    JS consumer (tool-status-store.js) accesses display_short_text and widget.
    """
    from decafclaw.media import WidgetRequest

    fake_widget = WidgetRequest(widget_type="text", data={"content": "hello"})
    fake_result = ToolResult(
        text="tool output",
        display_text="Display text",
        display_short_text="Short",
        media=[{"url": "http://example.com/img.png"}],
        widget=fake_widget,
    )

    published_events: list[dict] = []

    original_publish = ctx.publish

    async def capturing_publish(event_type, **kwargs):
        published_events.append({"type": event_type, **kwargs})
        await original_publish(event_type, **kwargs)

    ctx.publish = capturing_publish

    with patch(
        "decafclaw.tools.execute_tool",
        new=AsyncMock(return_value=fake_result),
    ), patch(
        "decafclaw.tool_execution.resolve_widget",
        return_value={"widget_type": "text", "target": "inline", "data": {"content": "hello"}},
    ):
        await execute(ctx, tool_call_step, workflow_state)

    tool_end_events = [e for e in published_events if e["type"] == "tool_end"]
    assert len(tool_end_events) == 1, f"Expected 1 tool_end event, got: {tool_end_events}"
    evt = tool_end_events[0]

    assert evt["result_text"] == "tool output"
    assert evt["display_text"] == "Display text"
    assert evt["display_short_text"] == "Short"
    assert evt["media"] == [{"url": "http://example.com/img.png"}]
    assert evt["widget"] == {"widget_type": "text", "target": "inline", "data": {"content": "hello"}}
    assert "tool_call_id" in evt and evt["tool_call_id"]


@pytest.mark.asyncio
async def test_tool_call_publishes_with_tool_call_id(ctx, tool_call_step, workflow_state):
    """execute_tool is called with a forked ctx that has current_tool_call_id set."""
    fake_result = ToolResult(text="ok")
    captured_ctx = None

    async def capture_ctx(call_ctx, name, args):
        nonlocal captured_ctx
        captured_ctx = call_ctx
        return fake_result

    with patch(
        "decafclaw.tools.execute_tool",
        new=capture_ctx,
    ):
        await execute(ctx, tool_call_step, workflow_state)

    # The forked ctx should have current_tool_call_id set (not None/empty)
    assert captured_ctx is not None
    assert captured_ctx.tools.current_call_id is not None
    assert captured_ctx.tools.current_call_id != ""
    # The forked ctx should be a different object (actually forked)
    assert captured_ctx is not ctx
