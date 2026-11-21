"""Tests for core tools — current time, web fetch, and debug context."""

from datetime import datetime

import httpx
import pytest

from decafclaw.media import ToolResult, WidgetRequest
from decafclaw.tools.core import (
    _format_bytes,
    _media_type,
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


# -- web fetch widget attachment (#1625) ------------------------------------


def test_media_type_strips_header_parameters():
    """_media_type returns the bare media type from a Content-Type value."""
    assert _media_type("text/html; charset=utf-8") == "text/html"
    assert _media_type("APPLICATION/JSON") == "application/json"
    assert _media_type("") == ""


def test_format_bytes_humanizes_sizes():
    """_format_bytes uses B / KB / MB like the data_table widget."""
    assert _format_bytes(0) == "0 B"
    assert _format_bytes(1023) == "1023 B"
    assert _format_bytes(48213) == "47.1 KB"
    assert _format_bytes(5 * 1024 * 1024) == "5.0 MB"


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


def _patch_response(monkeypatch, *, url="http://example.com", **kwargs):
    """Patch httpx.AsyncClient in core to return a canned response."""
    monkeypatch.setattr(
        "decafclaw.tools.core.httpx.AsyncClient",
        _fake_async_client(
            httpx.Response(
                kwargs.pop("status", 200),
                request=httpx.Request("GET", url),
                **kwargs,
            )
        ),
    )


@pytest.mark.asyncio
async def test_web_fetch_attaches_outcome_widget_for_html(ctx, monkeypatch):
    """web_fetch attaches the outcome widget instead of dumping the body."""
    _patch_response(
        monkeypatch,
        text="<html><head><title>Hi &amp; bye</title></head><body>hi</body></html>",
        headers={"content-type": "text/html; charset=utf-8"},
    )

    result = await tool_web_fetch(ctx, url="http://example.com")

    assert isinstance(result, ToolResult)
    assert result.widget is not None
    assert result.widget.widget_type == "web_fetch_result"
    assert result.widget.target == "inline"

    data = result.widget.data
    assert data["status"] == 200
    assert data["status_text"] == "OK"
    assert data["url"] == "http://example.com"
    assert data["content_type"] == "text/html"
    assert data["bytes"] == len(result.text.encode())
    assert isinstance(data["elapsed_ms"], int)
    assert data["truncated"] is False
    assert data["title"] == "Hi & bye"
    # The body deliberately stays out of the widget payload.
    assert "code" not in data and "body" not in data
    # The body is still the LLM-facing text contract.
    assert result.text.startswith("<html><head><title>")


@pytest.mark.asyncio
async def test_web_fetch_widget_reports_plain_text_without_title(ctx, monkeypatch):
    """Non-HTML content types get no title but keep the rest of the outcome."""
    _patch_response(monkeypatch, text="Just prose.", headers={"content-type": "text/plain"})

    result = await tool_web_fetch(ctx, url="http://example.com")

    assert result.text == "Just prose."
    assert result.widget.widget_type == "web_fetch_result"
    assert result.widget.data["content_type"] == "text/plain"
    assert "title" not in result.widget.data


@pytest.mark.asyncio
async def test_web_fetch_display_short_text_summarizes_the_outcome(ctx, monkeypatch):
    """The collapsed tool row gets status, type, size, and timing (#1625)."""
    _patch_response(monkeypatch, text="x" * 2048, headers={"content-type": "text/plain"})

    result = await tool_web_fetch(ctx, url="http://example.com")

    short = result.display_short_text
    assert short is not None
    assert short.startswith("200 OK")
    assert "text/plain" in short
    assert "2.0 KB" in short
    assert "ms" in short
    assert "truncated" not in short


@pytest.mark.asyncio
async def test_web_fetch_widget_marks_truncation(ctx, monkeypatch):
    """A body past the cap is truncated identically in text and widget."""
    _patch_response(monkeypatch, text="x" * 60000, headers={"content-type": "text/plain"})

    result = await tool_web_fetch(ctx, url="http://example.com")

    assert result.widget.data["truncated"] is True
    assert "[truncated at 50000 chars]" in result.text
    assert result.display_short_text.endswith("· truncated")
    # The widget reports the true body size, not the capped one.
    assert result.widget.data["bytes"] == 60000


@pytest.mark.asyncio
async def test_web_fetch_widget_reports_redirect_target(ctx, monkeypatch):
    """A followed redirect shows up as final_url and in the short text."""
    _patch_response(
        monkeypatch,
        url="https://example.com/final",
        text="ok",
        headers={"content-type": "text/plain"},
    )

    result = await tool_web_fetch(ctx, url="http://example.com")

    assert result.widget.data["url"] == "http://example.com"
    assert result.widget.data["final_url"] == "https://example.com/final"


@pytest.mark.asyncio
async def test_web_fetch_http_error_is_text_only(ctx, monkeypatch):
    """Failures stay widget-less so the error badge isn't doubled by a card."""
    _patch_response(monkeypatch, status=404, text="nope", headers={"content-type": "text/html"})

    result = await tool_web_fetch(ctx, url="http://example.com")

    assert isinstance(result, ToolResult)
    assert result.widget is None
    assert result.display_short_text is None
    assert result.text.startswith("[error: ")


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
