"""Tests for to-do list tools."""

from decafclaw.tools.todo_tools import (
    tool_todo_add,
    tool_todo_clear,
    tool_todo_complete,
    tool_todo_list,
)


def test_todo_add_creates_item(ctx):
    """Adding an item reports success and shows up in the list."""
    result = tool_todo_add(ctx, item="Buy milk")
    assert "Buy milk" in result
    assert "1 total" in result


def test_todo_complete_checks_item(ctx):
    """Marking an item complete changes its status."""
    tool_todo_add(ctx, item="Write tests")
    result = tool_todo_complete(ctx, index=1)
    assert "Completed" in result
    assert "Write tests" in result
    # Verify it shows as checked in the list
    listing = tool_todo_list(ctx)
    assert "[x]" in listing


def test_todo_list_shows_items(ctx):
    """Listing items returns correct format with numbering and checkboxes."""
    tool_todo_add(ctx, item="First task")
    tool_todo_add(ctx, item="Second task")
    listing = tool_todo_list(ctx)
    assert "1. [ ] First task" in listing
    assert "2. [ ] Second task" in listing
    assert "0/2 complete" in listing


def test_todo_clear_removes_all(ctx):
    """tool_todo_clear removes all items from the list."""
    tool_todo_add(ctx, item="Temporary task")
    result = tool_todo_clear(ctx)
    assert "cleared" in result.lower()
    listing = tool_todo_list(ctx)
    assert "No to-do items" in listing
