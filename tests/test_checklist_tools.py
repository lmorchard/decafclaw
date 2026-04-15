"""Tests for checklist tools."""

from decafclaw.media import ToolResult
from decafclaw.tools.checklist_tools import (
    tool_checklist_abort,
    tool_checklist_create,
    tool_checklist_status,
    tool_checklist_step_done,
)


def test_checklist_create(ctx):
    """Creating a checklist returns the first step."""
    result = tool_checklist_create(ctx, steps=["Step A", "Step B", "Step C"])
    assert isinstance(result, ToolResult)
    assert "3 steps" in result.text
    assert "Step A" in result.text


def test_checklist_create_empty(ctx):
    """Creating with empty steps returns an error."""
    result = tool_checklist_create(ctx, steps=[])
    assert "error" in result.text


def test_checklist_step_done_advances(ctx):
    """step_done marks current step and returns next (no end_turn mid-loop)."""
    tool_checklist_create(ctx, steps=["First", "Second", "Third"])
    result = tool_checklist_step_done(ctx, note="done with first")
    assert isinstance(result, ToolResult)
    assert result.end_turn is False  # mid-loop: agent keeps going
    assert "Second" in result.text


def test_checklist_step_done_all_complete(ctx):
    """step_done on last step returns completion message with end_turn."""
    tool_checklist_create(ctx, steps=["Only step"])
    result = tool_checklist_step_done(ctx)
    assert result.end_turn is True
    assert "complete" in result.text.lower()


def test_checklist_step_done_no_checklist(ctx):
    """step_done with no active checklist returns error."""
    result = tool_checklist_step_done(ctx)
    assert "error" in result.text.lower() or "no active" in result.text.lower()


def test_checklist_abort(ctx):
    """Aborting clears the checklist."""
    tool_checklist_create(ctx, steps=["Step 1", "Step 2"])
    result = tool_checklist_abort(ctx, reason="changed my mind")
    assert "aborted" in result.text.lower()
    assert "changed my mind" in result.text

    # Status should be empty after abort
    status = tool_checklist_status(ctx)
    assert "No active" in status.text


def test_checklist_abort_empty(ctx):
    """Aborting with no checklist is safe."""
    result = tool_checklist_abort(ctx)
    assert "No active" in result.text


def test_checklist_status(ctx):
    """Status shows progress."""
    tool_checklist_create(ctx, steps=["A", "B", "C"])
    tool_checklist_step_done(ctx)  # complete A

    status = tool_checklist_status(ctx)
    assert "[x]" in status.text
    assert "[ ]" in status.text
    assert "current" in status.text
    assert "1/3 complete" in status.text


def test_checklist_status_empty(ctx):
    """Status with no checklist says so."""
    result = tool_checklist_status(ctx)
    assert "No active" in result.text
