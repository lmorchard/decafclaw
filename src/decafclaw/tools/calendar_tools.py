"""Agent tools for one-shot calendar events (#1896)."""

import logging
import time
from dataclasses import asdict
from typing import TYPE_CHECKING

from ..calendar_events import (
    EVENT_STATUSES,
    SESSION_MODES,
    CalendarEvent,
    event_to_dict,
    new_event_id,
    now_iso,
    parse_fire_at,
    recurrence_summary,
    validate_session_key,
)
from ..calendar_manager import CalendarManager
from ..calendar_recurrence import validate_recurrence, validate_timezone
from ..media import ToolResult, WidgetRequest
from .schedule_tools import _is_disallowed_task_mode

if TYPE_CHECKING:
    from decafclaw.context import Context

log = logging.getLogger(__name__)


def _guard(ctx: "Context") -> ToolResult | None:
    """Calendar tools run only in interactive user conversations."""
    if getattr(ctx, "is_child", False) or _is_disallowed_task_mode(ctx) or getattr(ctx, "parent_is_unattended", False):
        return ToolResult(
            text="[error: calendar tools are only available in interactive user conversations]",
            data={"error": "unattended_or_child_not_permitted"},
        )
    return None


def _get_calendar_manager(ctx: "Context") -> CalendarManager:
    """Resolve the CalendarManager from manager or construct an ephemeral one."""
    if ctx.manager is not None and hasattr(ctx.manager, "calendar"):
        return ctx.manager.calendar
    return CalendarManager(ctx.config, manager=ctx.manager)


def _require_conv_id(ctx: "Context") -> ToolResult | None:
    if not ctx.conv_id:
        return ToolResult(
            text="[error: No active conversation ID found in context to manage calendar events]",
            data={"error": "missing_conv_id"},
        )
    return None


def _parse_optional_time(value: str | None) -> tuple[str, float] | None:
    if value is None or not str(value).strip():
        return None
    return parse_fire_at(str(value))


async def _publish_changed(ctx: "Context", event: CalendarEvent, action: str) -> None:
    try:
        await ctx.publish(
            "calendar_changed",
            conv_id=event.conv_id,
            event_id=event.event_id,
            action=action,
        )
    except Exception as exc:  # noqa: BLE001 - live refresh is best-effort
        log.debug("calendar_changed publish failed: %s", exc)


def _event_line(event: CalendarEvent) -> str:
    target = event.session_title or event.target_conv_id
    line = f"- {event.event_id}: {event.fire_at} — {event.title} (target: {target}, status: {event.status})"
    summary = recurrence_summary(event)
    if summary:
        line += f" [repeats: {summary}]"
    return line


async def tool_calendar_create(
    ctx: "Context",
    title: str,
    reason: str,
    fire_at: str,
    session_mode: str = "current",
    session_key: str = "",
    session_title: str = "",
    recurrence: str = "",
    timezone: str = "",
    recurrence_until: str = "",
) -> ToolResult:
    """Create a calendar event, optionally recurring."""
    error = _guard(ctx)
    if error is not None:
        return error
    error = _require_conv_id(ctx)
    if error is not None:
        return error

    title_text = str(title or "").strip()
    reason_text = str(reason or "").strip()
    if not title_text:
        return ToolResult(text="[error: title is required]", data={"error": "missing_title"})
    if not reason_text:
        return ToolResult(text="[error: reason is required]", data={"error": "missing_reason"})

    try:
        fire_at_iso, fire_at_ts = parse_fire_at(fire_at)
    except ValueError as exc:
        return ToolResult(text=f"[error: {exc}]", data={"error": "invalid_fire_at"})
    if fire_at_ts <= time.time():
        return ToolResult(text="[error: fire_at must be in the future]", data={"error": "past_fire_at"})

    recurrence_text = str(recurrence or "").strip()
    timezone_text = str(timezone or "").strip()
    until_text = str(recurrence_until or "").strip()
    if recurrence_text:
        try:
            timezone_text = validate_timezone(timezone_text)
            validate_recurrence(recurrence_text, fire_at_iso, timezone_text, until_text)
        except ValueError as exc:
            return ToolResult(text=f"[error: {exc}]", data={"error": "invalid_recurrence"})
    else:
        if until_text:
            return ToolResult(
                text="[error: recurrence_until requires recurrence]",
                data={"error": "recurrence_until_without_recurrence"},
            )
        if timezone_text:
            try:
                timezone_text = validate_timezone(timezone_text)
            except ValueError as exc:
                return ToolResult(text=f"[error: {exc}]", data={"error": "invalid_timezone"})

    mode = str(session_mode or "current").strip().lower()
    if mode not in SESSION_MODES:
        return ToolResult(
            text=f"[error: session_mode must be one of {', '.join(SESSION_MODES)}]",
            data={"error": "invalid_session_mode"},
        )

    key = ""
    session_name = ""
    if mode == "dedicated":
        if not getattr(ctx, "user_id", ""):
            return ToolResult(
                text="[error: dedicated sessions require an authenticated user]",
                data={"error": "missing_user_id"},
            )
        try:
            key = validate_session_key(session_key)
        except ValueError as exc:
            return ToolResult(text=f"[error: {exc}]", data={"error": "invalid_session_key"})
        session_name = str(session_title or "").strip()
        if not session_name:
            return ToolResult(
                text="[error: session_title is required for a dedicated session]",
                data={"error": "missing_session_title"},
            )

    event = CalendarEvent(
        event_id=new_event_id(),
        conv_id=ctx.conv_id,
        target_conv_id=ctx.conv_id,
        session_mode=mode,
        session_key=key,
        session_title=session_name,
        title=title_text,
        reason=reason_text,
        fire_at=fire_at_iso,
        fire_at_ts=fire_at_ts,
        created_at=now_iso(),
        updated_at=now_iso(),
        user_id=getattr(ctx, "user_id", "") or "",
        channel_id=getattr(ctx, "channel_id", "") or "",
        thread_id=getattr(ctx, "thread_id", "") or "",
        transport=getattr(ctx, "transport", "") or "",
        recurrence=recurrence_text,
        timezone=timezone_text,
        recurrence_start=fire_at_iso if recurrence_text else "",
        occurrence_at=fire_at_iso if recurrence_text else "",
        recurrence_until=until_text,
    )

    mgr = _get_calendar_manager(ctx)
    try:
        event = await mgr.add(event)
    except ValueError as exc:
        return ToolResult(text=f"[error: {exc}]", data={"error": str(exc)})
    except OSError as exc:
        log.warning("Persistence error creating calendar event: %s", exc)
        return ToolResult(
            text=f"[error: Failed to persist calendar event: {exc}]", data={"error": "persistence_failure"}
        )
    except Exception as exc:
        log.warning("Unexpected error creating calendar event: %s", exc)
        return ToolResult(text=f"[error: Failed to create calendar event: {exc}]", data={"error": str(exc)})

    await _publish_changed(ctx, event, "created")
    target = event.session_title or event.target_conv_id
    suffix = f" (repeats: {recurrence_summary(event)})" if event.recurrence else ""
    return ToolResult(
        text=f"Created calendar event '{event.event_id}' at {event.fire_at} for {target}: {event.title}{suffix}",
        data=asdict(event),
    )


async def tool_calendar_list(
    ctx: "Context",
    status: str = "pending",
    from_time: str | None = None,
    to_time: str | None = None,
) -> ToolResult:
    """List calendar events for the current conversation."""
    error = _guard(ctx)
    if error is not None:
        return error
    error = _require_conv_id(ctx)
    if error is not None:
        return error

    status_text = str(status or "pending").strip().lower()
    if status_text not in (*EVENT_STATUSES, "all"):
        return ToolResult(
            text=f"[error: status must be one of {', '.join((*EVENT_STATUSES, 'all'))}]",
            data={"error": "invalid_status"},
        )
    try:
        from_parsed = _parse_optional_time(from_time)
        to_parsed = _parse_optional_time(to_time)
    except ValueError as exc:
        return ToolResult(text=f"[error: {exc}]", data={"error": "invalid_time_range"})

    mgr = _get_calendar_manager(ctx)
    events = mgr.list_events(
        ctx.conv_id,
        status=status_text,
        from_ts=from_parsed[1] if from_parsed else None,
        to_ts=to_parsed[1] if to_parsed else None,
    )
    if not events:
        return ToolResult(
            text="No calendar events matched.",
            data={"events": []},
        )

    widget = WidgetRequest(
        widget_type="data_table",
        data={
            "caption": f"calendar_list: {len(events)} event(s)",
            "columns": [
                {"key": "event_id", "label": "Event ID"},
                {"key": "fire_at", "label": "Fire At"},
                {"key": "title", "label": "Title"},
                {"key": "target", "label": "Target"},
                {"key": "status", "label": "Status"},
                {"key": "recurrence", "label": "Repeats"},
            ],
            "rows": [
                {
                    "event_id": event.event_id,
                    "fire_at": event.fire_at,
                    "title": event.title,
                    "target": event.session_title or event.target_conv_id,
                    "status": event.status,
                    "recurrence": recurrence_summary(event),
                }
                for event in events
            ],
        },
        target="inline",
    )
    return ToolResult(
        text="\n".join([f"Found {len(events)} calendar event(s):", *(_event_line(event) for event in events)]),
        data={"events": [event_to_dict(event) for event in events]},
        widget=widget,
    )


async def tool_calendar_update(
    ctx: "Context",
    event_id: str,
    title: str | None = None,
    reason: str | None = None,
    fire_at: str | None = None,
    session_mode: str | None = None,
    session_key: str | None = None,
    session_title: str | None = None,
    recurrence: str | None = None,
    timezone: str | None = None,
    recurrence_until: str | None = None,
) -> ToolResult:
    """Update a pending calendar event."""
    error = _guard(ctx)
    if error is not None:
        return error
    error = _require_conv_id(ctx)
    if error is not None:
        return error

    event_key = str(event_id or "").strip()
    if not event_key:
        return ToolResult(text="[error: event_id is required]", data={"error": "missing_event_id"})

    mgr = _get_calendar_manager(ctx)
    event = mgr.get_event(ctx.conv_id, event_key)
    if event is None:
        return ToolResult(text=f"[error: Calendar event '{event_key}' not found]", data={"error": "not_found"})
    if event.status != "pending":
        return ToolResult(
            text=f"[error: Only pending events can be updated (status: {event.status})]",
            data={"error": "not_pending", "status": event.status},
        )

    changes: dict = {}
    if title is not None:
        title_text = str(title).strip()
        if not title_text:
            return ToolResult(text="[error: title cannot be empty]", data={"error": "empty_title"})
        changes["title"] = title_text
    if reason is not None:
        reason_text = str(reason).strip()
        if not reason_text:
            return ToolResult(text="[error: reason cannot be empty]", data={"error": "empty_reason"})
        changes["reason"] = reason_text
    if fire_at is not None:
        try:
            fire_at_iso, fire_at_ts = parse_fire_at(fire_at)
        except ValueError as exc:
            return ToolResult(text=f"[error: {exc}]", data={"error": "invalid_fire_at"})
        if fire_at_ts <= time.time():
            return ToolResult(text="[error: fire_at must be in the future]", data={"error": "past_fire_at"})
        changes["fire_at"] = fire_at_iso
        changes["fire_at_ts"] = fire_at_ts
        # Moving a series moves its anchor. Without this, next_occurrence keeps
        # computing from the old recurrence_start and the series reverts after
        # one fire (#1951). recurrence_until stays an absolute instant.
        if event.recurrence:
            changes["recurrence_start"] = fire_at_iso
            changes["occurrence_at"] = fire_at_iso

    if recurrence is not None or timezone is not None or recurrence_until is not None:
        fire_iso = changes.get("fire_at", event.fire_at)
        final_rule = event.recurrence if recurrence is None else str(recurrence).strip()
        final_tz = event.timezone if timezone is None else str(timezone).strip()
        final_until = event.recurrence_until if recurrence_until is None else str(recurrence_until).strip()
        if final_rule:
            try:
                final_tz = validate_timezone(final_tz)
                validate_recurrence(final_rule, fire_iso, final_tz, final_until)
            except ValueError as exc:
                return ToolResult(text=f"[error: {exc}]", data={"error": "invalid_recurrence"})
            changes.update(
                {
                    "recurrence": final_rule,
                    "timezone": final_tz,
                    "recurrence_until": final_until,
                    "recurrence_start": fire_iso,
                    "occurrence_at": fire_iso,
                }
            )
        else:
            if final_until:
                return ToolResult(
                    text="[error: recurrence_until requires recurrence]",
                    data={"error": "recurrence_until_without_recurrence"},
                )
            changes.update({"recurrence": "", "recurrence_until": "", "recurrence_start": "", "occurrence_at": ""})
            if timezone is not None:
                try:
                    changes["timezone"] = validate_timezone(final_tz)
                except ValueError as exc:
                    return ToolResult(text=f"[error: {exc}]", data={"error": "invalid_timezone"})

    mode = str(session_mode).strip().lower() if session_mode is not None else None
    if mode is None and (session_key is not None or session_title is not None):
        mode = "dedicated"
        changes["session_mode"] = "dedicated"
    if mode is not None:
        if mode not in SESSION_MODES:
            return ToolResult(
                text=f"[error: session_mode must be one of {', '.join(SESSION_MODES)}]",
                data={"error": "invalid_session_mode"},
            )
        changes["session_mode"] = mode
        if mode == "current":
            changes["session_key"] = ""
            changes["session_title"] = ""

    if session_key is not None and mode != "current":
        try:
            changes["session_key"] = validate_session_key(session_key)
        except ValueError as exc:
            return ToolResult(text=f"[error: {exc}]", data={"error": "invalid_session_key"})
    if session_title is not None and mode != "current":
        session_name = str(session_title).strip()
        if not session_name:
            return ToolResult(
                text="[error: session_title cannot be empty]",
                data={"error": "empty_session_title"},
            )
        changes["session_title"] = session_name

    final_mode = changes.get("session_mode", event.session_mode)
    if final_mode == "dedicated":
        final_key = changes.get("session_key", event.session_key)
        final_title = changes.get("session_title", event.session_title)
        if not final_key:
            return ToolResult(
                text="[error: session_key is required for a dedicated session]", data={"error": "missing_session_key"}
            )
        if not final_title:
            return ToolResult(
                text="[error: session_title is required for a dedicated session]",
                data={"error": "missing_session_title"},
            )

    if not changes:
        return ToolResult(text="[error: No changes were supplied]", data={"error": "no_changes"})

    try:
        updated = await mgr.update(ctx.conv_id, event_key, **changes)
    except ValueError as exc:
        return ToolResult(text=f"[error: {exc}]", data={"error": str(exc)})
    except OSError as exc:
        log.warning("Persistence error updating calendar event: %s", exc)
        return ToolResult(
            text=f"[error: Failed to persist calendar event: {exc}]", data={"error": "persistence_failure"}
        )
    except Exception as exc:
        log.warning("Unexpected error updating calendar event: %s", exc)
        return ToolResult(text=f"[error: Failed to update calendar event: {exc}]", data={"error": str(exc)})

    if updated is None:
        return ToolResult(text=f"[error: Calendar event '{event_key}' not found]", data={"error": "not_found"})
    await _publish_changed(ctx, updated, "updated")
    return ToolResult(
        text=f"Updated calendar event '{updated.event_id}' to fire at {updated.fire_at}: {updated.title}",
        data=asdict(updated),
    )


async def tool_calendar_cancel(ctx: "Context", event_id: str) -> ToolResult:
    """Cancel a pending calendar event."""
    error = _guard(ctx)
    if error is not None:
        return error
    error = _require_conv_id(ctx)
    if error is not None:
        return error

    event_key = str(event_id or "").strip()
    if not event_key:
        return ToolResult(text="[error: event_id is required]", data={"error": "missing_event_id"})

    mgr = _get_calendar_manager(ctx)
    try:
        cancelled = await mgr.cancel(ctx.conv_id, event_key)
    except OSError as exc:
        log.warning("Persistence error cancelling calendar event: %s", exc)
        return ToolResult(
            text=f"[error: Failed to persist calendar event: {exc}]", data={"error": "persistence_failure"}
        )
    if not cancelled:
        return ToolResult(
            text=f"[error: Calendar event '{event_key}' was not found or is not pending]",
            data={"error": "not_cancelled"},
        )
    await ctx.publish("calendar_changed", conv_id=ctx.conv_id, event_id=event_key, action="cancelled")
    return ToolResult(
        text=f"Cancelled calendar event '{event_key}'.",
        data={"event_id": event_key, "cancelled": True},
    )


async def tool_calendar_snooze(
    ctx: "Context",
    event_id: str,
    duration_seconds: int | None = None,
    until: str | None = None,
) -> ToolResult:
    """Move a calendar event forward."""
    error = _guard(ctx)
    if error is not None:
        return error
    error = _require_conv_id(ctx)
    if error is not None:
        return error

    event_key = str(event_id or "").strip()
    if not event_key:
        return ToolResult(text="[error: event_id is required]", data={"error": "missing_event_id"})

    mgr = _get_calendar_manager(ctx)
    try:
        updated = await mgr.snooze(
            ctx.conv_id,
            event_key,
            duration_seconds=duration_seconds,
            until=until,
        )
    except ValueError as exc:
        return ToolResult(text=f"[error: {exc}]", data={"error": str(exc)})
    except OSError as exc:
        log.warning("Persistence error snoozing calendar event: %s", exc)
        return ToolResult(
            text=f"[error: Failed to persist calendar event: {exc}]", data={"error": "persistence_failure"}
        )
    if updated is None:
        return ToolResult(text=f"[error: Calendar event '{event_key}' not found]", data={"error": "not_found"})
    await _publish_changed(ctx, updated, "snoozed")
    return ToolResult(
        text=f"Snoozed calendar event '{updated.event_id}' to {updated.fire_at}: {updated.title}",
        data=asdict(updated),
    )


CALENDAR_TOOLS = {
    "calendar_create": tool_calendar_create,
    "calendar_list": tool_calendar_list,
    "calendar_update": tool_calendar_update,
    "calendar_cancel": tool_calendar_cancel,
    "calendar_snooze": tool_calendar_snooze,
}

CALENDAR_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "priority": "normal",
        "writes": True,
        "summary": "Create a calendar event, optionally recurring.",
        "function": {
            "name": "calendar_create",
            "description": (
                "Create a calendar event with an absolute ISO-8601 time that includes a timezone. "
                "The event targets the current conversation by default. Set session_mode to 'dedicated' "
                "to target a separate named session that is created if absent and reused if it exists. "
                "Set recurrence to an RFC 5545 RRULE (for example FREQ=DAILY or FREQ=WEEKLY;BYDAY=MO,WE,FR) "
                "with an IANA timezone; recurrence_until optionally ends the series."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Short event title"},
                    "reason": {
                        "type": "string",
                        "description": "What the agent should do or say when the event fires",
                    },
                    "fire_at": {
                        "type": "string",
                        "description": "Absolute ISO-8601 timestamp with a timezone, e.g. 2026-11-26T08:00:00-08:00",
                    },
                    "recurrence": {
                        "type": "string",
                        "description": "Optional RFC 5545 RRULE, e.g. FREQ=DAILY or FREQ=WEEKLY;BYDAY=MO,WE,FR",
                    },
                    "timezone": {
                        "type": "string",
                        "description": "IANA timezone for a recurring event, e.g. America/Los_Angeles",
                    },
                    "recurrence_until": {
                        "type": "string",
                        "description": "Optional ISO-8601 end time with a timezone for the series",
                    },
                    "session_mode": {
                        "type": "string",
                        "enum": ["current", "dedicated"],
                        "description": "Target the current conversation or a dedicated session",
                    },
                    "session_key": {
                        "type": "string",
                        "description": "Stable lowercase slug for a dedicated session, e.g. morning-briefing",
                    },
                    "session_title": {
                        "type": "string",
                        "description": "Display title for a dedicated session",
                    },
                },
                "required": ["title", "reason", "fire_at"],
            },
        },
    },
    {
        "type": "function",
        "priority": "normal",
        "writes": False,
        "summary": "List calendar events for this conversation.",
        "function": {
            "name": "calendar_list",
            "description": "List calendar events created from the current conversation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["pending", "fired", "cancelled", "orphaned", "completed", "all"],
                        "description": "Filter by status (default: pending)",
                    },
                    "from_time": {
                        "type": "string",
                        "description": "Optional ISO-8601 lower bound with a timezone",
                    },
                    "to_time": {
                        "type": "string",
                        "description": "Optional ISO-8601 upper bound with a timezone",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "priority": "normal",
        "writes": True,
        "summary": "Update a pending calendar event.",
        "function": {
            "name": "calendar_update",
            "description": (
                "Update a pending calendar event. Use fire_at to reschedule. "
                "Set recurrence/timezone/recurrence_until to change or clear a series. "
                "Set session_mode to 'dedicated' with session_key and session_title to retarget it."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "event_id": {"type": "string", "description": "Calendar event id"},
                    "title": {"type": "string", "description": "New title"},
                    "reason": {"type": "string", "description": "New reason or prompt"},
                    "fire_at": {
                        "type": "string",
                        "description": "New absolute ISO-8601 time with a timezone",
                    },
                    "recurrence": {
                        "type": "string",
                        "description": "New RFC 5545 RRULE, or an empty string to clear the series",
                    },
                    "timezone": {
                        "type": "string",
                        "description": "New IANA timezone for the series",
                    },
                    "recurrence_until": {
                        "type": "string",
                        "description": "New ISO-8601 end time with a timezone, or an empty string to clear it",
                    },
                    "session_mode": {
                        "type": "string",
                        "enum": ["current", "dedicated"],
                        "description": "Retarget the event",
                    },
                    "session_key": {"type": "string", "description": "Dedicated session key"},
                    "session_title": {"type": "string", "description": "Dedicated session title"},
                },
                "required": ["event_id"],
            },
        },
    },
    {
        "type": "function",
        "priority": "normal",
        "writes": True,
        "summary": "Cancel a pending calendar event.",
        "function": {
            "name": "calendar_cancel",
            "description": "Cancel a pending calendar event by its event_id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "event_id": {"type": "string", "description": "Calendar event id"},
                },
                "required": ["event_id"],
            },
        },
    },
    {
        "type": "function",
        "priority": "normal",
        "writes": True,
        "summary": "Move a calendar event forward.",
        "function": {
            "name": "calendar_snooze",
            "description": (
                "Move a pending or fired calendar event forward by a positive duration, "
                "or to an absolute ISO-8601 time with a timezone."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "event_id": {"type": "string", "description": "Calendar event id"},
                    "duration_seconds": {
                        "type": "integer",
                        "description": "Seconds to move the event forward",
                    },
                    "until": {
                        "type": "string",
                        "description": "Absolute ISO-8601 time with a timezone",
                    },
                },
                "required": ["event_id"],
            },
        },
    },
]
