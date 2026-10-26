"""Deterministic lifecycle and readiness policy tests for MCP client (#1148)."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from decafclaw.context import Context
from decafclaw.events import EventBus
from decafclaw.mcp_client import (
    MCPConnectionError,
    MCPReadinessError,
    MCPRegistry,
    MCPServerConfig,
    MCPServerState,
    MCPTimeoutError,
    init_mcp,
    shutdown_mcp,
)
from decafclaw.schedules import ScheduleTask, parse_schedule_file, run_schedule_task
from decafclaw.tool_definitions import collect_all_tool_defs, refresh_dynamic_tools


def _make_fake_tool(name: str, description: str = "A tool"):
    tool = MagicMock()
    tool.name = name
    tool.description = description
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
async def test_connecting_and_ready_states_and_events():
    """Connecting server is tracked as connecting and not visible in tools until ready."""
    bus = EventBus()
    events = []

    async def on_event(ev):
        events.append(ev)

    bus.subscribe(on_event)

    proceed_event = asyncio.Event()
    mock_session = _make_mock_session([_make_fake_tool("echo_tool")])

    registry = MCPRegistry(event_bus=bus)

    async def slow_connect(exit_stack, cfg, message_handler=None):
        await proceed_event.wait()
        return mock_session

    registry._connect_stdio = slow_connect

    cfg = MCPServerConfig(name="slow-srv", type="stdio", command="dummy")
    task = registry.start_connect_server(cfg)

    # Allow event loop to start the task and publish connecting event
    await asyncio.sleep(0)

    state = registry.servers["slow-srv"]
    assert state.status == "connecting"
    assert not state.is_ready
    # Tools should remain hidden while connecting
    assert "mcp__slow_srv__echo_tool" not in registry.get_tools()
    assert len(registry.get_tool_definitions()) == 0

    assert any(e.get("type") == "mcp_server_connecting" and e.get("server") == "slow-srv" for e in events)
    assert not any(e.get("type") == "mcp_server_connected" for e in events)

    # Let connection finish
    proceed_event.set()
    await task

    assert state.status == "connected"
    assert state.is_ready
    assert "mcp__slow_srv__echo_tool" in registry.get_tools()
    assert len(registry.get_tool_definitions()) == 1
    assert any(e.get("type") == "mcp_server_connected" and e.get("server") == "slow-srv" for e in events)


@pytest.mark.asyncio
async def test_failed_connection_state_and_events():
    """Failed connection records error, sets status, publishes failure, and preserves healthy servers."""
    bus = EventBus()
    events = []

    async def on_event(ev):
        events.append(ev)

    bus.subscribe(on_event)

    registry = MCPRegistry(event_bus=bus)
    good_session = _make_mock_session([_make_fake_tool("good_tool")])

    async def mock_connect(exit_stack, cfg, message_handler=None):
        if cfg.name == "bad-srv":
            raise RuntimeError("handshake rejected")
        return good_session

    registry._connect_stdio = mock_connect

    good_cfg = MCPServerConfig(name="good-srv", type="stdio", command="good")
    bad_cfg = MCPServerConfig(name="bad-srv", type="stdio", command="bad")

    await registry.connect_all([good_cfg, bad_cfg], wait=True)

    assert registry.servers["good-srv"].status == "connected"
    assert registry.servers["bad-srv"].status == "failed"
    assert "handshake rejected" in (registry.servers["bad-srv"].error or "")

    # Healthy server tools are accessible; failed are not
    assert "mcp__good_srv__good_tool" in registry.get_tools()
    assert any(
        e.get("type") == "mcp_server_failed"
        and e.get("server") == "bad-srv"
        and "handshake rejected" in e.get("error", "")
        for e in events
    )


@pytest.mark.asyncio
async def test_canceled_connection_clean_drain():
    """Cancelling in-flight connection drains cleanly and cleans exit stack without leaking."""
    registry = MCPRegistry()
    never_ready = asyncio.Event()
    cleanup_called = False

    class MockExitStack:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            nonlocal cleanup_called
            cleanup_called = True

    async def slow_connect(exit_stack, cfg, message_handler=None):
        await never_ready.wait()
        return _make_mock_session()

    registry._connect_stdio = slow_connect

    cfg = MCPServerConfig(name="hang-srv", type="stdio", command="dummy")
    task = registry.start_connect_server(cfg)
    await asyncio.sleep(0)

    # Replace exit stack with our spy to verify cleanup on cancel
    state = registry.servers["hang-srv"]
    state._exit_stack = MockExitStack()

    await registry.disconnect_server("hang-srv")

    assert task.cancelled() or task.done()
    assert cleanup_called
    assert state.status == "disconnected"


@pytest.mark.asyncio
async def test_wait_for_server_immediate_when_connected():
    """wait_for_server returns True immediately if already connected."""
    registry = MCPRegistry()
    registry.servers["srv"] = MCPServerState(
        config=MCPServerConfig(name="srv", type="stdio"),
        status="connected",
    )
    registry.servers["srv"].settled_event.set()

    assert await registry.wait_for_server("srv", timeout=1.0) is True


@pytest.mark.asyncio
async def test_wait_for_server_waits_for_connecting():
    """wait_for_server waits until connecting server settles without fixed sleep."""
    registry = MCPRegistry()
    connect_gate = asyncio.Event()
    mock_session = _make_mock_session([_make_fake_tool("t1")])

    async def gated_connect(exit_stack, cfg, message_handler=None):
        await connect_gate.wait()
        return mock_session

    registry._connect_stdio = gated_connect

    cfg = MCPServerConfig(name="srv", type="stdio")
    task = registry.start_connect_server(cfg)
    await asyncio.sleep(0)

    waiter = asyncio.create_task(registry.wait_for_server("srv", timeout=2.0))
    await asyncio.sleep(0)
    assert not waiter.done()

    # Trigger connection completion
    connect_gate.set()
    result = await waiter
    assert result is True
    await task


@pytest.mark.asyncio
async def test_wait_for_server_fails_on_connection_error():
    """wait_for_server raises MCPConnectionError when server fails."""
    registry = MCPRegistry()

    async def fail_connect(exit_stack, cfg, message_handler=None):
        raise ConnectionRefusedError("remote port closed")

    registry._connect_stdio = fail_connect

    cfg = MCPServerConfig(name="broken", type="stdio")
    registry.start_connect_server(cfg)

    with pytest.raises(MCPConnectionError) as exc_info:
        await registry.wait_for_server("broken", timeout=2.0)
    assert "remote port closed" in str(exc_info.value)


@pytest.mark.asyncio
async def test_wait_for_server_fails_on_timeout():
    """wait_for_server raises MCPTimeoutError on deadline expiration."""
    registry = MCPRegistry()
    gate = asyncio.Event()

    async def hanging_connect(exit_stack, cfg, message_handler=None):
        await gate.wait()
        return _make_mock_session()

    registry._connect_stdio = hanging_connect

    cfg = MCPServerConfig(name="slow", type="stdio")
    registry.start_connect_server(cfg)

    with pytest.raises(MCPTimeoutError) as exc_info:
        await registry.wait_for_server("slow", timeout=0.01)
    assert "timed out waiting" in str(exc_info.value)
    await registry.disconnect_all()


@pytest.mark.asyncio
async def test_wait_for_server_unconfigured():
    """wait_for_server raises MCPReadinessError when server is not configured."""
    registry = MCPRegistry()
    with pytest.raises(MCPReadinessError) as exc_info:
        await registry.wait_for_server("nonexistent")
    assert "not configured" in str(exc_info.value)


@pytest.mark.asyncio
async def test_wait_for_servers_multiple_and_unrelated_slow_server():
    """wait_for_servers only waits for declared dependencies, ignoring unrelated slow servers."""
    registry = MCPRegistry()
    slow_gate = asyncio.Event()
    fast_session = _make_mock_session([_make_fake_tool("fast_tool")])

    async def mock_connect(exit_stack, cfg, message_handler=None):
        if cfg.name == "slow-unrelated":
            await slow_gate.wait()
            return _make_mock_session()
        return fast_session

    registry._connect_stdio = mock_connect

    fast_cfg = MCPServerConfig(name="dep-srv", type="stdio")
    slow_cfg = MCPServerConfig(name="slow-unrelated", type="stdio")

    registry.start_connect_server(fast_cfg)
    registry.start_connect_server(slow_cfg)

    # Waiting for only dep-srv finishes immediately without waiting for slow-unrelated
    res = await registry.wait_for_servers(["dep-srv"], timeout=2.0)
    assert res == {"dep-srv": True}
    assert registry.servers["slow-unrelated"].status == "connecting"

    await registry.disconnect_all()


@pytest.mark.asyncio
async def test_disconnect_all_cancels_and_drains_in_flight_tasks():
    """disconnect_all cancels and drains all active connection tasks."""
    registry = MCPRegistry()
    gate = asyncio.Event()

    async def hanging_connect(exit_stack, cfg, message_handler=None):
        await gate.wait()
        return _make_mock_session()

    registry._connect_stdio = hanging_connect

    t1 = registry.start_connect_server(MCPServerConfig(name="srv1", type="stdio"))
    t2 = registry.start_connect_server(MCPServerConfig(name="srv2", type="stdio"))
    await asyncio.sleep(0)

    assert len(registry._connection_tasks) == 2
    await registry.disconnect_all()

    assert t1.cancelled() or t1.done()
    assert t2.cancelled() or t2.done()
    assert len(registry._connection_tasks) == 0
    assert registry._closed is True


@pytest.mark.asyncio
async def test_reload_drains_old_registry_tasks(ctx, monkeypatch):
    """Reloading MCP servers cancels and drains previous generation tasks."""
    from decafclaw import mcp_client
    from decafclaw.skills.mcp.tools import _restart

    gate = asyncio.Event()
    old_registry = MCPRegistry()

    async def hanging_connect(exit_stack, cfg, message_handler=None):
        await gate.wait()
        return _make_mock_session()

    old_registry._connect_stdio = hanging_connect
    t_old = old_registry.start_connect_server(MCPServerConfig(name="old-srv", type="stdio"))
    monkeypatch.setattr(mcp_client, "_registry", old_registry)
    await asyncio.sleep(0)

    # Monkeypatch load_mcp_config to return a new config
    new_cfg = MCPServerConfig(name="new-srv", type="stdio")
    monkeypatch.setattr(mcp_client, "load_mcp_config", lambda cfg: [new_cfg])

    # Monkeypatch MCPRegistry._connect_stdio on newly created instances
    new_session = _make_mock_session([_make_fake_tool("new_tool")])
    monkeypatch.setattr(MCPRegistry, "_connect_stdio", AsyncMock(return_value=new_session))

    res = await _restart(ctx, old_registry, server_name="")

    assert "reloaded" in str(res)
    assert t_old.cancelled() or t_old.done()
    assert old_registry._closed is True
    assert mcp_client.get_registry() is not old_registry
    assert "new-srv" in mcp_client.get_registry().servers

    await mcp_client.shutdown_mcp()


@pytest.mark.asyncio
async def test_safe_turn_boundary_freezes_and_reconciles(ctx, monkeypatch):
    """Tool availability freezes during a turn and reconciles at turn start."""
    from decafclaw import mcp_client

    registry = MCPRegistry()

    registry.servers["srv_a"] = MCPServerState(
        config=MCPServerConfig(name="srv_a", type="stdio"),
        status="connected",
        tool_definitions=[{"function": {"name": "mcp__srv_a__tool_a"}}],
    )
    registry.servers["srv_b"] = MCPServerState(
        config=MCPServerConfig(name="srv_b", type="stdio"),
        status="connecting",
    )
    monkeypatch.setattr(mcp_client, "_registry", registry)

    # Turn 1 starts: safe turn boundary reconciles tools
    refresh_dynamic_tools(ctx)
    defs_turn1 = collect_all_tool_defs(ctx)
    names_turn1 = {d["function"]["name"] for d in defs_turn1}
    assert "mcp__srv_a__tool_a" in names_turn1
    assert "mcp__srv_b__tool_b" not in names_turn1

    # Freeze tools as TurnRunner does during a turn
    ctx.tools.mcp_frozen = True

    # Mid-turn: srv_b finishes connecting in background
    registry.servers["srv_b"].status = "connected"
    registry.servers["srv_b"].tool_definitions = [{"function": {"name": "mcp__srv_b__tool_b"}}]

    # Mid-turn dynamic refresh during iteration
    refresh_dynamic_tools(ctx)
    defs_mid_turn = collect_all_tool_defs(ctx)
    names_mid = {d["function"]["name"] for d in defs_mid_turn}
    # srv_b tools MUST NOT appear mid-turn
    assert "mcp__srv_b__tool_b" not in names_mid

    # Turn ends: unfreeze
    ctx.tools.mcp_frozen = False

    # Turn 2 starts: safe turn boundary reconciles tools
    refresh_dynamic_tools(ctx)
    defs_turn2 = collect_all_tool_defs(ctx)
    names_turn2 = {d["function"]["name"] for d in defs_turn2}
    # srv_b tools are now registered for the next eligible turn!
    assert "mcp__srv_b__tool_b" in names_turn2


@pytest.mark.asyncio
async def test_schedule_task_required_servers(ctx, tmp_path, monkeypatch):
    """Scheduled tasks wait for required servers and fail explicitly on deadline/failure."""
    from decafclaw import mcp_client

    registry = MCPRegistry()
    mock_session = _make_mock_session([_make_fake_tool("db_tool")])
    db_gate = asyncio.Event()

    async def mock_connect(exit_stack, cfg, message_handler=None):
        if cfg.name == "db":
            await db_gate.wait()
            return mock_session
        raise RuntimeError("failed server")

    registry._connect_stdio = mock_connect
    registry.start_connect_server(MCPServerConfig(name="db", type="stdio"))
    registry.start_connect_server(MCPServerConfig(name="bad", type="stdio"))
    monkeypatch.setattr(mcp_client, "_registry", registry)

    # 1. Task requiring "bad" fails with explicit error
    bad_task = ScheduleTask(
        name="task_bad",
        schedule="* * * * *",
        body="do something",
        source="admin",
        path=tmp_path / "task_bad.md",
        required_servers=["bad"],
        server_timeout=1.0,
    )
    dummy_manager = MagicMock()
    res_bad = await run_schedule_task(ctx.config, ctx.event_bus, dummy_manager, bad_task)
    assert res_bad["is_ok"] is False
    assert "failed to connect" in res_bad["response"]

    # 2. Task requiring "db" times out if gate is not set
    timeout_task = ScheduleTask(
        name="task_timeout",
        schedule="* * * * *",
        body="do something",
        source="admin",
        path=tmp_path / "task_timeout.md",
        required_servers=["db"],
        server_timeout=0.01,
    )
    res_timeout = await run_schedule_task(ctx.config, ctx.event_bus, dummy_manager, timeout_task)
    assert res_timeout["is_ok"] is False
    assert "timed out waiting" in res_timeout["response"]

    # 3. Task requiring "db" succeeds when gate is set
    db_gate.set()
    ok_task = ScheduleTask(
        name="task_ok",
        schedule="* * * * *",
        body="do something",
        source="admin",
        path=tmp_path / "task_ok.md",
        required_servers=["db"],
        server_timeout=2.0,
    )
    dummy_future = asyncio.Future()
    dummy_future.set_result(MagicMock(text="HEARTBEAT_OK", termination_reason=None))
    dummy_manager.enqueue_turn = AsyncMock(return_value=dummy_future)

    res_ok = await run_schedule_task(ctx.config, ctx.event_bus, dummy_manager, ok_task)
    assert res_ok["is_ok"] is True
    assert res_ok["response"] == "HEARTBEAT_OK"
    await registry.disconnect_all()


def test_parse_schedule_file_required_servers(tmp_path):
    """parse_schedule_file parses required-servers and server-timeout."""
    sched_file = tmp_path / "backup.md"
    sched_file.write_text(
        "---\n"
        "schedule: '0 0 * * *'\n"
        "required-servers:\n"
        "  - db\n"
        "  - storage\n"
        "server-timeout: 15\n"
        "---\n"
        "Run database backup.\n"
    )

    task = parse_schedule_file(sched_file)
    assert task is not None
    assert task.required_servers == ["db", "storage"]
    assert task.server_timeout == 15.0
    assert not task.unknown_keys


@pytest.mark.asyncio
async def test_reconcile_before_freeze_keeps_mcp_tools_frozen(ctx, monkeypatch):
    """reconcile_mcp_tools populates definitions before freeze, preventing mid-turn alteration."""
    from decafclaw import mcp_client
    from decafclaw.tool_definitions import reconcile_mcp_tools

    registry = MCPRegistry()
    registry.servers["srv_early"] = MCPServerState(
        config=MCPServerConfig(name="srv_early", type="stdio"),
        status="connected",
        tool_definitions=[{"function": {"name": "mcp__srv_early__tool1"}}],
    )
    registry.servers["srv_late"] = MCPServerState(
        config=MCPServerConfig(name="srv_late", type="stdio"),
        status="connecting",
    )
    monkeypatch.setattr(mcp_client, "_registry", registry)

    # Turn boundary: unfreeze, reconcile, freeze
    ctx.tools.mcp_frozen = False
    reconcile_mcp_tools(ctx)
    ctx.tools.mcp_frozen = True

    assert ctx.tools.mcp_definitions is not None
    assert len(ctx.tools.mcp_definitions) == 1
    assert ctx.tools.mcp_definitions[0]["function"]["name"] == "mcp__srv_early__tool1"

    # Mid-turn: late server completes
    registry.servers["srv_late"].status = "connected"
    registry.servers["srv_late"].tool_definitions = [{"function": {"name": "mcp__srv_late__tool2"}}]

    # Calling reconcile mid-turn does not alter the frozen definitions
    reconcile_mcp_tools(ctx)
    assert len(ctx.tools.mcp_definitions) == 1
    assert ctx.tools.mcp_definitions[0]["function"]["name"] == "mcp__srv_early__tool1"

    # Turn ends: unfreeze and reconcile for next turn
    ctx.tools.mcp_frozen = False
    reconcile_mcp_tools(ctx)
    assert len(ctx.tools.mcp_definitions) == 2


@pytest.mark.asyncio
async def test_unknown_server_type_cleanly_exits_exit_stack():
    """Unknown server type fails cleanly and cleans up exit stack."""
    registry = MCPRegistry()
    cleanup_called = False

    class MockExitStack:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            nonlocal cleanup_called
            cleanup_called = True

    cfg = MCPServerConfig(name="odd", type="custom_proto")
    state = MCPServerState(config=cfg, status="connecting", _exit_stack=MockExitStack())
    registry.servers["odd"] = state

    await registry.connect_server(cfg)

    assert state.status == "failed"
    assert "unknown server type" in (state.error or "")
    assert state.settled_event.is_set()
    assert state._exit_stack is None


@pytest.mark.asyncio
async def test_closed_registry_early_exit():
    """connect_server on closed registry sets disconnected status and settled_event."""
    registry = MCPRegistry()
    registry._closed = True

    cfg = MCPServerConfig(name="closed_srv", type="stdio")
    state = MCPServerState(config=cfg, status="connecting")
    registry.servers["closed_srv"] = state

    await registry.connect_server(cfg)

    assert state.status == "disconnected"
    assert state.settled_event.is_set()


@pytest.mark.asyncio
async def test_replacing_in_flight_connection_drains_without_clearing_replacement_exit_stack():
    """Replacing in-flight connection cancels old attempt without clobbering replacement's exit stack."""
    registry = MCPRegistry()
    gate1 = asyncio.Event()
    gate2 = asyncio.Event()
    cleanup1 = False
    cleanup2 = False

    async def record_cleanup1():
        nonlocal cleanup1
        cleanup1 = True

    async def record_cleanup2():
        nonlocal cleanup2
        cleanup2 = True

    stack2_ref = None
    attempt = 0

    async def mock_connect(exit_stack, cfg, message_handler=None):
        nonlocal stack2_ref, attempt
        if cfg.name == "replace_srv":
            attempt += 1
            if attempt == 1:
                exit_stack.push_async_callback(record_cleanup1)
                await gate1.wait()
            else:
                stack2_ref = exit_stack
                exit_stack.push_async_callback(record_cleanup2)
                await gate2.wait()
        return _make_mock_session([_make_fake_tool("t")])

    registry._connect_stdio = mock_connect

    cfg = MCPServerConfig(name="replace_srv", type="stdio")
    t1 = registry.start_connect_server(cfg)
    await asyncio.sleep(0)

    state = registry.servers["replace_srv"]

    # Start replacement while t1 is still pending
    t2 = registry.start_connect_server(cfg)
    await asyncio.sleep(0)

    # Verify t1 was cancelled and drained
    assert t1.cancelled() or t1.done()

    # Finish replacement
    gate1.set()
    gate2.set()
    await t2

    assert cleanup1 is True
    assert cleanup2 is False  # Replacement exit stack should NOT be torn down on success
    assert state._exit_stack is stack2_ref
    assert state.status == "connected"


@pytest.mark.asyncio
async def test_single_server_restart_cancels_previous_and_reconnects(ctx, monkeypatch):
    """Restarting a single server cancels any in-flight task and starts a replacement connection."""
    from decafclaw import mcp_client
    from decafclaw.skills.mcp.tools import _restart

    registry = MCPRegistry()
    gate = asyncio.Event()
    mock_session = _make_mock_session([_make_fake_tool("tool_x")])

    async def slow_connect(exit_stack, cfg, message_handler=None):
        await gate.wait()
        return mock_session

    registry._connect_stdio = slow_connect
    cfg = MCPServerConfig(name="restart_me", type="stdio")
    t_old = registry.start_connect_server(cfg)
    monkeypatch.setattr(mcp_client, "_registry", registry)
    await asyncio.sleep(0)

    res = await _restart(ctx, registry, server_name="restart_me")
    assert "restarting" in str(res)
    assert t_old.cancelled() or t_old.done()
    assert "restart_me" in registry._connection_tasks
    t_new = registry._connection_tasks["restart_me"]
    assert t_new is not t_old

    gate.set()
    await t_new
    assert registry.servers["restart_me"].status == "connected"


@pytest.mark.asyncio
async def test_cross_task_disconnect_anyio_cancel_scope_safety():
    """Disconnecting a server from a different task does not trigger AnyIO cancel scope errors."""
    import anyio

    registry = MCPRegistry()

    async def connect_with_anyio_scope(exit_stack, cfg, message_handler=None):
        scope = anyio.CancelScope()
        exit_stack.enter_context(scope)
        return _make_mock_session()

    registry._connect_stdio = connect_with_anyio_scope
    cfg = MCPServerConfig(name="anyio_srv", type="stdio")

    t = registry.start_connect_server(cfg)
    await registry.wait_for_server("anyio_srv")
    assert registry.servers["anyio_srv"].status == "connected"

    # Disconnect from a separate task
    async def external_disconnect():
        await registry.disconnect_server("anyio_srv")

    disconnect_task = asyncio.create_task(external_disconnect())
    await disconnect_task

    assert registry.servers["anyio_srv"].status == "disconnected"
    assert t.done()
