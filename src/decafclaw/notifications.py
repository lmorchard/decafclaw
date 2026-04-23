"""Notification inbox — append-only log of agent-initiated events.

Stores notifications and read-state as JSONL under
``{workspace}/notifications/``. Retention is time-based and enforced
opportunistically on append.

See docs/notifications.md for design rationale.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


# -- Record shape -------------------------------------------------------------


@dataclass
class NotificationRecord:
    """A single notification entry in the inbox."""
    id: str
    timestamp: str                  # ISO-8601 UTC, e.g. "2026-04-22T10:15:00Z"
    category: str                   # "heartbeat" | "schedule" | "background" | ...
    title: str
    priority: str = "normal"        # "low" | "normal" | "high"
    body: str = ""
    link: str | None = None
    conv_id: str | None = None

    def to_dict(self) -> dict:
        d: dict[str, Any] = {
            "id": self.id,
            "timestamp": self.timestamp,
            "category": self.category,
            "priority": self.priority,
            "title": self.title,
            "body": self.body,
            "link": self.link,
            "conv_id": self.conv_id,
        }
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "NotificationRecord":
        return cls(
            id=d.get("id", ""),
            timestamp=d.get("timestamp", ""),
            category=d.get("category", ""),
            title=d.get("title", ""),
            priority=d.get("priority", "normal"),
            body=d.get("body", ""),
            link=d.get("link"),
            conv_id=d.get("conv_id"),
        )


# -- Paths --------------------------------------------------------------------


def _notifications_dir(config) -> Path:
    return config.workspace_path / "notifications"


def _inbox_path(config) -> Path:
    return _notifications_dir(config) / "inbox.jsonl"


def _read_log_path(config) -> Path:
    return _notifications_dir(config) / "read.jsonl"


def _archive_dir(config) -> Path:
    return _notifications_dir(config) / "archive"


# -- Concurrency guard --------------------------------------------------------

# Multiple async tasks (heartbeat completion, background job exit, etc.) may
# call notify() concurrently. Lock per agent-id so interleaved appends can't
# corrupt the file.
_locks: dict[str, asyncio.Lock] = {}


def _get_lock(config) -> asyncio.Lock:
    key = config.agent.id or "default"
    if key not in _locks:
        _locks[key] = asyncio.Lock()
    return _locks[key]


# -- Time helpers -------------------------------------------------------------


def _now_iso() -> str:
    """UTC ISO-8601 with Z suffix, second precision."""
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(ts: str) -> datetime:
    """Parse an ISO-8601 UTC timestamp back to a datetime. Tolerant of trailing Z."""
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    return datetime.fromisoformat(ts)


# -- File IO ------------------------------------------------------------------


def _read_lines(path: Path) -> list[dict]:
    """Read a JSONL file; skip malformed lines with a warning. Missing file → []."""
    if not path.exists():
        return []
    records: list[dict] = []
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError as e:
                    log.warning("Malformed JSONL line in %s: %s", path, e)
    except OSError as e:
        log.warning("Failed to read %s: %s", path, e)
    return records


def _atomic_rewrite(path: Path, lines: list[dict]) -> None:
    """Rewrite a JSONL file atomically via temp file + os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for line in lines:
            f.write(json.dumps(line, separators=(",", ":")) + "\n")
    os.replace(tmp, path)


def _append_line(path: Path, record: dict) -> None:
    """Append one JSONL record. Creates parent dirs and file as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, separators=(",", ":")) + "\n")


# -- Rotation -----------------------------------------------------------------


def _partition_by_age(records: list[dict], retention_days: int) -> tuple[list[dict], list[dict]]:
    """Split records into (old, recent) by timestamp against retention."""
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=retention_days)
    old: list[dict] = []
    recent: list[dict] = []
    for r in records:
        ts = r.get("timestamp", "")
        try:
            dt = _parse_iso(ts)
        except (ValueError, TypeError):
            # Malformed timestamp — keep it, don't silently lose data
            recent.append(r)
            continue
        if dt < cutoff:
            old.append(r)
        else:
            recent.append(r)
    return old, recent


def _rotate_inbox_if_needed(config) -> None:
    """Opportunistic inbox rotation. Old records go to archive/YYYY-MM.jsonl; recent stay."""
    inbox = _inbox_path(config)
    if not inbox.exists():
        return

    records = _read_lines(inbox)
    if not records:
        return

    # Quick bail: if the first record is within retention, nothing to do.
    first_ts = records[0].get("timestamp", "")
    try:
        first_dt = _parse_iso(first_ts)
    except (ValueError, TypeError):
        first_dt = None
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=config.notifications.retention_days)
    if first_dt is not None and first_dt >= cutoff:
        return

    old, recent = _partition_by_age(records, config.notifications.retention_days)
    if not old:
        return

    # Group old records by year-month into archive files.
    by_month: dict[str, list[dict]] = {}
    for r in old:
        ts = r.get("timestamp", "")
        try:
            dt = _parse_iso(ts)
        except (ValueError, TypeError):
            continue
        key = dt.strftime("%Y-%m")
        by_month.setdefault(key, []).append(r)

    archive = _archive_dir(config)
    archive.mkdir(parents=True, exist_ok=True)
    for month_key, recs in by_month.items():
        archive_path = archive / f"{month_key}.jsonl"
        with archive_path.open("a", encoding="utf-8") as f:
            for rec in recs:
                f.write(json.dumps(rec, separators=(",", ":")) + "\n")

    _atomic_rewrite(inbox, recent)
    log.info(
        "Notification inbox rotated: %d record(s) archived across %d month(s)",
        len(old), len(by_month),
    )


def _rotate_read_log_if_needed(config) -> None:
    """Opportunistic read-log rotation. Old events are dropped (metadata, not content)."""
    path = _read_log_path(config)
    if not path.exists():
        return

    events = _read_lines(path)
    if not events:
        return

    first_ts = events[0].get("timestamp", "")
    try:
        first_dt = _parse_iso(first_ts)
    except (ValueError, TypeError):
        first_dt = None
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=config.notifications.retention_days)
    if first_dt is not None and first_dt >= cutoff:
        return

    _, recent = _partition_by_age(events, config.notifications.retention_days)
    if len(recent) == len(events):
        return
    _atomic_rewrite(path, recent)


# -- Public API ---------------------------------------------------------------


async def notify(
    config,
    *,
    category: str,
    title: str,
    body: str = "",
    priority: str = "normal",
    link: str | None = None,
    conv_id: str | None = None,
) -> NotificationRecord:
    """Append a notification to the inbox.

    In Phase 1 the inbox is the only consumer. Phase 2+ will dispatch to
    external channel adapters (Mattermost, email, vault page) after the
    inbox append. See docs/notifications.md.
    """
    record = NotificationRecord(
        id=secrets.token_hex(6),
        timestamp=_now_iso(),
        category=category,
        title=title,
        body=body,
        priority=priority,
        link=link,
        conv_id=conv_id,
    )

    lock = _get_lock(config)
    async with lock:
        _rotate_inbox_if_needed(config)
        _append_line(_inbox_path(config), record.to_dict())

    log.info(
        "notification: [%s/%s] %s (conv=%s)",
        category, priority, title, conv_id or "-",
    )
    return record


def read_inbox(
    config,
    *,
    limit: int | None = None,
    before: str | None = None,
) -> tuple[list[NotificationRecord], bool]:
    """Return inbox records, newest first.

    Args:
        limit: maximum number of records to return (None = all).
        before: ISO timestamp; only records strictly older than this are returned.

    Returns (records, has_more).
    """
    all_records = _read_lines(_inbox_path(config))
    # Newest first
    all_records.sort(key=lambda r: r.get("timestamp", ""), reverse=True)

    if before:
        all_records = [r for r in all_records if r.get("timestamp", "") < before]

    has_more = False
    if limit is not None and len(all_records) > limit:
        has_more = True
        all_records = all_records[:limit]

    return [NotificationRecord.from_dict(r) for r in all_records], has_more


async def mark_read(config, record_id: str) -> None:
    """Mark a single notification read. Idempotent."""
    event = {
        "event": "read",
        "id": record_id,
        "timestamp": _now_iso(),
    }
    lock = _get_lock(config)
    async with lock:
        _rotate_read_log_if_needed(config)
        _append_line(_read_log_path(config), event)


async def mark_all_read(config) -> None:
    """Mark all currently-visible notifications read."""
    event = {
        "event": "read-all",
        "timestamp": _now_iso(),
    }
    lock = _get_lock(config)
    async with lock:
        _rotate_read_log_if_needed(config)
        _append_line(_read_log_path(config), event)


def get_read_ids(config) -> set[str]:
    """Reconstruct the set of read notification IDs from the read-log.

    Filters against the current inbox so orphan IDs (from rotated-out
    records) are ignored.
    """
    events = _read_lines(_read_log_path(config))
    if not events:
        return set()

    # Live inbox ids (needed for read-all interpretation)
    live_ids: set[str] = set()
    live_by_timestamp: list[tuple[str, str]] = []
    for r in _read_lines(_inbox_path(config)):
        rid = r.get("id", "")
        ts = r.get("timestamp", "")
        if rid:
            live_ids.add(rid)
            live_by_timestamp.append((ts, rid))

    read_ids: set[str] = set()
    for event in events:
        kind = event.get("event")
        if kind == "read":
            rid = event.get("id", "")
            if rid:
                read_ids.add(rid)
        elif kind == "read-all":
            event_ts = event.get("timestamp", "")
            # Mark all inbox records present at the time of the read-all event.
            # For simplicity we mark all whose timestamp is <= the event timestamp.
            for ts, rid in live_by_timestamp:
                if ts <= event_ts:
                    read_ids.add(rid)

    # Filter orphans (ids from archived/rotated records)
    return read_ids & live_ids


def unread_count(config) -> int:
    """Count unread notifications in the live inbox."""
    records = _read_lines(_inbox_path(config))
    if not records:
        return 0
    read = get_read_ids(config)
    count = 0
    for r in records:
        rid = r.get("id", "")
        if rid and rid not in read:
            count += 1
    return count
