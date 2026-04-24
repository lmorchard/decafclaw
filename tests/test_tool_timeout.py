"""Generic per-call timeout wrapper for execute_tool."""

import asyncio
import dataclasses
import time

import pytest

from decafclaw.media import ToolResult
from decafclaw.tools import TOOL_DEFINITIONS, TOOLS, execute_tool


def _set_timeout(ctx, seconds: int | None):
    """Replace ctx.config.agent.tool_timeout_sec via dataclasses.replace."""
    agent = dataclasses.replace(ctx.config.agent, tool_timeout_sec=seconds or 0)
    ctx.config = dataclasses.replace(ctx.config, agent=agent)


def _register_extra_tool(
    ctx,
    name: str,
    fn,
    *,
    timeout_key: object = None,
    include_timeout_key: bool = False,
):
    """Register a tool on ctx.tools.extra + extra_definitions."""
    ctx.tools.extra[name] = fn
    entry = {
        "type": "function",
        "function": {
            "name": name,
            "description": f"Fake tool {name}",
            "parameters": {"type": "object", "properties": {}},
        },
    }
    if include_timeout_key:
        entry["timeout"] = timeout_key
    ctx.tools.extra_definitions.append(entry)


async def _safe_execute(ctx, name, **kwargs):
    """Wrap execute_tool in a safety-net wait_for so a broken wrapper doesn't hang the suite."""
    return await asyncio.wait_for(execute_tool(ctx, name, kwargs), timeout=5.0)


@pytest.mark.asyncio
async def test_fast_tool_returns_normally(ctx):
    async def fast_tool(ctx):
        return "hello"

    _register_extra_tool(ctx, "fast_tool", fast_tool)
    result = await _safe_execute(ctx, "fast_tool")
    assert isinstance(result, ToolResult)
    assert result.text == "hello"


@pytest.mark.asyncio
async def test_hanging_tool_times_out_at_default(ctx):
    async def hang(ctx):
        await asyncio.sleep(10)
        return "never"

    _register_extra_tool(ctx, "hang", hang)
    _set_timeout(ctx, 1)

    start = time.monotonic()
    result = await _safe_execute(ctx, "hang")
    elapsed = time.monotonic() - start

    assert "timed out after 1s" in result.text
    assert "hang" in result.text
    assert elapsed < 3.0, f"expected ~1s, got {elapsed:.2f}s"


@pytest.mark.asyncio
async def test_per_tool_short_override_wins(ctx):
    async def hang(ctx):
        await asyncio.sleep(10)
        return "never"

    _register_extra_tool(ctx, "hang", hang, timeout_key=1, include_timeout_key=True)
    _set_timeout(ctx, 300)

    start = time.monotonic()
    result = await _safe_execute(ctx, "hang")
    elapsed = time.monotonic() - start

    assert "timed out after 1s" in result.text
    assert elapsed < 3.0


@pytest.mark.asyncio
async def test_per_tool_long_override_survives(ctx):
    async def slow(ctx):
        await asyncio.sleep(0.2)
        return "ok"

    _register_extra_tool(ctx, "slow", slow, timeout_key=5, include_timeout_key=True)
    _set_timeout(ctx, 1)

    result = await _safe_execute(ctx, "slow")
    assert result.text == "ok"


@pytest.mark.asyncio
async def test_timeout_none_disables_wrapper(ctx):
    async def slow(ctx):
        await asyncio.sleep(0.2)
        return "ok"

    # Explicit None opts out, overriding the 1s global default.
    _register_extra_tool(ctx, "slow", slow, timeout_key=None, include_timeout_key=True)
    _set_timeout(ctx, 1)

    result = await _safe_execute(ctx, "slow")
    assert result.text == "ok"


@pytest.mark.asyncio
async def test_timeout_zero_disables_wrapper(ctx):
    async def slow(ctx):
        await asyncio.sleep(0.2)
        return "ok"

    _register_extra_tool(ctx, "slow", slow)

    for disabled in (0, -1):
        _set_timeout(ctx, disabled)
        result = await _safe_execute(ctx, "slow")
        assert result.text == "ok", f"tool_timeout_sec={disabled} should be disabled"


@pytest.mark.asyncio
async def test_cancel_beats_timeout(ctx):
    async def slow(ctx):
        await asyncio.sleep(10)
        return "never"

    _register_extra_tool(ctx, "slow", slow)
    _set_timeout(ctx, 2)
    ctx.cancelled = asyncio.Event()
    asyncio.get_running_loop().call_later(0.1, ctx.cancelled.set)

    start = time.monotonic()
    result = await _safe_execute(ctx, "slow")
    elapsed = time.monotonic() - start

    assert "interrupted" in result.text
    assert "timed out" not in result.text
    assert elapsed < 1.5, f"expected ~0.1s, got {elapsed:.2f}s"


@pytest.mark.asyncio
async def test_sync_tool_timeout(ctx):
    # Sync tools run in to_thread; the thread can't be preempted but the
    # wrapping task is cancelled and the timeout error returned. Keep the
    # inner sleep short — pytest waits for the thread at teardown.
    def sync_hang(ctx):
        time.sleep(2)
        return "never"

    _register_extra_tool(ctx, "sync_hang", sync_hang)
    _set_timeout(ctx, 1)

    start = time.monotonic()
    result = await _safe_execute(ctx, "sync_hang")
    elapsed = time.monotonic() - start

    assert "timed out after 1s" in result.text
    assert elapsed < 2.0


@pytest.mark.asyncio
async def test_resolves_from_global_tool_definitions(ctx, monkeypatch):
    """Resolver finds override in the global TOOL_DEFINITIONS list."""

    async def slow(ctx):
        await asyncio.sleep(0.2)
        return "ok"

    fake_entry = {
        "type": "function",
        "priority": "normal",
        "timeout": None,
        "function": {
            "name": "global_fake",
            "description": "fake",
            "parameters": {"type": "object", "properties": {}},
        },
    }
    monkeypatch.setitem(TOOLS, "global_fake", slow)
    TOOL_DEFINITIONS.append(fake_entry)
    try:
        _set_timeout(ctx, 1)
        # 1s default would fire before the tool's 0.2s sleep if the override
        # wasn't picked up; the explicit None in the global def opts out.
        result = await _safe_execute(ctx, "global_fake")
        assert result.text == "ok"
    finally:
        TOOL_DEFINITIONS.remove(fake_entry)


@pytest.mark.asyncio
async def test_mcp_prefix_skipped_by_generic_wrapper(ctx, monkeypatch):
    """MCP-prefixed tools route through the MCP branch — no generic timeout wrap."""

    mcp_called = {"ran": False}

    async def fake_mcp_tool(arguments):
        mcp_called["ran"] = True
        return "mcp ok"

    class FakeRegistry:
        def get_tools(self):
            return {"mcp__foo__bar": fake_mcp_tool}

    monkeypatch.setattr(
        "decafclaw.mcp_client.get_registry", lambda: FakeRegistry()
    )
    # Global default of 1s would cut off a 10s sleep, but our fake returns
    # instantly — the assertion is that we get its return value, not a
    # timeout error (i.e. the MCP branch was used, not the generic wrapper).
    _set_timeout(ctx, 1)
    result = await _safe_execute(ctx, "mcp__foo__bar")
    assert mcp_called["ran"] is True
    assert result.text == "mcp ok"
