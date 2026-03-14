"""Tests for core tools."""

from decafclaw.tools.core import tool_think


def test_think_returns_ok(ctx):
    result = tool_think(ctx, "I should search for cocktails first")
    assert result == "OK"


def test_think_with_empty_content(ctx):
    result = tool_think(ctx, "")
    assert result == "OK"
