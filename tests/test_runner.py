"""Tests for runner orchestrator startup and MCP lifecycle (#1148)."""

import asyncio
import contextlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from decafclaw.context import Context
from decafclaw.conversation_manager import ConversationManager
from decafclaw.events import EventBus
from decafclaw.mcp_client import (
    MCPRegistry,
    MCPServerConfig,
    shutdown_mcp,
)
from decafclaw.runner import run_all


def _make_fake_tool(name: str):
    tool = MagicMock()
    tool.name = name
    tool.description = "description"
    tool.inputSchema = {"type": "object", "properties": {}}
    return tool


def _make_mock_session(tools=None):
    session = AsyncMock()
    tools_result = MagicMock()
    tools_result.tools = tools or []
    session.list_tools = AsyncMock(return_value=tools_result)
    session.get_server_capabilities = MagicMock(return_value=MagicMock(resources=None, prompts=None))
    return session


@pytest.mark.asyncio
async def test_runner_starts_http_while_mcp_handshake_delayed(config):
    """runner.py starts HTTP server while slow MCP connection is still pending."""
    config.http.enabled = True
    bus = EventBus()
    ctx = Context(config=config, event_bus=bus)

    mcp_gate = asyncio.Event()
    http_started = asyncio.Event()
    http_stop = asyncio.Event()

    async def slow_connect_stdio(self, exit_stack, server_config, message_handler=None):
        await mcp_gate.wait()
        return _make_mock_session([_make_fake_tool("delayed_tool")])

    async def mock_run_http_server(*args, **kwargs):
        http_started.set()
        await http_stop.wait()

    async def mock_shutdown_http_server():
        http_stop.set()

    async def mock_schedule_timer(*args, **kwargs):
        pass

    cfg = MCPServerConfig(name="slow-mcp", type="stdio", command="dummy")

    with (
        patch("decafclaw.mcp_client.load_mcp_config", return_value=[cfg]),
        patch.object(MCPRegistry, "_connect_stdio", slow_connect_stdio),
        patch("decafclaw.http_server.run_http_server", side_effect=mock_run_http_server),
        patch("decafclaw.http_server.shutdown_http_server", side_effect=mock_shutdown_http_server),
        patch("decafclaw.schedules.run_schedule_timer", side_effect=mock_schedule_timer),
        patch("decafclaw.workspace_index.start_workspace_index_loop"),
        patch.object(asyncio.get_event_loop(), "add_signal_handler"),
        patch("decafclaw.runner.signal.SIGTERM", 15),
        patch("decafclaw.runner.signal.SIGINT", 2),
    ):
        runner_task = asyncio.create_task(run_all(ctx))

        # Wait until HTTP server starts
        await asyncio.wait_for(http_started.wait(), timeout=5.0)

        # HTTP is started, but MCP is still connecting!
        from decafclaw import mcp_client

        registry = mcp_client.get_registry()
        assert registry is not None
        assert "slow-mcp" in registry.servers
        assert registry.servers["slow-mcp"].status == "connecting"
        assert "mcp__slow_mcp__delayed_tool" not in registry.get_tools()

        # Now shut down while MCP is still connecting
        runner_task.cancel()
        try:
            await asyncio.wait_for(runner_task, timeout=5.0)
        except (asyncio.CancelledError, Exception):
            pass

        # Cleanup should have drained
        assert registry._closed is True
        await shutdown_mcp()


@pytest.mark.asyncio
async def test_runner_late_mcp_registration(config):
    """Late-completing MCP connection registers tools and is visible across turns."""
    config.http.enabled = True
    bus = EventBus()
    ctx = Context(config=config, event_bus=bus)

    mcp_gate = asyncio.Event()
    http_started = asyncio.Event()
    http_stop = asyncio.Event()

    mock_session = _make_mock_session([_make_fake_tool("late_tool")])

    async def slow_connect_stdio(self, exit_stack, server_config, message_handler=None):
        await mcp_gate.wait()
        return mock_session

    async def mock_run_http_server(*args, **kwargs):
        http_started.set()
        await http_stop.wait()

    async def mock_shutdown_http_server():
        http_stop.set()

    cfg = MCPServerConfig(name="late-mcp", type="stdio", command="dummy")

    with (
        patch("decafclaw.mcp_client.load_mcp_config", return_value=[cfg]),
        patch.object(MCPRegistry, "_connect_stdio", slow_connect_stdio),
        patch("decafclaw.http_server.run_http_server", side_effect=mock_run_http_server),
        patch("decafclaw.http_server.shutdown_http_server", side_effect=mock_shutdown_http_server),
        patch("decafclaw.schedules.run_schedule_timer", new_callable=AsyncMock),
        patch("decafclaw.workspace_index.start_workspace_index_loop"),
        patch.object(asyncio.get_event_loop(), "add_signal_handler"),
        patch("decafclaw.runner.signal.SIGTERM", 15),
        patch("decafclaw.runner.signal.SIGINT", 2),
    ):
        runner_task = asyncio.create_task(run_all(ctx))
        await asyncio.wait_for(http_started.wait(), timeout=5.0)

        from decafclaw import mcp_client
        from decafclaw.tool_definitions import collect_all_tool_defs, refresh_dynamic_tools

        registry = mcp_client.get_registry()
        assert registry.servers["late-mcp"].status == "connecting"

        # Turn 1: tool not yet available
        refresh_dynamic_tools(ctx)
        defs1 = collect_all_tool_defs(ctx)
        assert not any(d["function"]["name"] == "mcp__late_mcp__late_tool" for d in defs1)

        # MCP connection settles
        mcp_gate.set()
        await asyncio.wait_for(registry.wait_for_server("late-mcp"), timeout=2.0)
        assert registry.servers["late-mcp"].status == "connected"

        # Turn 2: tool is now registered and available
        refresh_dynamic_tools(ctx)
        defs2 = collect_all_tool_defs(ctx)
        assert any(d["function"]["name"] == "mcp__late_mcp__late_tool" for d in defs2)

        runner_task.cancel()
        try:
            await asyncio.wait_for(runner_task, timeout=5.0)
        except (asyncio.CancelledError, Exception):
            pass
        await shutdown_mcp()


def _schedule_switch_mocks(schedule_timer):
    """Shared mocks for the schedule-switch tests: run_all reaches the
    schedule-timer decision, then `post_schedule` is set (the next statement
    after it in run_all), so waiting on it proves the decision was executed."""
    post_schedule = asyncio.Event()

    async def mock_run_http_server(*args, **kwargs):
        pass

    async def scan_wakes(self):
        post_schedule.set()

    return (
        post_schedule,
        (
            patch("decafclaw.http_server.run_http_server", side_effect=mock_run_http_server),
            patch("decafclaw.http_server.shutdown_http_server", new_callable=AsyncMock),
            patch("decafclaw.schedules.run_schedule_timer", side_effect=schedule_timer),
            patch("decafclaw.workspace_index.start_workspace_index_loop"),
            patch.object(ConversationManager, "startup_scan", new_callable=AsyncMock),
            patch.object(ConversationManager, "startup_scan_workflows", new_callable=AsyncMock),
            patch.object(ConversationManager, "startup_scan_scheduled_wakes", scan_wakes),
            patch.object(asyncio.get_event_loop(), "add_signal_handler"),
            patch("decafclaw.runner.signal.SIGTERM", 15),
            patch("decafclaw.runner.signal.SIGINT", 2),
        ),
    )


async def _run_until_schedule_decision(ctx, patches, timeout=10.0):
    runner_task = asyncio.create_task(run_all(ctx))
    try:
        await patches[0].wait()
        return runner_task
    except BaseException:
        runner_task.cancel()
        raise


@pytest.mark.asyncio
async def test_runner_schedule_timer_disabled(config):
    """The schedule timer does not start when schedules.enabled is false (#957)."""
    config.http.enabled = True
    config.heartbeat.interval = ""
    config.schedules.enabled = False
    bus = EventBus()
    ctx = Context(config=config, event_bus=bus)

    schedule_started = asyncio.Event()

    async def schedule_timer(*args, **kwargs):
        schedule_started.set()

    post_schedule, patches = _schedule_switch_mocks(schedule_timer)
    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        runner_task = await _run_until_schedule_decision(ctx, (post_schedule, patches))
        await asyncio.sleep(0)  # let any (mistakenly created) timer task run
        assert not schedule_started.is_set()
        runner_task.cancel()
        try:
            await asyncio.wait_for(runner_task, timeout=5.0)
        except (asyncio.CancelledError, Exception):
            pass


@pytest.mark.asyncio
async def test_runner_schedule_timer_enabled(config):
    """Control: with schedules.enabled true (the default) the timer starts."""
    config.http.enabled = True
    config.heartbeat.interval = ""
    config.schedules.enabled = True
    bus = EventBus()
    ctx = Context(config=config, event_bus=bus)

    schedule_started = asyncio.Event()

    async def schedule_timer(*args, **kwargs):
        schedule_started.set()
        await asyncio.Event().wait()  # keep running until cancelled

    post_schedule, patches = _schedule_switch_mocks(schedule_timer)
    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        runner_task = await _run_until_schedule_decision(ctx, (post_schedule, patches))
        await asyncio.wait_for(schedule_started.wait(), timeout=5.0)
        assert schedule_started.is_set()
        runner_task.cancel()
        try:
            await asyncio.wait_for(runner_task, timeout=5.0)
        except (asyncio.CancelledError, Exception):
            pass
