"""JSONL inbox for out-of-band improvement suggestions (#1482).

The improvement scout extension appends one JSON object per line to
``workspace/improvement_inbox.jsonl``; the companion ``improvement-inbox``
contrib skill reads and updates the same file. Appends are a single locked
``os.write`` on an ``O_APPEND`` descriptor, so concurrent producers cannot
interleave partial lines. Status changes and deletions read, transform, and
rewrite the whole file while holding ``_APPEND_LOCK``, then replace it through
a temporary sibling plus ``os.replace``, preserving every unrelated line
verbatim.

The lock's guarantee is in-process: it serializes this module's appends and
rewrites so a concurrent local append cannot land between a read and its
replacement. It does not coordinate with another process that opens its own
``O_APPEND`` descriptor: an append made by such a process after the rewrite
read the file is still lost by the ``os.replace``.

Malformed lines are tolerated on read: they are logged and skipped, never
deleted, so a hand edit cannot silently destroy the audit log.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

log = logging.getLogger(__name__)

INBOX_FILENAME = "improvement_inbox.jsonl"
STATUSES = ("pending", "exported", "dismissed", "archived")
SEVERITIES = ("low", "medium", "high")

# One process can run several conversation turns at once. O_APPEND keeps
# cross-process appends from overwriting bytes, and this lock keeps a single
# process from interleaving multi-byte writes (and from racing a rewrite).
_APPEND_LOCK = threading.Lock()


def inbox_path(config: Any) -> Path:
    """Return the inbox path under the agent-writable workspace."""
    return Path(config.workspace_path) / INBOX_FILENAME


def make_item_id(now: datetime | None = None, *, suffix: str | None = None) -> str:
    """Build an ``imp_YYYYMMDD_HHMMSS_xxxx`` id (UTC, like the data contract)."""
    now = now or datetime.now(timezone.utc)
    if suffix is None:
        seed = f"{now.isoformat()}:{os.getpid()}:{time.time_ns()}"
        suffix = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:4]
    return f"imp_{now.strftime('%Y%m%d_%H%M%S')}_{suffix}"


def utc_timestamp(now: datetime | None = None) -> str:
    """ISO-8601 UTC timestamp with a trailing ``Z``, second resolution."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_item(
    *,
    conversation_id: str,
    trigger: str,
    title: str,
    observed_friction: str,
    suggested_improvement: str,
    subsystem: str = "unknown",
    severity: str = "medium",
    now: datetime | None = None,
    item_id: str | None = None,
    status: str = "pending",
) -> dict[str, Any]:
    """Build a record matching the #1482 data contract."""
    if status not in STATUSES:
        raise ValueError(f"unknown improvement item status: {status}")
    return {
        "id": item_id or make_item_id(now),
        "timestamp": utc_timestamp(now),
        "conversation_id": conversation_id,
        "trigger": trigger,
        "title": title,
        "observed_friction": observed_friction,
        "suggested_improvement": suggested_improvement,
        "subsystem": subsystem,
        "severity": severity if severity in SEVERITIES else "medium",
        "status": status,
        "status_updated_at": None,
        "dismissal_reason": None,
    }


def _serialize(item: dict[str, Any]) -> str:
    return json.dumps(item, ensure_ascii=False) + "\n"


def append_item(config: Any, item: dict[str, Any], *, path: Path | None = None) -> Path:
    """Append one record without ever leaving a partial line.

    ``os.write`` on an ``O_APPEND`` file places the whole buffer at the end of
    the file in one syscall; the process lock covers the partial-write retry
    so two local writers cannot interleave halves.
    """
    target = Path(path) if path is not None else inbox_path(config)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = _serialize(item).encode("utf-8")

    with _APPEND_LOCK:
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        try:
            written = 0
            while written < len(payload):
                written += os.write(fd, payload[written:])
        finally:
            os.close(fd)
    return target


def _read_raw_lines(target: Path) -> list[str]:
    try:
        text = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    return text.splitlines()


def read_items(config: Any, *, path: Path | None = None) -> list[dict[str, Any]]:
    """Read every record, skipping (and logging) malformed lines."""
    target = Path(path) if path is not None else inbox_path(config)
    items: list[dict[str, Any]] = []
    for lineno, line in enumerate(_read_raw_lines(target), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as e:
            log.warning("Skipping malformed improvement inbox line %d in %s: %s", lineno, target, e)
            continue
        if isinstance(record, dict):
            items.append(record)
        else:
            log.warning("Skipping non-object improvement inbox line %d in %s", lineno, target)
    return items


def filter_items(
    items: Iterable[dict[str, Any]],
    *,
    status: str | None = "pending",
    subsystem: str | None = None,
    severity: str | None = None,
) -> list[dict[str, Any]]:
    """Filter records by status/subsystem/severity (``None`` means any)."""
    filtered: list[dict[str, Any]] = []
    for item in items:
        if status is not None and item.get("status") != status:
            continue
        if subsystem and item.get("subsystem") != subsystem:
            continue
        if severity and item.get("severity") != severity:
            continue
        filtered.append(item)
    return filtered


def get_item(items: Iterable[dict[str, Any]], item_id: str) -> dict[str, Any] | None:
    """Return the record with ``item_id``, or ``None``."""
    for item in items:
        if item.get("id") == item_id:
            return item
    return None


def _rewrite_atomic(target: Path, lines: list[str]) -> None:
    """Replace the inbox with ``lines`` through a temp file plus ``os.replace``."""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".improvement_inbox.", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            for line in lines:
                fh.write(line)
                fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, target)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


def update_status(
    config: Any,
    item_ids: Iterable[str],
    status: str,
    *,
    reason: str | None = None,
    now: datetime | None = None,
    path: Path | None = None,
) -> list[str]:
    """Set ``status`` on the named records; return the ids actually changed.

    Unrelated and malformed lines are written back verbatim, so a concurrent
    append or a hand-written comment survives the rewrite.
    """
    if status not in STATUSES:
        raise ValueError(f"unknown improvement item status: {status}")
    wanted = {item_id for item_id in item_ids if item_id}
    target = Path(path) if path is not None else inbox_path(config)
    stamp = utc_timestamp(now)

    with _APPEND_LOCK:
        raw_lines = _read_raw_lines(target)
        if not raw_lines:
            return []

        updated: list[str] = []
        out_lines: list[str] = []
        for line in raw_lines:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                out_lines.append(line)
                continue
            if not isinstance(record, dict) or record.get("id") not in wanted:
                out_lines.append(line)
                continue
            record["status"] = status
            record["status_updated_at"] = stamp
            if status == "dismissed":
                record["dismissal_reason"] = reason
            updated.append(str(record["id"]))
            out_lines.append(json.dumps(record, ensure_ascii=False))

        if not updated:
            return []
        _rewrite_atomic(target, out_lines)
    return updated


def remove_items(
    config: Any,
    item_ids: Iterable[str],
    *,
    path: Path | None = None,
) -> list[str]:
    """Delete the named records from the inbox; return the ids removed."""
    wanted = {item_id for item_id in item_ids if item_id}
    target = Path(path) if path is not None else inbox_path(config)

    with _APPEND_LOCK:
        raw_lines = _read_raw_lines(target)
        if not raw_lines:
            return []

        removed: list[str] = []
        out_lines: list[str] = []
        for line in raw_lines:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                out_lines.append(line)
                continue
            if isinstance(record, dict) and record.get("id") in wanted:
                removed.append(str(record["id"]))
                continue
            out_lines.append(line)

        if not removed:
            return []
        _rewrite_atomic(target, out_lines)
    return removed
