"""Tests for the config change watcher (#149)."""

import asyncio
import json

from decafclaw.config import load_config
from decafclaw.config_watcher import config_change_watcher

TEST_INTERVAL = 0.05
RESTART_GRACE = 0.05


def _pin_env(monkeypatch, tmp_path) -> None:
    """Pin env so a fresh load_config() is deterministic and file-driven.

    DECACLAW_LOAD_DOTENV=0 keeps the checkout's .env (real credentials,
    HTTP_PORT) out of the watcher's fresh loads; the explicit pins give
    load_config the same agent dir the fixture config points at.
    """
    monkeypatch.setenv("DECACLAW_LOAD_DOTENV", "0")
    monkeypatch.setenv("DATA_HOME", str(tmp_path))
    monkeypatch.setenv("AGENT_ID", "test-agent")
    monkeypatch.setenv("HTTP_PORT", "18880")  # HttpConfig.port default


def _align_config(ctx, monkeypatch, tmp_path) -> None:
    """Make the live config a real load_config() product, as in production."""
    _pin_env(monkeypatch, tmp_path)
    ctx.config = load_config()


def _write_config(tmp_path, data: dict, filename: str = "config.json"):
    path = tmp_path / "test-agent" / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    return path


async def _run_watcher(ctx, **overrides):
    shutdown_event = asyncio.Event()
    kwargs = {"interval": TEST_INTERVAL, "restart_grace_sec": RESTART_GRACE, **overrides}
    task = asyncio.create_task(config_change_watcher(ctx, shutdown_event, **kwargs))
    return task, shutdown_event


async def _wait_for(condition, timeout: float = 3.0) -> None:
    """Poll until condition() holds; fail with a timeout if it never does."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if condition():
            return
        await asyncio.sleep(0.02)
    if not condition():
        raise AssertionError("timed out waiting for condition")


async def _cancel(task) -> None:
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def _settle(intervals: float = 3.0):
    """Let the watcher's poll loop run a few no-change passes."""
    await asyncio.sleep(TEST_INTERVAL * intervals)


async def test_no_change_is_silent(ctx, monkeypatch, tmp_path):
    """No config change → no events, no shutdown, watcher keeps running."""
    _align_config(ctx, monkeypatch, tmp_path)

    task, shutdown_event = await _run_watcher(ctx)
    events: list[dict] = []
    sub_id = ctx.event_bus.subscribe(events.append)

    try:
        await _settle()
        assert not shutdown_event.is_set()
        assert not task.done()
        assert events == []
    finally:
        ctx.event_bus.unsubscribe(sub_id)
        await _cancel(task)


async def test_safe_change_applies_and_publishes(ctx, monkeypatch, tmp_path):
    """A safe section change applies in place and announces exactly that section."""
    _align_config(ctx, monkeypatch, tmp_path)
    # Simulate runtime state a real process would hold.
    ctx.config.discovered_skills = ["sentinel-skill"]

    task, shutdown_event = await _run_watcher(ctx)
    events: list[dict] = []
    sub_id = ctx.event_bus.subscribe(events.append)

    try:
        await _settle()
        _write_config(tmp_path, {"llm": {"model": "custom-model"}})

        await _wait_for(lambda: ctx.config.llm.model == "custom-model")
        assert not shutdown_event.is_set()
        assert len(events) == 1
        assert events[0]["type"] == "config_changed"
        assert events[0]["requires_restart"] is False
        # model_configs migrates from the llm section, so it may cascade here.
        assert "llm" in events[0]["keys"]
        # Runtime-only state must survive the apply untouched.
        assert ctx.config.discovered_skills == ["sentinel-skill"]
    finally:
        ctx.event_bus.unsubscribe(sub_id)
        await _cancel(task)


async def test_safe_change_reverts_when_file_reverts(ctx, monkeypatch, tmp_path):
    """Reverting a section in the file must revert the live config (#149 review)."""
    _align_config(ctx, monkeypatch, tmp_path)

    task, shutdown_event = await _run_watcher(ctx)
    events: list[dict] = []
    sub_id = ctx.event_bus.subscribe(events.append)

    try:
        await _settle()

        _write_config(tmp_path, {"llm": {"model": "custom-model"}})
        await _wait_for(lambda: ctx.config.llm.model == "custom-model")

        _write_config(tmp_path, {})
        await _wait_for(lambda: ctx.config.llm.model != "custom-model")

        assert not shutdown_event.is_set()
        assert len(events) == 2
        assert events[-1]["requires_restart"] is False
        assert "llm" in events[-1]["keys"]
    finally:
        ctx.event_bus.unsubscribe(sub_id)
        await _cancel(task)


async def test_safe_sections_beyond_the_original_trio_apply(ctx, monkeypatch, tmp_path):
    """Every non-restart section applies in place, not just llm/agent/compaction."""
    _align_config(ctx, monkeypatch, tmp_path)

    task, shutdown_event = await _run_watcher(ctx)
    events: list[dict] = []
    sub_id = ctx.event_bus.subscribe(events.append)

    try:
        await _settle()
        assert ctx.config.notes.enabled is True

        _write_config(tmp_path, {"notes": {"enabled": False}})
        await _wait_for(lambda: ctx.config.notes.enabled is False)

        assert events == [{"type": "config_changed", "requires_restart": False, "keys": ["notes"]}]
    finally:
        ctx.event_bus.unsubscribe(sub_id)
        await _cancel(task)


async def test_unsafe_section_signals_restart(ctx, monkeypatch, tmp_path):
    """An http section change publishes requires_restart and stops the watcher."""
    _align_config(ctx, monkeypatch, tmp_path)

    task, shutdown_event = await _run_watcher(ctx)
    events: list[dict] = []
    sub_id = ctx.event_bus.subscribe(events.append)

    try:
        await _settle()

        _write_config(tmp_path, {"http": {"enabled": True}})

        await _wait_for(shutdown_event.is_set)
        await asyncio.wait_for(asyncio.shield(task), timeout=1.0)

        assert events == [{"type": "config_changed", "requires_restart": True, "keys": ["http"]}]
    finally:
        ctx.event_bus.unsubscribe(sub_id)
        if not task.done():
            await _cancel(task)


async def test_mcp_servers_change_requires_restart(ctx, monkeypatch, tmp_path):
    """An mcp_servers.json change is restart-required (#149 review: it was ignored)."""
    _align_config(ctx, monkeypatch, tmp_path)

    task, shutdown_event = await _run_watcher(ctx)
    events: list[dict] = []
    sub_id = ctx.event_bus.subscribe(events.append)

    try:
        await _settle()

        _write_config(
            tmp_path,
            {"mcpServers": {"tester": {"command": "echo"}}},
            filename="mcp_servers.json",
        )

        await _wait_for(shutdown_event.is_set)
        await asyncio.wait_for(asyncio.shield(task), timeout=1.0)

        assert events == [{"type": "config_changed", "requires_restart": True, "keys": ["mcp_servers"]}]
        assert ctx.config.llm.model != "custom-model"  # untouched
    finally:
        ctx.event_bus.unsubscribe(sub_id)
        if not task.done():
            await _cancel(task)


async def test_unparseable_config_is_skipped_until_it_parses(ctx, monkeypatch, tmp_path):
    """A torn/partial write must never reach the live config (#149 review)."""
    _align_config(ctx, monkeypatch, tmp_path)

    task, shutdown_event = await _run_watcher(ctx)
    events: list[dict] = []
    sub_id = ctx.event_bus.subscribe(events.append)

    try:
        await _settle()

        # Establish a non-default live model, as a real session would have.
        _write_config(tmp_path, {"llm": {"model": "custom-a"}})
        await _wait_for(lambda: ctx.config.llm.model == "custom-a")

        # Torn write: readable but invalid JSON.
        config_file = tmp_path / "test-agent" / "config.json"
        config_file.write_text('{"llm": {"mod')
        await _settle(6.0)
        assert len(events) == 1  # the torn write was skipped, not announced
        assert "llm" in events[0]["keys"]  # only the earlier apply so far
        assert ctx.config.llm.model == "custom-a"
        assert not shutdown_event.is_set()

        # The completed write is still seen as a change.
        _write_config(tmp_path, {"llm": {"model": "custom-b"}})
        await _wait_for(lambda: ctx.config.llm.model == "custom-b")
        assert len(events) == 2
        assert events[-1]["requires_restart"] is False
        assert "llm" in events[-1]["keys"]
    finally:
        ctx.event_bus.unsubscribe(sub_id)
        await _cancel(task)
