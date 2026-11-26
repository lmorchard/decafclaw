"""Calendar resource routes for the Web UI (#1899).

The Calendar tab lists events across the user's conversations, creates and
edits one-shot or recurring events, snoozes the next occurrence, and cancels
a series. Mutations publish ``calendar_changed`` on the global bus so every
open tab refreshes without a page reload.
"""

from __future__ import annotations

import logging
import time
from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel
from starlette.responses import JSONResponse

from ..calendar_events import (
    CalendarEvent,
    load_all_calendar_events,
    new_event_id,
    now_iso,
    parse_fire_at,
    recurrence_summary,
    validate_session_key,
)
from ..calendar_manager import CalendarManager
from ..calendar_recurrence import validate_recurrence, validate_timezone
from ..conversation_paths import conversation_dir
from .conversations import ConversationIndex
from .http_auth import _authenticated

log = logging.getLogger(__name__)


class CalendarEventOut(BaseModel):
    event_id: str
    conv_id: str
    target_conv_id: str
    session_mode: str
    session_key: str = ""
    session_title: str = ""
    title: str
    reason: str
    fire_at: str
    fire_at_ts: float
    created_at: str
    updated_at: str
    status: str
    last_fired_at: str = ""
    snooze_until: str = ""
    recurrence: str = ""
    timezone: str = ""
    recurrence_start: str = ""
    occurrence_at: str = ""
    recurrence_until: str = ""
    origin_title: str = ""
    target_title: str = ""
    recurrence_summary: str = ""


class CalendarEventListResponse(BaseModel):
    events: list[CalendarEventOut]


class CalendarEventResponse(BaseModel):
    event: CalendarEventOut


class CalendarEventCreateRequest(BaseModel):
    conv_id: str
    title: str
    reason: str
    fire_at: str
    timezone: str = ""
    recurrence: str = ""
    recurrence_until: str = ""
    session_mode: Literal["current", "dedicated"] = "current"
    session_key: str = ""
    session_title: str = ""


class CalendarEventUpdateRequest(BaseModel):
    title: str | None = None
    reason: str | None = None
    fire_at: str | None = None
    timezone: str | None = None
    recurrence: str | None = None
    recurrence_until: str | None = None
    session_mode: Literal["current", "dedicated"] | None = None
    session_key: str | None = None
    session_title: str | None = None


class CalendarSnoozeRequest(BaseModel):
    duration_seconds: int | None = None
    until: str | None = None


def _manager(request: Request) -> CalendarManager:
    manager = getattr(request.app.state, "manager", None)
    if manager is not None and hasattr(manager, "calendar"):
        return manager.calendar
    return CalendarManager(request.app.state.config, manager=manager)


def _title(index: ConversationIndex, conv_id: str) -> str:
    entry = index.get(conv_id)
    return entry.title if entry is not None else ""


def _event_out(config, event: CalendarEvent) -> CalendarEventOut:
    index = ConversationIndex(config)
    data = CalendarEventOut(
        **{
            key: getattr(event, key)
            for key in CalendarEventOut.model_fields
            if key not in ("origin_title", "target_title", "recurrence_summary")
        }
    )
    data.origin_title = _title(index, event.conv_id)
    data.target_title = _title(index, event.target_conv_id)
    data.recurrence_summary = recurrence_summary(event)
    return data


def _events_for(config, username: str) -> list[CalendarEvent]:
    return [event for event in load_all_calendar_events(config) if event.user_id == username]


def _find(config, username: str, event_id: str) -> CalendarEvent | None:
    for event in _events_for(config, username):
        if event.event_id == event_id:
            return event
    return None


async def _publish_changed(request: Request, event: CalendarEvent, action: str) -> None:
    try:
        await request.app.state.event_bus.publish(
            {
                "type": "calendar_changed",
                "conv_id": event.conv_id,
                "event_id": event.event_id,
                "action": action,
            }
        )
    except Exception as exc:  # noqa: BLE001 - live refresh is best-effort
        log.debug("calendar_changed publish failed: %s", exc)


def _validate_recurrence_fields(rule: str, timezone_name: str, start: str, until: str) -> tuple[str, str, str]:
    rule = str(rule or "").strip()
    timezone_name = str(timezone_name or "").strip()
    until = str(until or "").strip()
    if rule:
        timezone_name = validate_timezone(timezone_name)
        validate_recurrence(rule, start, timezone_name, until)
    elif until:
        raise ValueError("recurrence_until requires recurrence")
    elif timezone_name:
        timezone_name = validate_timezone(timezone_name)
    return rule, timezone_name, until


@_authenticated
async def calendar_events_list(request: Request, username: str) -> JSONResponse:
    """GET /api/calendar/events — list the user's events."""
    config = request.app.state.config
    status = request.query_params.get("status", "pending")
    conv_id = request.query_params.get("conv_id", "")
    from_raw = request.query_params.get("from", "")
    to_raw = request.query_params.get("to", "")
    try:
        from_ts = parse_fire_at(from_raw)[1] if from_raw else time.time()
        to_ts = parse_fire_at(to_raw)[1] if to_raw else None
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    events = _events_for(config, username)
    if status and status != "all":
        events = [event for event in events if event.status == status]
    if conv_id:
        events = [event for event in events if event.conv_id == conv_id]
    events = [event for event in events if event.fire_at_ts >= from_ts]
    if to_ts is not None:
        events = [event for event in events if event.fire_at_ts <= to_ts]
    events.sort(key=lambda event: event.fire_at_ts)
    return JSONResponse({"events": [_event_out(config, event).model_dump() for event in events]})


@_authenticated
async def calendar_events_create(request: Request, username: str) -> JSONResponse:
    """POST /api/calendar/events — create a one-shot or recurring event."""
    config = request.app.state.config
    try:
        body = CalendarEventCreateRequest(**(await request.json()))
    except Exception as exc:  # noqa: BLE001 - validation and JSON share a 400
        return JSONResponse({"error": f"invalid request: {exc}"}, status_code=400)
    if not conversation_dir(config, body.conv_id).exists():
        return JSONResponse({"error": "conversation not found"}, status_code=404)
    try:
        fire_at_iso, fire_at_ts = parse_fire_at(body.fire_at)
        if fire_at_ts <= time.time():
            raise ValueError("fire_at must be in the future")
        rule, timezone_name, until = _validate_recurrence_fields(
            body.recurrence, body.timezone, fire_at_iso, body.recurrence_until
        )
        session_key = body.session_key
        if body.session_mode == "dedicated":
            session_key = validate_session_key(session_key)
            if not body.session_title.strip():
                raise ValueError("session_title is required for a dedicated session")
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    event = CalendarEvent(
        event_id=new_event_id(),
        conv_id=body.conv_id,
        target_conv_id=body.conv_id,
        session_mode=body.session_mode,
        session_key=session_key,
        session_title=body.session_title.strip() if body.session_mode == "dedicated" else "",
        title=body.title.strip(),
        reason=body.reason.strip(),
        fire_at=fire_at_iso,
        fire_at_ts=fire_at_ts,
        created_at=now_iso(),
        updated_at=now_iso(),
        user_id=username,
        recurrence=rule,
        timezone=timezone_name,
        recurrence_start=fire_at_iso if rule else "",
        occurrence_at=fire_at_iso if rule else "",
        recurrence_until=until,
    )
    if not event.title or not event.reason:
        return JSONResponse({"error": "title and reason are required"}, status_code=400)
    try:
        event = await _manager(request).add(event)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    await _publish_changed(request, event, "created")
    return JSONResponse({"event": _event_out(config, event).model_dump()}, status_code=201)


@_authenticated
async def calendar_events_update(request: Request, username: str) -> JSONResponse:
    """PUT /api/calendar/events/{event_id} — update a pending event."""
    config = request.app.state.config
    event_id = request.path_params["event_id"]
    event = _find(config, username, event_id)
    if event is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    if event.status != "pending":
        return JSONResponse({"error": f"only pending events can be updated (status: {event.status})"}, status_code=400)
    try:
        body = CalendarEventUpdateRequest(**(await request.json()))
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": f"invalid request: {exc}"}, status_code=400)

    changes: dict = {}
    for field in ("title", "reason"):
        value = getattr(body, field)
        if value is not None:
            if not str(value).strip():
                return JSONResponse({"error": f"{field} cannot be empty"}, status_code=400)
            changes[field] = str(value).strip()
    fire_iso = event.fire_at
    if body.fire_at is not None:
        try:
            fire_iso, fire_ts = parse_fire_at(body.fire_at)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        if fire_ts <= time.time():
            return JSONResponse({"error": "fire_at must be in the future"}, status_code=400)
        changes["fire_at"] = fire_iso
        changes["fire_at_ts"] = fire_ts
    if body.session_mode is not None:
        changes["session_mode"] = body.session_mode
        if body.session_mode == "current":
            changes["session_key"] = ""
            changes["session_title"] = ""
    if body.session_key is not None:
        try:
            changes["session_key"] = validate_session_key(body.session_key)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
    if body.session_title is not None:
        changes["session_title"] = body.session_title.strip()

    if body.recurrence is not None or body.timezone is not None or body.recurrence_until is not None:
        final_rule = event.recurrence if body.recurrence is None else body.recurrence
        final_tz = event.timezone if body.timezone is None else body.timezone
        final_until = event.recurrence_until if body.recurrence_until is None else body.recurrence_until
        try:
            rule, tz_name, until = _validate_recurrence_fields(final_rule, final_tz, fire_iso, final_until)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        changes.update(
            {
                "recurrence": rule,
                "timezone": tz_name,
                "recurrence_until": until,
                "recurrence_start": fire_iso if rule else "",
                "occurrence_at": fire_iso if rule else "",
            }
        )

    if not changes:
        return JSONResponse({"error": "no changes supplied"}, status_code=400)
    try:
        updated = await _manager(request).update(event.conv_id, event_id, **changes)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    if updated is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    await _publish_changed(request, updated, "updated")
    return JSONResponse({"event": _event_out(config, updated).model_dump()})


@_authenticated
async def calendar_events_cancel(request: Request, username: str) -> JSONResponse:
    """DELETE /api/calendar/events/{event_id} — cancel the event or series."""
    config = request.app.state.config
    event_id = request.path_params["event_id"]
    event = _find(config, username, event_id)
    if event is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    cancelled = await _manager(request).cancel(event.conv_id, event_id)
    if not cancelled:
        return JSONResponse({"error": "event is not pending"}, status_code=400)
    updated = _find(config, username, event_id)
    if updated is not None:
        await _publish_changed(request, updated, "cancelled")
    return JSONResponse({"event_id": event_id, "cancelled": True})


@_authenticated
async def calendar_events_snooze(request: Request, username: str) -> JSONResponse:
    """POST /api/calendar/events/{event_id}/snooze — move the next occurrence."""
    config = request.app.state.config
    event_id = request.path_params["event_id"]
    event = _find(config, username, event_id)
    if event is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    try:
        body = CalendarSnoozeRequest(**(await request.json()))
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": f"invalid request: {exc}"}, status_code=400)
    try:
        updated = await _manager(request).snooze(
            event.conv_id,
            event_id,
            duration_seconds=body.duration_seconds,
            until=body.until,
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    if updated is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    await _publish_changed(request, updated, "snoozed")
    return JSONResponse({"event": _event_out(config, updated).model_dump()})


calendar_router = APIRouter()
calendar_router.add_api_route(
    "/api/calendar/events",
    calendar_events_list,
    methods=["GET"],
    response_model=CalendarEventListResponse,
    openapi_extra={
        "parameters": [
            {"name": "from", "in": "query", "required": False, "schema": {"type": "string"}},
            {"name": "to", "in": "query", "required": False, "schema": {"type": "string"}},
            {"name": "status", "in": "query", "required": False, "schema": {"type": "string"}},
            {"name": "conv_id", "in": "query", "required": False, "schema": {"type": "string"}},
        ]
    },
)
calendar_router.add_api_route(
    "/api/calendar/events",
    calendar_events_create,
    methods=["POST"],
    response_model=CalendarEventResponse,
    status_code=201,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": CalendarEventCreateRequest.model_json_schema()}},
        }
    },
)
calendar_router.add_api_route(
    "/api/calendar/events/{event_id}",
    calendar_events_update,
    methods=["PUT"],
    response_model=CalendarEventResponse,
    openapi_extra={
        "parameters": [{"name": "event_id", "in": "path", "required": True, "schema": {"type": "string"}}],
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": CalendarEventUpdateRequest.model_json_schema()}},
        },
    },
)
calendar_router.add_api_route(
    "/api/calendar/events/{event_id}",
    calendar_events_cancel,
    methods=["DELETE"],
    response_model=dict,
    openapi_extra={"parameters": [{"name": "event_id", "in": "path", "required": True, "schema": {"type": "string"}}]},
)
calendar_router.add_api_route(
    "/api/calendar/events/{event_id}/snooze",
    calendar_events_snooze,
    methods=["POST"],
    response_model=CalendarEventResponse,
    openapi_extra={
        "parameters": [{"name": "event_id", "in": "path", "required": True, "schema": {"type": "string"}}],
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": CalendarSnoozeRequest.model_json_schema()}},
        },
    },
)
