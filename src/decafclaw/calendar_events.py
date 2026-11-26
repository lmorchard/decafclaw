"""Durable one-shot calendar events (#1896).

Events live in per-origin-conversation sidecars under the admin data dir:
``data/{agent_id}/calendar/{conv_id}.json``. Each event targets its origin
conversation or a dedicated web session. The sidecar is outside the
agent-writable workspace, matching ``scheduled_wakes/`` and ``file_watches/``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from .conversation_paths import calendar_path

log = logging.getLogger(__name__)

SESSION_MODES = ("current", "dedicated")
EVENT_STATUSES = ("pending", "fired", "cancelled", "orphaned")
_SESSION_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


def now_iso() -> str:
    """Return the current time as an ISO-8601 UTC string."""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class CalendarEvent:
    """A one-shot calendar event."""

    event_id: str
    conv_id: str
    target_conv_id: str
    session_mode: str
    title: str
    reason: str
    fire_at: str
    fire_at_ts: float
    created_at: str
    updated_at: str
    session_key: str = ""
    session_title: str = ""
    user_id: str = ""
    channel_id: str = ""
    thread_id: str = ""
    transport: str = ""
    status: str = "pending"
    last_fired_at: str = ""
    snooze_until: str = ""


def new_event_id() -> str:
    """Return a short unique calendar event id."""
    return f"cal_{uuid.uuid4().hex[:8]}"


def parse_fire_at(value: str) -> tuple[str, float]:
    """Parse an absolute ISO-8601 timestamp and require a timezone."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("fire_at is required")
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError as exc:
        raise ValueError(f"fire_at must be an ISO-8601 timestamp: {exc}") from exc
    if parsed.tzinfo is None:
        raise ValueError("fire_at must include a timezone")
    return parsed.isoformat(), parsed.timestamp()


def validate_session_key(session_key: str) -> str:
    """Validate a dedicated-session key and return it unchanged."""
    key = str(session_key or "").strip()
    if not _SESSION_KEY_RE.match(key) or key.endswith("-"):
        raise ValueError("session_key must be a lowercase slug with letters, digits, and hyphens")
    return key


def calendar_session_conv_id(user_id: str, session_key: str) -> str:
    """Return the deterministic web conversation id for a dedicated session.

    The id keeps the ``web-{user_id}-{suffix}`` shape that
    ``is_web_conv_for_user`` expects. The suffix has no hyphen so the
    ownership check's ``rpartition("-")`` cannot split into the user id.
    """
    digest = hashlib.sha1(session_key.encode("utf-8")).hexdigest()[:8]
    return f"web-{user_id}-cal{digest}"


def load_calendar_events(config, conv_id: str) -> list[CalendarEvent]:
    """Load calendar events from one origin conversation's sidecar.

    Fail-open: a missing, empty, or corrupt file returns an empty list.
    """
    path = calendar_path(config, conv_id)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        entries = data.get("events", [])
        return [CalendarEvent(**item) for item in entries if isinstance(item, dict)]
    except Exception as exc:
        log.warning("Failed to load calendar events for conv %s: %s", conv_id, exc)
        return []


def save_calendar_events(config, conv_id: str, events: list[CalendarEvent]) -> None:
    """Save calendar events atomically to the conversation's sidecar."""
    path = calendar_path(config, conv_id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"events": [asdict(event) for event in events]}
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        log.warning("Failed to save calendar events for conv %s: %s", conv_id, exc)
        raise


def load_all_calendar_events(config) -> list[CalendarEvent]:
    """Load every calendar event sidecar under the admin data dir."""
    base = (config.agent_path / "calendar").resolve()
    if not base.exists():
        return []
    events: list[CalendarEvent] = []
    try:
        files = sorted(base.glob("*.json"))
    except OSError as exc:
        log.warning("Failed to list calendar event files: %s", exc)
        return []
    for file in files:
        events.extend(load_calendar_events(config, file.stem))
    return events


def event_to_dict(event: CalendarEvent) -> dict[str, Any]:
    """Return a JSON-ready event payload for tool results."""
    return asdict(event)
