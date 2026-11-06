"""Tests for core tools — current time, web fetch, and debug context."""

from datetime import datetime

import httpx
import pytest

from decafclaw.media import ToolResult, WidgetRequest
from decafclaw.tools.core import (
    _code_language_for_content_type,
    tool_current_time,
    tool_debug_context,
    tool_web_fetch,
)


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


# -- web fetch widget attachment -------------------------------------------


def test_code_language_for_content_type_maps_markup_and_code():
    """Content types that carry code or markup resolve to a code_block language."""
    assert _code_language_for_content_type("text/html; charset=utf-8") == "html"
    assert _code_language_for_content_type("application/xhtml+xml") == "html"
    assert _code_language_for_content_type("application/json") == "json"
    assert _code_language_for_content_type("text/css") == "css"
    assert _code_language_for_content_type("text/plain") is None
    assert _code_language_for_content_type("") is None


def _fake_async_client(response):
    """Build a minimal httpx.AsyncClient stand-in whose get() returns a canned response."""

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url):
            return response

    return _FakeAsyncClient


@pytest.mark.asyncio
async def test_web_fetch_attaches_code_block_widget_for_html(ctx, monkeypatch):
    """web_fetch attaches a code_block widget when the content type is HTML."""
    monkeypatch.setattr(
        "decafclaw.tools.core.httpx.AsyncClient",
        _fake_async_client(
            httpx.Response(
                200,
                text="<html><body>hi</body></html>",
                headers={"content-type": "text/html"},
                request=httpx.Request("GET", "http://example.com"),
            )
        ),
    )

    result = await tool_web_fetch(ctx, url="http://example.com")

    assert isinstance(result, ToolResult)
    assert result.widget is not None
    assert result.widget.widget_type == "code_block"
    assert result.widget.target == "inline"
    assert result.widget.data["code"] == "<html><body>hi</body></html>"
    assert result.widget.data["language"] == "html"
    assert result.widget.data["filename"] == "http://example.com"
    assert result.text == "<html><body>hi</body></html>"


@pytest.mark.asyncio
async def test_web_fetch_skips_code_block_widget_for_plain_text(ctx, monkeypatch):
    """web_fetch leaves the widget unset for prose content types."""
    monkeypatch.setattr(
        "decafclaw.tools.core.httpx.AsyncClient",
        _fake_async_client(
            httpx.Response(
                200,
                text="Just prose.",
                headers={"content-type": "text/plain"},
                request=httpx.Request("GET", "http://example.com"),
            )
        ),
    )

    result = await tool_web_fetch(ctx, url="http://example.com")

    assert isinstance(result, ToolResult)
    assert result.widget is None
    assert result.text == "Just prose."


# -- debug context widget attachment ---------------------------------------


def test_debug_context_attaches_json_view_widget(ctx):
    """debug_context attaches a json_view widget over the structured context."""
    ctx.messages = [
        {"role": "system", "content": "You are a test agent."},
        {"role": "user", "content": "Hello"},
    ]
    result = tool_debug_context(ctx)

    assert isinstance(result, ToolResult)
    assert result.widget is not None
    assert result.widget.widget_type == "json_view"
    assert result.widget.target == "inline"
    assert result.widget.data["value"]["messages"] == ctx.messages
    assert result.widget.data.get("expand_depth") == 1
