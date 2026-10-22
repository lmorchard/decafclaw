"""Tests for periodic auto-titling of web UI conversations (#1072).

Covers the cadence/trigger logic, the custom-title guard, the "material
change" rule, the conv_renamed bus event, cross-tab dedup, and the
websocket post-turn hook that fires the background task.
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
    yield
    auto_titling._attempted.clear()


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
