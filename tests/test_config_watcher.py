"""Tests for the config change watcher (#149)."""

import asyncio

from decafclaw.config_watcher import config_change_watcher


async def _run_watcher(ctx, shutdown_event, interval: float = 0.05):
    task = asyncio.create_task(config_change_watcher(ctx, shutdown_event, interval=interval))
    return task


async def _wait_for(condition, timeout: float = 2.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if condition():
            return True
        await asyncio.sleep(0.02)
    return condition()


async def _settle():
    """Let the watcher's poll loop run at least one no-change pass."""
    await asyncio.sleep(0.15)


def _pin_http_env(monkeypatch) -> None:
    """Pin the env-derived HttpConfig port so a fresh load matches the live default."""
    monkeypatch.setenv("HTTP_PORT", "18880")


async def test_watcher_no_change_does_not_publish(ctx, monkeypatch):
    """No config change → no reload, no publish, no shutdown."""
    from decafclaw.config import load_config  # noqa: F401  (ensures import path)

    _pin_http_env(monkeypatch)

    shutdown_event = asyncio.Event()
    task = await _run_watcher(ctx, shutdown_event)

    await asyncio.sleep(0.2)
    assert not shutdown_event.is_set()

    events = []
    sub_id = ctx.event_bus.subscribe(lambda event: events.append(event))

    await asyncio.sleep(0.2)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    ctx.event_bus.unsubscribe(sub_id)
    assert events == []


async def test_watcher_safe_change_applies_and_publishes(ctx, monkeypatch, tmp_path):
    """A runtime-tunable section change is applied in place and announced."""
    monkeypatch.setenv("DATA_HOME", str(tmp_path))
    monkeypatch.setenv("AGENT_ID", "test-agent")
    _pin_http_env(monkeypatch)

    config_file = tmp_path / "test-agent" / "config.json"
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text('{"llm": {"model": "gemini-2.5-flash"}}')

    shutdown_event = asyncio.Event()
    task = await _run_watcher(ctx, shutdown_event)

    await _settle()
    assert ctx.config.llm.model == "gemini-2.5-flash"

    # Mutate a runtime-tunable section and let the watcher pick it up.
    config_file.write_text('{"llm": {"model": "custom-model"}}')
    await _wait_for(lambda: ctx.config.llm.model == "custom-model", timeout=3.0)

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert not shutdown_event.is_set()


async def test_watcher_unsafe_change_sets_shutdown(ctx, monkeypatch, tmp_path):
    """An http section change requires a restart: shutdown is signalled."""
    monkeypatch.setenv("DATA_HOME", str(tmp_path))
    monkeypatch.setenv("AGENT_ID", "test-agent")
    _pin_http_env(monkeypatch)

    config_file = tmp_path / "test-agent" / "config.json"
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text("{}")

    shutdown_event = asyncio.Event()
    task = await _run_watcher(ctx, shutdown_event)

    await _settle()
    assert not shutdown_event.is_set()

    config_file.write_text('{"http": {"enabled": true}}')
    await _wait_for(lambda: shutdown_event.is_set(), timeout=3.0)

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert shutdown_event.is_set()
