"""Vault page notification channel adapter.

Subscribes to ``notification_created`` and appends each matching record
to a daily rollup file under the configured folder
(default ``<vault_root>/agent/pages/notifications/YYYY-MM-DD.md``).

Each entry becomes its own markdown section — subheading + metadata
block + body — so the daily page acts as a browsable, fragment-linkable
audit trail. Pages are plain markdown; they are deliberately NOT
embedded for semantic search (notifications are rolling logs, not
reference material — avoids re-embedding a growing day-page on every
append).

Concurrency is guarded by a per-path ``asyncio.Lock`` (same pattern as
``notifications._locks``) so multiple adapters firing in the same
second can't corrupt the file. Wired up at startup from
``notification_channels.init_notification_channels``; delivery failures
log at warning level and are otherwise swallowed — the inbox is the
source of truth.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Awaitable, Callable

from decafclaw.notifications import NotificationRecord, _parse_iso

from . import PRIORITY_GLYPH, meets_priority

log = logging.getLogger(__name__)


# Per-path locks so concurrent appends to the same daily page serialize.
# Keyed on the str() of the resolved path, mirroring notifications._locks.
_locks: dict[str, asyncio.Lock] = {}

# Folder paths we've already warned about, so a misconfigured folder
# doesn't spam the log once per notification.
_warned_bad_folders: set[str] = set()


def _get_lock(path: Path) -> asyncio.Lock:
    key = str(path)
    if key not in _locks:
        _locks[key] = asyncio.Lock()
    return _locks[key]


def _daily_page_path(config: Any, iso_timestamp: str) -> Path | None:
    """Resolve the daily page path for a notification timestamp.

    Returns None when the configured folder is empty, absolute, contains
    ``..``, or resolves outside the vault root — same sandboxing as the
    vault tools. On success returns ``<vault>/<folder>/YYYY-MM-DD.md``.

    Logs a warning the first time a given bad folder is rejected; stays
    quiet on repeats so a misconfigured channel doesn't spam the log
    per notification.
    """
    folder_cfg = config.notifications.channels.vault_page.folder
    if not folder_cfg:
        return _warn_once_and_none(folder_cfg, "empty")
    if folder_cfg.startswith("/") or ".." in folder_cfg:
        return _warn_once_and_none(folder_cfg, "absolute or contains '..'")

    vault = config.vault_root.resolve()
    folder = (vault / folder_cfg).resolve()
    if not folder.is_relative_to(vault):
        return _warn_once_and_none(folder_cfg, "escapes the vault root")

    try:
        date_part = _parse_iso(iso_timestamp).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        # Malformed timestamp — can't form a filename. Skip rather than
        # write to a fallback that'd be confusing.
        log.warning(
            "Vault page: malformed notification timestamp %r — skipping",
            iso_timestamp,
        )
        return None

    return folder / f"{date_part}.md"


def _warn_once_and_none(folder: str, reason: str) -> None:
    if folder not in _warned_bad_folders:
        log.warning(
            "Vault page: folder %r rejected (%s) — channel is effectively disabled",
            folder, reason,
        )
        _warned_bad_folders.add(folder)
    return None


def _format_new_page_header(date_str: str) -> str:
    """Initial contents for a freshly created daily page."""
    return (
        "---\n"
        f'title: "Notifications {date_str}"\n'
        "tags: [notifications, system]\n"
        "---\n\n"
        f"# Notifications — {date_str}\n\n"
    )


def _format_entry(record: NotificationRecord, base_url: str) -> str:
    """Format a single notification as a markdown section.

    Shape::

        ## HH:MM UTC · <glyph> [<category>] <title>

        - priority: <priority>
        - conv_id: <conv_id or —>
        - link: <link or —>

        <body or —>

    Trailing blank line so consecutive appends stay visually separated.
    """
    try:
        dt = _parse_iso(record.timestamp)
        time_str = dt.strftime("%H:%M UTC")
    except (ValueError, TypeError):
        time_str = "??:?? UTC"

    glyph = PRIORITY_GLYPH.get(record.priority, "🔔")
    heading = (
        f"## {time_str} · {glyph} [{record.category}] {record.title}"
    )

    conv_id = record.conv_id or "—"
    link = _resolve_link(record, base_url) or "—"
    body = record.body or "—"

    return (
        f"{heading}\n\n"
        f"- priority: {record.priority}\n"
        f"- conv_id: {conv_id}\n"
        f"- link: {link}\n\n"
        f"{body}\n\n"
    )


def _resolve_link(record: NotificationRecord, base_url: str) -> str | None:
    """Same resolution rules as the email channel — explicit http(s)
    link wins; else build ``<base_url>/#conv=<conv_id>`` when both are
    available; else None."""
    if record.link and (record.link.startswith("http://")
                        or record.link.startswith("https://")):
        return record.link
    if base_url and record.conv_id:
        return f"{base_url.rstrip('/')}/#conv={record.conv_id}"
    return None


def make_vault_page_adapter(
    config: Any,
) -> Callable[[dict], Awaitable[None]]:
    """Return an event-bus handler that appends notifications to a
    daily vault page.

    Closes over ``config``; all fields (enabled flag, min_priority,
    folder, vault_root, http.base_url) are re-read per event so
    in-process config mutations take effect on the next notification.
    """

    async def _deliver(record: NotificationRecord, path: Path,
                       base_url: str) -> None:
        """Background-task write — fire-and-forget from the handler."""
        try:
            lock = _get_lock(path)
            async with lock:
                path.parent.mkdir(parents=True, exist_ok=True)
                created_new = not path.exists()
                with path.open("a", encoding="utf-8") as f:
                    if created_new:
                        date_str = path.stem  # YYYY-MM-DD
                        f.write(_format_new_page_header(date_str))
                    f.write(_format_entry(record, base_url))
        except Exception as exc:
            log.warning(
                "Vault page delivery failed (path=%s category=%s "
                "priority=%s conv=%s): %s",
                path, record.category, record.priority,
                record.conv_id or "-", exc,
            )

    async def handle(event: dict) -> None:
        if event.get("type") != "notification_created":
            return
        ch = config.notifications.channels.vault_page
        if not ch.enabled:
            return
        record = NotificationRecord.from_dict(event["record"])
        if not meets_priority(record.priority, ch.min_priority):
            return
        # Folder validity (including emptiness) is checked inside
        # `_daily_page_path`, which emits a one-time warning per bad
        # folder and returns None — the handler stays quiet on repeat.
        path = _daily_page_path(config, record.timestamp)
        if path is None:
            return
        base_url = config.http.base_url
        asyncio.create_task(_deliver(record, path, base_url))

    return handle
