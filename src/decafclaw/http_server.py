"""HTTP server — FastAPI ASGI app for interactive callbacks and future web UI."""

import asyncio
import functools
import html
import json
import logging
import math
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, NotRequired, TypedDict, cast

import yaml
from croniter import croniter
from fastapi import FastAPI, Request
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, JsonValue, field_validator
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, Response
from starlette.routing import BaseRoute, Mount, WebSocketRoute
from starlette.staticfiles import StaticFiles

from .frontmatter import (
    join_frontmatter,
    merge_frontmatter,
    parse_frontmatter_block,
    split_frontmatter,
    to_json_safe,
)
from .mattermost_ui import get_token_registry
from .model_catalog import (
    set_aux_approval_model,
    set_default_model,
    set_reflection_model,
    set_verifier_model,
)
from .schedules import (
    _discover_skill_schedule_files,
    _safe_task_name,
    delete_overlay,
    discover_schedules,
    read_last_run,
    run_schedule_task,
    write_last_run,
    write_overlay,
)
from .skills import _extract_frontmatter_text
from .skills.vault._events import (
    KIND_CREATE,
    KIND_DELETE,
    KIND_RENAME,
    KIND_UPDATE,
    publish_vault_changed,
)
from .tags import collect_all_tags
from .web.bookmarks import KINDS_BY_TARGET, BookmarkStore, validate_bookmark_path
from .web.http_auth import _authenticated, _get_username_or_401
from .web.workspace_paths import (
    PathContainmentError,
    resolve_contained,
)
from .web.workspace_routes import workspace_router

log = logging.getLogger(__name__)

_SAFE_CONV_ID_RE = re.compile(r"^[a-zA-Z0-9._-]+$")


def _is_safe_conv_id(conv_id: str) -> bool:
    return bool(conv_id and _SAFE_CONV_ID_RE.match(conv_id))


_CONFIG_FILES = [
    {"name": "SOUL.md", "path": "SOUL.md", "description": "Core identity prompt", "scope": "admin"},
    {"name": "AGENT.md", "path": "AGENT.md", "description": "Behavioral instructions", "scope": "admin"},
    {"name": "USER.md", "path": "USER.md", "description": "User-specific context", "scope": "admin"},
    {"name": "HEARTBEAT.md", "path": "HEARTBEAT.md", "description": "Heartbeat check sections", "scope": "admin"},
    {"name": "COMPACTION.md", "path": "COMPACTION.md", "description": "Compaction prompt override", "scope": "admin"},
    {
        "name": "SHELL_APPROVAL.md",
        "path": "SHELL_APPROVAL.md",
        "description": "Shell auto-approval judge prompt override",
        "scope": "admin",
    },
    {
        "name": "activity_domains.jsonl",
        "path": "activity_domains.jsonl",
        "description": "Shell activity domain rules (JSONL)",
        "scope": "admin",
    },
]


def _validate_folder_param(folder_param: str) -> str | None:
    """Validate a folder query parameter. Returns error message or None.

    Empty input is OK (means "root"). Reject leading slashes and any
    segment that's empty or ``..``.
    """
    if not folder_param:
        return None
    if folder_param.startswith("/"):
        return "invalid folder path"
    segments = folder_param.split("/")
    if any(not seg or seg == ".." for seg in segments):
        return "invalid folder path"
    return None


class AttachmentResponse(BaseModel):
    filename: str
    path: str
    mime_type: str


class ConfigFileEntry(BaseModel):
    name: str
    path: str
    description: str
    scope: Literal["admin", "workspace"]
    modified: float | None
    exists: bool


class ConfigFileResponse(BaseModel):
    content: str
    modified: float | None
    name: str
    default: bool


class ConfigSaveRequest(BaseModel):
    content: str
    modified: float | None = None


class ConfigWriteResponse(BaseModel):
    ok: Literal[True]
    modified: float


class VaultFolderEntry(BaseModel):
    name: str
    path: str


class VaultPageListEntry(BaseModel):
    title: str
    path: str
    folder: str
    modified: float
    summary: str


class VaultListingResponse(BaseModel):
    folder: str
    folders: list[VaultFolderEntry]
    pages: list[VaultPageListEntry]


class VaultRecentResponse(BaseModel):
    pages: list[VaultPageListEntry]


class VaultTagEntry(BaseModel):
    tag: str
    count: int
    pages: list[str]


class VaultTagsResponse(BaseModel):
    tags: list[VaultTagEntry]


class BookmarkEntry(BaseModel):
    path: str
    label: str
    kind: Literal["file", "dir", "page", "folder"]


class BookmarksResponse(BaseModel):
    files: list[BookmarkEntry]
    vault: list[BookmarkEntry]


class HostMemoryGauge(BaseModel):
    """Memory and swap in bytes.

    ``available_bytes`` is ``MemAvailable`` — reclaimable cache included —
    rather than ``MemFree``, which reads alarmingly low on a healthy box.
    """

    total_bytes: int
    available_bytes: int
    used_bytes: int
    used_percent: float
    swap_total_bytes: int
    swap_used_bytes: int
    swap_used_percent: float


class HostLoadGauge(BaseModel):
    """Load averages plus the runnable/total task counts from /proc/loadavg."""

    one: float
    five: float
    fifteen: float
    running: int | None = None
    total: int | None = None


class HostDiskGauge(BaseModel):
    """One filesystem's capacity, using df's used/(used+available) ratio."""

    path: str
    total_bytes: int
    used_bytes: int
    available_bytes: int
    used_percent: float


class HostMetricsResponse(BaseModel):
    """Capacity and load for the VM DecafClaw runs on.

    Every field is optional: each source degrades independently, so an
    unreadable /proc file nulls its own gauge instead of failing the response.
    """

    timestamp: float
    cpu_count: int | None = None
    # None until two /proc/stat samples exist — a since-boot average is not a
    # gauge, so there is no honest value on a cold first read.
    cpu_percent: float | None = None
    memory: HostMemoryGauge | None = None
    load: HostLoadGauge | None = None
    disk_root: HostDiskGauge | None = None
    disk_tmpfs: HostDiskGauge | None = None
    process_count: int | None = None
    uptime_seconds: float | None = None


class BookmarkAddRequest(BaseModel):
    target: Literal["files", "vault"]
    path: str
    label: str | None = None
    kind: Literal["file", "dir", "page", "folder"]


class VaultCreateRequest(BaseModel):
    name: str
    content: str | None = None


class VaultCreateResponse(BaseModel):
    ok: Literal[True]
    page: str
    modified: float


class VaultFolderCreateRequest(BaseModel):
    folder: str


class VaultFolderCreateResponse(BaseModel):
    ok: Literal[True]
    folder: str


class VaultWriteRequest(BaseModel):
    content: str | None = None
    body: str | None = None
    modified: float | None = None
    frontmatter: dict[str, JsonValue] | None = None
    frontmatter_raw: str | None = None
    rename_to: str | None = None


class VaultWriteResponse(BaseModel):
    ok: Literal[True]
    modified: float
    frontmatter: dict[str, JsonValue] | None = None
    frontmatter_raw: str | None = None
    frontmatter_error: str | None = None
    title: str | None = None
    path: str | None = None
    folder: str | None = None


class VaultDeleteResponse(BaseModel):
    ok: Literal[True]


def _request_schema(model: type[BaseModel]) -> dict:
    """Build schema-only request metadata with refs into OpenAPI components."""
    schema = model.model_json_schema(ref_template="#/components/schemas/{model}")
    schema.pop("$defs", None)
    return schema


class VaultPageResponse(BaseModel):
    title: str
    path: str
    body: str
    modified: float
    frontmatter: dict[str, JsonValue]
    frontmatter_raw: str
    frontmatter_error: str | None = None


class VaultCompletion(BaseModel):
    type: Literal["vault"]
    id: str
    label: str
    description: str


class McpCompletion(BaseModel):
    type: Literal["mcp"]
    id: str
    label: str
    description: str


class FileCompletion(BaseModel):
    type: Literal["file"]
    id: str
    label: str
    description: str


class AutocompleteResponse(BaseModel):
    results: list[VaultCompletion | McpCompletion | FileCompletion]


def _vault_root(config) -> Path:
    return config.vault_root


def _resolve_vault_page(config, page_name: str):
    """Resolve a vault page name to a file path."""
    from .skills.vault.tools import resolve_page

    return resolve_page(config, page_name)


def validate_vault_page_name(name: str) -> str | None:
    """Validate a vault page or folder name against path traversal.

    Returns ``None`` when the name is safe, otherwise a single canonical
    error message. Rejects absolute paths and any path component that is
    exactly ``..`` (a real parent-directory hop). Names that merely *contain*
    the substring ``..`` — such as ``foo..bar`` or ``...`` — are allowed,
    since they resolve to an ordinary file name rather than a traversal.
    """
    if not isinstance(name, str) or not name:
        return "invalid vault page name"
    if name.startswith("/"):
        return "invalid vault page name"
    if any(part == ".." for part in name.split("/")):
        return "invalid vault page name"
    return None


def _vault_source_type(config, filepath: Path) -> str:
    """Determine source type for a vault file."""
    from .skills.vault.tools import _source_type_for_path

    return _source_type_for_path(config, filepath)


def _dump_frontmatter(metadata: dict) -> str | None:
    """Serialize a metadata dict to a raw frontmatter block, or None if empty.

    Returns the block body only — no delimiters, no trailing newline — for
    `join_frontmatter`.
    """
    if not metadata:
        return None
    return yaml.dump(
        metadata,
        default_flow_style=False,
        allow_unicode=True,
    ).rstrip("\n")


def _resolve_frontmatter(
    existing_raw: str | None,
    existing_meta: dict,
    fm_error: str | None,
    fm_raw: str | None,
    fm_patch: dict | None,
) -> tuple[str | None, dict | None]:
    """Resolve new raw frontmatter block from `frontmatter_raw` or `frontmatter` patch.

    - `frontmatter_raw` is stored verbatim (stripping outer newlines) to preserve
      user comments, custom formatting, and key order. It is checked to ensure
      it does not contain a column-0 `---` line (which would break vault file
      parser framing) and that it parses as a valid YAML mapping.
    - `frontmatter` patch merges updates into existing metadata and dumps back to YAML.
    """
    new_raw = existing_raw
    if fm_raw is not None:
        stripped = fm_raw.strip()
        if not stripped:
            new_raw = None
        else:
            # Reject column-0 '---' lines because they conflict with the file-level
            # vault frontmatter delimiter block.
            if re.search(r"(?m)^---$", fm_raw):
                return None, {"error": "frontmatter_raw must not contain a '---' line", "status_code": 400}
            meta_parsed, fm_validation_error = parse_frontmatter_block(stripped)
            if fm_validation_error is not None:
                return None, {"error": fm_validation_error, "status_code": 400}
            if not isinstance(meta_parsed, dict):
                return None, {"error": "frontmatter must be a YAML mapping", "status_code": 400}
            new_raw = fm_raw.strip("\n")
    elif fm_patch is not None:
        if fm_error is not None:
            return None, {"error": f"existing frontmatter is malformed: {fm_error}", "status_code": 400}
        merged = merge_frontmatter(existing_meta, fm_patch, overwrite=True)
        for key, value in fm_patch.items():
            if value is None:
                merged.pop(key, None)
        new_raw = _dump_frontmatter(merged)
    return new_raw, None


def _page_summary(path: Path) -> str:
    """Read a page's frontmatter `summary`, or "" if absent or unreadable.

    Fail-open: a malformed or unreadable page must not break a listing.
    UnicodeDecodeError is caught alongside OSError — it is a ValueError, not
    an OSError, so a page with invalid UTF-8 would otherwise 500 the whole
    listing.
    """
    try:
        raw_block, _ = split_frontmatter(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        log.debug("Could not read %s for summary: %s", path, exc)
        return ""
    metadata, _ = parse_frontmatter_block(raw_block)
    summary = metadata.get("summary")
    return str(summary) if summary else ""


# -- Health + confirmation callback handlers ---------------------------------


async def health(request: Request) -> JSONResponse:
    """Liveness probe — returns the static health snapshot."""
    from .tools.health import get_health_data

    return JSONResponse(get_health_data(request.app.state.config))


async def metrics_endpoint(request: Request) -> Response:
    """Prometheus text format metrics endpoint."""
    from .metrics import format_prometheus_metrics

    content = format_prometheus_metrics()
    return Response(content, media_type="text/plain")


@_authenticated
async def host_metrics(request: Request, username: str) -> JSONResponse:
    """Live capacity and load for the host VM (VM 302).

    Both processes and disk contents are visible here, so this is
    authenticated like the rest of ``/api`` — ``/health`` and ``/metrics`` are
    deliberately probe-only and carry no host inventory.
    """
    from .host_metrics import get_sampler

    try:
        snapshot = get_sampler().snapshot()
    except Exception:  # fail-open: the strip renders dashes, not an error page
        log.exception("host_metrics: snapshot failed")
        return JSONResponse({"error": "host metrics unavailable"}, status_code=503)
    return JSONResponse(snapshot)


async def _read_json_object(request: Request) -> dict:
    """Read a JSON object body, tolerating empty and non-object payloads.

    Mattermost sends a rich action payload; ntfy ``action=http`` callbacks
    typically send an empty POST and carry everything in the
    authenticated query token. A malformed body must fall through to the
    token data, not raise a 500.
    """
    try:
        body = await request.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


async def _read_json_body(request: Request) -> dict | None:
    """Read and parse a JSON object body, returning ``None`` when it is not one.

    ``request.json()`` raises an unhandled ``JSONDecodeError`` (a 500) on a
    malformed body. Endpoints that must answer a clean 400 for bad input call
    this helper and check the result: ``None`` means the body was malformed JSON
    or not a JSON object, so they should return ``400 invalid JSON body``.

    Distinct from :func:`_read_json_object`, which returns ``{}`` for a missing
    or malformed body because the confirm callbacks treat an empty POST as valid.
    """
    try:
        body = await request.json()
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


# Tolerance for mtime conflict detection, in seconds. An unchanged file stats
# to the same mtime on every read; drift beyond this means the file changed
# since the client's snapshot. 1ms absorbs float and filesystem-precision noise.
MTIME_CONFLICT_TOLERANCE = 1e-3


def check_mtime_conflict(existing_mtime: float, client_mtime: float) -> JSONResponse | None:
    """Return a 409 conflict response if the file changed since the client's snapshot.

    Shared by workspace, vault, and config writes: one tolerance
    (:data:`MTIME_CONFLICT_TOLERANCE`) and one payload key (``modified``,
    matching every read and write-success response in this API). Returns
    ``None`` when the mtimes agree within tolerance.
    """
    if abs(existing_mtime - client_mtime) > MTIME_CONFLICT_TOLERANCE:
        return JSONResponse(
            {"error": "conflict", "modified": existing_mtime},
            status_code=409,
        )
    return None


async def handle_confirm(request: Request) -> Response:
    """Handle interactive button/link callbacks for tool confirmation."""
    config = request.app.state.config
    event_bus = request.app.state.event_bus
    manager = request.app.state.manager

    # Verify token (single-use, per-confirmation)
    token = request.query_params.get("token", "")
    token_data = get_token_registry().consume(token)

    # Also check static secret as fallback (defense in depth)
    secret = request.query_params.get("secret", "")
    has_valid_secret = config.http.secret and secret == config.http.secret

    if not token_data and not has_valid_secret:
        log.warning("Confirm callback rejected: invalid token and no valid secret")
        if request.method == "GET":
            return HTMLResponse(
                "<!DOCTYPE html><html><head><meta name='viewport' content='width=device-width, initial-scale=1'>"
                "<title>Token Invalid</title>"
                "<style>body { font-family: system-ui, -apple-system, sans-serif; padding: 2rem; max-width: 500px; margin: 0 auto; text-align: center; color: #24292f; background: #f6f8fa; }"
                ".card { background: #fff; border: 1px solid #d0d7de; border-radius: 8px; padding: 2rem; box-shadow: 0 1px 3px rgba(0,0,0,0.05); }</style></head>"
                "<body><div class='card'><h2>⚠️ Token Expired or Invalid</h2><p>This confirmation link has already been used or has expired.</p></div></body></html>",
                status_code=403,
            )
        return JSONResponse({"error": "unauthorized"}, status_code=403)

    body = await _read_json_object(request)
    context = body.get("context", {})
    if not isinstance(context, dict):
        context = {}

    # Use token data if available, fall back to POST body context
    if token_data:
        action = token_data.get("action", "") or context.get("action", "")
        context_id = token_data["context_id"]
        tool_name = token_data["tool"]
        original_message = token_data["original_message"]
        tool_call_id = token_data.get("tool_call_id", "")
    else:
        action = context.get("action", "")
        context_id = context.get("context_id", "")
        tool_name = context.get("tool", "")
        original_message = context.get("original_message", "")
        tool_call_id = context.get("tool_call_id", "")

    # Extract manager-routing fields from token data
    conv_id = (token_data or {}).get("conv_id", "") or context.get("conv_id", "")
    confirmation_id = (token_data or {}).get("confirmation_id", "") or context.get("confirmation_id", "")

    log.info(f"Confirm callback: action={action} tool={tool_name} context={context_id[:8]}")

    # Map action to event fields
    approved = action in ("approve", "always", "add_pattern")
    always = action == "always"
    add_pattern = action == "add_pattern"

    # Route through manager if available, fall back to event bus
    if manager and conv_id and confirmation_id:
        await manager.respond_to_confirmation(
            conv_id,
            confirmation_id,
            approved=approved,
            always=always,
            add_pattern=add_pattern,
        )
    else:
        await event_bus.publish(
            {
                "type": "tool_confirm_response",
                "context_id": context_id,
                "tool": tool_name,
                "approved": approved,
                **({"tool_call_id": tool_call_id} if tool_call_id else {}),
                **({"always": True} if always else {}),
                **({"add_pattern": True} if add_pattern else {}),
            }
        )

    # Determine result label
    labels = {
        "approve": "\u2705 Approved",
        "always": "\u2705 Always approved",
        "add_pattern": "\U0001f4d3 Approved + pattern added",
        "deny": "\U0001f44e Denied",
    }
    label = labels.get(action, f"\u2753 Unknown action: {action}")

    if request.method == "GET":
        escaped_msg = html.escape(original_message).replace("\n", "<br>")
        html_content = (
            "<!DOCTYPE html><html><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width, initial-scale=1'>"
            f"<title>{label}</title>"
            "<style>"
            "body { font-family: system-ui, -apple-system, sans-serif; padding: 2rem; max-width: 600px; margin: 0 auto; color: #24292f; background: #f6f8fa; }"
            ".card { background: #ffffff; border: 1px solid #d0d7de; border-radius: 8px; padding: 1.5rem; box-shadow: 0 1px 3px rgba(0,0,0,0.05); }"
            "h2 { margin-top: 0; }"
            "pre { background: #f6f8fa; padding: 1rem; border-radius: 6px; overflow-x: auto; white-space: pre-wrap; font-size: 0.875rem; }"
            "</style></head><body>"
            f"<div class='card'><h2>{label}</h2><p>Decision has been recorded.</p>"
            f"{f'<pre>{escaped_msg}</pre>' if escaped_msg else ''}"
            "</div></body></html>"
        )
        return HTMLResponse(html_content)

    # Return update response — removes buttons, shows result
    return JSONResponse(
        {
            "update": {
                "message": f"{original_message}\n\n**Result:** {label}",
                "props": {"attachments": []},
            }
        }
    )


async def handle_cancel(request: Request) -> Response:
    """Handle interactive button/link callback for stop/cancel."""
    event_bus = request.app.state.event_bus
    manager = request.app.state.manager

    token = request.query_params.get("token", "")
    token_data = get_token_registry().consume(token)

    if not token_data:
        log.warning("Cancel callback rejected: invalid or expired token")
        if request.method == "GET":
            return HTMLResponse(
                "<!DOCTYPE html><html><head><meta name='viewport' content='width=device-width, initial-scale=1'>"
                "<title>Token Invalid</title>"
                "<style>body { font-family: system-ui, -apple-system, sans-serif; padding: 2rem; max-width: 500px; margin: 0 auto; text-align: center; color: #24292f; background: #f6f8fa; }"
                ".card { background: #fff; border: 1px solid #d0d7de; border-radius: 8px; padding: 2rem; box-shadow: 0 1px 3px rgba(0,0,0,0.05); }</style></head>"
                "<body><div class='card'><h2>⚠️ Token Expired or Invalid</h2><p>This cancellation link has already been used or has expired.</p></div></body></html>",
                status_code=403,
            )
        return JSONResponse({"error": "unauthorized"}, status_code=403)

    conv_id = token_data.get("conv_id", "") or token_data.get("context_id", "")
    log.info(f"Cancel button pressed for conversation {conv_id[:8]}")

    if manager and conv_id:
        await manager.cancel_turn(conv_id)
    else:
        await event_bus.publish(
            {
                "type": "cancel_turn",
                "conv_id": conv_id,
            }
        )

    if request.method == "GET":
        return HTMLResponse(
            "<!DOCTYPE html><html><head><meta name='viewport' content='width=device-width, initial-scale=1'>"
            "<title>Stopped</title>"
            "<style>body { font-family: system-ui, -apple-system, sans-serif; padding: 2rem; max-width: 500px; margin: 0 auto; text-align: center; color: #24292f; background: #f6f8fa; }"
            ".card { background: #fff; border: 1px solid #d0d7de; border-radius: 8px; padding: 2rem; box-shadow: 0 1px 3px rgba(0,0,0,0.05); }</style></head>"
            "<body><div class='card'><h2>⏹️ Stopped</h2><p>The turn was cancelled.</p></div></body></html>"
        )

    return JSONResponse(
        {
            "update": {
                "message": "\u23f9\ufe0f Stopped",
                "props": {"attachments": []},
            }
        }
    )


# -- Auth routes -------------------------------------------------------------


class LoginRequest(BaseModel):
    token: str


class LoginResponse(BaseModel):
    username: str


class LogoutResponse(BaseModel):
    ok: bool


async def auth_login(request: Request) -> JSONResponse:
    """Validate a one-time login token, then set the session cookie."""
    from .web.auth import validate_token

    config = request.app.state.config
    body = await request.json()
    token = body.get("token", "")
    username = validate_token(config, token)
    if not username:
        return JSONResponse({"error": "invalid token"}, status_code=401)
    response = JSONResponse({"username": username})
    response.set_cookie(
        "decafclaw_session",
        token,
        httponly=True,
        samesite="lax",
        max_age=30 * 24 * 3600,
    )
    return response


async def auth_logout(request: Request) -> JSONResponse:
    """Clear the session cookie."""
    response = JSONResponse({"ok": True})
    response.delete_cookie("decafclaw_session")
    return response


class UserResponse(BaseModel):
    username: str | None


async def auth_me(request: Request) -> UserResponse:
    """Return the current authenticated user."""
    username = _get_username_or_401(request)
    if not username:
        from fastapi import HTTPException

        raise HTTPException(status_code=401, detail="not authenticated")
    return UserResponse(username=username)


# -- Conversation routes ------------------------------------------------------


class ConversationListingItem(BaseModel):
    conv_id: str
    title: str
    created_at: str
    updated_at: str
    status: str = "idle"


class SystemConversationListingItem(BaseModel):
    conv_id: str
    title: str
    conv_type: str
    updated_at: str
    status: str = "idle"


class ConversationFolderEntry(BaseModel):
    name: str
    path: str


class VirtualConversationFolderEntry(ConversationFolderEntry):
    virtual: Literal[True]


class ConversationListingResponse(BaseModel):
    folder: str
    folders: list[ConversationFolderEntry | VirtualConversationFolderEntry]
    conversations: list[ConversationListingItem]


class SystemConversationListingResponse(BaseModel):
    folder: str
    folders: list[ConversationFolderEntry]
    conversations: list[SystemConversationListingItem]


class SessionConversationItem(BaseModel):
    """One conversation inside a session group — the parent or a subagent."""

    conv_id: str
    title: str
    kind: str = "parent"
    created_at: str = ""
    updated_at: str = ""
    status: str = "idle"


class SessionEntry(BaseModel):
    """A session: one root conversation plus its delegated subagent children."""

    session_id: str
    title: str
    created_at: str = ""
    updated_at: str = ""
    status: str = "idle"
    conversations: list[SessionConversationItem] = []


class SessionListingResponse(BaseModel):
    folder: str
    sessions: list[SessionEntry]


class SessionConversationsResponse(BaseModel):
    session_id: str
    conversations: list[SessionConversationItem]


class ConversationSearchMatch(BaseModel):
    conv_id: str
    title: str
    updated_at: str = ""
    snippet: str | None = None
    matched_role: str | None = None
    archived: bool = False


class ConversationSearchResponse(BaseModel):
    query: str
    matches: list[ConversationSearchMatch]


async def list_conversations(request: Request, folder: str = "") -> ConversationListingResponse | JSONResponse:
    """List conversations and subfolders for a specific folder.

    Query params:
        folder — folder path (default: top-level)

    Returns ``{folder, folders, conversations}`` mirroring vault_list pattern.
    """
    from .web.conversation_folders import ConversationFolderIndex
    from .web.conversations import ConversationIndex

    username = _get_username_or_401(request)
    if not username:
        return JSONResponse({"error": "not authenticated"}, status_code=401)
    config = request.app.state.config
    folder_param = folder.strip()
    err = _validate_folder_param(folder_param)
    if err:
        return JSONResponse({"error": err}, status_code=400)
    index = ConversationIndex(config)
    folder_index = ConversationFolderIndex(config, username)
    convs = index.list_for_user(username)
    assignments = await folder_index.get_all_assignments()
    filtered = [c for c in convs if assignments.get(c.conv_id, "") == folder_param]
    child_names = await folder_index.list_folders(folder_param)
    folders: list[dict] = [
        {"name": name, "path": f"{folder_param}/{name}" if folder_param else name} for name in child_names
    ]
    if not folder_param:
        folders.append({"name": "Archived", "path": "_archived", "virtual": True})
        folders.append({"name": "System", "path": "_system", "virtual": True})
    manager = getattr(request.app.state, "manager", None)
    conv_items = []
    for c in filtered:
        item = c.to_dict()
        item["status"] = manager.get_activity_status(c.conv_id) if manager else "idle"
        conv_items.append(item)
    return ConversationListingResponse.model_validate(
        {
            "folder": folder_param,
            "folders": folders,
            "conversations": conv_items,
        }
    )


async def list_archived_conversations(request: Request, folder: str = "") -> ConversationListingResponse | JSONResponse:
    """List archived conversations, optionally filtered by folder."""
    from .web.conversation_folders import ConversationFolderIndex
    from .web.conversations import ConversationIndex

    username = _get_username_or_401(request)
    if not username:
        return JSONResponse({"error": "not authenticated"}, status_code=401)
    config = request.app.state.config
    folder_param = folder.strip()
    err = _validate_folder_param(folder_param)
    if err:
        return JSONResponse({"error": err}, status_code=400)
    index = ConversationIndex(config)
    folder_index = ConversationFolderIndex(config, username)
    convs = index.list_for_user(username, include_archived=True)
    archived = [c for c in convs if c.archived]
    assignments = await folder_index.get_all_assignments()
    filtered = [c for c in archived if assignments.get(c.conv_id, "") == folder_param]
    # Derive child folders from archived conversation assignments — extract
    # immediate child segment of nested folder paths.
    prefix = f"{folder_param}/" if folder_param else ""
    child_names = set()
    for c in archived:
        folder = assignments.get(c.conv_id, "")
        if not folder:
            continue
        if folder_param == "":
            child_names.add(folder.split("/")[0])
        elif folder.startswith(prefix):
            rest = folder[len(prefix) :]
            if rest:
                child_names.add(rest.split("/")[0])
    folders = [
        {"name": name, "path": f"{folder_param}/{name}" if folder_param else name} for name in sorted(child_names)
    ]
    manager = getattr(request.app.state, "manager", None)
    conv_items = []
    for c in filtered:
        item = c.to_dict()
        item["status"] = manager.get_activity_status(c.conv_id) if manager else "idle"
        conv_items.append(item)
    return ConversationListingResponse.model_validate(
        {
            "folder": folder_param,
            "folders": folders,
            "conversations": conv_items,
        }
    )


async def list_system_conversations(
    request: Request, folder: str = ""
) -> SystemConversationListingResponse | JSONResponse:
    """List system conversations, grouped by type sub-folders."""
    from .web.conversations import list_system_conversations as list_sys

    username = _get_username_or_401(request)
    if not username:
        return JSONResponse({"error": "not authenticated"}, status_code=401)
    config = request.app.state.config
    folder_param = folder.strip()
    all_sys = list_sys(config, username=username)
    if not folder_param:
        folders = [
            {"name": "Heartbeat", "path": "heartbeat"},
            {"name": "Schedule", "path": "schedule"},
            {"name": "Delegated", "path": "delegated"},
        ]
        return SystemConversationListingResponse.model_validate(
            {
                "folder": "",
                "folders": folders,
                "conversations": [],
            }
        )
    valid_types = {"heartbeat", "schedule", "delegated"}
    if folder_param not in valid_types:
        return JSONResponse({"error": "invalid system folder"}, status_code=400)
    manager = getattr(request.app.state, "manager", None)
    filtered = [
        dict(c, status=manager.get_activity_status(c["conv_id"]) if manager else "idle")
        for c in all_sys
        if c.get("conv_type") == folder_param
    ]
    return SystemConversationListingResponse.model_validate(
        {
            "folder": folder_param,
            "folders": [],
            "conversations": filtered,
        }
    )


async def search_conversations(
    request: Request,
    q: str = "",
    limit: int = 20,
    include_archived: bool = True,
) -> ConversationSearchResponse | JSONResponse:
    """Search the user's sessions by title and message content (#1451).

    Query params:
        q — search phrase; empty returns no matches
        limit — maximum results (clamped by the search helper)
        include_archived — include archived sessions (default true, so old
            threads remain findable from the UI)
    """
    from .web.conversation_search import search_sessions

    username = _get_username_or_401(request)
    if not username:
        return JSONResponse({"error": "not authenticated"}, status_code=401)
    config = request.app.state.config
    hits = await asyncio.to_thread(
        search_sessions,
        config,
        username,
        q,
        limit=limit,
        include_archived=include_archived,
    )
    return ConversationSearchResponse.model_validate(
        {
            "query": q,
            "matches": [
                {
                    "conv_id": hit.meta.conv_id,
                    "title": hit.meta.title,
                    "updated_at": hit.meta.updated_at,
                    "snippet": hit.snippet,
                    "matched_role": hit.matched_role,
                    "archived": hit.meta.archived,
                }
                for hit in hits
            ],
        }
    )


async def list_sessions(request: Request, folder: str = "") -> SessionListingResponse | JSONResponse:
    """List sessions (root conversation + delegated subagents) for the sidebar hierarchy.

    Query params:
        folder — optional folder path (default: top-level). Sessions whose
            root conversation is assigned to the folder are returned.

    Returns ``{folder, sessions}`` where each session carries its root
    conversation metadata plus the nested subagent conversations.

    Subagent children are discovered from the archive layer — they have no
    ConversationIndex entries (see ``discover_child_convs``). A session's
    ``status`` reflects its root conversation only; children carry per-item
    status.
    """
    from .web.conversation_folders import ConversationFolderIndex
    from .web.conversations import (
        ConversationIndex,
        discover_child_convs,
        group_conversations_into_sessions,
        parent_conv_id_of,
    )

    username = _get_username_or_401(request)
    if not username:
        return JSONResponse({"error": "not authenticated"}, status_code=401)
    config = request.app.state.config
    folder_param = folder.strip()
    err = _validate_folder_param(folder_param)
    if err:
        return JSONResponse({"error": err}, status_code=400)
    index = ConversationIndex(config)
    folder_index = ConversationFolderIndex(config, username)
    convs = index.list_for_user(username)
    convs = convs + discover_child_convs(config, index, username)
    assignments = await folder_index.get_all_assignments()

    def _in_folder(c) -> bool:
        # A session's folder is its root's assignment — children have no
        # folder assignments of their own and ride with their root. Applied
        # at every level: top-level ("") shows sessions whose root has no
        # folder, mirroring the flat listing.
        parent_id = parent_conv_id_of(c.conv_id)
        if parent_id is None:
            return assignments.get(c.conv_id, "") == folder_param
        return assignments.get(parent_id, "") == folder_param

    convs = [c for c in convs if _in_folder(c)]
    manager = getattr(request.app.state, "manager", None)
    sessions = group_conversations_into_sessions(convs, manager)
    return SessionListingResponse.model_validate(
        {
            "folder": folder_param,
            "sessions": sessions,
        }
    )


async def list_session_conversations(request: Request, session_id: str) -> SessionConversationsResponse | JSONResponse:
    """List the conversations within one session.

    ``session_id`` may name a root conversation (optionally with delegated
    subagents) or one of its subagent conversation ids; both resolve to the
    same session list — the root plus its subagent children. Children are
    discovered from the archive layer; they have no ConversationIndex
    entries (see ``discover_child_convs``).
    """
    from .web.conversations import (
        ConversationIndex,
        discover_child_convs,
        group_conversations_into_sessions,
        parent_conv_id_of,
    )

    username = _get_username_or_401(request)
    if not username:
        return JSONResponse({"error": "not authenticated"}, status_code=401)
    config = request.app.state.config
    index = ConversationIndex(config)
    # A subagent conversation id resolves to its parent session.
    root_id = parent_conv_id_of(session_id) or session_id
    root = index.get(root_id)
    if root is None or root.user_id != username:
        return JSONResponse({"error": "not found"}, status_code=404)
    manager = getattr(request.app.state, "manager", None)
    members = [root] + [
        c
        for c in discover_child_convs(config, index, username)
        if c.conv_id != root_id and parent_conv_id_of(c.conv_id) == root_id
    ]
    sessions = group_conversations_into_sessions(members, manager)
    conversations = sessions[0]["conversations"] if sessions else []
    return SessionConversationsResponse.model_validate({"session_id": root_id, "conversations": conversations})


# Schema-only input: the handler retains legacy JSON parsing and coercions.
class ConversationCreateRequest(BaseModel):
    title: str = ""
    model: str = ""
    folder: str = ""
    effort: str = ""


class ConversationCreateResponse(TypedDict):
    conv_id: str
    title: str
    created_at: str
    updated_at: str
    folder: NotRequired[str]
    model: NotRequired[str]


class ConversationForkRequest(BaseModel):
    # One of these anchors the fork. The web UI sends the timestamp because
    # rendered rows are merged and do not map 1:1 onto raw archive lines.
    message_index: int | None = None
    message_timestamp: str = ""


class ConversationForkResponse(TypedDict):
    conv_id: str
    title: str
    created_at: str
    updated_at: str
    folder: NotRequired[str]
    model: NotRequired[str]


class ConversationLifecycleResponse(BaseModel):
    ok: Literal[True]


async def create_conversation(request: Request) -> JSONResponse:
    """Create a new conversation, optionally in a folder with a model."""
    username = _get_username_or_401(request)
    if not username:
        return JSONResponse({"error": "not authenticated"}, status_code=401)
    config = request.app.state.config
    body = await request.json()
    folder = str(body.get("folder", "")).strip()
    model_name = str(body.get("model", body.get("effort", ""))).strip()
    from .model_catalog import picker_models

    if model_name and model_name not in config.model_configs and model_name not in picker_models(config):
        return JSONResponse({"error": f"Unknown model: {model_name}"}, status_code=400)
    if folder:
        from .web.conversation_folders import ConversationFolderIndex

        folder_index = ConversationFolderIndex(config, username)
        if not await folder_index.folder_exists(folder):
            return JSONResponse({"error": "Folder does not exist"}, status_code=400)
    from .web.conversations import ConversationIndex

    index = ConversationIndex(config)
    conv = index.create(username, title=body.get("title", ""))
    if folder:
        await folder_index.set_folder(conv.conv_id, folder)
    if model_name:
        from .archive import append_message

        append_message(config, conv.conv_id, {"role": "model", "content": model_name})
    result = conv.to_dict()
    if folder:
        result["folder"] = folder
    if model_name:
        result["model"] = model_name
    return JSONResponse(result, status_code=201)


async def get_home_conversation(request: Request) -> JSONResponse:
    """GET /api/conversations/home — get or lazily create the user's Home/Lobby conversation."""
    username = _get_username_or_401(request)
    if not username:
        return JSONResponse({"error": "not authenticated"}, status_code=401)
    config = request.app.state.config
    from .web.conversations import ConversationIndex

    index = ConversationIndex(config)
    home_conv = index.get_or_create_home(username)
    return JSONResponse(home_conv.to_dict())


async def fork_conversation(request: Request, id: str) -> JSONResponse:
    """Fork a conversation up to one message (#1581).

    Body: ``{"message_index": N}`` (raw archive index, inclusive) or
    ``{"message_timestamp": "..."}``. The slice ends at the nearest complete
    turn boundary at or before the anchor. The new conversation inherits the
    source's folder and current model, and the source is left untouched.
    """
    username = _get_username_or_401(request)
    if not username:
        return JSONResponse({"error": "not authenticated"}, status_code=401)
    config = request.app.state.config
    from .web import conversation_fork
    from .web.conversation_folders import ConversationFolderIndex
    from .web.conversations import ConversationIndex, can_read_conversation

    index = ConversationIndex(config)
    if not can_read_conversation(config, index, id, username):
        return JSONResponse({"error": "not found"}, status_code=404)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "invalid json body"}, status_code=400)
    message_index = body.get("message_index")
    if message_index is not None and (isinstance(message_index, bool) or not isinstance(message_index, int)):
        return JSONResponse({"error": "message_index must be an integer"}, status_code=400)
    message_timestamp = body.get("message_timestamp", "")
    if not isinstance(message_timestamp, str):
        return JSONResponse({"error": "message_timestamp must be a string"}, status_code=400)
    if message_index is None and not message_timestamp:
        return JSONResponse({"error": "message_index or message_timestamp is required"}, status_code=400)
    folder_index = ConversationFolderIndex(config, username)
    source_folder = await folder_index.get_folder(id)
    try:
        conv, model = conversation_fork.fork_conversation(
            config,
            index,
            username,
            id,
            message_index=message_index,
            message_timestamp=message_timestamp,
        )
    except conversation_fork.ForkError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    if source_folder and await folder_index.folder_exists(source_folder):
        await folder_index.set_folder(conv.conv_id, source_folder)
    result = conv.to_dict()
    result["folder"] = source_folder
    if model:
        result["model"] = model
    return JSONResponse(result, status_code=201)


@_authenticated
async def get_conversation(request: Request, username: str) -> JSONResponse:
    """Get conversation metadata."""
    from .web.conversations import ConversationIndex

    config = request.app.state.config
    conv_id = request.path_params["id"]
    index = ConversationIndex(config)
    conv = index.get(conv_id)
    if not conv or conv.user_id != username:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse(conv.to_dict())


class ConversationPatchRequest(BaseModel):
    title: str | None = None
    folder: str | None = None
    # Per-conversation auto-compaction threshold overrides (#1099). Applied
    # through ConversationManager.set_flag so they land in the conversation's
    # per-conversation persisted state and apply to subsequent turns.
    compaction_threshold: int | None = None
    compaction_threshold_ratio: float | None = None


class ConversationPatchResponse(TypedDict):
    conv_id: str
    title: str
    created_at: str
    updated_at: str
    folder: NotRequired[str]


async def rename_conversation(request: Request, id: str) -> JSONResponse:
    """Rename and/or move a conversation to a different folder."""
    from .web.conversations import ConversationIndex

    username = _get_username_or_401(request)
    if not username:
        return JSONResponse({"error": "not authenticated"}, status_code=401)
    config = request.app.state.config
    manager = request.app.state.manager
    conv_id = id
    body = await request.json()
    index = ConversationIndex(config)
    conv = index.get(conv_id)
    if not conv or conv.user_id != username:
        return JSONResponse({"error": "not found"}, status_code=404)
    folder = body.get("folder")
    if folder is not None:
        folder = str(folder).strip()
        from .web.conversation_folders import ConversationFolderIndex

        folder_index = ConversationFolderIndex(config, username)
        if folder != "":
            if not await folder_index.folder_exists(folder):
                return JSONResponse({"error": "Folder does not exist"}, status_code=400)
    title = body.get("title")
    if title is not None:
        updated = index.rename(conv_id, title, manual=True)
        if not updated:
            return JSONResponse({"error": "not found"}, status_code=404)
        conv = updated
    if folder is not None:
        ok, err = await folder_index.set_folder(conv_id, folder)
        if not ok:
            return JSONResponse({"error": err}, status_code=400)
    # #1099 — per-conversation compaction threshold overrides. Accepted as
    # optional fields on PATCH /api/conversations/{id}; applied through the
    # manager so they persist in the conversation's per-conversation state.
    # Key-presence based (not value-based) so an explicit JSON null clears an
    # override — the context inspector reset control sends nulls while absent
    # keys leave the override untouched.
    if "compaction_threshold" in body:
        manager.set_flag(conv_id, "compaction_threshold", body.get("compaction_threshold"))
    if "compaction_threshold_ratio" in body:
        manager.set_flag(conv_id, "compaction_threshold_ratio", body.get("compaction_threshold_ratio"))
    result = conv.to_dict()
    if folder is not None:
        result["folder"] = folder
    return JSONResponse(result)


@_authenticated
async def get_conversation_history(request: Request, username: str) -> JSONResponse:
    """Load paginated conversation history."""
    from .web.conversations import ConversationIndex, can_read_conversation

    config = request.app.state.config
    conv_id = request.path_params["id"]
    index = ConversationIndex(config)
    if not can_read_conversation(config, index, conv_id, username):
        return JSONResponse({"error": "not found"}, status_code=404)
    limit = int(request.query_params.get("limit", "50"))
    before = request.query_params.get("before", "")
    messages, has_more = index.load_history(conv_id, limit=limit, before=before)
    return JSONResponse({"messages": messages, "has_more": has_more})


class SideQuestionRequest(BaseModel):
    question: str
    side_history: list[dict[str, str]] | None = None
    model: str | None = None


class SideQuestionCancelResponse(BaseModel):
    ok: Literal[True]


async def handle_side_question(request: Request, id: str) -> Response:
    """Stream an ephemeral explanation-only side question via SSE."""
    username = _get_username_or_401(request)
    if not username:
        return JSONResponse({"error": "not authenticated"}, status_code=401)
    config = request.app.state.config
    manager = request.app.state.manager
    conv_id = id
    if not conv_id:
        return JSONResponse({"error": "missing conversation id"}, status_code=400)

    from .web.conversations import ConversationIndex, can_read_conversation

    index = ConversationIndex(config)
    if not can_read_conversation(config, index, conv_id, username):
        return JSONResponse({"error": "not found"}, status_code=404)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)

    if not isinstance(body, dict):
        return JSONResponse({"error": "invalid json body"}, status_code=400)

    try:
        req_data = SideQuestionRequest(**body)
    except Exception as exc:
        return JSONResponse({"error": f"invalid request payload: {exc}"}, status_code=400)

    question = req_data.question.strip()
    if not question:
        return JSONResponse({"error": "question is required"}, status_code=400)

    side_history = req_data.side_history
    model_name = req_data.model
    if model_name and model_name not in config.model_configs:
        return JSONResponse({"error": f"Unknown model: {model_name}"}, status_code=400)

    if manager is None:
        return JSONResponse({"error": "manager not available"}, status_code=503)

    from sse_starlette.sse import EventSourceResponse

    async def event_generator():
        cancel_event = asyncio.Event()
        queue: asyncio.Queue[dict[str, str] | None] = asyncio.Queue()

        async def on_chunk(chunk_type: str, data: Any):
            if chunk_type == "text" and isinstance(data, str):
                await queue.put({"event": "chunk", "data": json.dumps({"type": "chunk", "text": data})})

        async def worker():
            try:
                result = await manager.run_side_question(
                    conv_id,
                    question,
                    side_history=side_history,
                    cancel_event=cancel_event,
                    on_chunk=on_chunk,
                    model_name=model_name,
                )
                full_text = result.get("content", "") if isinstance(result, dict) else str(result)
                await queue.put({"event": "done", "data": json.dumps({"type": "done", "text": full_text})})
            except asyncio.CancelledError:
                pass
            except Exception as e:
                log.exception("Side question error")
                await queue.put({"event": "error", "data": json.dumps({"type": "error", "error": str(e)})})
            finally:
                await queue.put(None)

        task = asyncio.create_task(worker())
        try:
            while True:
                if await request.is_disconnected():
                    cancel_event.set()
                    task.cancel()
                    break

                try:
                    item = await asyncio.wait_for(queue.get(), timeout=0.2)
                except asyncio.TimeoutError:
                    continue

                if item is None:
                    break
                yield item
        finally:
            cancel_event.set()
            if not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

    return EventSourceResponse(event_generator())


async def handle_cancel_side_question(request: Request, id: str) -> JSONResponse:
    """Cancel in-flight side questions for a conversation."""
    username = _get_username_or_401(request)
    if not username:
        return JSONResponse({"error": "not authenticated"}, status_code=401)
    config = request.app.state.config
    manager = request.app.state.manager
    conv_id = id
    if not conv_id:
        return JSONResponse({"error": "missing conversation id"}, status_code=400)

    from .web.conversations import ConversationIndex, can_read_conversation

    index = ConversationIndex(config)
    if not can_read_conversation(config, index, conv_id, username):
        return JSONResponse({"error": "not found"}, status_code=404)

    if manager:
        manager.cancel_side_question(conv_id)
    return JSONResponse({"ok": True})


class ContextMatch(BaseModel):
    name: str
    score: float


class ContextSourceDetails(BaseModel):
    top_score: float | None = None
    min_score: float | None = None
    budget_source: str | None = None
    deferred_mode: bool | None = None
    matches: list[ContextMatch] | None = None
    input_tokens: list[str] | None = None


class ContextSource(BaseModel):
    source: str
    tokens_estimated: int | None = None
    items_included: int | None = None
    items_truncated: int | None = None
    details: ContextSourceDetails | None = None


class ContextCandidate(BaseModel):
    file_path: str | None = None
    source_type: str | None = None
    composite_score: float | None = None
    tokens_estimated: int | None = None
    similarity: float | None = None
    recency: float | None = None
    importance: float | None = None
    linked_from: str | None = None


class ContextDiagnosticsResponse(BaseModel):
    sources: list[ContextSource] | None = None
    memory_candidates: list[ContextCandidate] | None = None
    total_tokens_estimated: int | None = None
    total_tokens_actual: int | None = None
    cached_prompt_tokens: int | None = None
    cache_hit_rate: float | None = None
    context_window_size: int | None = None
    compaction_threshold: int | None = None


class ContextRawResponse(BaseModel):
    model: str | None = None
    messages: list[dict[str, JsonValue]] | None = None
    tools: list[dict[str, JsonValue]] | None = None


async def get_context_diagnostics(request: Request, id: str) -> JSONResponse:
    """Return context composer diagnostics for a conversation."""
    from .context_sidecars import read_context_sidecar
    from .web.conversations import ConversationIndex, can_read_conversation

    username = _get_username_or_401(request)
    if not username:
        return JSONResponse({"error": "not authenticated"}, status_code=401)
    config = request.app.state.config
    conv_id = id
    index = ConversationIndex(config)
    if not can_read_conversation(config, index, conv_id, username):
        return JSONResponse({"error": "not found"}, status_code=404)
    data = read_context_sidecar(config, conv_id)
    if data is None:
        return JSONResponse({"error": "no context data"}, status_code=404)
    return JSONResponse(data)


async def get_context_raw(request: Request, id: str) -> JSONResponse:
    """Return raw last request payload for a conversation."""
    from .context_sidecars import read_last_request_sidecar
    from .web.conversations import ConversationIndex, can_read_conversation

    username = _get_username_or_401(request)
    if not username:
        return JSONResponse({"error": "not authenticated"}, status_code=401)
    config = request.app.state.config
    conv_id = id
    index = ConversationIndex(config)
    if not can_read_conversation(config, index, conv_id, username):
        return JSONResponse({"error": "not found"}, status_code=404)
    data = read_last_request_sidecar(config, conv_id)
    if data is None:
        return JSONResponse({"error": "no raw context data"}, status_code=404)
    return JSONResponse(data)


async def export_conversation(request: Request, id: str) -> Response:
    """Export a conversation as raw JSONL or rendered markdown.

    Query param ``format`` must be ``jsonl`` or ``markdown``. 400 on missing
    or unknown format, 404 if the conversation isn't owned by the user or
    no archive exists.
    """
    from .archive import archive_path, read_archive
    from .conversation_export import render_markdown
    from .web.conversations import ConversationIndex, can_read_conversation

    username = _get_username_or_401(request)
    if not username:
        return JSONResponse({"error": "not authenticated"}, status_code=401)
    config = request.app.state.config
    conv_id = id
    fmt = request.query_params.get("format", "")
    if fmt not in ("jsonl", "markdown"):
        return JSONResponse(
            {"error": "format must be 'jsonl' or 'markdown'"},
            status_code=400,
        )
    index = ConversationIndex(config)
    if not can_read_conversation(config, index, conv_id, username):
        return JSONResponse({"error": "not found"}, status_code=404)
    # Both formats gate on the archive file existing; an empty or corrupt
    # file is still a legitimate (if uninteresting) export — the markdown
    # form just becomes the header-only document.
    path = archive_path(config, conv_id)
    if not path.exists():
        return JSONResponse({"error": "no archive"}, status_code=404)
    if fmt == "jsonl":
        body = path.read_bytes()
        return Response(body, media_type="application/x-ndjson; charset=utf-8")
    messages = read_archive(config, conv_id)
    text = render_markdown(messages, conv_id)
    return Response(text, media_type="text/markdown; charset=utf-8")


async def archive_conversation(request: Request, id: str) -> JSONResponse:
    """Archive a conversation (hide from list, keep data)."""
    from .web.conversations import ConversationIndex

    username = _get_username_or_401(request)
    if not username:
        return JSONResponse({"error": "not authenticated"}, status_code=401)
    config = request.app.state.config
    conv_id = id
    index = ConversationIndex(config)
    conv = index.get(conv_id)
    if not conv or conv.user_id != username:
        return JSONResponse({"error": "not found"}, status_code=404)
    index.archive(conv_id)
    return JSONResponse({"ok": True})


async def unarchive_conversation(request: Request, id: str) -> JSONResponse:
    """Unarchive a conversation (restore to active list)."""
    from .web.conversations import ConversationIndex

    username = _get_username_or_401(request)
    if not username:
        return JSONResponse({"error": "not authenticated"}, status_code=401)
    config = request.app.state.config
    conv_id = id
    index = ConversationIndex(config)
    conv = index.get(conv_id)
    if not conv or conv.user_id != username:
        return JSONResponse({"error": "not found"}, status_code=404)
    index.unarchive(conv_id)
    return JSONResponse({"ok": True})


async def delete_conversation(request: Request, id: str) -> JSONResponse:
    """Permanently delete a conversation and all associated files."""
    from .conversation_paths import delete_conversation_files
    from .web.conversation_folders import ConversationFolderIndex
    from .web.conversations import ConversationIndex

    username = _get_username_or_401(request)
    if not username:
        return JSONResponse({"error": "not authenticated"}, status_code=401)
    config = request.app.state.config
    conv_id = id
    index = ConversationIndex(config)
    conv = index.get(conv_id)
    if not conv or conv.user_id != username:
        return JSONResponse({"error": "not found"}, status_code=404)

    # Kill any live terminal sessions before removing files out from under
    # them — the shell's cwd may be inside the conversation's workspace.
    terminal_registry = request.app.state.terminal_registry
    await terminal_registry.kill_sessions_for_conv(conv_id)

    # Cancel any active in-memory wake timers for this conversation
    manager = getattr(request.app.state, "manager", None)
    if manager and hasattr(manager, "scheduled_wakes"):
        await manager.scheduled_wakes.cancel_all_for_conversation(conv_id)
    if manager and hasattr(manager, "file_watches"):
        await manager.file_watches.cancel_all_for_conversation(conv_id)

    # Remove the {id}/ dir (archive, sidecars, uploads, workflow.json) and
    # any leftover flat legacy sidecar files.
    delete_conversation_files(config, conv_id)
    index.delete(conv_id)
    folder_index = ConversationFolderIndex(config, username)
    await folder_index.remove_assignment(conv_id)
    return JSONResponse({"ok": True})


# -- Conversation folder routes ----------------------------------------------


class ConversationFolderRequest(BaseModel):
    path: str


class ConversationFolderResponse(BaseModel):
    ok: Literal[True]


class ConversationFolderCreateResponse(ConversationFolderResponse):
    path: str


async def create_conv_folder(request: Request) -> JSONResponse:
    """Create a conversation folder."""
    from .web.conversation_folders import ConversationFolderIndex

    username = _get_username_or_401(request)
    if not username:
        return JSONResponse({"error": "not authenticated"}, status_code=401)
    config = request.app.state.config
    body = await request.json()
    path = body.get("path", "")
    if not path or not isinstance(path, str):
        return JSONResponse({"error": "path (string) required"}, status_code=400)
    folder_index = ConversationFolderIndex(config, username)
    ok, err = await folder_index.create_folder(path.strip())
    if not ok:
        status = 409 if "already exists" in err else 400
        return JSONResponse({"error": err}, status_code=status)
    return JSONResponse({"ok": True, "path": path.strip()})


async def delete_conv_folder(request: Request, path: str) -> JSONResponse:
    """Delete an empty conversation folder."""
    from .web.conversation_folders import ConversationFolderIndex

    username = _get_username_or_401(request)
    if not username:
        return JSONResponse({"error": "not authenticated"}, status_code=401)
    config = request.app.state.config
    if not path:
        return JSONResponse({"error": "path required"}, status_code=400)
    folder_index = ConversationFolderIndex(config, username)
    ok, err = await folder_index.delete_folder(path)
    if not ok:
        status = 409 if "contains" in err else 404
        return JSONResponse({"error": err}, status_code=status)
    return JSONResponse({"ok": True})


async def rename_conv_folder(request: Request, path: str) -> JSONResponse:
    """Rename/move a conversation folder. Merges if target exists."""
    from .web.conversation_folders import ConversationFolderIndex

    username = _get_username_or_401(request)
    if not username:
        return JSONResponse({"error": "not authenticated"}, status_code=401)
    config = request.app.state.config
    old_path = path
    if not old_path:
        return JSONResponse({"error": "path required"}, status_code=400)
    body = await request.json()
    new_path = body.get("path", "")
    if not new_path or not isinstance(new_path, str):
        return JSONResponse({"error": "path (string) required in body"}, status_code=400)
    folder_index = ConversationFolderIndex(config, username)
    ok, err = await folder_index.rename_folder(old_path, new_path.strip())
    if not ok:
        status = 404 if "not found" in err else 400
        return JSONResponse({"error": err}, status_code=status)
    return JSONResponse({"ok": True})


# -- Notification routes ------------------------------------------------------
# Phase 1: single-user. Inbox + read-state live in the agent workspace,
# not partitioned by authenticated user. All authenticated callers see
# the same records. Multi-user partitioning is tracked in docs/notifications.md
# "Coming in Phase 2+". Do not expose across tenants until partitioning lands.


class NotificationResponse(BaseModel):
    id: str
    timestamp: str
    category: str
    title: str
    priority: str
    body: str
    link: str | None
    conv_id: str | None
    read: bool


class NotificationListResponse(BaseModel):
    records: list[NotificationResponse]
    has_more: bool


class NotificationCountResponse(BaseModel):
    count: int


class NotificationReadResponse(BaseModel):
    ok: bool


async def list_notifications(request: Request) -> JSONResponse:
    """Return inbox records newest first, with a joined ``read`` bool."""
    if not _get_username_or_401(request):
        return JSONResponse({"error": "not authenticated"}, status_code=401)
    from . import notifications as notifs

    config = request.app.state.config
    try:
        limit = int(request.query_params.get("limit", "20"))
    except (TypeError, ValueError):
        return JSONResponse({"error": "limit must be an integer"}, status_code=400)
    if limit <= 0 or limit > 200:
        return JSONResponse({"error": "limit must be in [1, 200]"}, status_code=400)
    before = request.query_params.get("before") or None
    records, has_more = notifs.read_inbox(config, limit=limit, before=before)
    read_ids = notifs.get_read_ids(config)
    return JSONResponse(
        {
            "records": [{**r.to_dict(), "read": r.id in read_ids} for r in records],
            "has_more": has_more,
        }
    )


async def notifications_unread_count(request: Request) -> JSONResponse:
    """Return ``{"count": N}`` — called frequently, stays cheap."""
    if not _get_username_or_401(request):
        return JSONResponse({"error": "not authenticated"}, status_code=401)
    from . import notifications as notifs

    return JSONResponse({"count": notifs.unread_count(request.app.state.config)})


async def notifications_mark_read(request: Request, id: str) -> JSONResponse:
    """Mark a single notification read. Idempotent."""
    if not _get_username_or_401(request):
        return JSONResponse({"error": "not authenticated"}, status_code=401)
    from . import notifications as notifs

    config = request.app.state.config
    event_bus = request.app.state.event_bus
    record_id = id
    if not record_id:
        return JSONResponse({"error": "id required"}, status_code=400)
    await notifs.mark_read(config, record_id, event_bus=event_bus)
    return JSONResponse({"ok": True})


async def notifications_mark_all_read(request: Request) -> JSONResponse:
    """Mark all currently-visible notifications read."""
    if not _get_username_or_401(request):
        return JSONResponse({"error": "not authenticated"}, status_code=401)
    from . import notifications as notifs

    await notifs.mark_all_read(
        request.app.state.config,
        event_bus=request.app.state.event_bus,
    )
    return JSONResponse({"ok": True})


# -- Workspace routes ---------------------------------------------------------
# The workspace resource's models, handlers, and router live in
# web/workspace_routes.py; create_app aggregates its routes. The autocomplete
# route below is a separate resource that shares this area of the file.


# Autocomplete caps: one per source so a noisy vault or MCP server cannot crowd
# out the other sources, and one total to bound the response.
_MAX_AUTOCOMPLETE_PER_SOURCE = 20
_MAX_AUTOCOMPLETE_RESULTS = 50


@_authenticated
async def autocomplete(request: Request, username: str) -> JSONResponse:
    """Return autocomplete suggestions for files, MCP resources, and vault pages."""
    query = request.query_params.get("q", "").strip()
    if query.startswith("@"):
        query = query[1:]

    results = []

    # 1. Search Vault Pages
    config = request.app.state.config
    vault_root = config.vault_root
    if vault_root.is_dir():
        try:
            vault_resolved = vault_root.resolve()

            def _find_vault_pages():
                matches = []
                visited_dirs = set()

                for dirpath, dirnames, filenames in os.walk(vault_root, followlinks=True):
                    try:
                        resolved_dir = Path(dirpath).resolve()
                        if resolved_dir in visited_dirs:
                            dirnames[:] = []
                            continue
                        visited_dirs.add(resolved_dir)
                    except Exception as exc:
                        log.debug("Autocomplete vault search skipped unresolvable dir %s: %s", dirpath, exc)
                        dirnames[:] = []
                        continue

                    dirnames[:] = [d for d in dirnames if not d.startswith(".")]

                    for fname in filenames:
                        if fname.startswith(".") or not fname.endswith(".md"):
                            continue
                        fpath = Path(dirpath) / fname
                        try:
                            f_resolved = resolve_contained(vault_resolved, fpath.resolve(), label="vault")
                            if not f_resolved.is_file():
                                continue
                            rel = f_resolved.relative_to(vault_resolved)
                            if any(part.startswith(".") for part in rel.parts):
                                continue
                            page_name = rel.with_suffix("").as_posix()
                            if not query or query.lower() in page_name.lower():
                                matches.append(
                                    {
                                        "type": "vault",
                                        "id": page_name,
                                        "label": page_name,
                                        "description": "Vault Page",
                                    }
                                )
                        except (OSError, ValueError):
                            continue

                matches.sort(key=lambda x: x["label"].lower())
                return matches[:_MAX_AUTOCOMPLETE_PER_SOURCE]

            vault_matches = await asyncio.to_thread(_find_vault_pages)
            results.extend(vault_matches)
        except Exception as e:
            log.warning("Autocomplete vault page search failed: %s", e)

    # 2. Search MCP Resources
    from .mcp_client import get_registry

    registry = get_registry()
    if registry:
        try:
            mcp_resources = registry.get_resources()
            mcp_matches = []
            for server_name, res in mcp_resources:
                uri = str(getattr(res, "uri", ""))
                name = str(getattr(res, "name", uri))
                desc = str(getattr(res, "description", ""))
                mcp_id = f"{server_name}/{name}"
                if not query or any(query.lower() in val.lower() for val in [server_name, name, uri]):
                    mcp_matches.append(
                        {
                            "type": "mcp",
                            "id": mcp_id,
                            "label": f"mcp/{server_name}/{name}",
                            "description": desc or f"MCP Resource: {uri}",
                        }
                    )
            mcp_matches.sort(key=lambda x: x["label"].lower())
            results.extend(mcp_matches[:_MAX_AUTOCOMPLETE_PER_SOURCE])
        except Exception as e:
            log.warning("Autocomplete MCP search failed: %s", e)

    # 3. Search Workspace Files (via cached index)
    workspace_root = config.workspace_path
    if workspace_root.is_dir():
        try:
            from .workspace_index import get_workspace_files

            # Get files from cache (instant, triggers background refresh if stale)
            all_files = await get_workspace_files(config)

            # Filter and format matches
            matches = []
            for rel_str in all_files:
                if len(matches) >= _MAX_AUTOCOMPLETE_PER_SOURCE:
                    break
                if not query or query.lower() in rel_str.lower():
                    matches.append(
                        {
                            "type": "file",
                            "id": rel_str,
                            "label": rel_str,
                            "description": "Workspace File",
                        }
                    )

            results.extend(matches)
        except Exception as e:
            log.warning("Autocomplete workspace search failed: %s", e)

    return JSONResponse({"results": results[:_MAX_AUTOCOMPLETE_RESULTS]})


# -- Vault routes -------------------------------------------------------------


@_authenticated
async def vault_list(request: Request, username: str) -> JSONResponse:
    """List vault pages and subfolders for a specific folder.

    Returns ``{folder, folders, pages}`` where *folders* are immediate
    child directories that contain at least one ``.md`` file and *pages*
    are ``.md`` files directly in the requested folder.
    """
    config = request.app.state.config
    vault = _vault_root(config)
    if not vault.is_dir():
        return JSONResponse({"folder": "", "folders": [], "pages": []})

    folder_param = request.query_params.get("folder", "").strip()
    if folder_param:
        error = validate_vault_page_name(folder_param)
        if error:
            return JSONResponse({"error": error}, status_code=400)
        try:
            target_dir = resolve_contained(vault, folder_param, label="vault")
        except PathContainmentError:
            return JSONResponse({"error": "path outside vault"}, status_code=403)
        if not target_dir.is_dir():
            return JSONResponse({"error": "folder not found"}, status_code=404)
    else:
        target_dir = vault.resolve()

    vault_resolved = vault.resolve()
    pages = []
    for child in target_dir.iterdir():
        if child.is_file() and child.suffix == ".md":
            try:
                resolve_contained(vault_resolved, child, label="vault")
            except PathContainmentError:
                continue
            stat = child.stat()
            rel = child.relative_to(vault_resolved)
            pages.append(
                {
                    "title": child.stem,
                    "path": str(rel.with_suffix("")),
                    "folder": folder_param,
                    "modified": stat.st_mtime,
                    "summary": _page_summary(child),
                }
            )
    pages.sort(key=lambda p: p["title"].lower())

    folders = []
    for child in sorted(target_dir.iterdir(), key=lambda c: c.name.lower()):
        if child.is_dir() and not child.name.startswith("."):
            rel = child.relative_to(vault_resolved)
            folders.append({"name": child.name, "path": str(rel)})

    return JSONResponse(
        {
            "folder": folder_param,
            "folders": folders,
            "pages": pages,
        }
    )


@_authenticated
async def vault_recent(request: Request, username: str) -> JSONResponse:
    """List recently modified agent vault pages sorted by mtime descending.

    Only scans the agent's folder within the vault — pages and journal,
    not the user's personal vault files.
    """
    config = request.app.state.config
    agent_dir = config.vault_agent_dir
    if not agent_dir.is_dir():
        return JSONResponse({"pages": []})

    vault_resolved = _vault_root(config).resolve()
    agent_resolved = agent_dir.resolve()
    try:
        resolve_contained(vault_resolved, agent_resolved, label="vault")
    except PathContainmentError:
        log.warning("vault_agent_dir is outside vault_root, skipping recent changes")
        return JSONResponse({"pages": []})
    limit = config.vault.recent_changes_limit
    try:
        limit = int(request.query_params.get("limit", limit))
    except (ValueError, TypeError):
        pass
    limit = max(0, limit)

    pages = []
    for md_file in agent_resolved.rglob("*.md"):
        if not md_file.is_file():
            continue
        try:
            resolve_contained(agent_resolved, md_file, label="the agent vault folder")
        except PathContainmentError:
            continue
        # Skip hidden directories (.obsidian, .git, .trash, etc.)
        if any(part.startswith(".") for part in md_file.relative_to(agent_resolved).parts[:-1]):
            continue
        rel = md_file.relative_to(vault_resolved)
        pages.append(
            {
                "title": md_file.stem,
                "path": str(rel.with_suffix("")),
                "folder": str(rel.parent) if str(rel.parent) != "." else "",
                "modified": md_file.stat().st_mtime,
                "summary": _page_summary(md_file),
            }
        )

    pages.sort(key=lambda p: p["modified"], reverse=True)
    return JSONResponse({"pages": pages[:limit]})


@_authenticated
async def vault_tags(request: Request, username: str) -> JSONResponse:
    """List all tags in use across the vault with usage counts and pages.

    Sorted by count descending, tie-broken by (normalized) tag name
    ascending — same ordering as the ``vault_tags`` tool.
    """
    config = request.app.state.config
    tag_map = collect_all_tags(config)
    ordered = sorted(tag_map.items(), key=lambda kv: (-kv[1]["count"], kv[0]))
    tags = [{"tag": info["display"], "count": info["count"], "pages": info["pages"]} for _, info in ordered]
    return JSONResponse({"tags": tags})


@_authenticated
async def bookmarks_list(request: Request, username: str) -> JSONResponse:
    """GET /api/bookmarks — the agent's current bookmarks."""
    config = request.app.state.config
    data = await BookmarkStore(config).list()
    return JSONResponse(data)


@_authenticated
async def bookmarks_add(request: Request, username: str) -> JSONResponse:
    """POST /api/bookmarks — add (or update) a bookmark.

    Body: ``{"target": "files"|"vault", "path": str, "label"?: str,
    "kind": "file"|"dir"|"page"|"folder"}``. Returns the updated payload.
    """
    config = request.app.state.config
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    target = body.get("target")
    path = body.get("path")
    label = body.get("label")
    kind = body.get("kind")
    # `target` may arrive as a JSON array/object (malformed); an unhashable
    # value would blow up the dict membership test below with a TypeError, so
    # check the type before comparing.
    if not isinstance(target, str) or target not in KINDS_BY_TARGET:
        return JSONResponse({"error": "target must be 'files' or 'vault'"}, status_code=400)
    if not isinstance(path, str):
        return JSONResponse({"error": "path is required"}, status_code=400)
    err = validate_bookmark_path(path)
    if err:
        return JSONResponse({"error": err}, status_code=400)
    if not isinstance(kind, str) or kind not in KINDS_BY_TARGET[target]:
        return JSONResponse(
            {"error": f"kind must be one of {list(KINDS_BY_TARGET[target])} for target '{target}'"},
            status_code=400,
        )
    if label is not None and not isinstance(label, str):
        return JSONResponse({"error": "label must be a string"}, status_code=400)
    data = await BookmarkStore(config).add(target, path, kind, label)
    return JSONResponse(data)


@_authenticated
async def bookmarks_delete(request: Request, username: str) -> JSONResponse:
    """DELETE /api/bookmarks?target=...&path=... — remove a bookmark.

    ``target`` and ``path`` come from the query string; a JSON body with
    the same keys is accepted as a fallback. Returns the updated payload;
    404 when the bookmark is not present.
    """
    config = request.app.state.config
    target = request.query_params.get("target")
    path = request.query_params.get("path")
    if target is None or path is None:
        try:
            body = await request.json()
        except json.JSONDecodeError:
            body = None
        if isinstance(body, dict):
            target = body.get("target") if target is None else target
            path = body.get("path") if path is None else path
    # Same as POST: a malformed (array/object) `target` from the query-string
    # fallback body must be a 400, not an unhandled TypeError from the
    # unhashable-key dict lookup below.
    if not isinstance(target, str) or target not in KINDS_BY_TARGET:
        return JSONResponse({"error": "target must be 'files' or 'vault'"}, status_code=400)
    if not isinstance(path, str) or not path:
        return JSONResponse({"error": "path query parameter is required"}, status_code=400)
    data = await BookmarkStore(config).remove(target, path)
    if data is None:
        return JSONResponse({"error": "bookmark not found"}, status_code=404)
    return JSONResponse(data)


@_authenticated
async def vault_read(request: Request, username: str) -> JSONResponse:
    """Read a single vault page as JSON."""
    config = request.app.state.config
    page_name = request.path_params.get("page", "")
    if not page_name:
        return JSONResponse({"error": "page name required"}, status_code=400)
    resolved = _resolve_vault_page(config, page_name)
    if not resolved:
        return JSONResponse({"error": "not found"}, status_code=404)
    content = resolved.read_text(encoding="utf-8")
    stat = resolved.stat()
    vault = _vault_root(config).resolve()
    rel = resolved.relative_to(vault)
    raw_block, page_body = split_frontmatter(content)
    metadata, fm_error = parse_frontmatter_block(raw_block)
    payload = {
        "title": resolved.stem,
        "path": str(rel.with_suffix("")),
        # Arbitrary user YAML: dates/sets/NaN would otherwise 500 the render.
        "frontmatter": to_json_safe(metadata),
        # Always the real bytes: the raw editor has replace semantics, so
        # re-serializing the parsed dict would reorder keys and drop comments
        # the moment anyone opened the panel.
        "frontmatter_raw": raw_block or "",
        "body": page_body,
        "modified": stat.st_mtime,
    }
    if fm_error is not None:
        payload["frontmatter_error"] = fm_error
    return JSONResponse(payload)


def _schedule_vault_index(
    app,
    config,
    rel_path: str,
    content: str,
    source_type: str,
    *,
    delete_paths: tuple[tuple[str, str | None], ...] = (),
) -> None:
    """Index a vault page after the write response, without blocking it.

    Embedding a normal page takes 1-2s. Awaiting it inside the PUT left the
    client's optimistic-concurrency snapshot stale long enough for the next
    autosave to reuse it and earn a 409 (#1767). Writes are chained per path
    so an older, slower embed can never land after a newer one. Failures stay
    fail-open: the page is already on disk, and a later reindex repairs the
    index.
    """
    tasks: dict[str, asyncio.Task] = app.state.vault_index_tasks

    async def run(previous: asyncio.Task | None) -> None:
        if previous is not None:
            try:
                await previous
            except Exception:
                pass
        from .embeddings import delete_entries, index_entry

        try:
            for old_path, old_source_type in delete_paths:
                delete_entries(config, old_path, source_type=old_source_type)
            delete_entries(config, rel_path, source_type=source_type)
            await index_entry(config, rel_path, content, source_type=source_type)
        except Exception as e:
            log.warning(f"Failed to index vault page '{rel_path}': {e}")

    previous = tasks.get(rel_path)
    task = asyncio.create_task(run(previous))
    tasks[rel_path] = task

    def _forget(done: asyncio.Task) -> None:
        if tasks.get(rel_path) is done:
            tasks.pop(rel_path, None)

    task.add_done_callback(_forget)


async def _vault_rename(
    app,
    config,
    vault: Path,
    old_file: Path,
    old_name: str,
    rename_to: str,
    event_bus=None,
) -> JSONResponse:
    """Rename/move a vault page; re-indexes embeddings on success."""
    if not isinstance(rename_to, str) or not rename_to.strip():
        return JSONResponse({"error": "rename_to must be a non-empty string"}, status_code=400)
    rename_to = rename_to.strip()
    error = validate_vault_page_name(rename_to)
    if error:
        return JSONResponse({"error": error}, status_code=400)
    rename_path = Path(rename_to)
    if not rename_path.name:
        return JSONResponse({"error": "invalid rename path"}, status_code=400)
    if rename_path.suffix.lower() == ".md":
        rename_path = rename_path.with_suffix("")
    try:
        new_file = resolve_contained(vault, f"{rename_path}.md", label="vault")
    except PathContainmentError:
        return JSONResponse({"error": "path outside vault directory"}, status_code=403)
    if not old_file.exists():
        return JSONResponse({"error": "not found"}, status_code=404)
    if new_file.exists():
        return JSONResponse({"error": "target already exists"}, status_code=409)
    new_file.parent.mkdir(parents=True, exist_ok=True)
    old_file.rename(new_file)
    # Clean up empty parent directories from old location
    old_dir = old_file.parent
    vault_resolved = vault.resolve()
    while old_dir.resolve() != vault_resolved:
        try:
            old_dir.rmdir()
        except OSError:
            break
        old_dir = old_dir.parent
    old_rel = f"{old_name}.md"
    old_source_type = _vault_source_type(config, old_file)
    new_rel = str(new_file.relative_to(vault_resolved))
    new_content = new_file.read_text(encoding="utf-8")
    new_source_type = _vault_source_type(config, new_file)
    _schedule_vault_index(
        app,
        config,
        new_rel,
        new_content,
        new_source_type,
        delete_paths=((old_rel, old_source_type),),
    )
    await publish_vault_changed(event_bus, config, kind=KIND_RENAME, path=new_file)
    stat = new_file.stat()
    rel = new_file.relative_to(vault_resolved)
    folder = str(rel.parent) if rel.parent != Path(".") else ""
    return JSONResponse(
        {
            "ok": True,
            "title": new_file.stem,
            "path": str(rel.with_suffix("")),
            "folder": folder,
            "modified": stat.st_mtime,
        }
    )


@_authenticated
async def vault_write(request: Request, username: str) -> JSONResponse:
    """Create or update a vault page, or rename/move it."""
    config = request.app.state.config
    event_bus = request.app.state.event_bus
    page_name = request.path_params.get("page", "")
    if not page_name:
        return JSONResponse({"error": "page name required"}, status_code=400)
    error = validate_vault_page_name(page_name)
    if error:
        return JSONResponse({"error": error}, status_code=400)
    vault = _vault_root(config)
    vault.mkdir(parents=True, exist_ok=True)
    try:
        target = resolve_contained(vault, f"{page_name}.md", label="vault")
    except PathContainmentError:
        return JSONResponse({"error": "path outside vault directory"}, status_code=403)
    body = await _read_json_body(request)
    if body is None:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)

    rename_to = body.get("rename_to")
    if rename_to is not None:
        conflicting = [key for key in ("content", "body", "frontmatter", "frontmatter_raw") if key in body]
        if conflicting:
            return JSONResponse(
                {"error": f"cannot combine rename_to with {', '.join(sorted(conflicting))}"},
                status_code=400,
            )
        return await _vault_rename(
            request.app,
            config,
            vault,
            target,
            page_name,
            rename_to,
            event_bus=event_bus,
        )

    # `content` is the legacy alias for `body`. Remember which name the client
    # actually sent so a type error names the field they typed, not ours.
    body_field = "body"
    if "content" in body and "body" not in body:
        body["body"] = body.pop("content")
        body_field = "content"
    new_body = body.get("body")
    fm_patch = body.get("frontmatter")
    if new_body is not None and not isinstance(new_body, str):
        return JSONResponse(
            {"error": f"{body_field} must be a string"},
            status_code=400,
        )
    if fm_patch is not None and not isinstance(fm_patch, dict):
        return JSONResponse(
            {"error": "frontmatter must be an object"},
            status_code=400,
        )
    fm_raw = body.get("frontmatter_raw")
    if fm_raw is not None and not isinstance(fm_raw, str):
        return JSONResponse(
            {"error": "frontmatter_raw must be a string"},
            status_code=400,
        )
    if fm_patch is not None and fm_raw is not None:
        return JSONResponse(
            {"error": "frontmatter and frontmatter_raw are mutually exclusive"},
            status_code=400,
        )
    if new_body is None and fm_patch is None and fm_raw is None:
        return JSONResponse(
            {"error": "request must include body (or its alias content), frontmatter, or frontmatter_raw"},
            status_code=400,
        )

    modified = body.get("modified")
    if modified is not None:
        try:
            modified = float(modified)
        except (TypeError, ValueError):
            return JSONResponse({"error": "modified must be a number"}, status_code=400)
        if target.exists():
            conflict = check_mtime_conflict(target.stat().st_mtime, modified)
            if conflict is not None:
                return conflict

    existed = target.exists()
    existing_text = target.read_text(encoding="utf-8") if existed else ""
    existing_raw, existing_body = split_frontmatter(existing_text)
    existing_meta, fm_error = parse_frontmatter_block(existing_raw)

    new_raw, fm_err_dict = _resolve_frontmatter(
        existing_raw,
        existing_meta,
        fm_error,
        fm_raw,
        fm_patch,
    )
    if fm_err_dict is not None:
        return JSONResponse({"error": fm_err_dict["error"]}, status_code=fm_err_dict["status_code"])

    final_body = existing_body if new_body is None else new_body
    content = join_frontmatter(new_raw, final_body)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    result_meta, result_fm_error = parse_frontmatter_block(new_raw)
    source_type = _vault_source_type(config, target)
    rel_path = str(target.relative_to(vault.resolve()))
    _schedule_vault_index(request.app, config, rel_path, content, source_type)
    await publish_vault_changed(
        event_bus,
        config,
        kind=KIND_UPDATE if existed else KIND_CREATE,
        path=target,
    )
    return JSONResponse(
        {
            "ok": True,
            "modified": target.stat().st_mtime,
            # Coerced for the wire only — `new_raw` above was already written to
            # disk, so a real date stays unquoted in the user's file.
            "frontmatter": to_json_safe(result_meta),
            # Returned so the client can reseed its raw editor without a second
            # GET. The metadata paths validate before writing, so they always leave
            # a parseable block — but a body-only write splices an existing block
            # back verbatim, malformed YAML included, and then reports
            # `frontmatter: {}`. Mirror GET and report the parse error too, so no
            # caller has to guess whether `{}` means "empty" or "unparseable".
            "frontmatter_raw": new_raw or "",
            "frontmatter_error": result_fm_error or "",
        }
    )


@_authenticated
async def vault_create(request: Request, username: str) -> JSONResponse:
    """Create a new vault page."""
    config = request.app.state.config
    event_bus = request.app.state.event_bus
    body = await _read_json_body(request)
    if body is None:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    name = body.get("name")
    if not name or not isinstance(name, str):
        return JSONResponse({"error": "name (string) required"}, status_code=400)
    name = name.strip()
    if not name:
        return JSONResponse({"error": "name (string) required"}, status_code=400)
    error = validate_vault_page_name(name)
    if error:
        return JSONResponse({"error": error}, status_code=400)
    vault = _vault_root(config)
    vault.mkdir(parents=True, exist_ok=True)
    try:
        target = resolve_contained(vault, f"{name}.md", label="vault")
    except PathContainmentError:
        return JSONResponse({"error": "path outside vault directory"}, status_code=403)
    if target.exists():
        return JSONResponse({"error": "page already exists"}, status_code=409)
    content = body.get("content")
    if content is None or not isinstance(content, str):
        content = f"# {Path(name).stem}\n"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    source_type = _vault_source_type(config, target)
    rel_path = str(target.relative_to(vault.resolve()))
    _schedule_vault_index(request.app, config, rel_path, content, source_type)
    await publish_vault_changed(event_bus, config, kind=KIND_CREATE, path=target)
    return JSONResponse({"ok": True, "page": name, "modified": target.stat().st_mtime})


@_authenticated
async def vault_create_folder(request: Request, username: str) -> JSONResponse:
    """Create a new empty folder in the vault."""
    config = request.app.state.config
    event_bus = request.app.state.event_bus
    body = await _read_json_body(request)
    if body is None:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    folder = body.get("folder")
    if not folder or not isinstance(folder, str):
        return JSONResponse({"error": "folder (string) required"}, status_code=400)
    folder = folder.strip()
    if not folder:
        return JSONResponse({"error": "folder (string) required"}, status_code=400)
    error = validate_vault_page_name(folder)
    if error:
        return JSONResponse({"error": error}, status_code=400)
    vault = _vault_root(config)
    try:
        target = resolve_contained(vault, folder, label="vault")
    except PathContainmentError:
        return JSONResponse({"error": "path outside vault directory"}, status_code=403)
    if target.exists():
        return JSONResponse({"error": "folder already exists"}, status_code=409)
    target.mkdir(parents=True, exist_ok=True)
    await publish_vault_changed(event_bus, config, kind=KIND_CREATE, path=target)
    return JSONResponse({"ok": True, "folder": folder})


@_authenticated
async def vault_delete(request: Request, username: str) -> JSONResponse:
    """Delete a vault page."""
    config = request.app.state.config
    event_bus = request.app.state.event_bus
    page_name = request.path_params.get("page", "")
    if not page_name:
        return JSONResponse({"error": "page name required"}, status_code=400)
    error = validate_vault_page_name(page_name)
    if error:
        return JSONResponse({"error": error}, status_code=400)
    vault = _vault_root(config)
    try:
        target = resolve_contained(vault, f"{page_name}.md", label="vault")
    except PathContainmentError:
        return JSONResponse({"error": "path outside vault directory"}, status_code=403)
    if not target.exists():
        return JSONResponse({"error": "not found"}, status_code=404)
    target.unlink()
    parent = target.parent
    vault_resolved = vault.resolve()
    while parent.resolve() != vault_resolved:
        try:
            parent.rmdir()
        except OSError:
            break
        parent = parent.parent
    try:
        from .embeddings import delete_entries

        rel_path = f"{page_name}.md"
        source_type = _vault_source_type(config, target)
        delete_entries(config, rel_path, source_type=source_type)
    except Exception as e:
        log.warning(f"Failed to remove embeddings for '{page_name}': {e}")
    await publish_vault_changed(event_bus, config, kind=KIND_DELETE, path=target)
    return JSONResponse({"ok": True})


@_authenticated
async def serve_vault_page(request: Request, username: str):
    """Serve the vault page HTML shell."""
    vault_html = Path(__file__).parent / "web" / "static" / "vault.html"
    if not vault_html.exists():
        return JSONResponse({"error": "vault page not found"}, status_code=404)
    return FileResponse(str(vault_html))


# -- Upload route -------------------------------------------------------------


@_authenticated
async def handle_upload(request: Request, username: str) -> JSONResponse:
    """Handle file upload for a conversation."""
    from .attachments import save_attachment
    from .web.conversations import ConversationIndex

    config = request.app.state.config
    conv_id = request.path_params["conv_id"]
    index = ConversationIndex(config)
    conv = index.get(conv_id)
    if not conv or conv.user_id != username:
        return JSONResponse({"error": "not found"}, status_code=404)
    # Early size check via Content-Length header
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > config.http.max_upload_bytes:
                return JSONResponse({"error": "file too large"}, status_code=413)
        except ValueError:
            return JSONResponse({"error": "invalid content-length"}, status_code=400)
    try:
        form = await request.form()
    except RuntimeError:
        return JSONResponse({"error": "multipart form parsing unavailable"}, status_code=400)
    except ValueError:
        return JSONResponse({"error": "invalid form data"}, status_code=400)
    upload = form.get("file")
    if upload is None or isinstance(upload, str):
        return JSONResponse({"error": "no file in request"}, status_code=400)
    data = await upload.read()
    if len(data) > config.http.max_upload_bytes:
        return JSONResponse({"error": "file too large"}, status_code=413)
    content_type = upload.content_type or "application/octet-stream"
    filename = upload.filename or "upload"
    result = save_attachment(config, conv_id, filename, data, content_type)
    return JSONResponse(result, status_code=201)


# -- Config file routes -------------------------------------------------------


def _resolve_config_path(config, path_str: str) -> tuple:
    """Resolve a config-file relative path to (filesystem_path, scope).

    Returns ``(None, None)`` when the path doesn't match a known editable
    config file or schedules pattern.
    """
    for f in _CONFIG_FILES:
        if f["path"] == path_str:
            if path_str.startswith("workspace/"):
                return (config.workspace_path / path_str.removeprefix("workspace/"), f["scope"])
            return config.agent_path / path_str, f["scope"]
    if re.match(r"^schedules/[^/]+\.md$", path_str):
        return config.agent_path / path_str, "admin"
    if re.match(r"^workspace/schedules/[^/]+\.md$", path_str):
        return (config.workspace_path / path_str.removeprefix("workspace/"), "workspace")
    return None, None


@_authenticated
async def config_list_files(request: Request, username: str) -> JSONResponse:
    """List editable config files (admin + workspace scopes + schedules)."""
    config = request.app.state.config
    result = []
    for f in _CONFIG_FILES:
        if f["path"].startswith("workspace/"):
            fpath = config.workspace_path / f["path"].removeprefix("workspace/")
        else:
            fpath = config.agent_path / f["path"]
        exists = fpath.exists()
        modified = fpath.stat().st_mtime if exists else None
        result.append(
            {
                "name": f["name"],
                "path": f["path"],
                "description": f["description"],
                "scope": f["scope"],
                "modified": modified,
                "exists": exists,
            }
        )
    for scope, base, prefix in [
        ("admin", config.agent_path, "schedules"),
        ("workspace", config.workspace_path, "workspace/schedules"),
    ]:
        sched_dir = base / "schedules"
        if sched_dir.is_dir():
            for p in sorted(sched_dir.glob("*.md")):
                stat = p.stat()
                result.append(
                    {
                        "name": p.name,
                        "path": f"{prefix}/{p.name}",
                        "description": "Scheduled task",
                        "scope": scope,
                        "modified": stat.st_mtime,
                        "exists": True,
                    }
                )
    return JSONResponse(result)


@_authenticated
async def config_read_file(request: Request, username: str) -> JSONResponse:
    """Read a config file (falls through to bundled default if missing)."""
    config = request.app.state.config
    path_str = request.path_params.get("path", "")
    fpath, _scope = _resolve_config_path(config, path_str)
    if fpath is None:
        return JSONResponse({"error": "invalid config path"}, status_code=400)
    is_default = False
    if fpath.exists():
        content = fpath.read_text(encoding="utf-8")
        modified = fpath.stat().st_mtime
    else:
        # Fall through to bundled default in `prompts/` if present.
        prompts_dir = Path(__file__).parent / "prompts"
        bundled = prompts_dir / Path(path_str).name
        if bundled.exists():
            content = bundled.read_text(encoding="utf-8")
            modified = None
            is_default = True
        else:
            content = ""
            modified = None
            is_default = True
    return JSONResponse(
        {
            "content": content,
            "modified": modified,
            "name": Path(path_str).name,
            "default": is_default,
        }
    )


@_authenticated
async def config_write_file(request: Request, username: str) -> JSONResponse:
    """Write a config file with optimistic-concurrency mtime check."""
    config = request.app.state.config
    path_str = request.path_params.get("path", "")
    fpath, _scope = _resolve_config_path(config, path_str)
    if fpath is None:
        return JSONResponse({"error": "invalid config path"}, status_code=400)
    body = await request.json()
    content = body.get("content")
    if content is None or not isinstance(content, str):
        return JSONResponse({"error": "content (string) required"}, status_code=400)
    modified = body.get("modified")
    if modified is not None:
        try:
            modified = float(modified)
        except (TypeError, ValueError):
            return JSONResponse({"error": "modified must be a number"}, status_code=400)
        if fpath.exists():
            conflict = check_mtime_conflict(fpath.stat().st_mtime, modified)
            if conflict is not None:
                return conflict
    fpath.parent.mkdir(parents=True, exist_ok=True)
    fpath.write_text(content, encoding="utf-8")
    return JSONResponse({"ok": True, "modified": fpath.stat().st_mtime})


# -- WebSocket adapter --------------------------------------------------------


async def ws_chat(websocket):
    """WebSocket entry point — defers to the gateway in `web/websocket.py`."""
    from .web.websocket import websocket_chat

    state = websocket.app.state
    await websocket_chat(
        websocket,
        state.config,
        state.event_bus,
        state.app_ctx,
        manager=state.manager,
        terminal_registry=state.terminal_registry,
    )


async def ws_terminal(websocket):
    """WebSocket entry point — defers to the gateway in `web/websocket.py`."""
    from .web.websocket import websocket_terminal

    state = websocket.app.state
    await websocket_terminal(websocket, state.config, state.terminal_registry)


# -- Widget routes ------------------------------------------------------------


class WidgetDescriptorResponse(BaseModel):
    name: str
    tier: str
    description: str
    modes: list[str]
    accepts_input: bool
    data_schema: dict[str, JsonValue]
    js_url: str


class WidgetCatalogResponse(BaseModel):
    widgets: list[WidgetDescriptorResponse]


async def list_widgets(request: Request) -> JSONResponse:
    """Return the widget catalog with cache-busted js URLs."""
    if not _get_username_or_401(request):
        return JSONResponse({"error": "not authenticated"}, status_code=401)
    from .widgets import get_widget_registry

    registry = get_widget_registry()
    if registry is None:
        return JSONResponse({"widgets": []})
    out = []
    for d in registry.list():
        out.append(
            {
                "name": d.name,
                "tier": d.tier,
                "description": d.description,
                "modes": d.modes,
                "accepts_input": d.accepts_input,
                "data_schema": d.data_schema,
                "js_url": (f"/widgets/{d.tier}/{d.name}/widget.js?v={int(d.mtime * 1000)}"),
            }
        )
    return JSONResponse({"widgets": out})


@_authenticated
async def serve_widget_js(request: Request, username: str):
    """Serve widget.js for a registered widget.

    Tier in the URL must match the widget's actual tier so bundled and
    admin widgets of the same name don't leak across paths. The resolved
    js path is also confirmed to live under the expected tier root so a
    symlinked widget.js can't expose arbitrary files.
    """
    from .widgets import get_widget_registry

    tier = request.path_params.get("tier", "")
    name = request.path_params.get("name", "")
    if tier not in ("bundled", "admin"):
        return JSONResponse({"error": "unknown tier"}, status_code=404)
    registry = get_widget_registry()
    if registry is None:
        return JSONResponse({"error": "registry unavailable"}, status_code=404)
    desc = registry.get(name)
    if desc is None or desc.tier != tier:
        return JSONResponse({"error": "widget not found"}, status_code=404)
    try:
        resolved = desc.js_path.resolve(strict=True)
    except (OSError, RuntimeError):
        return JSONResponse({"error": "widget not found"}, status_code=404)
    try:
        resolved.relative_to(desc.tier_root)
    except ValueError:
        log.warning("widget %r resolves to %s outside tier root %s — refusing", name, resolved, desc.tier_root)
        return JSONResponse({"error": "widget not found"}, status_code=404)
    return FileResponse(
        str(resolved), media_type="application/javascript", headers={"X-Content-Type-Options": "nosniff"}
    )


# -- Canvas routes ------------------------------------------------------------


class CanvasTabResponse(BaseModel):
    id: str
    label: str
    widget_type: str
    data: dict[str, JsonValue]
    # Durable content handle for artifact-backed tabs (#1253).
    artifact_id: str | None = None


class CanvasStateResponse(BaseModel):
    schema_version: int
    active_tab: str | None
    next_tab_id: int
    tabs: list[CanvasTabResponse]


# Schema-only inputs: handlers retain legacy JSON parsing and status codes.
class CanvasNewTabRequest(BaseModel):
    widget_type: str
    data: dict[str, object]
    label: str | None = None
    background: bool = False


class CanvasTabRequest(BaseModel):
    tab_id: str


class CanvasNewTabResponse(BaseModel):
    ok: Literal[True]
    tab_id: str


class CanvasMutationResponse(BaseModel):
    ok: Literal[True]


def _user_owns_conv(config, conv_id: str, username: str) -> bool:
    """Authorization gate for canvas routes — caller must own the conv."""
    from .web.conversations import ConversationIndex

    index = ConversationIndex(config)
    conv = index.get(conv_id)
    return bool(conv and conv.user_id == username)


async def get_canvas_state(request: Request, conv_id: str) -> JSONResponse:
    """Load current canvas state for a conversation."""
    from . import canvas as canvas_mod

    config = request.app.state.config
    username = _get_username_or_401(request)
    if not username:
        return JSONResponse({"error": "not authenticated"}, status_code=401)
    if not _is_safe_conv_id(conv_id):
        return JSONResponse({"error": "invalid conv_id"}, status_code=400)
    if not _user_owns_conv(config, conv_id, username):
        return JSONResponse({"error": "not found"}, status_code=404)
    state = canvas_mod.read_canvas_state(config, conv_id)
    return JSONResponse(state)


class StickyResponse(BaseModel):
    widget_type: str | None
    data: dict[str, object] | None


async def get_sticky_state(request: Request, conv_id: str) -> StickyResponse | JSONResponse:
    """Load current sticky-slot state for a conversation (reload recovery)."""
    from . import sticky as sticky_mod

    config = request.app.state.config
    username = _get_username_or_401(request)
    if not username:
        return JSONResponse({"error": "not authenticated"}, status_code=401)
    if not _is_safe_conv_id(conv_id):
        return JSONResponse({"error": "invalid conv_id"}, status_code=400)
    if not _user_owns_conv(config, conv_id, username):
        return JSONResponse({"error": "not found"}, status_code=404)
    state = sticky_mod.read_sticky_state(config, conv_id)
    return StickyResponse(
        widget_type=state.get("widget_type"),
        data=state.get("data"),
    )


class StickySetRequest(BaseModel):
    widget_type: str
    data: dict[str, object]


async def post_sticky_state(request: Request, conv_id: str) -> JSONResponse:
    """Pin a widget into the conversation's sticky slot. Backs external
    progress updates (e.g. long-running scripts posting live status)."""
    from . import sticky as sticky_mod

    config = request.app.state.config
    manager = request.app.state.manager
    username = _get_username_or_401(request)
    if not username:
        return JSONResponse({"error": "not authenticated"}, status_code=401)
    if not _is_safe_conv_id(conv_id):
        return JSONResponse({"error": "invalid conv_id"}, status_code=400)
    if not _user_owns_conv(config, conv_id, username):
        return JSONResponse({"error": "not found"}, status_code=404)
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "body must be a JSON object"}, status_code=400)
    widget_type = body.get("widget_type")
    data = body.get("data")
    if not isinstance(widget_type, str) or not widget_type:
        return JSONResponse({"error": "widget_type must be a non-empty string"}, status_code=400)
    if not isinstance(data, dict):
        return JSONResponse({"error": "data must be a JSON object"}, status_code=400)
    emit = manager.emit if manager else None
    result = await sticky_mod.set_sticky(config, conv_id, widget_type, data, emit=emit)
    if not result.ok:
        return JSONResponse({"error": result.error}, status_code=400)
    return JSONResponse({"ok": True})


async def delete_sticky_state(request: Request, conv_id: str) -> JSONResponse:
    """Clear the conversation's sticky slot."""
    from . import sticky as sticky_mod

    config = request.app.state.config
    manager = request.app.state.manager
    username = _get_username_or_401(request)
    if not username:
        return JSONResponse({"error": "not authenticated"}, status_code=401)
    if not _is_safe_conv_id(conv_id):
        return JSONResponse({"error": "invalid conv_id"}, status_code=400)
    if not _user_owns_conv(config, conv_id, username):
        return JSONResponse({"error": "not found"}, status_code=404)
    emit = manager.emit if manager else None
    result = await sticky_mod.clear_sticky(config, conv_id, emit=emit)
    if not result.ok:
        return JSONResponse({"error": result.error}, status_code=500)
    return JSONResponse({"ok": True})


async def post_canvas_new_tab(request: Request, conv_id: str) -> JSONResponse:
    """Create a new canvas tab. Backs the inline 'Open in Canvas' button."""
    from . import canvas as canvas_mod

    config = request.app.state.config
    manager = request.app.state.manager
    username = _get_username_or_401(request)
    if not username:
        return JSONResponse({"error": "not authenticated"}, status_code=401)
    if not _is_safe_conv_id(conv_id):
        return JSONResponse({"error": "invalid conv_id"}, status_code=400)
    if not _user_owns_conv(config, conv_id, username):
        return JSONResponse({"error": "not found"}, status_code=404)
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "body must be a JSON object"}, status_code=400)
    widget_type = body.get("widget_type", "")
    data = body.get("data") or {}
    label = body.get("label")
    background = bool(body.get("background", False))
    emit = manager.emit if manager else None
    result = await canvas_mod.new_tab(
        config,
        conv_id,
        widget_type,
        data,
        label=label,
        emit=emit,
        enforce_agent_createable=False,
        background=background,
    )
    if not result.ok:
        return JSONResponse({"error": result.error}, status_code=400)
    return JSONResponse({"ok": True, "tab_id": result.tab_id})


async def post_canvas_active_tab(request: Request, conv_id: str) -> JSONResponse:
    """Set the active tab via user click in the panel."""
    from . import canvas as canvas_mod

    config = request.app.state.config
    manager = request.app.state.manager
    username = _get_username_or_401(request)
    if not username:
        return JSONResponse({"error": "not authenticated"}, status_code=401)
    if not _is_safe_conv_id(conv_id):
        return JSONResponse({"error": "invalid conv_id"}, status_code=400)
    if not _user_owns_conv(config, conv_id, username):
        return JSONResponse({"error": "not found"}, status_code=404)
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "body must be a JSON object"}, status_code=400)
    tab_id = body.get("tab_id", "")
    emit = manager.emit if manager else None
    result = await canvas_mod.set_active_tab(config, conv_id, tab_id, emit=emit)
    if not result.ok:
        return JSONResponse({"error": result.error}, status_code=400)
    return JSONResponse({"ok": True})


async def post_canvas_close_tab(request: Request, conv_id: str) -> JSONResponse:
    """Close a tab via user [×] click."""
    from . import canvas as canvas_mod

    config = request.app.state.config
    manager = request.app.state.manager
    username = _get_username_or_401(request)
    if not username:
        return JSONResponse({"error": "not authenticated"}, status_code=401)
    if not _is_safe_conv_id(conv_id):
        return JSONResponse({"error": "invalid conv_id"}, status_code=400)
    if not _user_owns_conv(config, conv_id, username):
        return JSONResponse({"error": "not found"}, status_code=404)
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "body must be a JSON object"}, status_code=400)
    tab_id = body.get("tab_id", "")
    emit = manager.emit if manager else None
    result = await canvas_mod.close_tab(
        config, conv_id, tab_id, emit=emit, registry=request.app.state.terminal_registry
    )
    if not result.ok:
        return JSONResponse({"error": result.error}, status_code=400)
    return JSONResponse({"ok": True})


@_authenticated
async def get_canvas_page(request: Request, username: str):
    """Serve the standalone canvas HTML (bare or tab-locked URL)."""
    from starlette.responses import Response

    config = request.app.state.config
    conv_id = request.path_params.get("conv_id", "")
    tab_id = request.path_params.get("tab_id", "")  # may be empty for bare URL
    if not _is_safe_conv_id(conv_id):
        return Response("Invalid conversation id", status_code=400)
    if tab_id and not _is_safe_conv_id(tab_id):
        return Response("Invalid tab id", status_code=400)
    if not _user_owns_conv(config, conv_id, username):
        return Response("Not found", status_code=404)
    html_path = Path(__file__).parent / "web" / "static" / "canvas-page.html"
    return Response(html_path.read_text(), media_type="text/html")


# -- Schedule helpers ---------------------------------------------------------


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


class ModelListResponse(BaseModel):
    models: list[str]
    default: str


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


@_authenticated
async def models_list(request: Request, username: str) -> JSONResponse:
    """GET /api/models — named model configs for UI pickers.

    A standalone route rather than a field on the schedules payload: the
    schedules page is reachable with no conversation open, and the WS
    `available_models` push is only populated once one loads.

    Returns the picker's model list: hand-authored configs plus enabled
    models discovered from providers (#1211), and the effective global
    default (catalog pointer → config.json default_model).
    """
    config = request.app.state.config
    from .model_catalog import effective_default_model, picker_models

    return JSONResponse(
        {
            "models": picker_models(config),
            "default": effective_default_model(config),
        }
    )


# -- Model catalog handlers (#1211) -------------------------------------------


class CatalogModelEntry(BaseModel):
    key: str
    id: str
    enabled: bool
    chat: bool | None = None
    tools: bool | None = None
    context_window_size: int | None = None
    owned_by: str = ""
    selectable: bool = False


class CatalogProviderEntry(BaseModel):
    discovered_at: str = ""
    error: str = ""
    models: list[CatalogModelEntry]


class ModelCatalogResponse(BaseModel):
    """GET/POST /api/models/catalog* — discovery state + UI-managed flags."""

    default_model: str
    default_pointer: str
    picker_models: list[str]
    providers: dict[str, CatalogProviderEntry]
    # Shell auto-approval judge pointer (#1213): raw sidecar pointer plus the
    # effective model it resolves to (empty = ride on the active conversation
    # model).
    aux_approval_model: str = ""
    aux_approval_pointer: str = ""
    # Reflection-judge / verifier pointers (#1212): raw sidecar pointers plus
    # the effective judge/verifier names they resolve to. Empty pointer = inherit.
    reflection_model: str = ""
    reflection_pointer: str = ""
    verifier_model: str = ""
    verifier_pointer: str = ""


class ModelCatalogUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str | None = None
    enabled: bool | None = None
    model: str | None = None


class CatalogProviderUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    enabled: bool


@_authenticated
async def models_catalog_get(request: Request, username: str) -> JSONResponse:
    """GET /api/models/catalog — full catalog for the management UI."""
    from .model_catalog import catalog_payload

    return JSONResponse(catalog_payload(request.app.state.config))


@_authenticated
async def models_catalog_discover(request: Request, username: str) -> JSONResponse:
    """POST /api/models/catalog/discover — run discovery on demand.

    Explicit refresh only, user-triggered; never polled. A failing
    gateway records its error and keeps cached entries.
    """
    from .model_catalog import catalog_payload, refresh_catalog

    config = request.app.state.config
    await refresh_catalog(config, event_bus=request.app.state.event_bus)
    return JSONResponse(catalog_payload(config))


@_authenticated
async def models_catalog_set_enabled(request: Request, username: str) -> JSONResponse:
    """POST /api/models/catalog/model — enable/disable a discovered model."""
    from .model_catalog import catalog_payload, set_model_enabled

    config = request.app.state.config
    try:
        body = await request.json()
    except Exception as exc:
        log.debug("models_catalog_set_enabled: invalid JSON body: %s", exc)
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    if not isinstance(body, dict) or not isinstance(body.get("key"), str) or not body["key"]:
        return JSONResponse({"error": "request body must include a model key"}, status_code=400)
    if not isinstance(body.get("enabled"), bool):
        return JSONResponse({"error": "request body must include a boolean 'enabled'"}, status_code=400)

    catalog = await set_model_enabled(config, body["key"], body["enabled"], event_bus=request.app.state.event_bus)
    if catalog is None:
        return JSONResponse({"error": f"unknown catalog model: {body['key']}"}, status_code=404)
    return JSONResponse(catalog_payload(config))


@_authenticated
async def models_catalog_set_provider_enabled(request: Request, username: str) -> JSONResponse:
    """POST /api/models/catalog/provider — bulk enable/disable a provider's discovered models.

    Applies to that provider's catalog entries only; hand-authored
    `model_configs` entries are never touched and `config.json` is never
    written.
    """
    from .model_catalog import catalog_payload, set_provider_models_enabled

    config = request.app.state.config
    try:
        body = await request.json()
    except Exception as exc:
        log.debug("models_catalog_set_provider_enabled: invalid JSON body: %s", exc)
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    if not isinstance(body, dict) or not isinstance(body.get("provider"), str) or not body["provider"]:
        return JSONResponse({"error": "request body must include a provider name"}, status_code=400)
    if not isinstance(body.get("enabled"), bool):
        return JSONResponse({"error": "request body must include a boolean 'enabled'"}, status_code=400)

    catalog = await set_provider_models_enabled(
        config, body["provider"], body["enabled"], event_bus=request.app.state.event_bus
    )
    if catalog is None:
        return JSONResponse({"error": f"unknown catalog provider: {body['provider']}"}, status_code=404)
    return JSONResponse(catalog_payload(config))


def _make_set_model_pointer_handler(action_name, setter):
    """Build a handler for one model-pointer endpoint.

    The four pointer endpoints (default, aux_approval, reflection,
    verifier) share the JSON-parse preamble and selectability check; only
    the action name (for logging) and the catalog setter differ. An empty
    `model` clears the pointer — each setter's docstring documents what
    resolution falls back to. A non-empty name must be selectable: an
    enabled discovered model or a hand-authored config.
    """

    async def handler(request: Request, username: str) -> JSONResponse:
        from .model_catalog import catalog_payload, picker_models

        config = request.app.state.config
        try:
            body = await request.json()
        except Exception as exc:
            log.debug("models_catalog_set_%s: invalid JSON body: %s", action_name, exc)
            return JSONResponse({"error": "invalid JSON body"}, status_code=400)
        if not isinstance(body, dict) or not isinstance(body.get("model"), str):
            return JSONResponse({"error": "request body must include a 'model' name"}, status_code=400)

        name = body["model"]
        if name and name not in picker_models(config):
            return JSONResponse({"error": f"model is not selectable: {name}"}, status_code=400)
        await setter(config, name, event_bus=request.app.state.event_bus)
        return JSONResponse(catalog_payload(config))

    handler.__name__ = f"models_catalog_set_{action_name}"
    return handler


# POST /api/models/catalog/default — set or clear the default-model pointer.
# Empty clears it (config.json's default_model takes over again).
models_catalog_set_default = _authenticated(_make_set_model_pointer_handler("default", set_default_model))

# POST /api/models/catalog/aux_approval — shell auto-approval judge pointer (#1213).
# Empty clears it (resolution falls through to the active conversation model).
models_catalog_set_aux_approval = _authenticated(
    _make_set_model_pointer_handler("aux_approval", set_aux_approval_model)
)

# POST /api/models/catalog/reflection — reflection-judge pointer (#1212).
# Empty clears it (config.json's reflection.model seeds the resolution chain);
# the judge then resolves through it on the next run — no restart.
models_catalog_set_reflection = _authenticated(_make_set_model_pointer_handler("reflection", set_reflection_model))

# POST /api/models/catalog/verifier — verifier-model pointer (#1212).
# Empty clears it (config.json's reflection.verifier_model takes over).
models_catalog_set_verifier = _authenticated(_make_set_model_pointer_handler("verifier", set_verifier_model))


# -- Schedule handlers --------------------------------------------------------


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


async def openapi_yaml(request: Request) -> Response:
    import yaml

    app = request.app
    return Response(content=yaml.dump(app.openapi(), sort_keys=False), media_type="application/x-yaml")


class RevalidatingStaticFiles(StaticFiles):
    """Static assets that every reload must revalidate (#1443).

    Starlette sends ``Last-Modified``/``ETag`` but no ``Cache-Control``.
    Browsers then apply heuristic freshness (10% of the file's age) and can
    keep running pre-fix JavaScript across ordinary reloads after a deploy,
    which made a merged web fix look unshipped. ``no-cache`` forbids reuse
    without revalidation; the conditional request still answers ``304`` when
    the file is unchanged.
    """

    async def get_response(self, path: str, scope) -> Response:
        response = await super().get_response(path, scope)
        response.headers["cache-control"] = "no-cache"
        return response


def create_app(config, event_bus, app_ctx=None, manager=None) -> FastAPI:
    """Wire up the FastAPI ASGI app — handlers live at module level
    and read deps off ``request.app.state`` (populated below).
    """

    routes: list[BaseRoute] = [
        APIRoute("/openapi.yaml", openapi_yaml, methods=["GET"], include_in_schema=False),
        APIRoute("/health", health, methods=["GET"]),
        APIRoute("/metrics", metrics_endpoint, methods=["GET"]),
        APIRoute("/actions/confirm", handle_confirm, methods=["GET", "POST"]),
        APIRoute("/actions/cancel", handle_cancel, methods=["GET", "POST"]),
        APIRoute(
            "/api/auth/login",
            auth_login,
            methods=["POST"],
            response_model=LoginResponse,
            openapi_extra={
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {"schema": LoginRequest.model_json_schema()},
                    },
                }
            },
        ),
        APIRoute("/api/auth/logout", auth_logout, methods=["POST"], response_model=LogoutResponse),
        APIRoute("/api/auth/me", auth_me, methods=["GET"]),
        APIRoute("/api/conversations", list_conversations, methods=["GET"], response_model=ConversationListingResponse),
        APIRoute("/api/conversations/home", get_home_conversation, methods=["GET"]),
        APIRoute(
            "/api/conversations/search",
            search_conversations,
            methods=["GET"],
            response_model=ConversationSearchResponse,
        ),
        APIRoute(
            "/api/conversations/archived",
            list_archived_conversations,
            methods=["GET"],
            response_model=ConversationListingResponse,
        ),
        APIRoute(
            "/api/conversations/system",
            list_system_conversations,
            methods=["GET"],
            response_model=SystemConversationListingResponse,
        ),
        APIRoute("/api/sessions", list_sessions, methods=["GET"], response_model=SessionListingResponse),
        APIRoute(
            "/api/sessions/{session_id}/conversations",
            list_session_conversations,
            methods=["GET"],
            response_model=SessionConversationsResponse,
        ),
        APIRoute(
            "/api/conversations",
            create_conversation,
            methods=["POST"],
            status_code=201,
            response_model=ConversationCreateResponse,
            openapi_extra={
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {"schema": ConversationCreateRequest.model_json_schema()},
                    },
                }
            },
        ),
        APIRoute(
            "/api/conversations/{id}/fork",
            fork_conversation,
            methods=["POST"],
            status_code=201,
            response_model=ConversationForkResponse,
            openapi_extra={
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {"schema": ConversationForkRequest.model_json_schema()},
                    },
                }
            },
        ),
        APIRoute("/api/conversations/{id}", get_conversation, methods=["GET"]),
        # Document the input without replacing this route's legacy parsing,
        # null/coercion behavior, authentication order, or 400 responses.
        APIRoute(
            "/api/conversations/{id}",
            rename_conversation,
            methods=["PATCH"],
            response_model=ConversationPatchResponse,
            openapi_extra={
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {"schema": ConversationPatchRequest.model_json_schema()},
                    },
                }
            },
        ),
        APIRoute("/api/conversations/{id}/history", get_conversation_history, methods=["GET"]),
        APIRoute(
            "/api/conversations/{id}/side-question",
            handle_side_question,
            methods=["POST"],
            response_class=Response,
            openapi_extra={
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {"schema": SideQuestionRequest.model_json_schema()},
                    },
                },
                "responses": {
                    "200": {
                        "description": "SSE stream of response tokens and completion events",
                        "content": {"text/event-stream": {}},
                    }
                },
            },
        ),
        APIRoute(
            "/api/conversations/{id}/side-question/cancel",
            handle_cancel_side_question,
            methods=["POST"],
            response_model=SideQuestionCancelResponse,
        ),
        APIRoute(
            "/api/conversations/{id}/context",
            get_context_diagnostics,
            methods=["GET"],
            response_model=ContextDiagnosticsResponse,
        ),
        APIRoute(
            "/api/conversations/{id}/context/raw",
            get_context_raw,
            methods=["GET"],
            response_model=ContextRawResponse,
        ),
        APIRoute(
            "/api/conversations/{id}/export",
            export_conversation,
            methods=["GET"],
            response_class=Response,
            # Describe the query without changing legacy 400/auth ordering.
            openapi_extra={
                "parameters": [
                    {
                        "name": "format",
                        "in": "query",
                        "required": True,
                        "schema": {"type": "string", "enum": ["jsonl", "markdown"]},
                    }
                ]
            },
            responses={
                200: {
                    "content": {
                        "application/x-ndjson": {"schema": {"type": "string"}},
                        "text/markdown": {"schema": {"type": "string"}},
                    }
                }
            },
        ),
        APIRoute(
            "/api/conversations/folders",
            create_conv_folder,
            methods=["POST"],
            response_model=ConversationFolderCreateResponse,
            openapi_extra={
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {"schema": ConversationFolderRequest.model_json_schema()},
                    },
                }
            },
        ),
        APIRoute(
            "/api/conversations/folders/{path:path}",
            delete_conv_folder,
            methods=["DELETE"],
            response_model=ConversationFolderResponse,
        ),
        APIRoute(
            "/api/conversations/folders/{path:path}",
            rename_conv_folder,
            methods=["PUT"],
            response_model=ConversationFolderResponse,
            openapi_extra={
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {"schema": ConversationFolderRequest.model_json_schema()},
                    },
                }
            },
        ),
        APIRoute(
            "/api/conversations/{id}",
            delete_conversation,
            methods=["DELETE"],
            response_model=ConversationLifecycleResponse,
        ),
        APIRoute(
            "/api/conversations/{id}/archive",
            archive_conversation,
            methods=["POST"],
            response_model=ConversationLifecycleResponse,
        ),
        APIRoute(
            "/api/conversations/{id}/unarchive",
            unarchive_conversation,
            methods=["POST"],
            response_model=ConversationLifecycleResponse,
        ),
        APIRoute(
            "/api/notifications",
            list_notifications,
            methods=["GET"],
            response_model=NotificationListResponse,
            # Schema-only queries preserve legacy parsing and 400 responses.
            openapi_extra={
                "parameters": [
                    {
                        "name": "limit",
                        "in": "query",
                        "required": False,
                        "schema": {"type": "integer", "default": 20, "minimum": 1, "maximum": 200},
                    },
                    {"name": "before", "in": "query", "required": False, "schema": {"type": "string"}},
                ]
            },
        ),
        APIRoute(
            "/api/notifications/unread-count",
            notifications_unread_count,
            methods=["GET"],
            response_model=NotificationCountResponse,
        ),
        APIRoute(
            "/api/notifications/read-all",
            notifications_mark_all_read,
            methods=["POST"],
            response_model=NotificationReadResponse,
        ),
        APIRoute(
            "/api/notifications/{id}/read",
            notifications_mark_read,
            methods=["POST"],
            response_model=NotificationReadResponse,
        ),
        APIRoute(
            "/api/upload/{conv_id}",
            handle_upload,
            methods=["POST"],
            response_model=AttachmentResponse,
            status_code=201,
            openapi_extra={
                "parameters": [
                    {
                        "name": "conv_id",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    }
                ],
                "requestBody": {
                    "required": True,
                    "content": {
                        "multipart/form-data": {
                            "schema": {
                                "type": "object",
                                "properties": {"file": {"type": "string", "format": "binary"}},
                                "required": ["file"],
                            }
                        }
                    },
                },
            },
        ),
        # The workspace resource's routes live in web/workspace_routes.py. Its
        # router registers them in historical order, but the autocomplete route
        # below is a separate resource that historically sat between them, so we
        # splice the router's routes around it. Registration order is part of
        # the contract: the generated OpenAPI spec lists paths in that order.
        *workspace_router.routes[:3],  # list, create, recent — literal paths first
        APIRoute(
            "/api/autocomplete",
            autocomplete,
            methods=["GET"],
            response_model=AutocompleteResponse,
            # Every browser caller supplies q, while the handler still
            # accepts a missing value for legacy clients.
            openapi_extra={
                "parameters": [
                    {
                        "name": "q",
                        "in": "query",
                        "required": True,
                        "schema": {"type": "string"},
                    }
                ]
            },
        ),
        *workspace_router.routes[3:],  # read, serve, write, delete — catch-alls last
        APIRoute("/api/config/files", config_list_files, methods=["GET"], response_model=list[ConfigFileEntry]),
        APIRoute(
            "/api/config/files/{path:path}",
            config_read_file,
            methods=["GET"],
            response_model=ConfigFileResponse,
            openapi_extra={
                "parameters": [
                    {
                        "name": "path",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    }
                ]
            },
        ),
        APIRoute(
            "/api/config/files/{path:path}",
            config_write_file,
            methods=["PUT"],
            response_model=ConfigWriteResponse,
            # Schema metadata only: keep manual parsing and its existing
            # 400 responses instead of introducing FastAPI 422s.
            openapi_extra={
                "parameters": [
                    {
                        "name": "path",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    }
                ],
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": _request_schema(ConfigSaveRequest),
                        },
                    },
                },
            },
        ),
        APIRoute("/api/models", models_list, methods=["GET"], response_model=ModelListResponse),
        APIRoute("/api/host", host_metrics, methods=["GET"], response_model=HostMetricsResponse),
        APIRoute(
            "/api/models/catalog",
            models_catalog_get,
            methods=["GET"],
            response_model=ModelCatalogResponse,
        ),
        APIRoute(
            "/api/models/catalog/discover",
            models_catalog_discover,
            methods=["POST"],
            response_model=ModelCatalogResponse,
        ),
        APIRoute(
            "/api/models/catalog/model",
            models_catalog_set_enabled,
            methods=["POST"],
            response_model=ModelCatalogResponse,
            openapi_extra={
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": _request_schema(ModelCatalogUpdateRequest),
                        },
                    },
                },
            },
        ),
        APIRoute(
            "/api/models/catalog/default",
            models_catalog_set_default,
            methods=["POST"],
            response_model=ModelCatalogResponse,
            openapi_extra={
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": _request_schema(ModelCatalogUpdateRequest),
                        },
                    },
                },
            },
        ),
        APIRoute(
            "/api/models/catalog/aux_approval",
            models_catalog_set_aux_approval,
            methods=["POST"],
            response_model=ModelCatalogResponse,
            openapi_extra={
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": _request_schema(ModelCatalogUpdateRequest),
                        },
                    },
                },
            },
        ),
        APIRoute(
            "/api/models/catalog/reflection",
            models_catalog_set_reflection,
            methods=["POST"],
            response_model=ModelCatalogResponse,
            openapi_extra={
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": _request_schema(ModelCatalogUpdateRequest),
                        },
                    },
                },
            },
        ),
        APIRoute(
            "/api/models/catalog/verifier",
            models_catalog_set_verifier,
            methods=["POST"],
            response_model=ModelCatalogResponse,
            openapi_extra={
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": _request_schema(ModelCatalogUpdateRequest),
                        },
                    },
                },
            },
        ),
        APIRoute(
            "/api/models/catalog/provider",
            models_catalog_set_provider_enabled,
            methods=["POST"],
            response_model=ModelCatalogResponse,
            openapi_extra={
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": _request_schema(CatalogProviderUpdateRequest),
                        },
                    },
                },
            },
        ),
        APIRoute("/api/schedules", schedules_list, methods=["GET"], response_model=ScheduleListResponse),
        APIRoute(
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
        ),
        APIRoute(
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
        ),
        APIRoute(
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
        ),
        APIRoute(
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
                            "schema": _request_schema(ScheduleUpdateRequest),
                        },
                    },
                },
            },
        ),
        APIRoute(
            "/api/vault",
            vault_create,
            methods=["POST"],
            response_model=VaultCreateResponse,
            openapi_extra={
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": _request_schema(VaultCreateRequest),
                        },
                    },
                }
            },
        ),
        APIRoute(
            "/api/vault",
            vault_list,
            methods=["GET"],
            response_model=VaultListingResponse,
            # Schema-only query metadata preserves the handler's current
            # validation and its established 400/403/404 responses.
            openapi_extra={
                "parameters": [
                    {
                        "name": "folder",
                        "in": "query",
                        "required": False,
                        "schema": {"type": "string"},
                    }
                ]
            },
        ),
        APIRoute(
            "/api/vault/folders",
            vault_create_folder,
            methods=["POST"],
            response_model=VaultFolderCreateResponse,
            openapi_extra={
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": _request_schema(VaultFolderCreateRequest),
                        },
                    },
                }
            },
        ),
        APIRoute("/api/vault/recent", vault_recent, methods=["GET"], response_model=VaultRecentResponse),
        APIRoute("/api/vault/tags", vault_tags, methods=["GET"], response_model=VaultTagsResponse),
        APIRoute(
            "/api/vault/{page:path}",
            vault_write,
            methods=["PUT"],
            response_model=VaultWriteResponse,
            # Schema metadata only: the handler keeps its manual parsing,
            # conflict checks, mutually-exclusive fields, and 400s.
            openapi_extra={
                "parameters": [
                    {
                        "name": "page",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    }
                ],
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": _request_schema(VaultWriteRequest),
                        },
                    },
                },
            },
        ),
        APIRoute(
            "/api/vault/{page:path}",
            vault_read,
            methods=["GET"],
            response_model=VaultPageResponse,
            openapi_extra={
                "parameters": [
                    {
                        "name": "page",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    }
                ]
            },
        ),
        APIRoute(
            "/api/vault/{page:path}",
            vault_delete,
            methods=["DELETE"],
            response_model=VaultDeleteResponse,
            openapi_extra={
                "parameters": [
                    {
                        "name": "page",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    }
                ]
            },
        ),
        APIRoute("/vault/{page:path}", serve_vault_page, methods=["GET"]),
        APIRoute("/api/bookmarks", bookmarks_list, methods=["GET"], response_model=BookmarksResponse),
        APIRoute(
            "/api/bookmarks",
            bookmarks_add,
            methods=["POST"],
            response_model=BookmarksResponse,
            openapi_extra={
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {"schema": BookmarkAddRequest.model_json_schema()},
                    },
                }
            },
        ),
        APIRoute(
            "/api/bookmarks",
            bookmarks_delete,
            methods=["DELETE"],
            response_model=BookmarksResponse,
            openapi_extra={
                "parameters": [
                    {
                        "name": "target",
                        "in": "query",
                        "required": True,
                        "schema": {"type": "string", "enum": ["files", "vault"]},
                    },
                    {"name": "path", "in": "query", "required": True, "schema": {"type": "string"}},
                ]
            },
        ),
        # Legacy /api/wiki/* aliases — vault handlers under the old name.
        APIRoute("/api/wiki", vault_list, methods=["GET"]),
        APIRoute("/api/wiki/{page:path}", vault_read, methods=["GET"]),
        APIRoute("/api/widgets", list_widgets, methods=["GET"], response_model=WidgetCatalogResponse),
        APIRoute("/widgets/{tier}/{name}/widget.js", serve_widget_js, methods=["GET"]),
        APIRoute("/api/canvas/{conv_id}", get_canvas_state, methods=["GET"], response_model=CanvasStateResponse),
        APIRoute("/api/sticky/{conv_id}", get_sticky_state, methods=["GET"], response_model=StickyResponse),
        APIRoute(
            "/api/sticky/{conv_id}",
            post_sticky_state,
            methods=["POST"],
            openapi_extra={
                "requestBody": {
                    "required": True,
                    "content": {"application/json": {"schema": StickySetRequest.model_json_schema()}},
                }
            },
        ),
        APIRoute("/api/sticky/{conv_id}", delete_sticky_state, methods=["DELETE"]),
        APIRoute(
            "/api/canvas/{conv_id}/new_tab",
            post_canvas_new_tab,
            methods=["POST"],
            response_model=CanvasNewTabResponse,
            openapi_extra={
                "requestBody": {
                    "required": True,
                    "content": {"application/json": {"schema": CanvasNewTabRequest.model_json_schema()}},
                }
            },
        ),
        APIRoute(
            "/api/canvas/{conv_id}/active_tab",
            post_canvas_active_tab,
            methods=["POST"],
            response_model=CanvasMutationResponse,
            openapi_extra={
                "requestBody": {
                    "required": True,
                    "content": {"application/json": {"schema": CanvasTabRequest.model_json_schema()}},
                }
            },
        ),
        APIRoute(
            "/api/canvas/{conv_id}/close_tab",
            post_canvas_close_tab,
            methods=["POST"],
            response_model=CanvasMutationResponse,
            openapi_extra={
                "requestBody": {
                    "required": True,
                    "content": {"application/json": {"schema": CanvasTabRequest.model_json_schema()}},
                }
            },
        ),
        APIRoute("/canvas/{conv_id}", get_canvas_page, methods=["GET"]),
        APIRoute("/canvas/{conv_id}/{tab_id}", get_canvas_page, methods=["GET"]),
        WebSocketRoute("/ws/chat", ws_chat),
        WebSocketRoute("/ws/terminal/{conv_id}/{tab_id}", ws_terminal),
    ]

    # Static file serving for web UI
    static_dir = Path(__file__).parent / "web" / "static"
    if static_dir.is_dir():

        async def serve_index(request: Request):
            return FileResponse(static_dir / "index.html", headers={"cache-control": "no-cache"})

        async def serve_favicon(request: Request):
            return FileResponse(static_dir / "icons" / "favicon.ico")

        routes.append(APIRoute("/", serve_index, methods=["GET"]))
        routes.append(APIRoute("/favicon.ico", serve_favicon, methods=["GET"], include_in_schema=False))
        routes.append(Mount("/static", RevalidatingStaticFiles(directory=str(static_dir)), name="static"))

    app = FastAPI(routes=routes)
    # Module-level handlers read deps off ``request.app.state``. Closures
    # currently capture them via the enclosing scope; as handlers migrate
    # out of `create_app` they switch to the state-based lookup.
    app.state.config = config
    app.state.event_bus = event_bus
    app.state.manager = manager
    app.state.app_ctx = app_ctx
    from .terminals import TerminalRegistry

    app.state.terminal_registry = TerminalRegistry(config)
    # Background vault reindex tasks, chained per vault-relative path.
    app.state.vault_index_tasks = {}

    # Wire terminal registry to manager if available
    if manager is not None:
        manager.terminal_registry = app.state.terminal_registry

    return app


_http_server = None  # uvicorn.Server instance, set by run_http_server
_http_app: FastAPI | None = None  # FastAPI app instance, set by run_http_server


async def run_http_server(config, event_bus, app_ctx=None, manager=None) -> None:
    """Start the HTTP server as an asyncio task."""
    global _http_server, _http_app
    import uvicorn

    app = create_app(config, event_bus, app_ctx=app_ctx, manager=manager)
    _http_app = app
    server_config = uvicorn.Config(
        app,
        host=config.http.host,
        port=config.http.port,
        log_level="info",
    )
    _http_server = uvicorn.Server(server_config)
    from .workspace_index import start_workspace_index_loop

    start_workspace_index_loop(config)
    # The host gauge strip polls this sampler. Started here, alongside the
    # other boot tasks, so its /proc/stat delta window covers the server's
    # whole life rather than being reset by each poll.
    from .host_metrics import get_sampler

    get_sampler().start()
    log.info(f"HTTP server starting on {config.http.host}:{config.http.port}")
    await _http_server.serve()


async def shutdown_http_server() -> None:
    """Gracefully shut down the HTTP server (avoids CancelledError tracebacks).

    Also kills any still-running terminal PTY sessions first — they're
    child processes of this server, not the OS session, and would
    otherwise be orphaned on restart/shutdown.
    """
    if _http_app is not None:
        reg = getattr(_http_app.state, "terminal_registry", None)
        if reg is not None:
            await reg.shutdown_all()
    from .host_metrics import get_sampler

    await get_sampler().stop()
    if _http_server is not None:
        _http_server.should_exit = True
