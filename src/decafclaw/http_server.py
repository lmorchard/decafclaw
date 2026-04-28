"""HTTP server — Starlette ASGI app for interactive callbacks and future web UI."""

import asyncio
import functools
import json
import logging
import os
import re
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, RedirectResponse
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles

from .mattermost_ui import get_token_registry
from .web.workspace_paths import (
    IMAGE_EXTENSIONS,
    detect_kind,
    is_readonly,
    is_secret,
    resolve_safe,
)

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
]

# Directories pruned from workspace_recent walks — they can grow huge and
# we don't want to stat every file inside on each request.
_WORKSPACE_RECENT_PRUNE_DIRS = frozenset({
    "conversations",
    ".schedule_last_run",
    "attachments",
})


def _get_username_or_401(request: Request) -> str | None:
    """Resolve the authenticated username from cookies, or None.

    Used by handlers that need the username outside the
    ``_authenticated`` decorator (e.g. the WebSocket entry point and
    raw-vault page serving). Returning ``None`` means "no valid
    session"; callers decide whether to 401 or return a redirect.
    """
    from .web.auth import get_current_user
    return get_current_user(request, request.app.state.config)


def _authenticated(handler):
    """Decorator: extracts the authenticated username, 401 if missing.

    Forwards to ``handler(request, username)``. Reads the active
    ``Config`` off ``request.app.state``, set up by ``create_app``.
    """
    @functools.wraps(handler)
    async def wrapper(request):
        username = _get_username_or_401(request)
        if not username:
            return JSONResponse({"error": "not authenticated"},
                                status_code=401)
        return await handler(request, username)
    return wrapper


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


def _prune_empty_parents(start: Path, stop_at: Path) -> None:
    """Remove empty parent directories starting at ``start``, bounded by ``stop_at``.

    Stops on the first non-empty parent or when it reaches ``stop_at``. Safe
    to call on a freshly-deleted file's parent chain.
    """
    stop_resolved = stop_at.resolve()
    cur = start
    while cur.resolve() != stop_resolved:
        try:
            cur.rmdir()
        except OSError:
            break
        cur = cur.parent


def _workspace_file_entry(resolved_path: Path, rel_path: str) -> dict:
    """Build the per-file response payload used by both list and recent."""
    stat = resolved_path.stat()
    return {
        "name": resolved_path.name,
        "path": rel_path,
        "size": stat.st_size,
        "modified": stat.st_mtime,
        "kind": detect_kind(resolved_path),
        "readonly": is_readonly(rel_path),
        "secret": is_secret(rel_path),
    }


def _can_write_as_text(path: Path) -> bool:
    """Return True if this path may be written as text by the Files-tab editor.

    For existing files: defers to ``detect_kind`` (rejects image/binary).
    For new files: rejects known image extensions; accepts known text and
    unknown extensions (the editor only produces text).
    """
    if path.exists():
        return detect_kind(path) == "text"
    ext = path.suffix.lower()
    if ext in IMAGE_EXTENSIONS:
        return False
    return True


def _vault_root(config) -> Path:
    return config.vault_root


def _resolve_vault_page(config, page_name: str):
    """Resolve a vault page name to a file path."""
    from .skills.vault.tools import resolve_page
    return resolve_page(config, page_name)


def _vault_source_type(config, filepath: Path) -> str:
    """Determine source type for a vault file."""
    from .skills.vault.tools import _source_type_for_path
    return _source_type_for_path(config, filepath)


def _collect_recent_workspace_files(
    workspace_root: Path,
) -> list[tuple[float, Path, str]]:
    """Walk the workspace and return up to 50 (mtime, path, rel_str) tuples.

    Pure helper; safe to call from ``asyncio.to_thread``. Prunes known-heavy
    subtrees (see ``_WORKSPACE_RECENT_PRUNE_DIRS``) during descent.
    """
    collected: list[tuple[float, Path, str]] = []
    workspace_resolved = workspace_root.resolve()
    for dirpath, dirnames, filenames in os.walk(workspace_root):
        # In-place prune so os.walk skips descent into these subtrees.
        dirnames[:] = [d for d in dirnames if d not in _WORKSPACE_RECENT_PRUNE_DIRS]
        for fname in filenames:
            fpath = Path(dirpath) / fname
            # Resolve symlinks and confirm the target still lives under the
            # workspace root before stat'ing (prevents leaking metadata from
            # symlinks that point outside the sandbox).
            try:
                resolved = fpath.resolve()
                rel = resolved.relative_to(workspace_resolved)
            except (OSError, ValueError):
                continue
            try:
                stat_result = resolved.stat()
            except OSError as exc:
                log.debug("workspace_recent: stat failed for %s: %s", resolved, exc)
                continue
            collected.append((stat_result.st_mtime, resolved, rel.as_posix()))
    collected.sort(key=lambda t: t[0], reverse=True)
    return collected[:50]


# -- Health + confirmation callback handlers ---------------------------------


async def health(request: Request) -> JSONResponse:
    """Liveness probe — returns the static health snapshot."""
    from .tools.health import get_health_data
    return JSONResponse(get_health_data(request.app.state.config))


async def handle_confirm(request: Request) -> JSONResponse:
    """Handle Mattermost interactive button callbacks for tool confirmation."""
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
        return JSONResponse({"error": "unauthorized"}, status_code=403)

    body = await request.json()
    context = body.get("context", {})

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
            conv_id, confirmation_id,
            approved=approved, always=always, add_pattern=add_pattern,
        )
    else:
        await event_bus.publish({
            "type": "tool_confirm_response",
            "context_id": context_id,
            "tool": tool_name,
            "approved": approved,
            **({"tool_call_id": tool_call_id} if tool_call_id else {}),
            **({"always": True} if always else {}),
            **({"add_pattern": True} if add_pattern else {}),
        })

    # Determine result label
    labels = {
        "approve": "\u2705 Approved",
        "always": "\u2705 Always approved",
        "add_pattern": "\U0001f4d3 Approved + pattern added",
        "deny": "\U0001f44e Denied",
    }
    label = labels.get(action, f"\u2753 Unknown action: {action}")

    # Return update response — removes buttons, shows result
    return JSONResponse({
        "update": {
            "message": f"{original_message}\n\n**Result:** {label}",
            "props": {"attachments": []},
        }
    })


async def handle_cancel(request: Request) -> JSONResponse:
    """Handle Mattermost interactive button callback for stop/cancel."""
    event_bus = request.app.state.event_bus
    manager = request.app.state.manager

    token = request.query_params.get("token", "")
    token_data = get_token_registry().consume(token)

    if not token_data:
        log.warning("Cancel callback rejected: invalid or expired token")
        return JSONResponse({"error": "unauthorized"}, status_code=403)

    conv_id = token_data.get("conv_id", "") or token_data.get("context_id", "")
    log.info(f"Cancel button pressed for conversation {conv_id[:8]}")

    if manager and conv_id:
        await manager.cancel_turn(conv_id)
    else:
        await event_bus.publish({
            "type": "cancel_turn",
            "conv_id": conv_id,
        })

    return JSONResponse({
        "update": {
            "message": "\u23f9\ufe0f Stopped",
            "props": {"attachments": []},
        }
    })


# -- Auth routes -------------------------------------------------------------


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
        "decafclaw_session", token,
        httponly=True, samesite="lax", max_age=30 * 24 * 3600,
    )
    return response


async def auth_logout(request: Request) -> JSONResponse:
    """Clear the session cookie."""
    response = JSONResponse({"ok": True})
    response.delete_cookie("decafclaw_session")
    return response


async def auth_me(request: Request) -> JSONResponse:
    """Return the current authenticated user."""
    username = _get_username_or_401(request)
    if not username:
        return JSONResponse({"error": "not authenticated"}, status_code=401)
    return JSONResponse({"username": username})


# -- Conversation routes ------------------------------------------------------


@_authenticated
async def list_conversations(request: Request, username: str) -> JSONResponse:
    """List conversations and subfolders for a specific folder.

    Query params:
        folder — folder path (default: top-level)

    Returns ``{folder, folders, conversations}`` mirroring vault_list pattern.
    """
    from .web.conversation_folders import ConversationFolderIndex
    from .web.conversations import ConversationIndex
    config = request.app.state.config
    folder_param = request.query_params.get("folder", "").strip()
    err = _validate_folder_param(folder_param)
    if err:
        return JSONResponse({"error": err}, status_code=400)
    index = ConversationIndex(config)
    folder_index = ConversationFolderIndex(config, username)
    convs = index.list_for_user(username)
    assignments = await folder_index.get_all_assignments()
    filtered = [
        c for c in convs
        if assignments.get(c.conv_id, "") == folder_param
    ]
    child_names = await folder_index.list_folders(folder_param)
    folders: list[dict] = [
        {"name": name, "path": f"{folder_param}/{name}" if folder_param else name}
        for name in child_names
    ]
    if not folder_param:
        folders.append({"name": "Archived", "path": "_archived", "virtual": True})
        folders.append({"name": "System", "path": "_system", "virtual": True})
    return JSONResponse({
        "folder": folder_param,
        "folders": folders,
        "conversations": [c.to_dict() for c in filtered],
    })


@_authenticated
async def list_archived_conversations(request: Request, username: str) -> JSONResponse:
    """List archived conversations, optionally filtered by folder."""
    from .web.conversation_folders import ConversationFolderIndex
    from .web.conversations import ConversationIndex
    config = request.app.state.config
    folder_param = request.query_params.get("folder", "").strip()
    err = _validate_folder_param(folder_param)
    if err:
        return JSONResponse({"error": err}, status_code=400)
    index = ConversationIndex(config)
    folder_index = ConversationFolderIndex(config, username)
    convs = index.list_for_user(username, include_archived=True)
    archived = [c for c in convs if c.archived]
    assignments = await folder_index.get_all_assignments()
    filtered = [
        c for c in archived
        if assignments.get(c.conv_id, "") == folder_param
    ]
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
            rest = folder[len(prefix):]
            if rest:
                child_names.add(rest.split("/")[0])
    folders = [
        {"name": name, "path": f"{folder_param}/{name}" if folder_param else name}
        for name in sorted(child_names)
    ]
    return JSONResponse({
        "folder": folder_param,
        "folders": folders,
        "conversations": [c.to_dict() for c in filtered],
    })


@_authenticated
async def list_system_conversations(request: Request, username: str) -> JSONResponse:
    """List system conversations, grouped by type sub-folders."""
    from .web.conversations import list_system_conversations as list_sys
    config = request.app.state.config
    folder_param = request.query_params.get("folder", "").strip()
    all_sys = list_sys(config, username=username)
    if not folder_param:
        folders = [
            {"name": "Heartbeat", "path": "heartbeat"},
            {"name": "Schedule", "path": "schedule"},
            {"name": "Delegated", "path": "delegated"},
        ]
        return JSONResponse({
            "folder": "",
            "folders": folders,
            "conversations": [],
        })
    valid_types = {"heartbeat", "schedule", "delegated"}
    if folder_param not in valid_types:
        return JSONResponse({"error": "invalid system folder"}, status_code=400)
    filtered = [c for c in all_sys if c.get("conv_type") == folder_param]
    return JSONResponse({
        "folder": folder_param,
        "folders": [],
        "conversations": filtered,
    })


@_authenticated
async def create_conversation(request: Request, username: str) -> JSONResponse:
    """Create a new conversation, optionally in a folder with a model."""
    config = request.app.state.config
    body = await request.json()
    folder = str(body.get("folder", "")).strip()
    model_name = str(body.get("model", body.get("effort", ""))).strip()
    if model_name and model_name not in config.model_configs:
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
        append_message(config, conv.conv_id,
                       {"role": "model", "content": model_name})
    result = conv.to_dict()
    if folder:
        result["folder"] = folder
    if model_name:
        result["model"] = model_name
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


@_authenticated
async def rename_conversation(request: Request, username: str) -> JSONResponse:
    """Rename and/or move a conversation to a different folder."""
    from .web.conversations import ConversationIndex
    config = request.app.state.config
    conv_id = request.path_params["id"]
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
        updated = index.rename(conv_id, title)
        if not updated:
            return JSONResponse({"error": "not found"}, status_code=404)
        conv = updated
    if folder is not None:
        ok, err = await folder_index.set_folder(conv_id, folder)
        if not ok:
            return JSONResponse({"error": err}, status_code=400)
    result = conv.to_dict()
    if folder is not None:
        result["folder"] = folder
    return JSONResponse(result)


@_authenticated
async def get_conversation_history(request: Request, username: str) -> JSONResponse:
    """Load paginated conversation history."""
    from .web.conversations import ConversationIndex
    config = request.app.state.config
    conv_id = request.path_params["id"]
    index = ConversationIndex(config)
    conv = index.get(conv_id)
    if not conv or conv.user_id != username:
        return JSONResponse({"error": "not found"}, status_code=404)
    limit = int(request.query_params.get("limit", "50"))
    before = request.query_params.get("before", "")
    messages, has_more = index.load_history(conv_id, limit=limit, before=before)
    return JSONResponse({"messages": messages, "has_more": has_more})


@_authenticated
async def get_context_diagnostics(request: Request, username: str) -> JSONResponse:
    """Return context composer diagnostics for a conversation."""
    from .context_composer import read_context_sidecar
    from .web.conversations import ConversationIndex
    config = request.app.state.config
    conv_id = request.path_params["id"]
    index = ConversationIndex(config)
    conv = index.get(conv_id)
    if not conv or conv.user_id != username:
        return JSONResponse({"error": "not found"}, status_code=404)
    data = read_context_sidecar(config, conv_id)
    if data is None:
        return JSONResponse({"error": "no context data"}, status_code=404)
    return JSONResponse(data)


@_authenticated
async def archive_conversation(request: Request, username: str) -> JSONResponse:
    """Archive a conversation (hide from list, keep data)."""
    from .web.conversations import ConversationIndex
    config = request.app.state.config
    conv_id = request.path_params["id"]
    index = ConversationIndex(config)
    conv = index.get(conv_id)
    if not conv or conv.user_id != username:
        return JSONResponse({"error": "not found"}, status_code=404)
    index.archive(conv_id)
    return JSONResponse({"ok": True})


@_authenticated
async def unarchive_conversation(request: Request, username: str) -> JSONResponse:
    """Unarchive a conversation (restore to active list)."""
    from .web.conversations import ConversationIndex
    config = request.app.state.config
    conv_id = request.path_params["id"]
    index = ConversationIndex(config)
    conv = index.get(conv_id)
    if not conv or conv.user_id != username:
        return JSONResponse({"error": "not found"}, status_code=404)
    index.unarchive(conv_id)
    return JSONResponse({"ok": True})


@_authenticated
async def delete_conversation(request: Request, username: str) -> JSONResponse:
    """Permanently delete a conversation and all associated files."""
    from .attachments import delete_conversation_uploads
    from .web.conversation_folders import ConversationFolderIndex
    from .web.conversations import ConversationIndex
    config = request.app.state.config
    conv_id = request.path_params["id"]
    index = ConversationIndex(config)
    conv = index.get(conv_id)
    if not conv or conv.user_id != username:
        return JSONResponse({"error": "not found"}, status_code=404)

    # Delete sidecar files on disk (with path sandboxing).
    conv_dir = config.workspace_path / "conversations"
    for suffix in [".jsonl", ".compacted.jsonl", ".context.json", ".decisions.json", ".notes.md"]:
        path = (conv_dir / f"{conv_id}{suffix}").resolve()
        if not path.is_relative_to(conv_dir.resolve()):
            continue  # path traversal guard
        if path.exists():
            path.unlink()

    delete_conversation_uploads(config, conv_id)
    index.delete(conv_id)
    folder_index = ConversationFolderIndex(config, username)
    await folder_index.remove_assignment(conv_id)
    return JSONResponse({"ok": True})


# -- Conversation folder routes ----------------------------------------------


@_authenticated
async def create_conv_folder(request: Request, username: str) -> JSONResponse:
    """Create a conversation folder."""
    from .web.conversation_folders import ConversationFolderIndex
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


@_authenticated
async def delete_conv_folder(request: Request, username: str) -> JSONResponse:
    """Delete an empty conversation folder."""
    from .web.conversation_folders import ConversationFolderIndex
    config = request.app.state.config
    path = request.path_params.get("path", "")
    if not path:
        return JSONResponse({"error": "path required"}, status_code=400)
    folder_index = ConversationFolderIndex(config, username)
    ok, err = await folder_index.delete_folder(path)
    if not ok:
        status = 409 if "contains" in err else 404
        return JSONResponse({"error": err}, status_code=status)
    return JSONResponse({"ok": True})


@_authenticated
async def rename_conv_folder(request: Request, username: str) -> JSONResponse:
    """Rename/move a conversation folder. Merges if target exists."""
    from .web.conversation_folders import ConversationFolderIndex
    config = request.app.state.config
    old_path = request.path_params.get("path", "")
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


@_authenticated
async def list_notifications(request: Request, username: str) -> JSONResponse:
    """Return inbox records newest first, with a joined ``read`` bool."""
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
    return JSONResponse({
        "records": [
            {**r.to_dict(), "read": r.id in read_ids}
            for r in records
        ],
        "has_more": has_more,
    })


@_authenticated
async def notifications_unread_count(request: Request, username: str) -> JSONResponse:
    """Return ``{"count": N}`` — called frequently, stays cheap."""
    from . import notifications as notifs
    return JSONResponse({"count": notifs.unread_count(request.app.state.config)})


@_authenticated
async def notifications_mark_read(request: Request, username: str) -> JSONResponse:
    """Mark a single notification read. Idempotent."""
    from . import notifications as notifs
    config = request.app.state.config
    event_bus = request.app.state.event_bus
    record_id = request.path_params.get("id", "")
    if not record_id:
        return JSONResponse({"error": "id required"}, status_code=400)
    await notifs.mark_read(config, record_id, event_bus=event_bus)
    return JSONResponse({"ok": True})


@_authenticated
async def notifications_mark_all_read(request: Request, username: str) -> JSONResponse:
    """Mark all currently-visible notifications read."""
    from . import notifications as notifs
    await notifs.mark_all_read(
        request.app.state.config, event_bus=request.app.state.event_bus,
    )
    return JSONResponse({"ok": True})


# -- Workspace routes ---------------------------------------------------------


@_authenticated
async def serve_workspace_file(request: Request, username: str):
    """Serve a file from the agent workspace (authenticated, read-only)."""
    import mimetypes
    config = request.app.state.config
    file_path = request.path_params.get("path", "")
    if not file_path:
        return JSONResponse({"error": "path required"}, status_code=400)
    if is_secret(file_path):
        return JSONResponse({"error": "secret path"}, status_code=403)
    workspace = config.workspace_path.resolve()
    resolved = (workspace / file_path).resolve()
    if not str(resolved).startswith(str(workspace)):
        return JSONResponse({"error": "path outside workspace"}, status_code=403)
    if not resolved.is_file():
        return JSONResponse({"error": "not found"}, status_code=404)
    content_type = mimetypes.guess_type(str(resolved))[0] or "application/octet-stream"
    # Inline display only for safe image types; everything else (incl. SVG)
    # is forced to download to prevent XSS.
    safe_inline = content_type.startswith("image/") and content_type != "image/svg+xml"
    headers = {"X-Content-Type-Options": "nosniff"}
    if not safe_inline:
        headers["Content-Disposition"] = f'attachment; filename="{resolved.name}"'
    return FileResponse(str(resolved), media_type=content_type, headers=headers)


@_authenticated
async def workspace_list(request: Request, username: str) -> JSONResponse:
    """List workspace files and subfolders for a given folder.

    Returns ``{folder, folders, files}``. Folders alphabetical first, then
    files alphabetical. Dotfiles included; the frontend hides what it wants.
    """
    config = request.app.state.config
    workspace = config.workspace_path
    if not workspace.is_dir():
        return JSONResponse({"folder": "", "folders": [], "files": []})

    folder_param = request.query_params.get("folder", "").strip()
    target_dir = resolve_safe(workspace, folder_param)
    if target_dir is None or not target_dir.is_dir():
        return JSONResponse({"error": "folder not found"}, status_code=404)

    workspace_resolved = workspace.resolve()
    folders: list[dict] = []
    files: list[dict] = []
    for child in target_dir.iterdir():
        try:
            rel = child.resolve().relative_to(workspace_resolved)
        except ValueError:
            continue  # symlink escape
        rel_str = rel.as_posix()
        if child.is_dir():
            folders.append({"name": child.name, "path": rel_str})
        elif child.is_file():
            try:
                files.append(_workspace_file_entry(child, rel_str))
            except OSError as exc:
                log.debug("workspace_list: stat failed for %s: %s", child, exc)

    folders.sort(key=lambda f: f["name"].lower())
    files.sort(key=lambda f: f["name"].lower())
    return JSONResponse({
        "folder": folder_param,
        "folders": folders,
        "files": files,
    })


@_authenticated
async def workspace_read_json(request: Request, username: str) -> JSONResponse:
    """Return text file content as JSON for the Files-tab editor."""
    config = request.app.state.config
    file_path = request.path_params.get("path", "")
    if not file_path:
        return JSONResponse({"error": "path required"}, status_code=400)
    if is_secret(file_path):
        return JSONResponse({"error": "secret path"}, status_code=403)
    workspace = config.workspace_path
    resolved = resolve_safe(workspace, file_path)
    if resolved is None or not resolved.is_file():
        return JSONResponse({"error": "not found"}, status_code=404)
    kind = detect_kind(resolved)
    if kind != "text":
        return JSONResponse({"error": "not text"}, status_code=415)
    try:
        content = resolved.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        log.debug("workspace_read_json: read failed for %s: %s", resolved, exc)
        return JSONResponse({"error": "read failed"}, status_code=415)
    stat = resolved.stat()
    return JSONResponse({
        "content": content,
        "modified": stat.st_mtime,
        "readonly": is_readonly(file_path),
    })


async def _workspace_rename(
    workspace: Path,
    old_file: Path,
    old_rel: str,
    rename_to: str,
) -> JSONResponse:
    """Rename/move a workspace file.

    Secret/readonly on either side → 403. Missing source → 404. Target
    already exists → 409. Path-escape on either side → 404. Creates
    intermediate destination dirs; prunes empty source parent dirs after.
    """
    if not isinstance(rename_to, str) or not rename_to.strip():
        return JSONResponse({"error": "rename_to must be a non-empty string"},
                            status_code=400)
    rename_to = rename_to.strip()
    # Secret/readonly checks BEFORE resolving so 403 vs 404 don't leak existence.
    if is_secret(old_rel) or is_secret(rename_to):
        return JSONResponse({"error": "secret path"}, status_code=403)
    if is_readonly(old_rel) or is_readonly(rename_to):
        return JSONResponse({"error": "readonly path"}, status_code=403)
    new_file = resolve_safe(workspace, rename_to)
    if new_file is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    if not old_file.is_file():
        return JSONResponse({"error": "not found"}, status_code=404)
    if new_file.exists():
        return JSONResponse({"error": "target already exists"}, status_code=409)
    new_file.parent.mkdir(parents=True, exist_ok=True)
    old_file.rename(new_file)
    workspace_resolved = workspace.resolve()
    _prune_empty_parents(old_file.parent, workspace)
    stat = new_file.stat()
    rel = new_file.relative_to(workspace_resolved)
    return JSONResponse({
        "ok": True,
        "path": rel.as_posix(),
        "modified": stat.st_mtime,
    })


@_authenticated
async def workspace_write(request: Request, username: str) -> JSONResponse:
    """Create or update a workspace text file, or rename if ``rename_to`` set."""
    config = request.app.state.config
    file_path = request.path_params.get("path", "")
    if not file_path:
        return JSONResponse({"error": "path required"}, status_code=400)
    workspace = config.workspace_path
    workspace.mkdir(parents=True, exist_ok=True)
    resolved = resolve_safe(workspace, file_path)
    if resolved is None:
        return JSONResponse({"error": "not found"}, status_code=404)

    rename_to = request.query_params.get("rename_to")
    if rename_to is not None:
        return await _workspace_rename(workspace, resolved, file_path, rename_to)

    if is_secret(file_path):
        return JSONResponse({"error": "secret path"}, status_code=403)
    if is_readonly(file_path):
        return JSONResponse({"error": "readonly path"}, status_code=403)
    if not _can_write_as_text(resolved):
        return JSONResponse({"error": "not text"}, status_code=415)

    try:
        body = await request.json()
    except Exception as exc:
        log.debug("workspace_write: invalid JSON body: %s", exc)
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    content = body.get("content")
    if content is None or not isinstance(content, str):
        return JSONResponse({"error": "content (string) required"}, status_code=400)

    modified = body.get("modified")
    if modified is not None:
        try:
            modified = float(modified)
        except (TypeError, ValueError):
            return JSONResponse({"error": "modified must be a number"}, status_code=400)
        if resolved.exists():
            file_mtime = resolved.stat().st_mtime
            if abs(file_mtime - modified) > 1e-3:
                return JSONResponse(
                    {"error": "conflict", "modified": file_mtime},
                    status_code=409,
                )

    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(content, encoding="utf-8")
    return JSONResponse({"ok": True, "modified": resolved.stat().st_mtime})


@_authenticated
async def workspace_delete(request: Request, username: str) -> JSONResponse:
    """Delete a workspace file or empty folder, prune empty parents."""
    config = request.app.state.config
    file_path = request.path_params.get("path", "")
    if not file_path:
        return JSONResponse({"error": "path required"}, status_code=400)
    if is_secret(file_path):
        return JSONResponse({"error": "secret path"}, status_code=403)
    if is_readonly(file_path):
        return JSONResponse({"error": "readonly path"}, status_code=403)
    workspace = config.workspace_path
    resolved = resolve_safe(workspace, file_path)
    if resolved is None or not resolved.exists():
        return JSONResponse({"error": "not found"}, status_code=404)

    if resolved.is_dir():
        try:
            resolved.rmdir()
        except OSError as exc:
            log.debug("workspace_delete: rmdir failed for %s: %s", resolved, exc)
            return JSONResponse({"error": "not empty"}, status_code=409)
    elif resolved.is_file():
        resolved.unlink()
    else:
        return JSONResponse({"error": "not found"}, status_code=404)

    _prune_empty_parents(resolved.parent, workspace)
    return JSONResponse({"ok": True})


@_authenticated
async def workspace_create(request: Request, username: str) -> JSONResponse:
    """Create a new workspace file or folder.

    Body: ``{"type": "file"|"folder", "path": str, "content"?: str}``.
    Secret / readonly paths → 403. Path-escape → 404. Already-exists → 409.
    """
    config = request.app.state.config
    try:
        body = await request.json()
    except Exception as exc:
        log.debug("workspace_create: invalid JSON body: %s", exc)
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)

    kind = body.get("type")
    if kind not in ("file", "folder"):
        return JSONResponse({"error": "type must be 'file' or 'folder'"},
                            status_code=400)
    rel_path = body.get("path")
    if not isinstance(rel_path, str) or not rel_path.strip():
        return JSONResponse({"error": "path (string) required"}, status_code=400)
    rel_path = rel_path.strip()

    workspace = config.workspace_path
    workspace.mkdir(parents=True, exist_ok=True)
    resolved = resolve_safe(workspace, rel_path)
    if resolved is None:
        return JSONResponse({"error": "not found"}, status_code=404)

    if is_secret(rel_path):
        return JSONResponse({"error": "secret path"}, status_code=403)
    if is_readonly(rel_path):
        return JSONResponse({"error": "readonly path"}, status_code=403)

    if kind == "folder":
        try:
            resolved.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            return JSONResponse({"error": "folder already exists"}, status_code=409)
        return JSONResponse({"ok": True, "path": rel_path})

    # kind == "file"
    if resolved.exists():
        return JSONResponse({"error": "file already exists"}, status_code=409)
    content = body.get("content", "")
    if not isinstance(content, str):
        return JSONResponse({"error": "content must be a string"}, status_code=400)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(content, encoding="utf-8")
    return JSONResponse({
        "ok": True,
        "path": rel_path,
        "modified": resolved.stat().st_mtime,
    })


@_authenticated
async def workspace_recent(request: Request, username: str) -> JSONResponse:
    """Return up to 50 workspace files sorted by mtime descending."""
    config = request.app.state.config
    workspace = config.workspace_path
    if not workspace.is_dir():
        return JSONResponse({"files": []})

    workspace_resolved = workspace.resolve()
    collected = await asyncio.to_thread(
        _collect_recent_workspace_files, workspace_resolved
    )
    files: list[dict] = []
    for _mtime, fpath, rel_str in collected:
        try:
            files.append(_workspace_file_entry(fpath, rel_str))
        except OSError as exc:
            log.debug("workspace_recent: entry build failed for %s: %s", fpath, exc)
    return JSONResponse({"files": files})


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
        if ".." in folder_param or folder_param.startswith("/"):
            return JSONResponse({"error": "invalid folder path"}, status_code=400)
        target_dir = (vault / folder_param).resolve()
        if not target_dir.is_relative_to(vault.resolve()):
            return JSONResponse({"error": "path outside vault"}, status_code=403)
        if not target_dir.is_dir():
            return JSONResponse({"error": "folder not found"}, status_code=404)
    else:
        target_dir = vault.resolve()

    vault_resolved = vault.resolve()
    pages = []
    for child in target_dir.iterdir():
        if child.is_file() and child.suffix == ".md":
            if not child.resolve().is_relative_to(vault_resolved):
                continue
            stat = child.stat()
            rel = child.relative_to(vault_resolved)
            pages.append({
                "title": child.stem,
                "path": str(rel.with_suffix("")),
                "folder": folder_param,
                "modified": stat.st_mtime,
            })
    pages.sort(key=lambda p: p["title"].lower())

    folders = []
    for child in sorted(target_dir.iterdir(), key=lambda c: c.name.lower()):
        if child.is_dir() and not child.name.startswith('.'):
            rel = child.relative_to(vault_resolved)
            folders.append({"name": child.name, "path": str(rel)})

    return JSONResponse({
        "folder": folder_param,
        "folders": folders,
        "pages": pages,
    })


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
    if not agent_resolved.is_relative_to(vault_resolved):
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
        if not md_file.resolve().is_relative_to(agent_resolved):
            continue
        # Skip hidden directories (.obsidian, .git, .trash, etc.)
        if any(part.startswith('.')
               for part in md_file.relative_to(agent_resolved).parts[:-1]):
            continue
        rel = md_file.relative_to(vault_resolved)
        pages.append({
            "title": md_file.stem,
            "path": str(rel.with_suffix("")),
            "folder": str(rel.parent) if str(rel.parent) != "." else "",
            "modified": md_file.stat().st_mtime,
        })

    pages.sort(key=lambda p: p["modified"], reverse=True)
    return JSONResponse({"pages": pages[:limit]})


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
    return JSONResponse({
        "title": resolved.stem,
        "path": str(rel.with_suffix("")),
        "content": content,
        "modified": stat.st_mtime,
    })


async def _vault_rename(
    config, vault: Path, old_file: Path, old_name: str, rename_to: str,
) -> JSONResponse:
    """Rename/move a vault page; re-indexes embeddings on success."""
    if not isinstance(rename_to, str) or not rename_to.strip():
        return JSONResponse({"error": "rename_to must be a non-empty string"},
                            status_code=400)
    rename_to = rename_to.strip()
    if ".." in rename_to or rename_to.startswith("/"):
        return JSONResponse({"error": "invalid rename path"}, status_code=400)
    rename_path = Path(rename_to)
    if not rename_path.name:
        return JSONResponse({"error": "invalid rename path"}, status_code=400)
    if rename_path.suffix.lower() == ".md":
        rename_path = rename_path.with_suffix("")
    new_file = (vault / f"{rename_path}.md").resolve()
    if not new_file.is_relative_to(vault.resolve()):
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
    try:
        from .embeddings import delete_entries, index_entry
        old_rel = f"{old_name}.md"
        old_source_type = _vault_source_type(config, old_file)
        delete_entries(config, old_rel, source_type=old_source_type)
        new_rel = str(new_file.relative_to(vault_resolved))
        new_content = new_file.read_text(encoding="utf-8")
        new_source_type = _vault_source_type(config, new_file)
        await index_entry(config, new_rel, new_content, source_type=new_source_type)
    except Exception as e:
        log.warning(f"Failed to re-index after rename '{old_name}' -> '{rename_to}': {e}")
    stat = new_file.stat()
    rel = new_file.relative_to(vault_resolved)
    folder = str(rel.parent) if rel.parent != Path(".") else ""
    return JSONResponse({
        "ok": True,
        "title": new_file.stem,
        "path": str(rel.with_suffix("")),
        "folder": folder,
        "modified": stat.st_mtime,
    })


@_authenticated
async def vault_write(request: Request, username: str) -> JSONResponse:
    """Create or update a vault page, or rename/move it."""
    config = request.app.state.config
    page_name = request.path_params.get("page", "")
    if not page_name:
        return JSONResponse({"error": "page name required"}, status_code=400)
    if ".." in page_name or page_name.startswith("/"):
        return JSONResponse({"error": "invalid page path"}, status_code=400)
    vault = _vault_root(config)
    vault.mkdir(parents=True, exist_ok=True)
    target = (vault / f"{page_name}.md").resolve()
    if not target.is_relative_to(vault.resolve()):
        return JSONResponse({"error": "path outside vault directory"}, status_code=403)
    body = await request.json()

    rename_to = body.get("rename_to")
    if rename_to is not None:
        if "content" in body:
            return JSONResponse(
                {"error": "cannot combine rename_to with content"},
                status_code=400,
            )
        return await _vault_rename(config, vault, target, page_name, rename_to)

    content = body.get("content")
    if content is None or not isinstance(content, str):
        return JSONResponse({"error": "content (string) required"}, status_code=400)
    modified = body.get("modified")
    if modified is not None:
        try:
            modified = float(modified)
        except (TypeError, ValueError):
            return JSONResponse({"error": "modified must be a number"}, status_code=400)
        if target.exists():
            file_mtime = target.stat().st_mtime
            if file_mtime > modified + 1.0:
                return JSONResponse(
                    {"error": "conflict", "server_modified": file_mtime},
                    status_code=409,
                )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    source_type = _vault_source_type(config, target)
    try:
        from .embeddings import delete_entries, index_entry
        rel_path = str(target.relative_to(vault.resolve()))
        delete_entries(config, rel_path, source_type=source_type)
        await index_entry(config, rel_path, content, source_type=source_type)
    except Exception as e:
        log.warning(f"Failed to index vault page '{page_name}': {e}")
    return JSONResponse({"ok": True, "modified": target.stat().st_mtime})


@_authenticated
async def vault_create(request: Request, username: str) -> JSONResponse:
    """Create a new vault page."""
    config = request.app.state.config
    body = await request.json()
    name = body.get("name")
    if not name or not isinstance(name, str):
        return JSONResponse({"error": "name (string) required"}, status_code=400)
    name = name.strip()
    if not name:
        return JSONResponse({"error": "name (string) required"}, status_code=400)
    if ".." in name or name.startswith("/"):
        return JSONResponse({"error": "invalid page name"}, status_code=400)
    vault = _vault_root(config)
    vault.mkdir(parents=True, exist_ok=True)
    target = (vault / f"{name}.md").resolve()
    if not target.is_relative_to(vault.resolve()):
        return JSONResponse({"error": "path outside vault directory"}, status_code=403)
    if target.exists():
        return JSONResponse({"error": "page already exists"}, status_code=409)
    content = body.get("content")
    if content is None or not isinstance(content, str):
        content = f"# {Path(name).stem}\n"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    source_type = _vault_source_type(config, target)
    try:
        from .embeddings import index_entry
        rel_path = str(target.relative_to(vault.resolve()))
        await index_entry(config, rel_path, content, source_type=source_type)
    except Exception as e:
        log.warning(f"Failed to index new vault page '{name}': {e}")
    return JSONResponse({"ok": True, "page": name, "modified": target.stat().st_mtime})


@_authenticated
async def vault_create_folder(request: Request, username: str) -> JSONResponse:
    """Create a new empty folder in the vault."""
    config = request.app.state.config
    body = await request.json()
    folder = body.get("folder")
    if not folder or not isinstance(folder, str):
        return JSONResponse({"error": "folder (string) required"}, status_code=400)
    folder = folder.strip()
    if not folder:
        return JSONResponse({"error": "folder (string) required"}, status_code=400)
    if ".." in folder or folder.startswith("/"):
        return JSONResponse({"error": "invalid folder path"}, status_code=400)
    vault = _vault_root(config)
    target = (vault / folder).resolve()
    if not target.is_relative_to(vault.resolve()):
        return JSONResponse({"error": "path outside vault directory"}, status_code=403)
    if target.exists():
        return JSONResponse({"error": "folder already exists"}, status_code=409)
    target.mkdir(parents=True, exist_ok=True)
    return JSONResponse({"ok": True, "folder": folder})


@_authenticated
async def vault_delete(request: Request, username: str) -> JSONResponse:
    """Delete a vault page."""
    config = request.app.state.config
    page_name = request.path_params.get("page", "")
    if not page_name:
        return JSONResponse({"error": "page name required"}, status_code=400)
    if ".." in page_name or page_name.startswith("/"):
        return JSONResponse({"error": "invalid page path"}, status_code=400)
    vault = _vault_root(config)
    target = (vault / f"{page_name}.md").resolve()
    if not target.is_relative_to(vault.resolve()):
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
    return JSONResponse({"ok": True})


async def serve_vault_page(request: Request):
    """Serve the vault page HTML shell."""
    username = _get_username_or_401(request)
    if not username:
        return JSONResponse({"error": "not authenticated"}, status_code=401)
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
        return JSONResponse({"error": "multipart form parsing unavailable"},
                            status_code=400)
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
                return (config.workspace_path / path_str.removeprefix("workspace/"),
                        f["scope"])
            return config.agent_path / path_str, f["scope"]
    if re.match(r"^schedules/[^/]+\.md$", path_str):
        return config.agent_path / path_str, "admin"
    if re.match(r"^workspace/schedules/[^/]+\.md$", path_str):
        return (config.workspace_path / path_str.removeprefix("workspace/"),
                "workspace")
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
        result.append({
            "name": f["name"],
            "path": f["path"],
            "description": f["description"],
            "scope": f["scope"],
            "modified": modified,
            "exists": exists,
        })
    for scope, base, prefix in [
        ("admin", config.agent_path, "schedules"),
        ("workspace", config.workspace_path, "workspace/schedules"),
    ]:
        sched_dir = base / "schedules"
        if sched_dir.is_dir():
            for p in sorted(sched_dir.glob("*.md")):
                stat = p.stat()
                result.append({
                    "name": p.name,
                    "path": f"{prefix}/{p.name}",
                    "description": "Scheduled task",
                    "scope": scope,
                    "modified": stat.st_mtime,
                    "exists": True,
                })
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
    return JSONResponse({
        "content": content,
        "modified": modified,
        "name": Path(path_str).name,
        "default": is_default,
    })


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
            file_mtime = fpath.stat().st_mtime
            if file_mtime > modified + 1.0:
                return JSONResponse(
                    {"error": "conflict", "server_modified": file_mtime},
                    status_code=409,
                )
    fpath.parent.mkdir(parents=True, exist_ok=True)
    fpath.write_text(content, encoding="utf-8")
    return JSONResponse({"ok": True, "modified": fpath.stat().st_mtime})


# -- WebSocket adapter --------------------------------------------------------


async def ws_chat(websocket):
    """WebSocket entry point — defers to the gateway in `web/websocket.py`."""
    from .web.websocket import websocket_chat
    state = websocket.app.state
    await websocket_chat(
        websocket, state.config, state.event_bus, state.app_ctx,
        manager=state.manager,
    )


# -- Widget routes ------------------------------------------------------------


@_authenticated
async def list_widgets(request: Request, username: str) -> JSONResponse:
    """Return the widget catalog with cache-busted js URLs."""
    from .widgets import get_widget_registry
    registry = get_widget_registry()
    if registry is None:
        return JSONResponse({"widgets": []})
    out = []
    for d in registry.list():
        out.append({
            "name": d.name,
            "tier": d.tier,
            "description": d.description,
            "modes": d.modes,
            "accepts_input": d.accepts_input,
            "data_schema": d.data_schema,
            "js_url": (f"/widgets/{d.tier}/{d.name}/widget.js"
                       f"?v={int(d.mtime * 1000)}"),
        })
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
        log.warning(
            "widget %r resolves to %s outside tier root %s — refusing",
            name, resolved, desc.tier_root)
        return JSONResponse({"error": "widget not found"}, status_code=404)
    return FileResponse(
        str(resolved),
        media_type="application/javascript",
        headers={"X-Content-Type-Options": "nosniff"})


# -- Canvas routes ------------------------------------------------------------


def _user_owns_conv(config, conv_id: str, username: str) -> bool:
    """Authorization gate for canvas routes — caller must own the conv."""
    from .web.conversations import ConversationIndex
    index = ConversationIndex(config)
    conv = index.get(conv_id)
    return bool(conv and conv.user_id == username)


@_authenticated
async def get_canvas_state(request: Request, username: str) -> JSONResponse:
    """Load current canvas state for a conversation."""
    from . import canvas as canvas_mod
    config = request.app.state.config
    conv_id = request.path_params.get("conv_id", "")
    if not _is_safe_conv_id(conv_id):
        return JSONResponse({"error": "invalid conv_id"}, status_code=400)
    if not _user_owns_conv(config, conv_id, username):
        return JSONResponse({"error": "not found"}, status_code=404)
    state = canvas_mod.read_canvas_state(config, conv_id)
    return JSONResponse(state)


@_authenticated
async def post_canvas_set(request: Request, username: str) -> JSONResponse:
    """Push a widget to the canvas (used by 'Open in Canvas' button)."""
    from . import canvas as canvas_mod
    config = request.app.state.config
    manager = request.app.state.manager
    conv_id = request.path_params.get("conv_id", "")
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
    emit = manager.emit if manager else None
    result = await canvas_mod.set_canvas(
        config, conv_id, widget_type, data, label=label, emit=emit,
    )
    if not result.ok:
        return JSONResponse({"error": result.error}, status_code=400)
    return JSONResponse({"ok": True, "text": result.text})


@_authenticated
async def get_canvas_page(request: Request, username: str):
    """Serve the standalone canvas HTML page."""
    from starlette.responses import Response
    config = request.app.state.config
    conv_id = request.path_params.get("conv_id", "")
    if not _is_safe_conv_id(conv_id):
        return Response("Invalid conversation id", status_code=400)
    if not _user_owns_conv(config, conv_id, username):
        return Response("Not found", status_code=404)
    html_path = Path(__file__).parent / "web" / "static" / "canvas-page.html"
    return Response(html_path.read_text(), media_type="text/html")


# -- Wiki redirect ------------------------------------------------------------


def _redirect_wiki_to_vault(request: Request) -> RedirectResponse:
    """Legacy /wiki/* → /vault/* redirect."""
    return RedirectResponse(
        f"/vault/{request.path_params['page']}", status_code=301,
    )


def create_app(config, event_bus, app_ctx=None, manager=None) -> Starlette:
    """Wire up the Starlette ASGI app — handlers live at module level
    and read deps off ``request.app.state`` (populated below).
    """

    routes = [
        Route("/health", health, methods=["GET"]),
        Route("/actions/confirm", handle_confirm, methods=["POST"]),
        Route("/actions/cancel", handle_cancel, methods=["POST"]),
        Route("/api/auth/login", auth_login, methods=["POST"]),
        Route("/api/auth/logout", auth_logout, methods=["POST"]),
        Route("/api/auth/me", auth_me, methods=["GET"]),
        Route("/api/conversations", list_conversations, methods=["GET"]),
        Route("/api/conversations/archived", list_archived_conversations, methods=["GET"]),
        Route("/api/conversations/system", list_system_conversations, methods=["GET"]),
        Route("/api/conversations", create_conversation, methods=["POST"]),
        Route("/api/conversations/{id}", get_conversation, methods=["GET"]),
        Route("/api/conversations/{id}", rename_conversation, methods=["PATCH"]),
        Route("/api/conversations/{id}/history", get_conversation_history, methods=["GET"]),
        Route("/api/conversations/{id}/context", get_context_diagnostics, methods=["GET"]),
        Route("/api/conversations/folders", create_conv_folder, methods=["POST"]),
        Route("/api/conversations/folders/{path:path}", delete_conv_folder, methods=["DELETE"]),
        Route("/api/conversations/folders/{path:path}", rename_conv_folder, methods=["PUT"]),
        Route("/api/conversations/{id}", delete_conversation, methods=["DELETE"]),
        Route("/api/conversations/{id}/archive", archive_conversation, methods=["POST"]),
        Route("/api/conversations/{id}/unarchive", unarchive_conversation, methods=["POST"]),
        Route("/api/notifications", list_notifications, methods=["GET"]),
        Route("/api/notifications/unread-count", notifications_unread_count, methods=["GET"]),
        Route("/api/notifications/read-all", notifications_mark_all_read, methods=["POST"]),
        Route("/api/notifications/{id}/read", notifications_mark_read, methods=["POST"]),
        Route("/api/upload/{conv_id}", handle_upload, methods=["POST"]),
        # Literal workspace routes must come before the {path:path} catch-all.
        Route("/api/workspace", workspace_list, methods=["GET"]),
        Route("/api/workspace", workspace_create, methods=["POST"]),
        Route("/api/workspace/recent", workspace_recent, methods=["GET"]),
        Route("/api/workspace-file/{path:path}", workspace_read_json, methods=["GET"]),
        Route("/api/workspace/{path:path}", serve_workspace_file, methods=["GET"]),
        Route("/api/workspace/{path:path}", workspace_write, methods=["PUT"]),
        Route("/api/workspace/{path:path}", workspace_delete, methods=["DELETE"]),
        Route("/api/config/files", config_list_files, methods=["GET"]),
        Route("/api/config/files/{path:path}", config_read_file, methods=["GET"]),
        Route("/api/config/files/{path:path}", config_write_file, methods=["PUT"]),
        Route("/api/vault", vault_create, methods=["POST"]),
        Route("/api/vault", vault_list, methods=["GET"]),
        Route("/api/vault/folders", vault_create_folder, methods=["POST"]),
        Route("/api/vault/recent", vault_recent, methods=["GET"]),
        Route("/api/vault/{page:path}", vault_write, methods=["PUT"]),
        Route("/api/vault/{page:path}", vault_read, methods=["GET"]),
        Route("/api/vault/{page:path}", vault_delete, methods=["DELETE"]),
        Route("/vault/{page:path}", serve_vault_page, methods=["GET"]),
        # Legacy wiki routes (redirect to vault)
        Route("/api/wiki", vault_list, methods=["GET"]),
        Route("/api/wiki/{page:path}", vault_read, methods=["GET"]),
        Route("/wiki/{page:path}", _redirect_wiki_to_vault, methods=["GET"]),
        Route("/api/widgets", list_widgets, methods=["GET"]),
        Route("/widgets/{tier}/{name}/widget.js", serve_widget_js,
              methods=["GET"]),
        Route("/api/canvas/{conv_id}", get_canvas_state, methods=["GET"]),
        Route("/api/canvas/{conv_id}/set", post_canvas_set, methods=["POST"]),
        Route("/canvas/{conv_id}", get_canvas_page, methods=["GET"]),
        WebSocketRoute("/ws/chat", ws_chat),
    ]

    # Static file serving for web UI
    static_dir = Path(__file__).parent / "web" / "static"
    if static_dir.is_dir():
        async def serve_index(request: Request):
            return FileResponse(static_dir / "index.html")

        routes.append(Route("/", serve_index, methods=["GET"]))
        routes.append(Mount("/static", StaticFiles(directory=str(static_dir)), name="static"))

    app = Starlette(routes=routes)
    # Module-level handlers read deps off ``request.app.state``. Closures
    # currently capture them via the enclosing scope; as handlers migrate
    # out of `create_app` they switch to the state-based lookup.
    app.state.config = config
    app.state.event_bus = event_bus
    app.state.manager = manager
    app.state.app_ctx = app_ctx
    return app


_http_server = None  # uvicorn.Server instance, set by run_http_server


async def run_http_server(config, event_bus, app_ctx=None, manager=None) -> None:
    """Start the HTTP server as an asyncio task."""
    global _http_server
    import uvicorn
    app = create_app(config, event_bus, app_ctx=app_ctx, manager=manager)
    server_config = uvicorn.Config(
        app,
        host=config.http.host,
        port=config.http.port,
        log_level="info",
    )
    _http_server = uvicorn.Server(server_config)
    log.info(f"HTTP server starting on {config.http.host}:{config.http.port}")
    await _http_server.serve()


async def shutdown_http_server() -> None:
    """Gracefully shut down the HTTP server (avoids CancelledError tracebacks)."""
    if _http_server is not None:
        _http_server.should_exit = True
