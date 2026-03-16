"""Tests for heartbeat — parsing, interval, section logic, cycle runner, and timer."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from decafclaw.heartbeat import (
    build_section_prompt,
    is_heartbeat_ok,
    load_heartbeat_sections,
    parse_interval,
    run_heartbeat_cycle,
    run_heartbeat_timer,
)

# -- interval parsing tests --


def test_parse_interval_minutes():
    assert parse_interval("30m") == 1800


def test_parse_interval_hours():
    assert parse_interval("1h") == 3600


def test_parse_interval_hours_and_minutes():
    assert parse_interval("1h30m") == 5400


def test_parse_interval_plain_seconds():
    assert parse_interval("90") == 90


def test_parse_interval_empty():
    assert parse_interval("") is None


def test_parse_interval_zero():
    assert parse_interval("0") is None


def test_parse_interval_invalid():
    assert parse_interval("garbage") is None


def test_parse_interval_whitespace():
    assert parse_interval("  30m  ") == 1800


# -- section parsing tests --


def test_load_sections_admin_only(config):
    admin_path = config.agent_path / "HEARTBEAT.md"
    admin_path.parent.mkdir(parents=True, exist_ok=True)
    admin_path.write_text("## Check status\n\nLook at the thing.\n")

    sections = load_heartbeat_sections(config)
    assert len(sections) == 1
    assert sections[0]["title"] == "Check status"
    assert "Look at the thing" in sections[0]["body"]


def test_load_sections_workspace_only(config):
    ws_path = config.workspace_path / "HEARTBEAT.md"
    ws_path.parent.mkdir(parents=True, exist_ok=True)
    ws_path.write_text("## Agent task\n\nDo the thing.\n")

    sections = load_heartbeat_sections(config)
    assert len(sections) == 1
    assert sections[0]["title"] == "Agent task"


def test_load_sections_merged(config):
    admin_path = config.agent_path / "HEARTBEAT.md"
    admin_path.parent.mkdir(parents=True, exist_ok=True)
    admin_path.write_text("## Admin task\n\nAdmin stuff.\n")

    ws_path = config.workspace_path / "HEARTBEAT.md"
    ws_path.parent.mkdir(parents=True, exist_ok=True)
    ws_path.write_text("## Agent task\n\nAgent stuff.\n")

    sections = load_heartbeat_sections(config)
    assert len(sections) == 2
    assert sections[0]["title"] == "Admin task"
    assert sections[1]["title"] == "Agent task"


def test_load_sections_missing_files(config):
    sections = load_heartbeat_sections(config)
    assert sections == []


def test_load_sections_content_before_header(config):
    admin_path = config.agent_path / "HEARTBEAT.md"
    admin_path.parent.mkdir(parents=True, exist_ok=True)
    admin_path.write_text("Do this checklist item.\n\n## Also this\n\nMore stuff.\n")

    sections = load_heartbeat_sections(config)
    assert len(sections) == 2
    assert sections[0]["title"] == "General"
    assert "checklist item" in sections[0]["body"]
    assert sections[1]["title"] == "Also this"


def test_load_sections_multiple(config):
    admin_path = config.agent_path / "HEARTBEAT.md"
    admin_path.parent.mkdir(parents=True, exist_ok=True)
    admin_path.write_text(
        "## First\n\nOne.\n\n## Second\n\nTwo.\n\n## Third\n\nThree.\n"
    )

    sections = load_heartbeat_sections(config)
    assert len(sections) == 3
    assert [s["title"] for s in sections] == ["First", "Second", "Third"]


def test_load_sections_empty_file(config):
    admin_path = config.agent_path / "HEARTBEAT.md"
    admin_path.parent.mkdir(parents=True, exist_ok=True)
    admin_path.write_text("")

    sections = load_heartbeat_sections(config)
    assert sections == []


# -- HEARTBEAT_OK detection tests --


def test_is_heartbeat_ok_present():
    assert is_heartbeat_ok("HEARTBEAT_OK") is True


def test_is_heartbeat_ok_case_insensitive():
    assert is_heartbeat_ok("Everything is fine. heartbeat_ok") is True


def test_is_heartbeat_ok_beyond_300_chars():
    padding = "x" * 300
    assert is_heartbeat_ok(padding + "HEARTBEAT_OK") is False


def test_is_heartbeat_ok_not_present():
    assert is_heartbeat_ok("Something happened that needs attention.") is False


# -- prompt building tests --


def test_build_section_prompt_titled():
    section = {"title": "Check status", "body": "Look at the thing."}
    prompt = build_section_prompt(section)
    assert "scheduled heartbeat check" in prompt
    assert "HEARTBEAT_OK" in prompt
    assert "## Check status" in prompt
    assert "Look at the thing." in prompt


def test_build_section_prompt_general():
    section = {"title": "General", "body": "Do the checklist."}
    prompt = build_section_prompt(section)
    assert "## General" not in prompt
    assert "Do the checklist." in prompt
    assert "HEARTBEAT_OK" in prompt


# -- cycle runner tests --


@pytest.mark.asyncio
async def test_run_heartbeat_cycle(config):
    """Runs sections and collects results."""
    from decafclaw.events import EventBus

    # Write a HEARTBEAT.md with two sections
    admin_path = config.agent_path / "HEARTBEAT.md"
    admin_path.parent.mkdir(parents=True, exist_ok=True)
    admin_path.write_text("## Task one\n\nDo thing one.\n\n## Task two\n\nDo thing two.\n")

    mock_agent = AsyncMock(side_effect=["Result one", "HEARTBEAT_OK nothing to report"])
    bus = EventBus()

    with patch("decafclaw.agent.run_agent_turn", mock_agent):
        results = await run_heartbeat_cycle(config, bus)

    assert len(results) == 2
    assert results[0]["title"] == "Task one"
    assert results[0]["response"] == "Result one"
    assert results[0]["is_ok"] is False
    assert results[1]["title"] == "Task two"
    assert results[1]["is_ok"] is True


@pytest.mark.asyncio
async def test_run_heartbeat_cycle_empty(config):
    """No HEARTBEAT.md files returns empty list."""
    from decafclaw.events import EventBus
    results = await run_heartbeat_cycle(config, EventBus())
    assert results == []


@pytest.mark.asyncio
async def test_run_heartbeat_cycle_section_failure(config):
    """A failing section doesn't stop subsequent sections."""
    from decafclaw.events import EventBus

    admin_path = config.agent_path / "HEARTBEAT.md"
    admin_path.parent.mkdir(parents=True, exist_ok=True)
    admin_path.write_text("## Fails\n\nBoom.\n\n## Works\n\nOK.\n")

    call_count = 0

    async def flaky_agent(ctx, prompt, history):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("section exploded")
        return "HEARTBEAT_OK"

    with patch("decafclaw.agent.run_agent_turn", flaky_agent):
        results = await run_heartbeat_cycle(config, EventBus())

    assert len(results) == 2
    assert "[error:" in results[0]["response"]
    assert results[1]["is_ok"] is True


@pytest.mark.asyncio
async def test_run_heartbeat_cycle_isolated_history(config):
    """Each section gets its own empty history."""
    from decafclaw.events import EventBus

    admin_path = config.agent_path / "HEARTBEAT.md"
    admin_path.parent.mkdir(parents=True, exist_ok=True)
    admin_path.write_text("## A\n\nTask A.\n\n## B\n\nTask B.\n")

    histories_seen = []

    async def capture_agent(ctx, prompt, history):
        histories_seen.append(list(history))  # snapshot
        return "HEARTBEAT_OK"

    with patch("decafclaw.agent.run_agent_turn", capture_agent):
        await run_heartbeat_cycle(config, EventBus())

    assert len(histories_seen) == 2
    assert histories_seen[0] == []
    assert histories_seen[1] == []


# -- timer tests --


@pytest.mark.asyncio
async def test_timer_disabled(config):
    """Timer returns immediately when interval is disabled."""
    config.heartbeat_interval = ""
    shutdown = asyncio.Event()
    # Should return immediately, not block
    await asyncio.wait_for(
        run_heartbeat_timer(config, None, shutdown),
        timeout=1.0,
    )


@pytest.mark.asyncio
async def test_timer_fires_callback(config):
    """Timer fires on_results callback after interval."""
    from decafclaw.events import EventBus

    config.heartbeat_interval = "1"  # 1 second

    admin_path = config.agent_path / "HEARTBEAT.md"
    admin_path.parent.mkdir(parents=True, exist_ok=True)
    admin_path.write_text("## Quick check\n\nSay hello.\n")

    results_received = []

    async def capture_results(results):
        results_received.append(results)

    shutdown = asyncio.Event()

    async def stop_after_one():
        # Wait for one result, then shut down
        while not results_received:
            await asyncio.sleep(0.1)
        shutdown.set()

    import decafclaw.heartbeat as hb
    original_poll = hb._POLL_INTERVAL
    hb._POLL_INTERVAL = 0.5  # fast polling for tests

    try:
        with patch("decafclaw.agent.run_agent_turn", AsyncMock(return_value="HEARTBEAT_OK")):
            await asyncio.gather(
                run_heartbeat_timer(config, EventBus(), shutdown, on_results=capture_results),
                stop_after_one(),
            )
    finally:
        hb._POLL_INTERVAL = original_poll

    assert len(results_received) == 1
    assert results_received[0][0]["is_ok"] is True


@pytest.mark.asyncio
async def test_timer_respects_shutdown(config):
    """Timer stops when shutdown event is set."""
    from decafclaw.events import EventBus

    config.heartbeat_interval = "300"  # 5 minutes — would block without shutdown
    shutdown = asyncio.Event()

    # Signal shutdown after a short delay
    async def signal_shutdown():
        await asyncio.sleep(0.1)
        shutdown.set()

    import decafclaw.heartbeat as hb
    original_poll = hb._POLL_INTERVAL
    hb._POLL_INTERVAL = 0.5

    try:
        await asyncio.gather(
            run_heartbeat_timer(config, EventBus(), shutdown),
            signal_shutdown(),
        )
    finally:
        hb._POLL_INTERVAL = original_poll
