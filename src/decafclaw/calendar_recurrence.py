"""RRULE recurrence helpers for calendar events (#1898).

Occurrences are computed in the event's IANA timezone using naive local
wall-clock times, then localized. This keeps "08:00 every day" at 08:00 across
daylight-saving transitions instead of drifting by an hour.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dateutil.rrule import rrulestr

from .calendar_events import CalendarEvent

#: Frequencies an event may repeat at. Anything denser than daily turns a
#: Month view into millions of occurrences (#1953); RFC 5545 also defines
#: HOURLY, MINUTELY, and SECONDLY, which we refuse.
ALLOWED_FREQUENCIES = ("DAILY", "WEEKLY", "MONTHLY", "YEARLY")

#: Matches the UNTIL value inside an RRULE string.
_UNTIL_RE = re.compile(r"UNTIL=([0-9T:.Z+-]+)", re.IGNORECASE)

_FREQ_RE = re.compile(r"FREQ=([A-Za-z]+)", re.IGNORECASE)


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


def _normalize_until(rule: str, tz: ZoneInfo) -> str:
    """Rewrite an absolute UNTIL into the event's local wall-clock time.

    Rules are built from a naive local ``dtstart`` so occurrences keep their
    local time across DST. dateutil refuses the RFC's UTC UNTIL form against a
    naive DTSTART ("must be specified in UTC when DTSTART is timezone-aware"),
    which is both wrong about the cause and blocks the form an agent is most
    likely to write (#1954). Converting the instant to local naive time keeps
    the RFC form usable and the meaning identical.
    """

    def replace(match: re.Match[str]) -> str:
        raw = match.group(1)
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return match.group(0)
        if parsed.tzinfo is None:
            return match.group(0)
        local = parsed.astimezone(tz).replace(tzinfo=None)
        return f"UNTIL={local.strftime('%Y%m%dT%H%M%S')}"

    return _UNTIL_RE.sub(replace, rule)


def _build_rule(rule: str, anchor: datetime, tz: ZoneInfo):
    """Parse an RRULE string against a naive local anchor."""
    try:
        return rrulestr(_normalize_until(str(rule or "").strip(), tz), dtstart=anchor)
    except Exception as exc:  # dateutil raises several parser errors
        raise ValueError(f"invalid recurrence: {exc}") from exc


def _check_frequency(rule: str) -> None:
    match = _FREQ_RE.search(rule)
    if match is None:
        raise ValueError("recurrence must include FREQ")
    freq = match.group(1).upper()
    if freq not in ALLOWED_FREQUENCIES:
        allowed = ", ".join(ALLOWED_FREQUENCIES)
        raise ValueError(f"unsupported recurrence frequency {freq}; use one of {allowed}")


def _first_occurrence(rule, anchor: datetime) -> datetime | None:
    """Return the first occurrence of ``rule`` at or after ``anchor``.

    dateutil truncates microseconds from ``dtstart``, so truncate before
    stepping back — otherwise the anchor's own occurrence is skipped.
    """
    try:
        return rule.after(anchor.replace(microsecond=0) - timedelta(microseconds=1), inc=False)
    except (ValueError, TypeError):
        return None


def validate_recurrence(rule: str, start: str, tz_name: str, until: str = "") -> None:
    """Raise ValueError when the RRULE, timezone, or until is invalid."""
    tz = ZoneInfo(validate_timezone(tz_name))
    rule = str(rule or "").strip()
    _check_frequency(rule)
    anchor = _local_naive(start, tz)
    parsed = _build_rule(rule, anchor, tz)
    if parsed is None:
        raise ValueError("invalid recurrence")

    # dateutil does not require the dtstart to satisfy the rule, so a
    # contradictory pair (a Thursday fire_at with BYDAY=MO) would fire once on
    # Thursday and then silently switch to Mondays. Reject it instead (#1954).
    # dateutil also truncates microseconds from dtstart, so compare at the
    # resolution it actually keeps.
    first = _first_occurrence(parsed, anchor)
    if first is not None and first.replace(microsecond=0) != anchor.replace(microsecond=0):
        raise ValueError(
            f"fire_at {start} does not match the recurrence rule; the first occurrence would be {first.isoformat()}"
        )

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
    rule = _build_rule(event.recurrence, anchor, tz)
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
