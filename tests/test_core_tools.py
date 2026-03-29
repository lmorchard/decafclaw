"""Tests for core tools — think, current_time."""

from datetime import datetime

from decafclaw.tools.core import tool_current_time, tool_think


def test_tool_think_returns_input(ctx):
    """tool_think returns 'OK' (content is logged but not echoed)."""
    result = tool_think(ctx, content="hello")
    assert result == "OK"


def test_tool_current_time_returns_iso(ctx):
    """tool_current_time returns a string containing a parseable date."""
    result = tool_current_time(ctx)
    # Format is "YYYY-MM-DD HH:MM:SS (Weekday)" — extract the datetime part
    date_part = result.split("(")[0].strip()
    parsed = datetime.strptime(date_part, "%Y-%m-%d %H:%M:%S")
    assert parsed.year >= 2024


def test_tool_current_time_includes_timezone(ctx):
    """The output includes day-of-week info (the parenthetical weekday name)."""
    result = tool_current_time(ctx)
    # The format includes a weekday name in parens, e.g. "(Saturday)"
    assert "(" in result and ")" in result
    # Extract the weekday and verify it's a real day name
    weekday = result.split("(")[1].rstrip(")")
    valid_days = {"Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"}
    assert weekday in valid_days
