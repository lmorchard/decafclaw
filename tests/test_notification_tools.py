"""Tests for the ``send_notification`` agent tool."""

from __future__ import annotations

import pytest

from decafclaw import notifications as notifs
from decafclaw.tools.notification_tools import tool_send_notification


def _read_inbox(config):
    path = notifs._inbox_path(config)
    if not path.exists():
        return []
    import json
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


@pytest.mark.asyncio
async def test_writes_record_to_inbox(ctx):
    result = await tool_send_notification(
        ctx, title="Hello", body="world", priority="high",
    )
    assert "sent" in result.text.lower()
    records = _read_inbox(ctx.config)
    assert len(records) == 1
    rec = records[0]
    assert rec["title"] == "Hello"
    assert rec["body"] == "world"
    assert rec["priority"] == "high"
    assert rec["category"] == "agent"  # default


@pytest.mark.asyncio
async def test_defaults(ctx):
    """Default category is 'agent' and default priority is 'normal'."""
    await tool_send_notification(ctx, title="Ping")
    rec = _read_inbox(ctx.config)[0]
    assert rec["category"] == "agent"
    assert rec["priority"] == "normal"
    assert rec["body"] == ""


@pytest.mark.asyncio
async def test_custom_category(ctx):
    await tool_send_notification(
        ctx, title="t", category="research-task",
    )
    assert _read_inbox(ctx.config)[0]["category"] == "research-task"


@pytest.mark.asyncio
async def test_auto_populates_conv_id(ctx):
    ctx.conv_id = "conv-xyz"
    await tool_send_notification(ctx, title="t")
    assert _read_inbox(ctx.config)[0]["conv_id"] == "conv-xyz"


@pytest.mark.asyncio
async def test_link_passthrough(ctx):
    await tool_send_notification(
        ctx, title="t", link="https://example.com/details",
    )
    assert _read_inbox(ctx.config)[0]["link"] == "https://example.com/details"


@pytest.mark.asyncio
async def test_rejects_empty_title(ctx):
    result = await tool_send_notification(ctx, title="")
    assert "error" in result.text.lower()
    assert "title" in result.text.lower()
    # Nothing written to the inbox
    assert _read_inbox(ctx.config) == []


@pytest.mark.asyncio
async def test_rejects_whitespace_title(ctx):
    result = await tool_send_notification(ctx, title="   ")
    assert "error" in result.text.lower()
    assert _read_inbox(ctx.config) == []


@pytest.mark.asyncio
async def test_rejects_invalid_priority(ctx):
    result = await tool_send_notification(
        ctx, title="t", priority="urgent",
    )
    assert "error" in result.text.lower()
    assert "priority" in result.text.lower()
    assert _read_inbox(ctx.config) == []


@pytest.mark.asyncio
async def test_result_includes_record_id(ctx):
    result = await tool_send_notification(ctx, title="x")
    rec = _read_inbox(ctx.config)[0]
    # The returned text mentions the generated id so the agent can
    # reference it in a follow-up turn.
    assert rec["id"] in result.text


@pytest.mark.asyncio
async def test_dispatches_event_on_bus(ctx):
    """The tool uses the real notify() path, which publishes a
    `notification_created` event on ctx.event_bus — so channel adapters
    registered there will see it."""
    received: list[dict] = []
    ctx.event_bus.subscribe(lambda e: received.append(e))
    await tool_send_notification(ctx, title="Ping", priority="high")
    assert len(received) == 1
    assert received[0]["type"] == "notification_created"
    assert received[0]["record"]["title"] == "Ping"


@pytest.mark.asyncio
async def test_tool_is_registered():
    """Smoke-test the tool landed in the combined registry."""
    from decafclaw.tools import TOOL_DEFINITIONS, TOOLS
    assert "send_notification" in TOOLS
    names = {td["function"]["name"] for td in TOOL_DEFINITIONS}
    assert "send_notification" in names
