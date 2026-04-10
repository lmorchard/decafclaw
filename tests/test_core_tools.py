"""Tests for core tools — current_time."""

from datetime import datetime

from decafclaw.tools.core import tool_current_time


def test_tool_current_time_returns_parseable_datetime(ctx):
    """tool_current_time returns a parseable datetime string."""
    result = tool_current_time(ctx)
    # Format is "YYYY-MM-DD HH:MM:SS (Weekday)" — extract the datetime part
    date_part = result.split("(")[0].strip()
    parsed = datetime.strptime(date_part, "%Y-%m-%d %H:%M:%S")
    assert parsed.year >= 2024


def test_tool_current_time_includes_weekday(ctx):
    """The output includes a parenthetical weekday name."""
    result = tool_current_time(ctx)
    # The format includes a weekday name in parens, e.g. "(Saturday)"
    assert "(" in result and ")" in result
    # Extract the weekday and verify it's a real day name
    weekday = result.split("(")[1].rstrip(")")
    valid_days = {"Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"}
    assert weekday in valid_days
