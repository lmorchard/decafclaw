"""Tests for one-shot calendar events (#1896)."""

import asyncio
import time
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from decafclaw.calendar_events import (
    CalendarEvent,
    calendar_session_conv_id,
    load_all_calendar_events,
    load_calendar_events,
    new_event_id,
    parse_fire_at,
    save_calendar_events,
    validate_session_key,
)
from decafclaw.calendar_manager import CalendarManager, ensure_dedicated_session
from decafclaw.context import TaskMode
from decafclaw.conversation_paths import calendar_path, conversation_dir, delete_conversation_files
from decafclaw.tools.calendar_tools import (
    tool_calendar_cancel,
    tool_calendar_create,
    tool_calendar_list,
    tool_calendar_snooze,
    tool_calendar_update,
)
from decafclaw.web.conversations import ConversationIndex, is_web_conv_for_user


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _event(
    conv_id: str,
    *,
    fire_at_ts: float | None = None,
    session_mode: str = "current",
    session_key: str = "",
    session_title: str = "",
    user_id: str = "testuser",
    title: str = "Test event",
    reason: str = "Do the thing",
    status: str = "pending",
) -> CalendarEvent:
    ts = fire_at_ts if fire_at_ts is not None else time.time() + 3600
    now = _iso(time.time())
    return CalendarEvent(
        event_id=new_event_id(),
        conv_id=conv_id,
        target_conv_id=conv_id,
        session_mode=session_mode,
        session_key=session_key,
        session_title=session_title,
        title=title,
        reason=reason,
        fire_at=_iso(ts),
        fire_at_ts=ts,
        created_at=now,
        updated_at=now,
        user_id=user_id,
        status=status,
    )


def _make_conv_dir(config, conv_id: str) -> None:
    conversation_dir(config, conv_id).mkdir(parents=True, exist_ok=True)


@pytest.fixture
async def mock_manager(config):
    mgr = MagicMock()
    mgr.config = config
    mgr.event_bus = None
    mgr.enqueue_turn = AsyncMock()
    mgr.get_state = MagicMock(return_value=None)
    mgr.calendar = CalendarManager(config, manager=mgr)
    yield mgr
    await mgr.calendar.shutdown()


# -- store --------------------------------------------------------------------


def test_store_round_trip(config):
    event = _event("conv-a")
    save_calendar_events(config, "conv-a", [event])

    loaded = load_calendar_events(config, "conv-a")
    assert len(loaded) == 1
    assert loaded[0].event_id == event.event_id
    assert loaded[0].title == event.title
    assert loaded[0].fire_at == event.fire_at


def test_store_corrupt_file_returns_empty(config):
    path = calendar_path(config, "conv-a")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")

    assert load_calendar_events(config, "conv-a") == []


def test_load_all_scans_every_origin(config):
    save_calendar_events(config, "conv-a", [_event("conv-a")])
    save_calendar_events(config, "conv-b", [_event("conv-b")])

    events = load_all_calendar_events(config)
    assert {event.conv_id for event in events} == {"conv-a", "conv-b"}


def test_calendar_path_stays_outside_workspace(config):
    path = calendar_path(config, "conv-a")
    assert path.is_relative_to(config.agent_path)
    assert not path.is_relative_to(config.workspace_path)


def test_delete_conversation_files_removes_calendar(config):
    save_calendar_events(config, "conv-a", [_event("conv-a")])
    path = calendar_path(config, "conv-a")
    assert path.exists()

    delete_conversation_files(config, "conv-a")
    assert not path.exists()


def test_parse_fire_at_requires_timezone():
    with pytest.raises(ValueError, match="timezone"):
        parse_fire_at("2026-11-26T08:00:00")

    iso, ts = parse_fire_at("2026-11-26T08:00:00-08:00")
    assert iso == "2026-11-26T08:00:00-08:00"
    assert ts == datetime(2026, 11, 26, 16, 0, tzinfo=timezone.utc).timestamp()


def test_calendar_session_id_is_owned_and_stable():
    conv_id = calendar_session_conv_id("alice", "morning-briefing")
    assert conv_id == calendar_session_conv_id("alice", "morning-briefing")
    assert conv_id.startswith("web-alice-cal")
    assert is_web_conv_for_user(conv_id, "alice")
    assert not is_web_conv_for_user(conv_id, "bob")


def test_validate_session_key():
    assert validate_session_key("morning-briefing") == "morning-briefing"
    for bad in ("", "Morning", "with space", "trailing-", "-leading"):
        with pytest.raises(ValueError):
            validate_session_key(bad)


# -- conversation index upsert ------------------------------------------------


def test_get_or_create_reuses_and_preserves_title(config):
    index = ConversationIndex(config)
    conv_id = calendar_session_conv_id("alice", "morning-briefing")

    created = index.get_or_create(conv_id, "alice", "Morning Briefing")
    assert created.title == "Morning Briefing"

    reused = index.get_or_create(conv_id, "alice", "Different Title")
    assert reused.conv_id == conv_id
    assert reused.title == "Morning Briefing"


def test_get_or_create_unarchives(config):
    index = ConversationIndex(config)
    conv_id = calendar_session_conv_id("alice", "morning-briefing")
    index.get_or_create(conv_id, "alice", "Morning Briefing")
    index.archive(conv_id)

    assert index.get(conv_id).archived is True
    index.get_or_create(conv_id, "alice", "Morning Briefing")
    assert index.get(conv_id).archived is False


def test_get_or_create_refuses_other_user(config):
    index = ConversationIndex(config)
    conv_id = calendar_session_conv_id("alice", "morning-briefing")
    index.get_or_create(conv_id, "alice", "Morning Briefing")

    with pytest.raises(ValueError, match="another user"):
        index.get_or_create(conv_id, "bob", "Morning Briefing")


def test_ensure_dedicated_session_recreates_after_delete(config):
    conv_id = ensure_dedicated_session(config, "alice", "morning-briefing", "Morning Briefing")
    index = ConversationIndex(config)
    assert index.get(conv_id) is not None

    index.delete(conv_id)
    assert index.get(conv_id) is None

    recreated = ensure_dedicated_session(config, "alice", "morning-briefing", "Morning Briefing")
    assert recreated == conv_id
    assert index.get(conv_id).title == "Morning Briefing"


# -- manager firing -----------------------------------------------------------


@pytest.mark.asyncio
async def test_fire_current_enqueues_wake_and_marks_fired(config, mock_manager):
    _make_conv_dir(config, "conv-fire")
    event = _event("conv-fire", fire_at_ts=time.time())

    added = await mock_manager.calendar.add(event)
    task = mock_manager.calendar._tasks[added.event_id]
    await task

    kwargs = mock_manager.enqueue_turn.await_args.kwargs
    assert kwargs["conv_id"] == "conv-fire"
    assert kwargs["kind"].value == "wake"
    assert kwargs["bypass_wake_rate_limit"] is True
    assert kwargs["metadata"]["calendar_event_id"] == added.event_id
    assert kwargs["metadata"]["suppressible"] is False
    assert "Calendar event: Test event." in kwargs["prompt"]
    assert "Due:" in kwargs["prompt"]
    assert "BACKGROUND_WAKE_OK" not in kwargs["prompt"]

    loaded = load_calendar_events(config, "conv-fire")[0]
    assert loaded.status == "fired"
    assert loaded.last_fired_at


@pytest.mark.asyncio
async def test_fire_current_missing_conversation_marks_orphaned(config, mock_manager, monkeypatch):
    notify = AsyncMock()
    monkeypatch.setattr("decafclaw.notifications.notify", notify)

    event = _event("conv-missing", fire_at_ts=time.time())
    added = await mock_manager.calendar.add(event)
    await mock_manager.calendar._tasks[added.event_id]

    loaded = load_calendar_events(config, "conv-missing")[0]
    assert loaded.status == "orphaned"
    mock_manager.enqueue_turn.assert_not_awaited()
    notify.assert_awaited_once()
    assert notify.await_args.kwargs["category"] == "calendar"


@pytest.mark.asyncio
async def test_fire_dedicated_creates_and_reuses_session(config, mock_manager):
    event1 = _event(
        "conv-dedicated",
        fire_at_ts=time.time(),
        session_mode="dedicated",
        session_key="morning-briefing",
        session_title="Morning Briefing",
    )
    added1 = await mock_manager.calendar.add(event1)
    await mock_manager.calendar._tasks[added1.event_id]
    assert added1.target_conv_id.startswith("web-testuser-cal")

    event2 = _event(
        "conv-dedicated",
        fire_at_ts=time.time(),
        session_mode="dedicated",
        session_key="morning-briefing",
        session_title="Morning Briefing",
    )
    added2 = await mock_manager.calendar.add(event2)
    await mock_manager.calendar._tasks[added2.event_id]

    assert added2.target_conv_id == added1.target_conv_id
    index = ConversationIndex(config)
    assert len(index.list_for_user("testuser")) == 1
    assert mock_manager.enqueue_turn.await_args_list[-1].kwargs["conv_id"] == added1.target_conv_id


@pytest.mark.asyncio
async def test_startup_scan_resumes_pending_event(config, mock_manager):
    _make_conv_dir(config, "conv-startup")
    event = _event("conv-startup", fire_at_ts=time.time())
    save_calendar_events(config, "conv-startup", [event])

    recovered = await mock_manager.calendar.startup_scan()
    assert recovered == 1
    task = mock_manager.calendar._tasks[event.event_id]
    await task

    assert load_calendar_events(config, "conv-startup")[0].status == "fired"
    mock_manager.enqueue_turn.assert_awaited_once()
    assert "Due:" in mock_manager.enqueue_turn.await_args.kwargs["prompt"]

    mock_manager.enqueue_turn.reset_mock()
    assert await mock_manager.calendar.startup_scan() == 0
    mock_manager.enqueue_turn.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancel_all_for_conversation(config, mock_manager):
    _make_conv_dir(config, "conv-delete")
    event = _event("conv-delete", fire_at_ts=time.time() + 3600)
    added = await mock_manager.calendar.add(event)

    await mock_manager.calendar.cancel_all_for_conversation("conv-delete")

    loaded = load_calendar_events(config, "conv-delete")[0]
    assert loaded.status == "cancelled"
    assert added.event_id not in mock_manager.calendar._tasks


@pytest.mark.asyncio
async def test_shutdown_cancels_timers_and_rejects_new_events(config, mock_manager):
    _make_conv_dir(config, "conv-shutdown")
    event = _event("conv-shutdown", fire_at_ts=time.time() + 3600)
    await mock_manager.calendar.add(event)

    await mock_manager.calendar.shutdown()
    assert mock_manager.calendar._tasks == {}

    with pytest.raises(RuntimeError, match="shutting down"):
        await mock_manager.calendar.add(_event("conv-shutdown", fire_at_ts=time.time() + 3600))


@pytest.mark.asyncio
async def test_stale_timer_does_not_fire_after_update(config, mock_manager):
    _make_conv_dir(config, "conv-stale")
    event = _event("conv-stale", fire_at_ts=time.time() + 3600)
    added = await mock_manager.calendar.add(event)

    await mock_manager.calendar.update(
        "conv-stale",
        added.event_id,
        fire_at=_iso(time.time() + 7200),
        fire_at_ts=time.time() + 7200,
    )
    await mock_manager.calendar._fire(added)

    mock_manager.enqueue_turn.assert_not_awaited()
    assert load_calendar_events(config, "conv-stale")[0].status == "pending"


@pytest.mark.asyncio
async def test_notify_skipped_when_conversation_has_subscriber(config, mock_manager, monkeypatch):
    notify = AsyncMock()
    monkeypatch.setattr("decafclaw.notifications.notify", notify)
    _make_conv_dir(config, "conv-live")
    mock_manager.get_state = MagicMock(return_value=SimpleNamespace(subscribers={"tab-1"}, last_context_setup=None))

    event = _event("conv-live", fire_at_ts=time.time())
    added = await mock_manager.calendar.add(event)
    await mock_manager.calendar._tasks[added.event_id]

    notify.assert_not_awaited()


@pytest.mark.asyncio
async def test_notify_on_unattended_fire(config, mock_manager, monkeypatch):
    notify = AsyncMock()
    monkeypatch.setattr("decafclaw.notifications.notify", notify)
    _make_conv_dir(config, "conv-unattended")

    event = _event("conv-unattended", fire_at_ts=time.time())
    added = await mock_manager.calendar.add(event)
    await mock_manager.calendar._tasks[added.event_id]

    notify.assert_awaited_once()
    assert notify.await_args.args[1] is None
    kwargs = notify.await_args.kwargs
    assert kwargs["category"] == "calendar"
    assert kwargs["link"] == "conv://conv-unattended"
    assert kwargs["conv_id"] == "conv-unattended"


# -- tools --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_current_event(ctx, mock_manager):
    ctx.manager = mock_manager
    ctx.conv_id = "conv-tools"
    fire_at = _iso(time.time() + 3600)

    result = await tool_calendar_create(ctx, title="Dentist", reason="Call the dentist", fire_at=fire_at)

    assert "[error" not in result.text
    assert result.data["session_mode"] == "current"
    assert result.data["target_conv_id"] == "conv-tools"
    events = load_calendar_events(ctx.config, "conv-tools")
    assert len(events) == 1
    assert events[0].title == "Dentist"


@pytest.mark.asyncio
async def test_create_validation(ctx, mock_manager):
    ctx.manager = mock_manager
    ctx.conv_id = "conv-tools"

    past = await tool_calendar_create(
        ctx,
        title="Past",
        reason="Too late",
        fire_at=_iso(time.time() - 60),
    )
    assert "must be in the future" in past.text

    naive = await tool_calendar_create(
        ctx,
        title="Naive",
        reason="No timezone",
        fire_at="2026-11-26T08:00:00",
    )
    assert "timezone" in naive.text

    empty = await tool_calendar_create(ctx, title="", reason="x", fire_at=_iso(time.time() + 60))
    assert "title is required" in empty.text

    no_reason = await tool_calendar_create(ctx, title="x", reason="", fire_at=_iso(time.time() + 60))
    assert "reason is required" in no_reason.text

    bad_mode = await tool_calendar_create(
        ctx,
        title="x",
        reason="x",
        fire_at=_iso(time.time() + 60),
        session_mode="new",
    )
    assert "session_mode" in bad_mode.text
    assert load_calendar_events(ctx.config, "conv-tools") == []


@pytest.mark.asyncio
async def test_create_dedicated_session_upserts(ctx, mock_manager):
    ctx.manager = mock_manager
    ctx.conv_id = "conv-tools"
    fire_at = _iso(time.time() + 3600)

    first = await tool_calendar_create(
        ctx,
        title="Morning",
        reason="Brief me",
        fire_at=fire_at,
        session_mode="dedicated",
        session_key="morning-briefing",
        session_title="Morning Briefing",
    )
    assert "[error" not in first.text
    assert first.data["target_conv_id"].startswith("web-testuser-cal")

    second = await tool_calendar_create(
        ctx,
        title="Morning again",
        reason="Brief me again",
        fire_at=fire_at,
        session_mode="dedicated",
        session_key="morning-briefing",
        session_title="Morning Briefing",
    )
    assert second.data["target_conv_id"] == first.data["target_conv_id"]

    index = ConversationIndex(ctx.config)
    assert len(index.list_for_user("testuser")) == 1


@pytest.mark.asyncio
async def test_create_dedicated_requires_user_and_fields(ctx, mock_manager):
    ctx.manager = mock_manager
    ctx.conv_id = "conv-tools"
    fire_at = _iso(time.time() + 3600)

    ctx.user_id = ""
    missing_user = await tool_calendar_create(
        ctx,
        title="Morning",
        reason="Brief me",
        fire_at=fire_at,
        session_mode="dedicated",
        session_key="morning-briefing",
        session_title="Morning Briefing",
    )
    assert "authenticated user" in missing_user.text

    ctx.user_id = "testuser"
    missing_key = await tool_calendar_create(
        ctx,
        title="Morning",
        reason="Brief me",
        fire_at=fire_at,
        session_mode="dedicated",
        session_title="Morning Briefing",
    )
    assert "session_key" in missing_key.text

    missing_title = await tool_calendar_create(
        ctx,
        title="Morning",
        reason="Brief me",
        fire_at=fire_at,
        session_mode="dedicated",
        session_key="morning-briefing",
    )
    assert "session_title" in missing_title.text


@pytest.mark.asyncio
async def test_list_filters_and_widget(ctx, mock_manager):
    ctx.manager = mock_manager
    ctx.conv_id = "conv-tools"
    fire_at = _iso(time.time() + 3600)

    first = await tool_calendar_create(ctx, title="One", reason="First", fire_at=fire_at)
    await tool_calendar_create(ctx, title="Two", reason="Second", fire_at=_iso(time.time() + 7200))
    await tool_calendar_cancel(ctx, first.data["event_id"])

    pending = await tool_calendar_list(ctx)
    assert len(pending.data["events"]) == 1
    assert pending.data["events"][0]["title"] == "Two"
    assert pending.widget is not None

    all_events = await tool_calendar_list(ctx, status="all")
    assert len(all_events.data["events"]) == 2

    cancelled = await tool_calendar_list(ctx, status="cancelled")
    assert len(cancelled.data["events"]) == 1


@pytest.mark.asyncio
async def test_update_reschedules(ctx, mock_manager):
    ctx.manager = mock_manager
    ctx.conv_id = "conv-tools"
    created = await tool_calendar_create(
        ctx,
        title="Move me",
        reason="Later",
        fire_at=_iso(time.time() + 3600),
    )
    event_id = created.data["event_id"]
    new_fire = _iso(time.time() + 7200)

    result = await tool_calendar_update(ctx, event_id, fire_at=new_fire, title="Moved")

    assert "[error" not in result.text
    assert result.data["title"] == "Moved"
    assert result.data["fire_at"] == new_fire
    assert result.data["status"] == "pending"


@pytest.mark.asyncio
async def test_update_retargets_to_dedicated(ctx, mock_manager):
    ctx.manager = mock_manager
    ctx.conv_id = "conv-tools"
    created = await tool_calendar_create(
        ctx,
        title="Retarget me",
        reason="Move to a session",
        fire_at=_iso(time.time() + 3600),
    )

    result = await tool_calendar_update(
        ctx,
        created.data["event_id"],
        session_key="morning-briefing",
        session_title="Morning Briefing",
    )

    assert "[error" not in result.text
    assert result.data["session_mode"] == "dedicated"
    assert result.data["target_conv_id"].startswith("web-testuser-cal")
    assert result.data["session_title"] == "Morning Briefing"


@pytest.mark.asyncio
async def test_update_requires_changes(ctx, mock_manager):
    ctx.manager = mock_manager
    ctx.conv_id = "conv-tools"
    created = await tool_calendar_create(
        ctx,
        title="No change",
        reason="Nothing",
        fire_at=_iso(time.time() + 3600),
    )

    result = await tool_calendar_update(ctx, created.data["event_id"])
    assert "No changes" in result.text


@pytest.mark.asyncio
async def test_cancel_event(ctx, mock_manager):
    ctx.manager = mock_manager
    ctx.conv_id = "conv-tools"
    created = await tool_calendar_create(
        ctx,
        title="Cancel me",
        reason="Stop",
        fire_at=_iso(time.time() + 3600),
    )
    event_id = created.data["event_id"]

    result = await tool_calendar_cancel(ctx, event_id)
    assert "[error" not in result.text
    assert load_calendar_events(ctx.config, "conv-tools")[0].status == "cancelled"

    again = await tool_calendar_cancel(ctx, event_id)
    assert "not found or is not pending" in again.text


@pytest.mark.asyncio
async def test_snooze_event(ctx, mock_manager):
    ctx.manager = mock_manager
    ctx.conv_id = "conv-tools"
    created = await tool_calendar_create(
        ctx,
        title="Snooze me",
        reason="Later",
        fire_at=_iso(time.time() + 3600),
    )
    event_id = created.data["event_id"]
    before = created.data["fire_at_ts"]

    result = await tool_calendar_snooze(ctx, event_id, duration_seconds=600)

    assert "[error" not in result.text
    assert result.data["fire_at_ts"] > before
    assert result.data["status"] == "pending"


@pytest.mark.asyncio
async def test_calendar_tools_reject_scheduled_runs(ctx, mock_manager):
    ctx.manager = mock_manager
    ctx.conv_id = "conv-tools"
    ctx.task_mode = TaskMode.SCHEDULED

    result = await tool_calendar_create(
        ctx,
        title="Nope",
        reason="Not here",
        fire_at=_iso(time.time() + 3600),
    )
    assert "only available in interactive user conversations" in result.text
    assert result.data["error"] == "unattended_or_child_not_permitted"


def test_calendar_tools_registered():
    from decafclaw.tools import TOOL_DEFINITIONS, TOOLS

    expected = {"calendar_create", "calendar_list", "calendar_update", "calendar_cancel", "calendar_snooze"}
    assert expected <= set(TOOLS)
    assert expected <= {definition["function"]["name"] for definition in TOOL_DEFINITIONS}
