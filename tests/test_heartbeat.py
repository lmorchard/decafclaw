"""Tests for heartbeat — parsing, interval, section logic, cycle runner, and timer."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from decafclaw.heartbeat import (
    build_section_prompt,
    is_heartbeat_ok,
    load_heartbeat_sections,
    parse_interval,
    response_starts_with_sentinel,
    run_heartbeat_cycle,
    run_heartbeat_timer,
)
from decafclaw.media import ToolResult

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
    assert is_heartbeat_ok(ToolResult(text="HEARTBEAT_OK")) is True


def test_is_heartbeat_ok_case_insensitive():
    assert is_heartbeat_ok(ToolResult(text="heartbeat_ok")) is True
    assert is_heartbeat_ok(ToolResult(text="Heartbeat_OK — nothing to report")) is True


def test_is_heartbeat_ok_ignores_mid_response_mention():
    """Tightened in #450: merely *mentioning* the sentinel is not a quiet cycle.

    The old substring rule is what forced agent.py to order the loop-breaker
    note ahead of accumulated preambles — a preamble mentioning HEARTBEAT_OK
    landed in the 300-char window and suppressed the alert.
    """
    assert is_heartbeat_ok(ToolResult(text="Everything is fine. heartbeat_ok")) is False
    assert is_heartbeat_ok(ToolResult(text="I could say HEARTBEAT_OK but things changed")) is False


def test_is_heartbeat_ok_allows_leading_whitespace():
    assert is_heartbeat_ok(ToolResult(text="  \n HEARTBEAT_OK")) is True


def test_is_heartbeat_ok_requires_a_word_boundary():
    """HEARTBEAT_OKAY is not HEARTBEAT_OK — the property _BACKGROUND_WAKE_OK_RE
    already had, now shared rather than duplicated."""
    assert is_heartbeat_ok(ToolResult(text="HEARTBEAT_OKAY then more")) is False


# -- shared sentinel matcher --


def test_sentinel_helper_is_start_anchored():
    from decafclaw.heartbeat import response_starts_with_sentinel
    assert response_starts_with_sentinel("FOO_OK trailing", "foo_ok") is True
    assert response_starts_with_sentinel("  \n FOO_OK", "FOO_OK") is True
    assert response_starts_with_sentinel("prefix FOO_OK", "FOO_OK") is False
    assert response_starts_with_sentinel("x" * 300 + "FOO_OK", "FOO_OK") is False
    assert response_starts_with_sentinel(None, "FOO_OK") is False
    assert response_starts_with_sentinel("", "FOO_OK") is False


def test_sentinel_helper_word_boundary_only_for_word_endings():
    """A sentinel ending in a word char gets \\b; one ending in punctuation
    can't (\\b after ']' would demand a following word char, so "[SILENT] x"
    would match but "[SILENT]" alone would not)."""
    from decafclaw.heartbeat import response_starts_with_sentinel
    assert response_starts_with_sentinel("FOO_OKAY", "FOO_OK") is False
    assert response_starts_with_sentinel("[SILENT]", "[SILENT]") is True
    assert response_starts_with_sentinel("[SILENT] nothing changed", "[SILENT]") is True
    assert response_starts_with_sentinel("[SILENTLY] hmm", "[SILENT]") is False


def test_is_heartbeat_ok_beyond_300_chars():
    padding = "x" * 300
    assert is_heartbeat_ok(ToolResult(text=padding + "HEARTBEAT_OK")) is False


def test_is_heartbeat_ok_not_present():
    assert is_heartbeat_ok(ToolResult(text="Something happened that needs attention.")) is False


def test_is_heartbeat_ok_false_on_abnormal_termination():
    """An abnormally-terminated heartbeat/scheduled turn must never be reported
    as OK, however its text reads (#710).

    `_finalize_with_note` in agent.py delivers the termination note first and
    the turn's accumulated mid-turn preambles after it, so an abnormally
    terminated turn's `ToolResult.text` always carries one of the two markers.
    polling.py instructs the agent to say "HEARTBEAT_OK" when there is nothing
    to report, which makes a sentinel-bearing preamble a plausible utterance.

    The sentinel is placed FIRST here, with the marker after it. #450 tightened
    the sentinel match to start-anchored, which already rejects the original
    note-first fixture — so a note-first fixture no longer exercises the
    override at all, and this guard would pass with
    `_ABNORMAL_TERMINATION_MARKERS` deleted. Sentinel-first is the arrangement
    only the whole-response marker check can catch, which is exactly why #714
    scoped that check to the whole response rather than to the scan window.
    Both markers are covered; asserting only the max-iterations one would let
    the loop-breaker path stay broken.
    """
    # Markers mirror _finalize_max_iterations / _finalize_loop_break (agent.py).
    max_iterations_text = (
        "HEARTBEAT_OK — nothing new since the last check."
        "\n\n[Agent reached max tool iterations (30) without a final response]"
    )
    loop_breaker_text = (
        "HEARTBEAT_OK — nothing new since the last check."
        "\n\n[loop-breaker] Stopped after repeated failures: you called "
        "vault_search 3 times with identical arguments without progress."
    )

    # Premise guards: the markers really are present, verbatim.
    assert "[Agent reached max tool iterations" in max_iterations_text
    assert "[loop-breaker] Stopped" in loop_breaker_text

    for label, text in (
        ("max-iterations", max_iterations_text),
        ("loop-breaker", loop_breaker_text),
    ):
        # Premise guard: absent the override, the tightened matcher WOULD call
        # this OK — so the assertion below is discriminating, not incidental.
        assert response_starts_with_sentinel(text, "HEARTBEAT_OK") is True, (
            f"{label}: fixture no longer exercises the override"
        )
        assert is_heartbeat_ok(ToolResult(text=text, termination_reason=label)) is False, (
            f"{label}: abnormal termination reported as OK"
        )


def test_is_heartbeat_ok_abnormal_marker_matched_beyond_scan_window():
    """The marker check spans the whole response, not `_SENTINEL_SCAN_CHARS`.

    #707 puts the termination note first, but scoping the marker check to the
    window would re-couple this to that ordering — the length-contingency #710
    exists to remove.
    """
    text = "HEARTBEAT_OK — nothing to report.\n\n" + "detail. " * 60 + (
        "\n\n[Agent reached max tool iterations (30) without a final response]"
    )
    assert text.index("[Agent reached max tool iterations") > 300
    assert is_heartbeat_ok(ToolResult(text=text, termination_reason="max_iterations")) is False


# -- BACKGROUND_WAKE_OK detection tests --


def test_is_background_wake_ok_detects_sentinel():
    from decafclaw.heartbeat import is_background_wake_ok
    # Sentinel at start — TRUE
    assert is_background_wake_ok(ToolResult(text="BACKGROUND_WAKE_OK"))
    assert is_background_wake_ok(ToolResult(text="background_wake_ok — nothing to report"))
    assert is_background_wake_ok(ToolResult(text="Background_Wake_OK"))  # case-insensitive
    assert not is_background_wake_ok(ToolResult(text="Something else"))
    assert not is_background_wake_ok(ToolResult(text=""))
    assert not is_background_wake_ok(None if None is None else None)
    # Only check first 300 chars.
    assert not is_background_wake_ok(ToolResult(text="x" * 300 + "BACKGROUND_WAKE_OK"))


def test_is_background_wake_ok_requires_prefix():
    from decafclaw.heartbeat import is_background_wake_ok
    # Prefix with leading whitespace — TRUE
    assert is_background_wake_ok(ToolResult(text="BACKGROUND_WAKE_OK"))
    assert is_background_wake_ok(ToolResult(text="  BACKGROUND_WAKE_OK — noted"))
    assert is_background_wake_ok(ToolResult(text="\n  background_wake_ok trailing text"))
    # Case-insensitive
    assert is_background_wake_ok(ToolResult(text="Background_Wake_OK noted"))
    # Mid-text mention — FALSE (this is the stricter behavior)
    assert not is_background_wake_ok(ToolResult(text="I could say BACKGROUND_WAKE_OK but I won't"))
    assert not is_background_wake_ok(ToolResult(text="The agent replied with BACKGROUND_WAKE_OK at the end"))
    # Empty/None — FALSE
    assert not is_background_wake_ok(ToolResult(text=""))
    assert not is_background_wake_ok(None if None is None else None)
    # Word boundary: "BACKGROUND_WAKE_OKAY" must be FALSE
    assert not is_background_wake_ok(ToolResult(text="BACKGROUND_WAKE_OKAY"))


# -- prompt building tests --


def test_build_section_prompt_titled():
    section = {"title": "Check status", "body": "Look at the thing."}
    prompt = build_section_prompt(section)
    assert "scheduled heartbeat check" in prompt
    # Both preamble branches preserve HEARTBEAT_OK (#362). Heartbeat keeps
    # it as the bare-token "nothing to report" signal; the scheduled-task
    # branch requires narrative AND keeps it as a leading quiet-cycle
    # marker. This test covers the heartbeat branch.
    assert "HEARTBEAT_OK" in prompt
    assert "## Check status" in prompt
    assert "Look at the thing." in prompt


def test_build_section_prompt_general():
    section = {"title": "General", "body": "Do the checklist."}
    prompt = build_section_prompt(section)
    assert "## General" not in prompt
    assert "Do the checklist." in prompt
    # Heartbeat path preserves the bare-token "nothing to report" wording.
    # See #362 for why the scheduled-task path differs (narrative + marker).
    assert "HEARTBEAT_OK" in prompt


# -- cycle runner tests --


@pytest.mark.asyncio
async def test_run_heartbeat_cycle(config):
    """Runs sections and collects results."""
    from decafclaw.conversation_manager import ConversationManager
    from decafclaw.events import EventBus

    # Write a HEARTBEAT.md with two sections
    admin_path = config.agent_path / "HEARTBEAT.md"
    admin_path.parent.mkdir(parents=True, exist_ok=True)
    admin_path.write_text("## Task one\n\nDo thing one.\n\n## Task two\n\nDo thing two.\n")

    mock_agent = AsyncMock(side_effect=[
        ToolResult(text="Result one"),
        ToolResult(text="HEARTBEAT_OK nothing to report"),
    ])
    bus = EventBus()
    manager = ConversationManager(config, bus)

    with patch("decafclaw.agent.run_agent_turn", mock_agent):
        results = await run_heartbeat_cycle(config, bus, manager)

    assert len(results) == 2
    assert results[0]["title"] == "Task one"
    assert results[0]["response"] == "Result one"
    assert results[0]["is_ok"] is False
    assert results[1]["title"] == "Task two"
    assert results[1]["is_ok"] is True


@pytest.mark.asyncio
async def test_run_heartbeat_cycle_empty(config):
    """No HEARTBEAT.md files returns empty list."""
    from decafclaw.conversation_manager import ConversationManager
    from decafclaw.events import EventBus

    bus = EventBus()
    manager = ConversationManager(config, bus)
    results = await run_heartbeat_cycle(config, bus, manager)
    assert results == []


@pytest.mark.asyncio
async def test_run_heartbeat_cycle_section_failure(config):
    """A failing section doesn't stop subsequent sections."""
    from decafclaw.conversation_manager import ConversationManager
    from decafclaw.events import EventBus

    admin_path = config.agent_path / "HEARTBEAT.md"
    admin_path.parent.mkdir(parents=True, exist_ok=True)
    admin_path.write_text("## Fails\n\nBoom.\n\n## Works\n\nOK.\n")

    call_count = 0

    async def flaky_agent(ctx, prompt, history, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("section exploded")
        return ToolResult(text="HEARTBEAT_OK")

    bus = EventBus()
    manager = ConversationManager(config, bus)

    with patch("decafclaw.agent.run_agent_turn", flaky_agent):
        results = await run_heartbeat_cycle(config, bus, manager)

    assert len(results) == 2
    assert "[error:" in results[0]["response"]
    assert results[1]["is_ok"] is True


@pytest.mark.asyncio
async def test_run_heartbeat_cycle_isolated_history(config):
    """Each section gets its own empty history."""
    from decafclaw.conversation_manager import ConversationManager
    from decafclaw.events import EventBus

    admin_path = config.agent_path / "HEARTBEAT.md"
    admin_path.parent.mkdir(parents=True, exist_ok=True)
    admin_path.write_text("## A\n\nTask A.\n\n## B\n\nTask B.\n")

    histories_seen = []

    async def capture_agent(ctx, prompt, history, **kwargs):
        histories_seen.append(list(history))  # snapshot
        return ToolResult(text="HEARTBEAT_OK")

    bus = EventBus()
    manager = ConversationManager(config, bus)

    with patch("decafclaw.agent.run_agent_turn", capture_agent):
        await run_heartbeat_cycle(config, bus, manager)

    assert len(histories_seen) == 2
    assert histories_seen[0] == []
    assert histories_seen[1] == []


# -- routing tests --


@pytest.mark.asyncio
async def test_run_section_turn_routes_through_manager(config, monkeypatch):
    """run_section_turn routes through ConversationManager with the right TurnKind."""
    from decafclaw.conversation_manager import ConversationManager, TurnKind
    from decafclaw.events import EventBus
    from decafclaw.heartbeat import run_section_turn

    bus = EventBus()
    manager = ConversationManager(config, bus)
    seen = []

    orig_enqueue = manager.enqueue_turn

    async def spy_enqueue(conv_id, *, kind, prompt, **kwargs):
        seen.append({"conv_id": conv_id, "kind": kind, "prompt": prompt[:40]})
        return await orig_enqueue(conv_id, kind=kind, prompt=prompt, **kwargs)

    monkeypatch.setattr(manager, "enqueue_turn", spy_enqueue)

    async def fake_run_agent_turn(ctx, user_message, history, **kwargs):
        return ToolResult(text="HEARTBEAT_OK")

    monkeypatch.setattr("decafclaw.agent.run_agent_turn", fake_run_agent_turn)

    section = {"title": "General", "body": "Do nothing.", "source": "workspace"}
    result = await run_section_turn(config, bus, manager, section, "T", 0)

    assert len(seen) == 1
    assert seen[0]["conv_id"] == "heartbeat-T-0"
    assert seen[0]["kind"] is TurnKind.HEARTBEAT_SECTION
    assert result["is_ok"] is True
    assert result["context_id"] is None


# -- timer tests --


@pytest.mark.asyncio
async def test_timer_disabled(config):
    """Timer returns immediately when interval is disabled."""
    config.heartbeat.interval = ""
    shutdown = asyncio.Event()
    # Should return immediately, not block
    await asyncio.wait_for(
        run_heartbeat_timer(config, None, None, shutdown),
        timeout=1.0,
    )


@pytest.mark.asyncio
async def test_timer_fires_callback(config):
    """Timer fires on_results callback after interval."""
    from decafclaw.conversation_manager import ConversationManager
    from decafclaw.events import EventBus

    config.heartbeat.interval = "1"  # 1 second

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

    bus = EventBus()
    manager = ConversationManager(config, bus)

    try:
        with patch("decafclaw.agent.run_agent_turn", AsyncMock(return_value=ToolResult(text="HEARTBEAT_OK"))):
            await asyncio.gather(
                run_heartbeat_timer(config, bus, manager, shutdown, on_results=capture_results),
                stop_after_one(),
            )
    finally:
        hb._POLL_INTERVAL = original_poll

    assert len(results_received) == 1
    assert results_received[0][0]["is_ok"] is True


# -- heartbeat_tools tests --


@pytest.mark.asyncio
async def test_tool_heartbeat_trigger_without_manager_returns_error(config):
    """heartbeat_trigger returns an error when ctx has no manager."""
    from decafclaw.context import Context
    from decafclaw.events import EventBus
    from decafclaw.tools.heartbeat_tools import tool_heartbeat_trigger

    bus = EventBus()
    ctx = Context(config=config, event_bus=bus)
    ctx.manager = None

    result = await tool_heartbeat_trigger(ctx)
    text = result.text if hasattr(result, "text") else str(result)
    assert "error" in text.lower()
    assert "manager" in text.lower()


@pytest.mark.asyncio
async def test_timer_respects_shutdown(config):
    """Timer stops when shutdown event is set."""
    from decafclaw.conversation_manager import ConversationManager
    from decafclaw.events import EventBus

    config.heartbeat.interval = "300"  # 5 minutes — would block without shutdown
    shutdown = asyncio.Event()

    # Signal shutdown after a short delay
    async def signal_shutdown():
        await asyncio.sleep(0.1)
        shutdown.set()

    import decafclaw.heartbeat as hb
    original_poll = hb._POLL_INTERVAL
    hb._POLL_INTERVAL = 0.5

    bus = EventBus()
    manager = ConversationManager(config, bus)

    try:
        await asyncio.gather(
            run_heartbeat_timer(config, bus, manager, shutdown),
            signal_shutdown(),
        )
    finally:
        hb._POLL_INTERVAL = original_poll


def test_sentinel_helper_rejects_an_empty_sentinel():
    """An empty sentinel compiles to `^\\s*`, which matches everything — a
    config-driven empty value would silently suppress every response."""
    from decafclaw.heartbeat import response_starts_with_sentinel
    assert response_starts_with_sentinel("anything at all", "") is False
    assert response_starts_with_sentinel("anything at all", "   ") is False
