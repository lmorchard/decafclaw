"""Generic per-call timeout wrapper for execute_tool."""

import asyncio
import dataclasses
import time

import pytest

from decafclaw.media import ToolResult
from decafclaw.skills.tabstack.tools import (
    TOOL_DEFINITIONS as TABSTACK_TOOL_DEFINITIONS,
)
from decafclaw.tool_execution import _tool_heartbeat, execute_single_tool
from decafclaw.tools import TOOL_DEFINITIONS, TOOLS, execute_tool


def _set_timeout(ctx, seconds: float | None):
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


@pytest.fixture
def fast_tool_timeout(monkeypatch):
    """Scale resolved timeouts down 5x so 1s cases run in 0.2s.

    The real resolver still decides the value (global default, per-tool
    override, or None), so override precedence is exercised; only the wall
    clock is shortened.
    """
    import decafclaw.tools as tools_mod

    real = tools_mod._resolve_tool_timeout

    def scaled(ctx, name):
        value = real(ctx, name)
        return value / 5 if value else None

    monkeypatch.setattr(tools_mod, "_resolve_tool_timeout", scaled)


@pytest.mark.asyncio
async def test_fast_tool_returns_normally(ctx):
    async def fast_tool(ctx):
        return "hello"

    _register_extra_tool(ctx, "fast_tool", fast_tool)
    result = await _safe_execute(ctx, "fast_tool")
    assert isinstance(result, ToolResult)
    assert result.text == "hello"


@pytest.mark.asyncio
async def test_hanging_tool_times_out_at_default(ctx, fast_tool_timeout):
    async def hang(ctx):
        await asyncio.sleep(10)
        return "never"

    _register_extra_tool(ctx, "hang", hang)
    _set_timeout(ctx, 1)

    start = time.monotonic()
    result = await _safe_execute(ctx, "hang")
    elapsed = time.monotonic() - start

    assert "timed out" in result.text
    assert "hang" in result.text
    assert elapsed < 1.0, f"expected ~0.2s, got {elapsed:.2f}s"


@pytest.mark.asyncio
async def test_timeout_reports_tool_progress(ctx, fast_tool_timeout):
    """A tool that records partial progress gets it named in the timeout
    result, so an interrupted bulk write is diagnosable (#1687)."""

    async def partial(ctx):
        ctx.tools.progress_text = "37 page(s) written so far"
        await asyncio.sleep(10)
        return "never"

    _register_extra_tool(ctx, "partial", partial)
    _set_timeout(ctx, 1)

    result = await _safe_execute(ctx, "partial")

    assert "timed out" in result.text
    assert "37 page(s) written so far" in result.text


@pytest.mark.asyncio
async def test_cancel_reports_tool_progress(ctx):
    """The interrupt path reports partial progress too (#1687)."""

    async def partial(ctx):
        ctx.tools.progress_text = "12 page(s) written so far"
        await asyncio.sleep(10)
        return "never"

    _register_extra_tool(ctx, "partial", partial)
    ctx.cancelled = asyncio.Event()
    asyncio.get_running_loop().call_later(0.1, ctx.cancelled.set)

    result = await _safe_execute(ctx, "partial")

    assert "interrupted" in result.text
    assert "12 page(s) written so far" in result.text


@pytest.mark.asyncio
async def test_per_tool_short_override_wins(ctx, fast_tool_timeout):
    async def hang(ctx):
        await asyncio.sleep(10)
        return "never"

    _register_extra_tool(ctx, "hang", hang, timeout_key=1, include_timeout_key=True)
    _set_timeout(ctx, 300)

    start = time.monotonic()
    result = await _safe_execute(ctx, "hang")
    elapsed = time.monotonic() - start

    assert "timed out" in result.text
    assert elapsed < 1.0


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
async def test_sync_tool_timeout(ctx, fast_tool_timeout):
    # Sync tools run in to_thread; the thread can't be preempted but the
    # wrapping task is cancelled and the timeout error returned. Keep the
    # inner sleep short — pytest waits for the thread at teardown.
    def sync_hang(ctx):
        time.sleep(0.3)
        return "never"

    _register_extra_tool(ctx, "sync_hang", sync_hang)
    _set_timeout(ctx, 1)

    start = time.monotonic()
    result = await _safe_execute(ctx, "sync_hang")
    elapsed = time.monotonic() - start

    assert "timed out" in result.text
    assert elapsed < 1.0


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

    monkeypatch.setattr("decafclaw.mcp_client.get_registry", lambda: FakeRegistry())
    # Global default of 1s would cut off a 10s sleep, but our fake returns
    # instantly — the assertion is that we get its return value, not a
    # timeout error (i.e. the MCP branch was used, not the generic wrapper).
    _set_timeout(ctx, 1)
    result = await _safe_execute(ctx, "mcp__foo__bar")
    assert mcp_called["ran"] is True
    assert result.text == "mcp ok"


def test_tabstack_research_has_configured_timeout():
    """Regression guard for #613: tabstack_research's iterative research
    takes longer than the 180s TOOL_TIMEOUT_SEC default; the definition
    carries an explicit 600s timeout. If a future tabstack refactor
    drops the override, this test flags it before /research silently
    regresses to timing out again."""
    entry = next(
        (d for d in TABSTACK_TOOL_DEFINITIONS if (d.get("function") or {}).get("name") == "tabstack_research"),
        None,
    )
    assert entry is not None, "tabstack_research not in TOOL_DEFINITIONS"
    assert entry.get("timeout") == 600, f"expected timeout=600, got {entry.get('timeout')!r}"


@pytest.mark.asyncio
async def test_confirmation_active_pauses_tool_timeout(ctx, fast_tool_timeout):
    """When a confirmation prompt is active, the tool execution watchdog pauses."""

    async def prompt_tool(ctx):
        ctx.confirmation_active.set()
        await asyncio.sleep(0.5)
        ctx.confirmation_active.clear()
        return "approved and finished"

    _register_extra_tool(ctx, "prompt_tool", prompt_tool)
    _set_timeout(ctx, 1)

    # 1s watchdog (scaled to 0.2s) would fire before 0.5s if confirmation_active didn't pause it
    result = await _safe_execute(ctx, "prompt_tool")
    assert result.text == "approved and finished"


@pytest.mark.asyncio
async def test_timeout_logs_watchdog_warning(ctx, caplog, fast_tool_timeout):
    """The watchdog announces the kill instead of silently returning (#1569)."""

    async def hang(ctx):
        await asyncio.sleep(10)

    _register_extra_tool(ctx, "hang", hang)
    _set_timeout(ctx, 1)

    with caplog.at_level("WARNING", logger="decafclaw.tools"):
        result = await _safe_execute(ctx, "hang")

    assert result.timed_out is True
    assert result.timeout_sec == 0.2
    warnings = [r for r in caplog.records if "[watchdog]" in r.message]
    assert warnings, "watchdog did not log a warning"
    assert "hang" in warnings[0].message


@pytest.mark.asyncio
async def test_timeout_tags_tool_end_and_tool_message(ctx, fast_tool_timeout):
    """tool_end and the archived tool message carry structured outcome tags."""

    async def hang(ctx):
        await asyncio.sleep(10)

    _register_extra_tool(ctx, "hang", hang)
    _set_timeout(ctx, 1)

    events: list[dict] = []

    async def _capture(ev):
        events.append(ev)

    ctx.event_bus.subscribe(_capture)
    tc = {"id": "tc-timeout", "function": {"name": "hang", "arguments": "{}"}}
    tool_msg, _ = await execute_single_tool(ctx, tc, asyncio.Semaphore(1))

    end = next(e for e in events if e.get("type") == "tool_end")
    assert end["is_error"] is True
    assert end["timed_out"] is True
    assert end["timeout_sec"] == 0.2

    assert tool_msg["is_error"] is True
    assert tool_msg["timed_out"] is True
    assert tool_msg["timeout_sec"] == 0.2


@pytest.mark.asyncio
async def test_success_tags_tool_end_without_error(ctx):
    """A successful tool is not tagged as an error."""

    async def fast(ctx):
        return "fine"

    _register_extra_tool(ctx, "fast", fast)

    events: list[dict] = []

    async def _capture(ev):
        events.append(ev)

    ctx.event_bus.subscribe(_capture)
    tc = {"id": "tc-ok", "function": {"name": "fast", "arguments": "{}"}}
    tool_msg, _ = await execute_single_tool(ctx, tc, asyncio.Semaphore(1))

    end = next(e for e in events if e.get("type") == "tool_end")
    assert "is_error" not in end
    assert "timed_out" not in end
    assert "is_error" not in tool_msg


@pytest.mark.asyncio
async def test_long_tool_emits_progress_heartbeat(ctx, monkeypatch):
    """A silent long-running tool produces periodic tool_status events (#1569)."""

    from decafclaw import tool_execution

    monkeypatch.setattr(tool_execution, "TOOL_HEARTBEAT_INTERVAL_SEC", 0.01)

    statuses: list[dict] = []
    release = asyncio.Event()

    async def _capture(ev):
        if ev.get("type") == "tool_status" and ev.get("tool") == "slow_tool":
            statuses.append(ev)
            release.set()

    ctx.event_bus.subscribe(_capture)

    async def slow_tool(ctx):
        await asyncio.wait_for(release.wait(), timeout=5)
        return "done"

    _register_extra_tool(ctx, "slow_tool", slow_tool)
    tc = {"id": "tc-heartbeat", "function": {"name": "slow_tool", "arguments": "{}"}}
    tool_msg, _ = await execute_single_tool(ctx, tc, asyncio.Semaphore(1))

    assert tool_msg["content"] == "done"
    assert statuses, "no heartbeat tool_status emitted"
    assert "still running" in statuses[0]["message"]
    assert statuses[0]["tool_call_id"] == "tc-heartbeat"


@pytest.mark.asyncio
async def test_heartbeat_quiet_when_tool_reports_progress(ctx):
    """A native tool_status suppresses the heartbeat for the next interval."""
    statuses: list[dict] = []

    async def _capture(ev):
        if ev.get("type") == "tool_status":
            statuses.append(ev)

    ctx.event_bus.subscribe(_capture)

    ticks = 0
    statuses_at_tick2: list[int] = []

    async def fake_sleep(_seconds):
        nonlocal ticks
        ticks += 1
        if ticks == 1:
            # The tool published its own progress during the first interval.
            ctx.tools.last_status_at = time.monotonic()
        elif ticks == 2:
            statuses_at_tick2.append(len(statuses))
        elif ticks >= 3:
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await _tool_heartbeat(ctx, "slow_tool", "tc-hb", time.monotonic(), interval=1.0, sleep=fake_sleep)

    assert statuses_at_tick2 == [0], "heartbeat fired despite native progress"
    # Once the native report ages out, the heartbeat resumes.
    assert len(statuses) == 1


@pytest.mark.asyncio
async def test_heartbeat_quiet_during_confirmation(ctx):
    """A paused watchdog (awaiting a human) produces no progress heartbeats."""
    statuses: list[dict] = []

    async def _capture(ev):
        if ev.get("type") == "tool_status":
            statuses.append(ev)

    ctx.event_bus.subscribe(_capture)

    ticks = 0

    async def fake_sleep(_seconds):
        nonlocal ticks
        ticks += 1
        if ticks == 1:
            ctx.confirmation_active.set()
        elif ticks >= 3:
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await _tool_heartbeat(ctx, "prompt_tool", "tc-confirm", time.monotonic(), interval=1.0, sleep=fake_sleep)

    assert statuses == []


def test_confirmation_gated_tools_have_timeout_none():
    """Regression guard for #969: confirmation-gated tools opt out of the
    generic 180s watchdog via explicit timeout: None so user deliberation
    time is not capped by tool_timeout_sec."""
    checked = [
        "shell",
        "shell_guidance",
        "activate_skill",
        "admin_write",
        "admin_replace_lines",
        "admin_edit",
        "admin_delete",
        "update_system_prompt",
        "http_request",
        "send_email",
    ]
    for tool_name in checked:
        entry = next(
            (d for d in TOOL_DEFINITIONS if (d.get("function") or {}).get("name") == tool_name),
            None,
        )
        assert entry is not None, f"{tool_name} not found in TOOL_DEFINITIONS"
        assert "timeout" in entry, f"{tool_name} must explicitly declare 'timeout' key"
        assert entry.get("timeout") is None, f"{tool_name} expected timeout=None, got {entry.get('timeout')!r}"
