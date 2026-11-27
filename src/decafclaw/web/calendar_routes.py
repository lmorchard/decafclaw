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
from pydantic import BaseModel, Field
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
from ..calendar_policy import split_allowed_tools, validate_tool_names
from ..calendar_recurrence import next_occurrence, validate_recurrence, validate_timezone
from ..conversation_paths import conversation_dir
from .conversations import ConversationIndex, can_read_conversation
from .http_auth import _authenticated

log = logging.getLogger(__name__)

#: Hard ceiling on occurrences returned by one expansion request. A dense
#: series over a wide range is otherwise unbounded work (#1953); the response
#: carries ``truncated`` so the client can tell it got a partial view.
MAX_OCCURRENCES = 2000

#: Longest accepted ``to - from`` span for the occurrences endpoint. A Month
#: view needs a bit over 31 days; a year is generous headroom.
MAX_OCCURRENCE_SPAN_SEC = 400 * 86400


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
    allowed_tools: list[str] = Field(default_factory=list)
    disallowed_tools: list[str] = Field(default_factory=list)
    shell_patterns: list[str] = Field(default_factory=list)
    required_skills: list[str] = Field(default_factory=list)
    policy_granted: bool = False
    model: str = ""
    origin_title: str = ""
    target_title: str = ""
    recurrence_summary: str = ""


class CalendarEventListResponse(BaseModel):
    events: list[CalendarEventOut]


class CalendarOccurrenceOut(BaseModel):
    event_id: str
    occurrence_at: str
    occurrence_ts: float
    title: str
    target_conv_id: str
    target_title: str = ""
    status: str
    recurrence: bool = False


class CalendarOccurrenceListResponse(BaseModel):
    occurrences: list[CalendarOccurrenceOut]
    truncated: bool = False


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
    # Tool policy (#1971). This route is the human-controlled surface, so it is
    # the one path allowed to grant preapproval.
    allowed_tools: list[str] = Field(default_factory=list)
    disallowed_tools: list[str] = Field(default_factory=list)
    shell_patterns: list[str] = Field(default_factory=list)
    required_skills: list[str] = Field(default_factory=list)
    # Model pin (#1972).
    model: str = ""


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
    # Tool policy (#1971) / model pin (#1972). None leaves the stored value
    # alone; an empty list explicitly clears it.
    allowed_tools: list[str] | None = None
    disallowed_tools: list[str] | None = None
    shell_patterns: list[str] | None = None
    required_skills: list[str] | None = None
    model: str | None = None


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


def _event_out(config, index: ConversationIndex, event: CalendarEvent) -> CalendarEventOut:
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


def _events_for(config, index: ConversationIndex, username: str) -> list[CalendarEvent]:
    return [event for event in load_all_calendar_events(config) if event.user_id == username]


def _find(config, index: ConversationIndex, username: str, event_id: str) -> CalendarEvent | None:
    for event in _events_for(config, index, username):
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
    index = ConversationIndex(config)
    status = request.query_params.get("status", "pending")
    conv_id = request.query_params.get("conv_id", "")
    from_raw = request.query_params.get("from", "")
    to_raw = request.query_params.get("to", "")
    try:
        from_ts = parse_fire_at(from_raw)[1] if from_raw else time.time()
        to_ts = parse_fire_at(to_raw)[1] if to_raw else None
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    events = _events_for(config, index, username)
    if status and status != "all":
        events = [event for event in events if event.status == status]
    if conv_id:
        events = [event for event in events if event.conv_id == conv_id]
    # An explicit from bound means "give me this window", including past
    # events: the editor needs a fired event to snooze or cancel it (#1952).
    # The default (no from) stays "upcoming only".
    lower_ts = from_ts if from_raw else time.time()
    events = [event for event in events if event.fire_at_ts >= lower_ts]
    if to_ts is not None:
        events = [event for event in events if event.fire_at_ts <= to_ts]
    events.sort(key=lambda event: event.fire_at_ts)
    return JSONResponse({"events": [_event_out(config, index, event).model_dump() for event in events]})


@_authenticated
async def calendar_occurrences_list(request: Request, username: str) -> JSONResponse:
    """GET /api/calendar/occurrences — expand events in a range."""
    config = request.app.state.config
    from_raw = request.query_params.get("from", "")
    to_raw = request.query_params.get("to", "")
    conv_id = request.query_params.get("conv_id", "")
    include_fired = request.query_params.get("include_fired", "false").lower() == "true"
    try:
        from_ts = parse_fire_at(from_raw)[1] if from_raw else time.time()
        to_ts = parse_fire_at(to_raw)[1] if to_raw else from_ts + 86400
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    if to_ts < from_ts:
        return JSONResponse({"error": "to must be after from"}, status_code=400)
    if to_ts - from_ts > MAX_OCCURRENCE_SPAN_SEC:
        return JSONResponse({"error": "range too large; narrow the from/to span"}, status_code=400)

    occurrences: list[dict] = []
    index = ConversationIndex(config)
    truncated = False
    for event in _events_for(config, index, username):
        if conv_id and event.conv_id != conv_id:
            continue
        if event.status == "cancelled":
            continue
        if not include_fired and event.status in ("fired", "completed"):
            continue
        target_title = _title(index, event.target_conv_id)
        if event.recurrence:
            cursor = from_ts - 1
            while True:
                if len(occurrences) >= MAX_OCCURRENCES:
                    truncated = True
                    break
                following = next_occurrence(event, cursor)
                if following is None:
                    break
                iso, ts = following
                if ts > to_ts:
                    break
                if ts >= from_ts:
                    occurrences.append(
                        {
                            "event_id": event.event_id,
                            "occurrence_at": iso,
                            "occurrence_ts": ts,
                            "title": event.title,
                            "target_conv_id": event.target_conv_id,
                            "target_title": target_title,
                            "status": event.status,
                            "recurrence": True,
                        }
                    )
                cursor = ts
        elif from_ts <= event.fire_at_ts <= to_ts:
            occurrences.append(
                {
                    "event_id": event.event_id,
                    "occurrence_at": event.fire_at,
                    "occurrence_ts": event.fire_at_ts,
                    "title": event.title,
                    "target_conv_id": event.target_conv_id,
                    "target_title": target_title,
                    "status": event.status,
                    "recurrence": False,
                }
            )
        if truncated:
            break
    occurrences.sort(key=lambda item: item["occurrence_ts"])
    return JSONResponse({"occurrences": occurrences, "truncated": truncated})


@_authenticated
async def calendar_events_create(request: Request, username: str) -> JSONResponse:
    """POST /api/calendar/events — create a one-shot or recurring event."""
    config = request.app.state.config
    try:
        body = CalendarEventCreateRequest(**(await request.json()))
    except Exception as exc:  # noqa: BLE001 - validation and JSON share a 400
        return JSONResponse({"error": f"invalid request: {exc}"}, status_code=400)
    index = ConversationIndex(config)
    # The caller supplies conv_id, and the event's fires land in it with the
    # caller's user_id. Without an ownership check, any authenticated user
    # could aim an attacker-authored reminder at someone else's conversation
    # (#1949). can_read_conversation is the shared rule used elsewhere; it
    # deliberately allows system conversations (schedule, heartbeat, children)
    # while rejecting another user's top-level web conversation.
    if not conversation_dir(config, body.conv_id).exists():
        return JSONResponse({"error": "conversation not found"}, status_code=404)
    if not can_read_conversation(config, index, body.conv_id, username):
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
        allowed, inline_patterns = split_allowed_tools(body.allowed_tools)
        disallowed = validate_tool_names(body.disallowed_tools, field_name="disallowed_tools")
        skills = validate_tool_names(body.required_skills, field_name="required_skills")
        shell_patterns = [
            str(p or "").strip() for p in list(body.shell_patterns) + inline_patterns if str(p or "").strip()
        ]
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
        allowed_tools=allowed,
        disallowed_tools=disallowed,
        shell_patterns=shell_patterns,
        required_skills=skills,
        # This route requires an authenticated session, so it is the human
        # control surface schedules get from their `source` tier (#1971).
        policy_granted=bool(allowed or shell_patterns or disallowed or skills),
        model=body.model.strip(),
    )
    if not event.title or not event.reason:
        return JSONResponse({"error": "title and reason are required"}, status_code=400)
    try:
        event = await _manager(request).add(event)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    await _publish_changed(request, event, "created")
    return JSONResponse({"event": _event_out(config, index, event).model_dump()}, status_code=201)


@_authenticated
async def calendar_events_update(request: Request, username: str) -> JSONResponse:
    """PUT /api/calendar/events/{event_id} — update a pending event."""
    config = request.app.state.config
    index = ConversationIndex(config)
    event_id = request.path_params["event_id"]
    event = _find(config, index, username, event_id)
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
        # Moving a series moves its anchor, matching the agent tool (#1951).
        if event.recurrence:
            changes["recurrence_start"] = fire_iso
            changes["occurrence_at"] = fire_iso
    if body.session_mode is not None:
        changes["session_mode"] = body.session_mode
        if body.session_mode == "current":
            changes["session_key"] = ""
            changes["session_title"] = ""
    # The editor always posts the whole form, so a current-mode event arrives
    # with session_key="" / session_title="". Those are legal for current mode
    # and must not be slug-validated (#1952). Only validate when the final mode
    # is dedicated, mirroring tool_calendar_update.
    final_mode = changes.get("session_mode", event.session_mode)
    if body.session_key is not None and final_mode != "current":
        try:
            changes["session_key"] = validate_session_key(body.session_key)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
    # Tool policy (#1971). This route is the human-controlled surface, so a
    # policy update here may grant preapproval.
    try:
        if body.allowed_tools is not None:
            names, inline_patterns = split_allowed_tools(body.allowed_tools)
            changes["allowed_tools"] = names
            if inline_patterns:
                changes["shell_patterns"] = sorted(set(inline_patterns) | set(body.shell_patterns or ()))
        if body.shell_patterns is not None:
            changes["shell_patterns"] = [str(p or "").strip() for p in body.shell_patterns if str(p or "").strip()]
        if body.disallowed_tools is not None:
            changes["disallowed_tools"] = validate_tool_names(body.disallowed_tools, field_name="disallowed_tools")
        if body.required_skills is not None:
            changes["required_skills"] = validate_tool_names(body.required_skills, field_name="required_skills")
        if body.model is not None:
            changes["model"] = body.model.strip()
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    if any(key in changes for key in ("allowed_tools", "shell_patterns", "disallowed_tools", "required_skills")):
        changes["policy_granted"] = True
    if body.session_title is not None and final_mode != "current":
        changes["session_title"] = body.session_title.strip()
    elif body.session_title is not None:
        changes["session_title"] = ""

    if final_mode == "dedicated":
        final_key = changes.get("session_key", event.session_key)
        final_title = changes.get("session_title", event.session_title)
        if not final_key:
            return JSONResponse({"error": "session_key is required for a dedicated session"}, status_code=400)
        if not final_title:
            return JSONResponse({"error": "session_title is required for a dedicated session"}, status_code=400)

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
    return JSONResponse({"event": _event_out(config, index, updated).model_dump()})


@_authenticated
async def calendar_events_cancel(request: Request, username: str) -> JSONResponse:
    """DELETE /api/calendar/events/{event_id} — cancel the event or series."""
    config = request.app.state.config
    index = ConversationIndex(config)
    event_id = request.path_params["event_id"]
    event = _find(config, index, username, event_id)
    if event is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    cancelled = await _manager(request).cancel(event.conv_id, event_id)
    if not cancelled:
        return JSONResponse({"error": "event is not pending"}, status_code=400)
    updated = _find(config, index, username, event_id)
    if updated is not None:
        await _publish_changed(request, updated, "cancelled")
    return JSONResponse({"event_id": event_id, "cancelled": True})


@_authenticated
async def calendar_events_snooze(request: Request, username: str) -> JSONResponse:
    """POST /api/calendar/events/{event_id}/snooze — move the next occurrence."""
    config = request.app.state.config
    index = ConversationIndex(config)
    event_id = request.path_params["event_id"]
    event = _find(config, index, username, event_id)
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
    return JSONResponse({"event": _event_out(config, index, updated).model_dump()})


@_authenticated
async def calendar_events_run(request: Request, username: str) -> JSONResponse:
    """POST /api/calendar/events/{event_id}/run — rehearse a pending event now.

    Fires the wake turn without advancing the schedule, so pressing this on a
    weekly event does not skip that week (`CalendarManager.fire_now`).

    **Web route only — deliberately no agent tool.** Creation already reserves
    the preapproval grant to this authenticated surface (#1971): the agent tool
    sets ``policy_granted=False``. Exposing a run trigger as a tool would hand
    that grant back on demand — the agent could start an unattended turn under
    the human's preapprovals whenever it liked, which is the boundary #731
    exists to hold. Test cover in `test_calendar_routes.py` asserts no
    `calendar_run` tool is registered.
    """
    config = request.app.state.config
    index = ConversationIndex(config)
    event_id = request.path_params["event_id"]
    event = _find(config, index, username, event_id)
    if event is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    if event.status != "pending":
        return JSONResponse(
            {"error": f"cannot run a {event.status} event"},
            status_code=409,
        )
    updated = await _manager(request).fire_now(event.conv_id, event_id)
    if updated is None:
        # Only reachable when the wake could not be enqueued (no manager, no
        # resolvable target, or rate-limited despite the bypass).
        return JSONResponse({"error": "the event could not be started"}, status_code=503)
    await _publish_changed(request, updated, "ran")
    return JSONResponse({"event": _event_out(config, index, updated).model_dump()})


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
    "/api/calendar/occurrences",
    calendar_occurrences_list,
    methods=["GET"],
    response_model=CalendarOccurrenceListResponse,
    openapi_extra={
        "parameters": [
            {"name": "from", "in": "query", "required": False, "schema": {"type": "string"}},
            {"name": "to", "in": "query", "required": False, "schema": {"type": "string"}},
            {"name": "conv_id", "in": "query", "required": False, "schema": {"type": "string"}},
            {"name": "include_fired", "in": "query", "required": False, "schema": {"type": "boolean"}},
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
calendar_router.add_api_route(
    "/api/calendar/events/{event_id}/run",
    calendar_events_run,
    methods=["POST"],
    response_model=CalendarEventResponse,
    openapi_extra={"parameters": [{"name": "event_id", "in": "path", "required": True, "schema": {"type": "string"}}]},
)
