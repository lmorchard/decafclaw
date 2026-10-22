"""Tests for periodic auto-titling of web UI conversations (#1072).

Covers the cadence/trigger logic, the guard order (cheap index lookups
before any archive read), the custom-title guard, the "material change"
rule, the turn-count exclusion of widget-response synthetics, the
conv_renamed bus event, cross-tab dedup, and the websocket post-turn hook
that fires the background task.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from decafclaw.archive import append_message
from decafclaw.events import EventBus
from decafclaw.web import auto_titling, websocket
from decafclaw.web.auto_titling import _clean_title, maybe_auto_title
from decafclaw.web.conversations import ConversationIndex


@pytest.fixture(autouse=True)
def _reset_dedup():
    auto_titling._attempted.clear()
    auto_titling._exhausted.clear()
    yield
    auto_titling._attempted.clear()
    auto_titling._exhausted.clear()


def _seed(config, conv_id: str, user_turns: int) -> None:
    """Seed `user_turns` user/assistant pairs into the archive."""
    for i in range(user_turns):
        append_message(config, conv_id, {"role": "user", "content": f"user turn {i}"})
        append_message(config, conv_id, {"role": "assistant", "content": f"assistant turn {i}"})


def _bus_with_recorder() -> tuple[EventBus, list]:
    events: list = []
    bus = EventBus()
    bus.subscribe(events.append)
    return bus, events


def _spy_archive_reads(monkeypatch) -> list:
    """Record every archive read the titling path performs."""
    calls: list = []
    real = auto_titling.read_archive

    def spy(config, conv_id):
        calls.append(conv_id)
        return real(config, conv_id)

    monkeypatch.setattr(auto_titling, "read_archive", spy)
    return calls


# -- cadence and guards --------------------------------------------------------


@pytest.mark.asyncio
async def test_no_attempt_before_cadence(config, stub_llm):
    index = ConversationIndex(config)
    conv = index.create("alice", "Opening scaffolding message")
    _seed(config, conv.conv_id, 1)

    await maybe_auto_title(config, conv.conv_id, EventBus())

    assert stub_llm == []
    assert index.get(conv.conv_id).title == "Opening scaffolding message"


@pytest.mark.asyncio
async def test_titles_at_turn_2_and_publishes_event(config, stub_llm):
    index = ConversationIndex(config)
    conv = index.create("alice", "Opening scaffolding message")
    _seed(config, conv.conv_id, 2)
    stub_llm.response = {"content": '  "Refined conversation title."  '}
    bus, events = _bus_with_recorder()

    await maybe_auto_title(config, conv.conv_id, bus)

    assert len(stub_llm) == 1
    updated = index.get(conv.conv_id)
    assert updated.title == "Refined conversation title"
    assert updated.custom_title is False
    assert events == [
        {
            "type": "conv_renamed",
            "conv_id": conv.conv_id,
            "title": "Refined conversation title",
            "updated_at": updated.updated_at,
        }
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("turns", [3, 4, 6, 7, 21])
async def test_no_attempt_off_cadence(config, stub_llm, turns):
    index = ConversationIndex(config)
    conv = index.create("alice", "Opening scaffolding message")
    _seed(config, conv.conv_id, turns)
    stub_llm.response = {"content": "Some new title"}

    await maybe_auto_title(config, conv.conv_id, EventBus())

    assert stub_llm == []
    assert index.get(conv.conv_id).title == "Opening scaffolding message"


@pytest.mark.asyncio
async def test_later_cadence_points_fire(config, stub_llm):
    index = ConversationIndex(config)
    conv = index.create("alice", "Opening scaffolding message")
    _seed(config, conv.conv_id, 3)
    stub_llm.response = {"content": "Title at three"}
    await maybe_auto_title(config, conv.conv_id, EventBus())
    assert stub_llm == []  # turn 3 is not a cadence point

    _seed(config, conv.conv_id, 2)  # bring the total to 5 turns
    stub_llm.response = {"content": "Title at five"}
    await maybe_auto_title(config, conv.conv_id, EventBus())
    assert len(stub_llm) == 1
    assert index.get(conv.conv_id).title == "Title at five"


@pytest.mark.asyncio
async def test_custom_title_never_re_titled(config, stub_llm):
    index = ConversationIndex(config)
    conv = index.create("alice", "Opening scaffolding message")
    index.rename(conv.conv_id, "User kept this", manual=True)
    _seed(config, conv.conv_id, 2)
    stub_llm.response = {"content": "Should not apply"}

    await maybe_auto_title(config, conv.conv_id, EventBus())

    assert stub_llm == []
    assert index.get(conv.conv_id).title == "User kept this"


@pytest.mark.asyncio
async def test_no_retitle_when_title_unchanged(config, stub_llm):
    index = ConversationIndex(config)
    conv = index.create("alice", "Same title")
    _seed(config, conv.conv_id, 2)
    stub_llm.response = {"content": "same TITLE"}
    bus, events = _bus_with_recorder()

    await maybe_auto_title(config, conv.conv_id, bus)

    assert len(stub_llm) == 1
    assert index.get(conv.conv_id).title == "Same title"
    assert events == []


@pytest.mark.asyncio
async def test_unknown_conv_is_noop(config, stub_llm):
    _seed(config, "web-alice-00000000", 2)
    stub_llm.response = {"content": "Title"}

    await maybe_auto_title(config, "web-alice-00000000", EventBus())

    assert stub_llm == []


@pytest.mark.asyncio
async def test_llm_failure_is_swallowed(config, stub_llm, monkeypatch):
    index = ConversationIndex(config)
    conv = index.create("alice", "Opening scaffolding message")
    _seed(config, conv.conv_id, 2)

    async def boom(*a, **k):
        raise RuntimeError("provider down")

    monkeypatch.setattr(auto_titling.llm, "call_llm", boom)

    await maybe_auto_title(config, conv.conv_id, EventBus())  # must not raise

    assert index.get(conv.conv_id).title == "Opening scaffolding message"


@pytest.mark.asyncio
async def test_dedup_across_concurrent_triggers(config, stub_llm):
    """Two tabs of the same conv both see turn_complete: one LLM call only."""
    index = ConversationIndex(config)
    conv = index.create("alice", "Opening scaffolding message")
    _seed(config, conv.conv_id, 2)
    stub_llm.response = {"content": "Deduped title"}
    bus, events = _bus_with_recorder()

    await asyncio.gather(
        maybe_auto_title(config, conv.conv_id, bus),
        maybe_auto_title(config, conv.conv_id, bus),
    )

    assert len(stub_llm) == 1
    assert index.get(conv.conv_id).title == "Deduped title"
    assert len(events) == 1


# -- guard order and turn counting ---------------------------------------------


@pytest.mark.asyncio
async def test_custom_title_skips_archive_read(config, stub_llm, monkeypatch):
    """The user-rename guard bails out before any archive read (#1072 F1)."""
    index = ConversationIndex(config)
    conv = index.create("alice", "Opening scaffolding message")
    index.rename(conv.conv_id, "User kept this", manual=True)
    _seed(config, conv.conv_id, 2)
    calls = _spy_archive_reads(monkeypatch)

    await maybe_auto_title(config, conv.conv_id, EventBus())

    assert calls == []
    assert stub_llm == []


@pytest.mark.asyncio
async def test_unknown_conv_skips_archive_read(config, stub_llm, monkeypatch):
    """Conversations absent from the web index bail before any archive read."""
    _seed(config, "web-alice-00000000", 2)
    calls = _spy_archive_reads(monkeypatch)

    await maybe_auto_title(config, "web-alice-00000000", EventBus())

    assert calls == []
    assert stub_llm == []


@pytest.mark.asyncio
async def test_exhausted_conv_skips_archive_read(config, stub_llm, monkeypatch):
    """Once the last cadence point was attempted, later turns skip the
    archive read entirely (#1072 F1)."""
    index = ConversationIndex(config)
    conv = index.create("alice", "Opening scaffolding message")
    _seed(config, conv.conv_id, 20)
    stub_llm.response = {"content": "Final title"}

    await maybe_auto_title(config, conv.conv_id, EventBus())
    assert len(stub_llm) == 1

    _seed(config, conv.conv_id, 1)  # a later turn brings the total to 21
    calls = _spy_archive_reads(monkeypatch)
    await maybe_auto_title(config, conv.conv_id, EventBus())
    await maybe_auto_title(config, conv.conv_id, EventBus())

    assert calls == []
    assert len(stub_llm) == 1
    assert index.get(conv.conv_id).title == "Final title"


@pytest.mark.asyncio
async def test_widget_response_reaches_cadence(config, stub_llm):
    """Two real user turns plus one widget response counts as 2 turns, so
    the turn-2 cadence fires (#1072 note 2)."""
    index = ConversationIndex(config)
    conv = index.create("alice", "Opening scaffolding message")
    _seed(config, conv.conv_id, 2)
    append_message(
        config,
        conv.conv_id,
        {"role": "user", "source": "widget_response", "content": "widget pick"},
    )
    stub_llm.response = {"content": "Real turn title"}

    await maybe_auto_title(config, conv.conv_id, EventBus())

    assert len(stub_llm) == 1
    assert index.get(conv.conv_id).title == "Real turn title"


@pytest.mark.asyncio
async def test_widget_response_does_not_reach_cadence(config, stub_llm):
    """One real user turn plus one widget response is 1 turn, not 2: no
    turn-2 attempt."""
    index = ConversationIndex(config)
    conv = index.create("alice", "Opening scaffolding message")
    _seed(config, conv.conv_id, 1)
    append_message(
        config,
        conv.conv_id,
        {"role": "user", "source": "widget_response", "content": "widget pick"},
    )
    stub_llm.response = {"content": "Should not apply"}

    await maybe_auto_title(config, conv.conv_id, EventBus())

    assert stub_llm == []
    assert index.get(conv.conv_id).title == "Opening scaffolding message"


# -- title normalization --------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('"Quoted title"', "Quoted title"),
        ("  Padded  title  ", "Padded title"),
        ("Trailing punctuation.", "Trailing punctuation"),
        ("  ", ""),
        ("", ""),
    ],
)
def test_clean_title(raw, expected):
    assert _clean_title(raw) == expected


# -- websocket post-turn hook ---------------------------------------------------


@pytest.mark.asyncio
async def test_turn_complete_fires_background_titling(config, stub_llm, monkeypatch):
    """The turn_complete branch schedules the titling task and does not
    block on it: the client's TURN_COMPLETE frame goes out first."""
    index = ConversationIndex(config)
    conv = index.create("alice", "Opening scaffolding message")
    _seed(config, conv.conv_id, 2)
    stub_llm.response = {"content": "Hook title"}

    manager = MagicMock()
    captured: dict = {}

    def fake_subscribe(conv_id, callback):
        captured["callback"] = callback
        return "sub-1"

    manager.subscribe.side_effect = fake_subscribe

    sent = []

    async def ws_send(msg):
        sent.append(msg)

    state = {"config": config, "event_bus": EventBus(), "manager": manager, "ws_send": ws_send}
    websocket._subscribe_to_conv(state, conv.conv_id)

    # Spy on task creation so we can await the background work deterministically.
    created = []
    real_create_task = asyncio.create_task

    def spy_create_task(coro, **kwargs):
        task = real_create_task(coro, **kwargs)
        created.append(task)
        return task

    monkeypatch.setattr(asyncio, "create_task", spy_create_task)

    await captured["callback"]({"type": "turn_complete", "conv_id": conv.conv_id})

    # The turn frame is delivered; exactly one background task was scheduled.
    assert sent == [{"type": "turn_complete", "conv_id": conv.conv_id}]
    assert len(created) == 1

    # stub_llm shares this test's monkeypatch instance, so keep the patch in
    # place until the background task has actually run.
    await asyncio.gather(*created)
    monkeypatch.undo()

    assert index.get(conv.conv_id).title == "Hook title"


@pytest.mark.asyncio
async def test_conv_renamed_forwarder_filters_by_user(config):
    """The bus forwarder only relays conv_renamed events for this user's
    web conversations, with the client wire shape."""
    sent = []

    async def ws_send(msg):
        sent.append(msg)

    forward = websocket._make_conv_renamed_forwarder(ws_send, "alice")

    await forward({"type": "conv_renamed", "conv_id": "web-alice-12345678", "title": "Mine", "updated_at": "t1"})
    await forward({"type": "conv_renamed", "conv_id": "web-bob-12345678", "title": "Not mine", "updated_at": "t2"})
    await forward({"type": "conversation_status", "conv_id": "web-alice-12345678", "status": "idle"})
    await forward({"type": "conv_renamed", "conv_id": "system-20260101-000000", "title": "System", "updated_at": "t3"})

    assert sent == [{"type": "conv_renamed", "conv_id": "web-alice-12345678", "title": "Mine", "updated_at": "t1"}]
