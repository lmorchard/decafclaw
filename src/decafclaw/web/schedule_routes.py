"""Schedules resource routes — per-resource route module.

One ``web/<resource>_routes.py`` module holds a resource's request/response
models, its handlers, and an ``APIRouter`` that ties them together.
``http_server.create_app`` aggregates the routers; it no longer defines a
resource's routes inline.

Conventions for adding or editing a resource module:

- **Models + handlers + router live here.** ``http_server`` imports the
  router and extends its route list with it. Handlers read their dependencies
  off ``request.app.state`` (populated by ``create_app``) and take the
  authenticated username via :func:`decafclaw.web.http_auth._authenticated`.
- **Shared auth lives in ``http_auth``**, not here: the decorator is applied at
  module load, so it must be importable without a cycle back to ``http_server``.
  Cross-cutting helpers that several resources share stay in ``http_server``
  and are imported at call time.
- **Route order is part of the contract.** The generated OpenAPI spec lists
  paths in registration order, and ``make check`` fails on any drift. Register
  routes so the app's final order is unchanged: this router's routes land in
  their historical positions (see ``create_app``).

The schedules resource serves the Schedules tab: list, edit, and run
cron-style schedule files (admin ``schedules/``, workspace
``workspace/schedules/``, and skill ``SCHEDULE.md`` sidecars) under
``/api/schedules``.
"""

import asyncio
import logging
import math
from datetime import datetime, timezone
from typing import Literal

from croniter import croniter
from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, field_validator
from starlette.responses import JSONResponse

from ..schedules import (
    _discover_skill_schedule_files,
    _safe_task_name,
    delete_overlay,
    discover_schedules,
    read_last_run,
    run_schedule_task,
    write_last_run,
    write_overlay,
)
from ..skills import _extract_frontmatter_text
from .http_auth import _authenticated

log = logging.getLogger(__name__)


# -- Helpers -------------------------------------------------------------------


def _has_overlay_for(config, task, skill_schedule_names: set | None = None) -> bool:
    """True iff an admin standalone file shadows a same-named skill SCHEDULE.md.

    Pass a pre-computed ``skill_schedule_names`` set (from
    ``_discover_skill_schedule_files``) to avoid repeated directory walks when
    serializing many tasks at once.
    """
    if task.source != "admin":
        return False
    if skill_schedule_names is None:
        skill_schedule_names = set(_discover_skill_schedule_files(config))
    return task.name in skill_schedule_names


def _source_path_for_display(task) -> str:
    """Return a tier-relative path to avoid leaking absolute filesystem paths."""
    if task.source == "workspace":
        return f"workspace/schedules/{task.name}.md"
    # Admin standalone files live under data/{agent_id}/schedules/
    if task.path.parent.name == "schedules":
        return f"schedules/{task.name}.md"
    # Skill SCHEDULE.md sidecars (bundled / admin / extra)
    return f"skills/{task.name}/SCHEDULE.md"


def _next_run_iso(config, task) -> str | None:
    """ISO 8601 timestamp for the next scheduled run, or None if undetermined."""
    try:
        last_run = read_last_run(config, task.name)
        if last_run:
            anchor = datetime.fromtimestamp(last_run, tz=timezone.utc)
        else:
            anchor = datetime.now(tz=timezone.utc)
        return croniter(task.schedule, anchor).get_next(datetime).isoformat()
    except Exception as exc:
        log.debug("_next_run_iso: error computing next run for %s: %s", task.name, exc)
        return None


def _last_run_iso(config, task) -> str | None:
    """ISO 8601 timestamp for the last run, or None if never run."""
    last_run = read_last_run(config, task.name)
    if not last_run:
        return None
    return datetime.fromtimestamp(last_run, tz=timezone.utc).isoformat()


def _read_frontmatter_raw(task) -> str:
    """The frontmatter block as it appears on disk.

    Deliberately not a re-serialization of the parsed task:
    ``serialize_to_markdown`` writes only recognized fields, which would
    omit exactly the unrecognized keys this view exists to surface.
    """
    try:
        return _extract_frontmatter_text(task.path.read_text())
    except OSError as exc:
        log.debug("frontmatter_raw: cannot read %s: %s", task.path, exc)
        return ""


def _schedule_to_dict(config, task, skill_schedule_names: set | None = None) -> dict:
    """Serialize a ScheduleTask to a JSON-serializable dict.

    Pass a pre-computed ``skill_schedule_names`` set to avoid O(N) directory
    walks when serializing many tasks at once (see ``schedules_list``).
    """
    return {
        "name": task.name,
        "source_tier": task.source,
        "source_path": _source_path_for_display(task),
        "has_overlay": _has_overlay_for(config, task, skill_schedule_names),
        "enabled": task.enabled,
        "schedule": task.schedule,
        "channel": task.channel,
        "model": task.model,
        "allowed_tools": list(task.allowed_tools),
        "disallowed_tools": list(task.disallowed_tools),
        "required_skills": list(task.required_skills),
        "required_servers": list(task.required_servers),
        "server_timeout": task.server_timeout,
        "shell_patterns": list(task.shell_patterns),
        "email_recipients": list(task.email_recipients),
        "pre_script": task.pre_script,
        "unknown_keys": list(task.unknown_keys),
        "frontmatter_raw": _read_frontmatter_raw(task),
        "body": task.body,
        "modified": task.path.stat().st_mtime if task.path.exists() else 0,
        "next_run_iso": _next_run_iso(config, task),
        "last_run_iso": _last_run_iso(config, task),
    }


# -- Models --------------------------------------------------------------------


class ScheduleResponse(BaseModel):
    name: str
    source_tier: Literal["admin", "workspace", "bundled", "extra"]
    source_path: str
    has_overlay: bool
    enabled: bool
    schedule: str
    channel: str
    model: str
    allowed_tools: list[str]
    disallowed_tools: list[str]
    required_skills: list[str]
    required_servers: list[str] = []
    server_timeout: float = 30.0
    shell_patterns: list[str]
    email_recipients: list[str]
    pre_script: str
    unknown_keys: list[str]
    frontmatter_raw: str
    body: str
    modified: float
    next_run_iso: str | None
    last_run_iso: str | None


class ScheduleListResponse(BaseModel):
    schedules: list[ScheduleResponse]


class ScheduleDetailResponse(BaseModel):
    schedule: ScheduleResponse
    body: str
    modified: float


# Schema-only input: the handler keeps its manual parsing, aliases, unknown-key
# rejection, and established 400 responses instead of introducing FastAPI 422s.
class ScheduleUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str | None = None
    body: str | None = None
    modified: float | None = None
    enabled: bool | None = None
    schedule: str | None = None
    channel: str | None = None
    model: str | None = None
    allowed_tools: list[str] | None = None
    disallowed_tools: list[str] | None = None
    required_skills: list[str] | None = None
    required_servers: list[str] | None = None
    server_timeout: float | None = None
    shell_patterns: list[str] | None = None
    email_recipients: list[str] | None = None
    pre_script: str | None = None

    @field_validator("server_timeout")
    @classmethod
    def validate_server_timeout(cls, v: float | None) -> float | None:
        if v is not None:
            if not math.isfinite(v) or v <= 0:
                raise ValueError("server_timeout must be a finite positive number")
        return v


class ScheduleUpdateResponse(BaseModel):
    schedule: ScheduleResponse
    modified: float


class ScheduleResetResponse(BaseModel):
    schedule: ScheduleResponse


class ScheduleRunResponse(BaseModel):
    conv_id: str
    task_name: str
    started_at: str


# -- Handlers ------------------------------------------------------------------


@_authenticated
async def schedules_list(request: Request, username: str) -> JSONResponse:
    """GET /api/schedules — list resolved schedules with metadata."""
    config = request.app.state.config
    # Pre-compute once to avoid O(N) directory walks inside _has_overlay_for.
    skill_names = set(_discover_skill_schedule_files(config))
    items = [_schedule_to_dict(config, t, skill_names) for t in discover_schedules(config)]
    return JSONResponse({"schedules": items})


@_authenticated
async def schedules_update(request: Request, username: str) -> JSONResponse:
    """PUT /api/schedules/{name} — apply patch and write/overlay."""
    config = request.app.state.config
    name = request.path_params["name"]
    try:
        body = await request.json()
    except Exception as exc:
        log.debug("schedules_update: invalid JSON body: %s", exc)
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "request body must be a JSON object"}, status_code=400)
    # wiki-editor sends {content, modified}; alias content → body
    if "content" in body and "body" not in body:
        body["body"] = body.pop("content")
    # modified is wiki-editor's conflict hint; we don't currently enforce it
    body.pop("modified", None)
    try:
        task = write_overlay(config, name, body)
    except KeyError:
        return JSONResponse({"error": "not found"}, status_code=404)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    sched = _schedule_to_dict(config, task)
    # wiki-editor reads data.modified at the top level of the PUT response
    return JSONResponse({"schedule": sched, "modified": sched["modified"]})


@_authenticated
async def schedules_get(request: Request, username: str) -> JSONResponse:
    """GET /api/schedules/{name} — single schedule fetch."""
    config = request.app.state.config
    name = request.path_params["name"]
    tasks = {t.name: t for t in discover_schedules(config)}
    task = tasks.get(name)
    if task is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    sched = _schedule_to_dict(config, task)
    # wiki-editor's conflict-banner reload reads body/modified at the top
    # level and knows nothing of the `schedule` envelope — same alias
    # schedules_update applies above for the same consumer.
    return JSONResponse(
        {
            "schedule": sched,
            "body": sched["body"],
            "modified": sched["modified"],
        }
    )


@_authenticated
async def schedules_reset(request: Request, username: str) -> JSONResponse:
    """DELETE /api/schedules/{name}/overlay — revert to skill default."""
    config = request.app.state.config
    name = request.path_params["name"]
    # Validate name safety before any filesystem access.
    if _safe_task_name(name) != name:
        return JSONResponse({"error": f"unsafe schedule name: {name!r}"}, status_code=400)
    # Guard: only delete an overlay when a skill SCHEDULE.md exists as the
    # fallback.  Without this check, deleting an admin *standalone* file would
    # destroy it and return 404 with no way to recover.
    tasks = {t.name: t for t in discover_schedules(config)}
    task = tasks.get(name)
    skill_names = set(_discover_skill_schedule_files(config))
    if task is None or not _has_overlay_for(config, task, skill_names):
        return JSONResponse({"error": "no overlay"}, status_code=404)
    try:
        new_task = delete_overlay(config, name)
    except FileNotFoundError:
        return JSONResponse({"error": "no overlay"}, status_code=404)
    except KeyError:
        return JSONResponse({"error": "not found"}, status_code=404)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse({"schedule": _schedule_to_dict(config, new_task)})


def _consume_exception(t: asyncio.Task) -> None:  # type: ignore[type-arg]
    """Done callback for fire-and-forget tasks — logs unhandled exceptions."""
    if not t.cancelled():
        exc = t.exception()
        if exc is not None:
            log.error("schedules_run: background task failed: %s", exc, exc_info=exc)


@_authenticated
async def schedules_run(request: Request, username: str) -> JSONResponse:
    """POST /api/schedules/{name}/run — fire a schedule immediately.

    Bypasses the `enabled` flag (manual click = explicit intent). Writes
    last_run before firing so the cron timer doesn't double-fire shortly after.
    Returns 202 Accepted with the generated conv_id.
    """
    config = request.app.state.config
    event_bus = request.app.state.event_bus
    manager = request.app.state.manager
    name = request.path_params["name"]

    tasks = {t.name: t for t in discover_schedules(config)}
    task = tasks.get(name)
    if task is None:
        return JSONResponse({"error": "not found"}, status_code=404)

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    conv_id = f"schedule-{task.name}-{timestamp}"
    write_last_run(config, task.name)
    t = asyncio.create_task(run_schedule_task(config, event_bus, manager, task, conv_id=conv_id))
    t.add_done_callback(_consume_exception)
    return JSONResponse(
        {
            "conv_id": conv_id,
            "task_name": task.name,
            "started_at": datetime.now(timezone.utc).isoformat(),
        },
        status_code=202,
    )


# -- Router --------------------------------------------------------------------

schedule_router = APIRouter()

schedule_router.add_api_route("/api/schedules", schedules_list, methods=["GET"], response_model=ScheduleListResponse)
schedule_router.add_api_route(
    "/api/schedules/{name}/run",
    schedules_run,
    methods=["POST"],
    response_model=ScheduleRunResponse,
    status_code=202,
    openapi_extra={
        "parameters": [
            {
                "name": "name",
                "in": "path",
                "required": True,
                "schema": {"type": "string"},
            }
        ]
    },
)
schedule_router.add_api_route(
    "/api/schedules/{name}/overlay",
    schedules_reset,
    methods=["DELETE"],
    response_model=ScheduleResetResponse,
    openapi_extra={
        "parameters": [
            {
                "name": "name",
                "in": "path",
                "required": True,
                "schema": {"type": "string"},
            }
        ]
    },
)
schedule_router.add_api_route(
    "/api/schedules/{name}",
    schedules_get,
    methods=["GET"],
    response_model=ScheduleDetailResponse,
    openapi_extra={
        "parameters": [
            {
                "name": "name",
                "in": "path",
                "required": True,
                "schema": {"type": "string"},
            }
        ]
    },
)
schedule_router.add_api_route(
    "/api/schedules/{name}",
    schedules_update,
    methods=["PUT"],
    response_model=ScheduleUpdateResponse,
    openapi_extra={
        "parameters": [
            {
                "name": "name",
                "in": "path",
                "required": True,
                "schema": {"type": "string"},
            }
        ],
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    # Flat model: identical to http_server._request_schema, which
                    # stays there for the routes that still live in http_server.
                    "schema": ScheduleUpdateRequest.model_json_schema(),
                },
            },
        },
    },
)
