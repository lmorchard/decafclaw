"""RRULE recurrence helpers for calendar events (#1898).

Occurrences are computed in the event's IANA timezone using naive local
wall-clock times, then localized. This keeps "08:00 every day" at 08:00 across
daylight-saving transitions instead of drifting by an hour.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dateutil.rrule import rrulestr

from .calendar_events import CalendarEvent


def validate_timezone(name: str) -> str:
    """Validate and return an IANA timezone name."""
    value = str(name or "").strip()
    if not value:
        raise ValueError("timezone is required for a recurring event")
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"unknown timezone {value!r}") from exc
    return value


def _local_naive(value: str, tz: ZoneInfo) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("recurrence timestamps must include a timezone")
    return parsed.astimezone(tz).replace(tzinfo=None)


def validate_recurrence(rule: str, start: str, tz_name: str, until: str = "") -> None:
    """Raise ValueError when the RRULE, timezone, or until is invalid."""
    tz = ZoneInfo(validate_timezone(tz_name))
    try:
        parsed = rrulestr(str(rule or "").strip(), dtstart=_local_naive(start, tz))
    except Exception as exc:  # dateutil raises several parser errors
        raise ValueError(f"invalid recurrence: {exc}") from exc
    if parsed is None:
        raise ValueError("invalid recurrence")
    if until:
        until_dt = datetime.fromisoformat(until)
        if until_dt.tzinfo is None:
            raise ValueError("recurrence_until must include a timezone")
        if until_dt <= datetime.fromisoformat(start):
            raise ValueError("recurrence_until must be after fire_at")


def next_occurrence(event: CalendarEvent, after_ts: float) -> tuple[str, float] | None:
    """Return (ISO, timestamp) of the next occurrence after ``after_ts``, or None.

    ``recurrence_start`` is the original anchor. ``after_ts`` lets a missed
    occurrence skip forward to the next future one without firing every gap.
    """
    if not event.recurrence:
        return None
    tz = ZoneInfo(validate_timezone(event.timezone))
    anchor = _local_naive(event.recurrence_start or event.fire_at, tz)
    try:
        rule = rrulestr(event.recurrence, dtstart=anchor)
    except Exception as exc:
        raise ValueError(f"invalid recurrence: {exc}") from exc
    after_local = datetime.fromtimestamp(after_ts, tz=tz).replace(tzinfo=None)
    nxt = rule.after(after_local, inc=False)
    if nxt is None:
        return None
    if event.recurrence_until:
        until = _local_naive(event.recurrence_until, tz)
        if nxt > until:
            return None
    aware = nxt.replace(tzinfo=tz)
    return aware.isoformat(), aware.timestamp()
