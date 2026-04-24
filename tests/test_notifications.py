"""Tests for the notification inbox module."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from decafclaw import notifications as notifs

# -- Helpers ------------------------------------------------------------------


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def _past(days: int) -> str:
    dt = datetime.now(tz=timezone.utc) - timedelta(days=days)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _now() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture
def monotonic_now(monkeypatch):
    """Make ``_now_iso`` return a monotonically increasing timestamp per call.

    ``_now_iso`` has second precision, so two calls in the same wall-clock
    second produce identical timestamps. Tests that need ordered timestamps
    would otherwise have to `asyncio.sleep(1.05)` between writes — slow and
    flaky. This fixture assigns each call a unique timestamp one second
    apart.

    Base is anchored on real ``datetime.now()`` so the timestamps fall
    inside the retention window — opportunistic rotation in ``notify()``
    would otherwise archive our test records the moment they're appended.
    """
    base = datetime.now(tz=timezone.utc)
    counter = iter(range(10_000))

    def _fake_now() -> str:
        return (base + timedelta(seconds=next(counter))).strftime("%Y-%m-%dT%H:%M:%SZ")

    monkeypatch.setattr(notifs, "_now_iso", _fake_now)


# -- Record shape -------------------------------------------------------------


class TestNotificationRecord:
    def test_roundtrip(self):
        rec = notifs.NotificationRecord(
            id="abc",
            timestamp="2026-04-22T10:15:00Z",
            category="heartbeat",
            title="Hello",
            body="world",
            priority="high",
            link="conv://x",
            conv_id="conv1",
        )
        roundtripped = notifs.NotificationRecord.from_dict(rec.to_dict())
        assert roundtripped == rec

    def test_defaults(self):
        rec = notifs.NotificationRecord.from_dict({
            "id": "x", "timestamp": "t", "category": "c", "title": "T",
        })
        assert rec.priority == "normal"
        assert rec.body == ""
        assert rec.link is None
        assert rec.conv_id is None


# -- notify() -----------------------------------------------------------------


class TestNotify:
    @pytest.mark.asyncio
    async def test_appends_record(self, config):
        rec = await notifs.notify(config, category="test", title="Hello")
        lines = _read_jsonl(notifs._inbox_path(config))
        assert len(lines) == 1
        assert lines[0]["id"] == rec.id
        assert lines[0]["category"] == "test"
        assert lines[0]["title"] == "Hello"
        # Timestamp is ISO-8601 UTC with Z suffix
        assert lines[0]["timestamp"].endswith("Z")

    @pytest.mark.asyncio
    async def test_id_is_hex(self, config):
        rec = await notifs.notify(config, category="test", title="Hello")
        assert len(rec.id) == 12
        int(rec.id, 16)  # parses as hex

    @pytest.mark.asyncio
    async def test_distinct_ids(self, config):
        recs = [
            await notifs.notify(config, category="test", title=f"#{i}")
            for i in range(5)
        ]
        assert len({r.id for r in recs}) == 5

    @pytest.mark.asyncio
    async def test_creates_parent_dir(self, config):
        # Ensure dir doesn't exist before first write
        assert not notifs._notifications_dir(config).exists()
        await notifs.notify(config, category="test", title="Hello")
        assert notifs._notifications_dir(config).exists()

    @pytest.mark.asyncio
    async def test_preserves_optional_fields(self, config):
        await notifs.notify(
            config, category="test", title="Hi",
            body="body text", priority="high",
            link="conv://abc", conv_id="conv-1",
        )
        lines = _read_jsonl(notifs._inbox_path(config))
        assert lines[0]["body"] == "body text"
        assert lines[0]["priority"] == "high"
        assert lines[0]["link"] == "conv://abc"
        assert lines[0]["conv_id"] == "conv-1"

    @pytest.mark.asyncio
    async def test_concurrent_notify_safe(self, config):
        """Concurrent notify() calls don't interleave or lose records."""
        async def fire(i: int):
            await notifs.notify(config, category="test", title=f"#{i}")

        await asyncio.gather(*(fire(i) for i in range(10)))
        lines = _read_jsonl(notifs._inbox_path(config))
        assert len(lines) == 10
        # All ids are distinct and hex
        ids = {line["id"] for line in lines}
        assert len(ids) == 10

    @pytest.mark.asyncio
    async def test_publishes_event_when_bus_provided(self, config):
        """notify() publishes notification_created after the inbox append."""
        from decafclaw.events import EventBus
        bus = EventBus()
        received: list[dict] = []
        bus.subscribe(lambda e: received.append(e))

        rec = await notifs.notify(
            config, bus, category="t", title="Hello", priority="high",
        )

        assert len(received) == 1
        event = received[0]
        assert event["type"] == "notification_created"
        assert event["record"]["id"] == rec.id
        assert event["record"]["priority"] == "high"
        # unread_count is computed at publish time so subscribers don't
        # need to re-read the inbox.
        assert event["unread_count"] == notifs.unread_count(config)
        assert event["unread_count"] == 1
        # Inbox write still happened
        lines = _read_jsonl(notifs._inbox_path(config))
        assert lines[0]["id"] == rec.id

    @pytest.mark.asyncio
    async def test_no_event_when_bus_omitted(self, config):
        """Without an event bus, notify() still writes the inbox (back-compat)."""
        rec = await notifs.notify(config, category="t", title="Hello")
        lines = _read_jsonl(notifs._inbox_path(config))
        assert lines[0]["id"] == rec.id
        # No crash, no partial state — the write is what matters.


# -- Rotation -----------------------------------------------------------------


class TestInboxRotation:
    @pytest.mark.asyncio
    async def test_no_rotation_when_all_recent(self, config):
        # Seed with records all within retention
        inbox = notifs._inbox_path(config)
        inbox.parent.mkdir(parents=True, exist_ok=True)
        _write_jsonl(inbox, [
            {"id": "a", "timestamp": _past(5), "category": "t", "title": "A"},
            {"id": "b", "timestamp": _past(2), "category": "t", "title": "B"},
        ])
        await notifs.notify(config, category="t", title="C")
        lines = _read_jsonl(inbox)
        assert len(lines) == 3
        # No archive created
        assert not notifs._archive_dir(config).exists()

    @pytest.mark.asyncio
    async def test_rotates_old_records_to_archive(self, config):
        config.notifications.retention_days = 30
        inbox = notifs._inbox_path(config)
        inbox.parent.mkdir(parents=True, exist_ok=True)
        _write_jsonl(inbox, [
            {"id": "old1", "timestamp": _past(60), "category": "t", "title": "Old 1"},
            {"id": "old2", "timestamp": _past(45), "category": "t", "title": "Old 2"},
            {"id": "new1", "timestamp": _past(5), "category": "t", "title": "New 1"},
        ])
        await notifs.notify(config, category="t", title="New 2")

        # Inbox now has recent + new record
        lines = _read_jsonl(inbox)
        assert len(lines) == 2
        ids = {line["id"] for line in lines}
        assert "new1" in ids
        assert "old1" not in ids
        assert "old2" not in ids

        # Archive contains old records
        archive_files = list(notifs._archive_dir(config).glob("*.jsonl"))
        assert archive_files, "archive file should exist"
        archived: list[dict] = []
        for af in archive_files:
            archived.extend(_read_jsonl(af))
        archived_ids = {r["id"] for r in archived}
        assert "old1" in archived_ids
        assert "old2" in archived_ids


class TestReadLogRotation:
    @pytest.mark.asyncio
    async def test_drops_old_read_events(self, config):
        config.notifications.retention_days = 30
        read_log = notifs._read_log_path(config)
        read_log.parent.mkdir(parents=True, exist_ok=True)
        _write_jsonl(read_log, [
            {"event": "read", "id": "old", "timestamp": _past(60)},
            {"event": "read", "id": "new", "timestamp": _past(5)},
        ])
        # Marking another as read triggers rotation
        await notifs.mark_read(config, "other")
        events = _read_jsonl(read_log)
        event_ids = [e.get("id") for e in events]
        assert "old" not in event_ids
        assert "new" in event_ids
        assert "other" in event_ids


# -- Read-state reconstruction ------------------------------------------------


class TestReadState:
    @pytest.mark.asyncio
    async def test_read_marks_id(self, config):
        rec = await notifs.notify(config, category="t", title="A")
        await notifs.mark_read(config, rec.id)
        read_ids = notifs.get_read_ids(config)
        assert rec.id in read_ids

    @pytest.mark.asyncio
    async def test_unknown_read_id_filtered(self, config):
        """Read events for unknown ids are ignored (orphan filter)."""
        await notifs.notify(config, category="t", title="A")
        await notifs.mark_read(config, "does-not-exist")
        read_ids = notifs.get_read_ids(config)
        assert read_ids == set()

    @pytest.mark.asyncio
    async def test_read_all_marks_all_current(self, config):
        a = await notifs.notify(config, category="t", title="A")
        b = await notifs.notify(config, category="t", title="B")
        await notifs.mark_all_read(config)
        read_ids = notifs.get_read_ids(config)
        assert a.id in read_ids
        assert b.id in read_ids

    @pytest.mark.asyncio
    async def test_read_all_does_not_mark_future(self, config, monotonic_now):
        """A read-all event doesn't mark records created after it."""
        a = await notifs.notify(config, category="t", title="A")
        await notifs.mark_all_read(config)
        b = await notifs.notify(config, category="t", title="B")
        read_ids = notifs.get_read_ids(config)
        assert a.id in read_ids
        assert b.id not in read_ids


class TestReadEvents:
    """Event-bus publishes from mark_read / mark_all_read (WebSocket push)."""

    @pytest.mark.asyncio
    async def test_mark_read_publishes_event(self, config):
        from decafclaw.events import EventBus
        bus = EventBus()
        received: list[dict] = []
        bus.subscribe(lambda e: received.append(e))

        await notifs.notify(config, category="t", title="A")
        rec = await notifs.notify(config, category="t", title="B")
        received.clear()  # ignore the notification_created events

        await notifs.mark_read(config, rec.id, event_bus=bus)

        assert len(received) == 1
        ev = received[0]
        assert ev["type"] == "notification_read"
        assert ev["ids"] == [rec.id]
        assert ev["unread_count"] == 1  # the other record is still unread

    @pytest.mark.asyncio
    async def test_mark_read_no_bus_no_publish(self, config):
        """Back-compat: omitting event_bus still persists the read-state."""
        rec = await notifs.notify(config, category="t", title="A")
        await notifs.mark_read(config, rec.id)  # no event_bus kwarg
        assert notifs.unread_count(config) == 0

    @pytest.mark.asyncio
    async def test_mark_all_read_aggregates_ids(self, config):
        from decafclaw.events import EventBus
        bus = EventBus()
        received: list[dict] = []
        bus.subscribe(lambda e: received.append(e))

        a = await notifs.notify(config, category="t", title="A")
        b = await notifs.notify(config, category="t", title="B")
        c = await notifs.notify(config, category="t", title="C")
        # Pre-read one; mark_all should only emit ids for the two that
        # actually transition from unread to read.
        await notifs.mark_read(config, a.id)
        received.clear()

        await notifs.mark_all_read(config, event_bus=bus)

        assert len(received) == 1
        ev = received[0]
        assert ev["type"] == "notification_read"
        assert set(ev["ids"]) == {b.id, c.id}
        assert a.id not in ev["ids"]
        assert ev["unread_count"] == 0

    @pytest.mark.asyncio
    async def test_mark_all_read_empty_inbox_skips_publish(self, config):
        """Nothing to mark → no event (avoids no-op churn)."""
        from decafclaw.events import EventBus
        bus = EventBus()
        received: list[dict] = []
        bus.subscribe(lambda e: received.append(e))

        await notifs.mark_all_read(config, event_bus=bus)
        assert received == []

    @pytest.mark.asyncio
    async def test_mark_all_read_fully_read_inbox_skips_publish(self, config):
        from decafclaw.events import EventBus
        bus = EventBus()
        received: list[dict] = []
        bus.subscribe(lambda e: received.append(e))

        rec = await notifs.notify(config, category="t", title="A")
        await notifs.mark_read(config, rec.id)
        received.clear()

        await notifs.mark_all_read(config, event_bus=bus)
        assert received == []

    @pytest.mark.asyncio
    async def test_mark_all_read_no_bus_no_publish(self, config):
        """Back-compat: omitting event_bus still persists read-all state."""
        await notifs.notify(config, category="t", title="A")
        await notifs.mark_all_read(config)
        assert notifs.unread_count(config) == 0


class TestUnreadCount:
    @pytest.mark.asyncio
    async def test_empty_inbox(self, config):
        assert notifs.unread_count(config) == 0

    @pytest.mark.asyncio
    async def test_all_unread(self, config):
        for i in range(3):
            await notifs.notify(config, category="t", title=f"#{i}")
        assert notifs.unread_count(config) == 3

    @pytest.mark.asyncio
    async def test_some_read(self, config):
        a = await notifs.notify(config, category="t", title="A")
        await notifs.notify(config, category="t", title="B")
        await notifs.mark_read(config, a.id)
        assert notifs.unread_count(config) == 1

    @pytest.mark.asyncio
    async def test_read_all(self, config):
        for i in range(3):
            await notifs.notify(config, category="t", title=f"#{i}")
        await notifs.mark_all_read(config)
        assert notifs.unread_count(config) == 0


# -- read_inbox() -------------------------------------------------------------


class TestReadInbox:
    @pytest.mark.asyncio
    async def test_empty(self, config):
        records, has_more = notifs.read_inbox(config)
        assert records == []
        assert has_more is False

    @pytest.mark.asyncio
    async def test_newest_first(self, config, monotonic_now):
        await notifs.notify(config, category="t", title="A")
        await notifs.notify(config, category="t", title="B")
        records, _ = notifs.read_inbox(config)
        assert [r.title for r in records] == ["B", "A"]

    @pytest.mark.asyncio
    async def test_limit_and_has_more(self, config, monotonic_now):
        for i in range(5):
            await notifs.notify(config, category="t", title=f"#{i}")
        records, has_more = notifs.read_inbox(config, limit=3)
        assert len(records) == 3
        assert has_more is True

    @pytest.mark.asyncio
    async def test_before_cursor(self, config, monotonic_now):
        a = await notifs.notify(config, category="t", title="A")
        b = await notifs.notify(config, category="t", title="B")
        # Only A should come back when we query "before B's timestamp"
        records, _ = notifs.read_inbox(config, before=b.timestamp)
        ids = [r.id for r in records]
        assert a.id in ids
        assert b.id not in ids


# -- ctx.notify wrapper -------------------------------------------------------


class TestCtxNotify:
    @pytest.mark.asyncio
    async def test_populates_conv_id_from_ctx(self, ctx):
        ctx.conv_id = "conv-42"
        await ctx.notify(category="t", title="Hello")
        lines = _read_jsonl(notifs._inbox_path(ctx.config))
        assert len(lines) == 1
        assert lines[0]["conv_id"] == "conv-42"

    @pytest.mark.asyncio
    async def test_explicit_conv_id_wins(self, ctx):
        ctx.conv_id = "from-ctx"
        await ctx.notify(category="t", title="Hello", conv_id="explicit")
        lines = _read_jsonl(notifs._inbox_path(ctx.config))
        assert lines[0]["conv_id"] == "explicit"

    @pytest.mark.asyncio
    async def test_no_conv_id_from_empty_ctx(self, ctx):
        ctx.conv_id = ""
        await ctx.notify(category="t", title="Hello")
        lines = _read_jsonl(notifs._inbox_path(ctx.config))
        assert lines[0]["conv_id"] is None
