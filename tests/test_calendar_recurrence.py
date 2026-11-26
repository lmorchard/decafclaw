"""RRULE next-occurrence tests for calendar recurrence (#1898)."""

from datetime import datetime

import pytest

from decafclaw.calendar_events import CalendarEvent
from decafclaw.calendar_recurrence import next_occurrence, validate_recurrence, validate_timezone


def _event(rule: str, start: str, tz: str, until: str = "") -> CalendarEvent:
    return CalendarEvent(
        event_id="cal_test",
        conv_id="conv-a",
        target_conv_id="conv-a",
        session_mode="current",
        title="Recurring",
        reason="Do it",
        fire_at=start,
        fire_at_ts=datetime.fromisoformat(start).timestamp(),
        created_at=start,
        updated_at=start,
        recurrence=rule,
        timezone=tz,
        recurrence_start=start,
        occurrence_at=start,
        recurrence_until=until,
    )


def _ts(iso: str) -> float:
    return datetime.fromisoformat(iso).timestamp()


def test_daily_keeps_wall_clock_across_spring_forward():
    event = _event("FREQ=DAILY", "2027-03-13T08:00:00-08:00", "America/Los_Angeles")
    nxt = next_occurrence(event, _ts("2027-03-13T08:00:00-08:00"))
    assert nxt is not None
    assert nxt[0] == "2027-03-14T08:00:00-07:00"


def test_daily_keeps_wall_clock_across_fall_back():
    event = _event("FREQ=DAILY", "2027-11-06T08:00:00-07:00", "America/Los_Angeles")
    nxt = next_occurrence(event, _ts("2027-11-06T08:00:00-07:00"))
    assert nxt is not None
    assert nxt[0] == "2027-11-07T08:00:00-08:00"


def test_weekly_on_selected_weekday():
    event = _event("FREQ=WEEKLY;BYDAY=MO", "2027-03-15T09:00:00+00:00", "UTC")
    nxt = next_occurrence(event, _ts("2027-03-15T09:00:00+00:00"))
    assert nxt is not None
    assert nxt[0].startswith("2027-03-22T09:00:00")


def test_weekdays_skip_the_weekend():
    event = _event("FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR", "2027-03-19T09:00:00+00:00", "UTC")
    nxt = next_occurrence(event, _ts("2027-03-19T09:00:00+00:00"))
    assert nxt is not None
    assert nxt[0].startswith("2027-03-22T09:00:00")


def test_interval():
    event = _event("FREQ=DAILY;INTERVAL=3", "2027-03-15T09:00:00+00:00", "UTC")
    nxt = next_occurrence(event, _ts("2027-03-15T09:00:00+00:00"))
    assert nxt is not None
    assert nxt[0].startswith("2027-03-18T09:00:00")


def test_until_stops_the_series():
    event = _event(
        "FREQ=DAILY",
        "2027-03-15T09:00:00+00:00",
        "UTC",
        until="2027-03-16T09:00:00+00:00",
    )
    assert next_occurrence(event, _ts("2027-03-15T09:00:00+00:00")) is not None
    assert next_occurrence(event, _ts("2027-03-16T09:00:00+00:00")) is None


def test_invalid_timezone_and_rule_raise():
    with pytest.raises(ValueError, match="timezone"):
        validate_timezone("Not/AZone")
    with pytest.raises(ValueError, match="invalid recurrence"):
        validate_recurrence("FREQ=NOPE", "2027-03-15T09:00:00+00:00", "UTC")
