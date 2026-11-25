"""Workspace resource routes — the per-resource module exemplar.

This is the reference for the per-resource route-module pattern (issue #1390):
one ``web/<resource>_routes.py`` module holds a resource's request/response
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
  Cross-cutting helpers that several resources share (e.g. mtime-conflict
  detection) stay in ``http_server`` and are imported at call time.
- **Route order is part of the contract.** The generated OpenAPI spec lists
  paths in registration order, and ``make check`` fails on any drift. Register
  routes so the app's final order is unchanged: literal paths before the
  ``{path:path}`` catch-all, and this router's routes in their historical
  positions (see the split around ``/api/autocomplete`` in ``create_app``).

The workspace resource serves the Files tab: browse, view, edit, create, and
delete agent-workspace files under ``/api/workspace``.
"""

import asyncio
import logging
import os
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel
from starlette.responses import FileResponse, JSONResponse

from .http_auth import _authenticated
from .workspace_paths import (
    IMAGE_EXTENSIONS,
    PathContainmentError,
    detect_kind,
    is_readonly,
    is_secret,
    resolve_contained,
)

log = logging.getLogger(__name__)


# -- Constants -----------------------------------------------------------------

# Directories pruned from workspace_recent walks — they can grow huge and
# we don't want to stat every file inside on each request.
_WORKSPACE_RECENT_PRUNE_DIRS = frozenset(
    {
        "conversations",
        ".schedule_last_run",
        "attachments",
    }
)

# Cap on the workspace-recent listing, in files. Distinct from the autocomplete
# caps: this bounds the Files tab's recent list, not suggestions.
_WORKSPACE_RECENT_MAX_FILES = 50


# -- Models --------------------------------------------------------------------


class WorkspaceFolderEntry(BaseModel):
    name: str
    path: str


class WorkspaceFileEntry(BaseModel):
    name: str
    path: str
    size: int
    modified: float
    kind: Literal["text", "image", "binary"]
    readonly: bool
    secret: bool


class WorkspaceListingResponse(BaseModel):
    folder: str
    folders: list[WorkspaceFolderEntry]
    files: list[WorkspaceFileEntry]


class WorkspaceRecentResponse(BaseModel):
    files: list[WorkspaceFileEntry]


class WorkspaceTextResponse(BaseModel):
    content: str
    modified: float
    readonly: bool


class WorkspaceSaveRequest(BaseModel):
    content: str
    modified: float | None = None


class WorkspaceWriteResponse(BaseModel):
    ok: Literal[True]
    modified: float
    path: str | None = None


class WorkspaceDeleteResponse(BaseModel):
    ok: Literal[True]


# -- Helpers -------------------------------------------------------------------


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


def _collect_recent_workspace_files(
    workspace_root: Path,
) -> list[tuple[float, Path, str]]:
    """Walk the workspace and return up to ``_WORKSPACE_RECENT_MAX_FILES`` (mtime, path, rel_str) tuples.

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
    return collected[:_WORKSPACE_RECENT_MAX_FILES]


# -- Handlers ------------------------------------------------------------------


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
    # Containment uses relative_to, not string prefix: a sibling directory
    # sharing the workspace's name prefix (workspace-evil) must not pass.
    try:
        resolved = resolve_contained(workspace, file_path, label="workspace")
    except PathContainmentError:
        return JSONResponse({"error": "path outside workspace"}, status_code=403)
    if not resolved.is_file():
        # Stored paths from before make migrate-sessions (#1112) point at
        # conversations/{id}/...; follow the file to its session location.
        from ..conversation_paths import remap_legacy_rel_path

        remapped = remap_legacy_rel_path(config, file_path)
        if remapped:
            resolved = (workspace / remapped).resolve()
    if not resolved.is_file():
        return JSONResponse({"error": "not found"}, status_code=404)
    content_type = mimetypes.guess_type(str(resolved))[0] or "application/octet-stream"
    # Inline display only for safe image types; everything else (incl. SVG)
    # is forced to download to prevent XSS.
    safe_inline = content_type.startswith("image/") and content_type != "image/svg+xml"
    # Let FileResponse build Content-Disposition: it emits an RFC 5987
    # filename*=utf-8''... value for names that need quoting, which a
    # hand-built Latin-1 header cannot carry (#895).
    return FileResponse(
        str(resolved),
        media_type=content_type,
        headers={"X-Content-Type-Options": "nosniff"},
        filename=None if safe_inline else resolved.name,
        content_disposition_type="attachment",
    )


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
    try:
        target_dir = resolve_contained(workspace, folder_param, label="workspace")
    except PathContainmentError:
        return JSONResponse({"error": "folder not found"}, status_code=404)
    if not target_dir.is_dir():
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
    return JSONResponse(
        {
            "folder": folder_param,
            "folders": folders,
            "files": files,
        }
    )


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
    try:
        resolved = resolve_contained(workspace, file_path, label="workspace")
    except PathContainmentError:
        return JSONResponse({"error": "not found"}, status_code=404)
    if not resolved.is_file():
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
    return JSONResponse(
        {
            "content": content,
            "modified": stat.st_mtime,
            "readonly": is_readonly(file_path),
        }
    )


async def _workspace_rename(
    config,
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
        return JSONResponse({"error": "rename_to must be a non-empty string"}, status_code=400)
    rename_to = rename_to.strip()
    # Secret/readonly checks BEFORE resolving so 403 vs 404 don't leak existence.
    if is_secret(old_rel) or is_secret(rename_to):
        return JSONResponse({"error": "secret path"}, status_code=403)
    if is_readonly(old_rel) or is_readonly(rename_to):
        return JSONResponse({"error": "readonly path"}, status_code=403)
    try:
        new_file = resolve_contained(workspace, rename_to, label="workspace")
    except PathContainmentError:
        return JSONResponse({"error": "not found"}, status_code=404)
    if not old_file.is_file():
        return JSONResponse({"error": "not found"}, status_code=404)
    if new_file.exists():
        return JSONResponse({"error": "target already exists"}, status_code=409)
    new_file.parent.mkdir(parents=True, exist_ok=True)
    old_file.rename(new_file)
    workspace_resolved = workspace.resolve()
    _prune_empty_parents(old_file.parent, workspace)
    from ..workspace_index import invalidate_workspace_file_cache

    invalidate_workspace_file_cache(config)
    stat = new_file.stat()
    rel = new_file.relative_to(workspace_resolved)
    return JSONResponse(
        {
            "ok": True,
            "path": rel.as_posix(),
            "modified": stat.st_mtime,
        }
    )


@_authenticated
async def workspace_write(request: Request, username: str) -> JSONResponse:
    """Create or update a workspace text file, or rename if ``rename_to`` set."""
    config = request.app.state.config
    file_path = request.path_params.get("path", "")
    if not file_path:
        return JSONResponse({"error": "path required"}, status_code=400)
    workspace = config.workspace_path
    workspace.mkdir(parents=True, exist_ok=True)
    try:
        resolved = resolve_contained(workspace, file_path, label="workspace")
    except PathContainmentError:
        return JSONResponse({"error": "not found"}, status_code=404)

    rename_to = request.query_params.get("rename_to")
    if rename_to is not None:
        return await _workspace_rename(config, workspace, resolved, file_path, rename_to)

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
            # Function-level import: http_server imports this module for its
            # router, so a module-level import back would be circular.
            from ..http_server import check_mtime_conflict

            conflict = check_mtime_conflict(resolved.stat().st_mtime, modified)
            if conflict is not None:
                return conflict

    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(content, encoding="utf-8")
    from ..workspace_index import invalidate_workspace_file_cache

    invalidate_workspace_file_cache(config)
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
    try:
        resolved = resolve_contained(workspace, file_path, label="workspace")
    except PathContainmentError:
        return JSONResponse({"error": "not found"}, status_code=404)
    if not resolved.exists():
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
    from ..workspace_index import invalidate_workspace_file_cache

    invalidate_workspace_file_cache(config)
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
        return JSONResponse({"error": "type must be 'file' or 'folder'"}, status_code=400)
    rel_path = body.get("path")
    if not isinstance(rel_path, str) or not rel_path.strip():
        return JSONResponse({"error": "path (string) required"}, status_code=400)
    rel_path = rel_path.strip()

    workspace = config.workspace_path
    workspace.mkdir(parents=True, exist_ok=True)
    try:
        resolved = resolve_contained(workspace, rel_path, label="workspace")
    except PathContainmentError:
        return JSONResponse({"error": "not found"}, status_code=404)

    if is_secret(rel_path):
        return JSONResponse({"error": "secret path"}, status_code=403)
    if is_readonly(rel_path):
        return JSONResponse({"error": "readonly path"}, status_code=403)

    if kind == "folder":
        try:
            resolved.mkdir(parents=True, exist_ok=False)
            from ..workspace_index import invalidate_workspace_file_cache

            invalidate_workspace_file_cache(config)
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
    from ..workspace_index import invalidate_workspace_file_cache

    invalidate_workspace_file_cache(config)
    return JSONResponse(
        {
            "ok": True,
            "path": rel_path,
            "modified": resolved.stat().st_mtime,
        }
    )


@_authenticated
async def workspace_recent(request: Request, username: str) -> JSONResponse:
    """Return up to ``_WORKSPACE_RECENT_MAX_FILES`` workspace files sorted by mtime descending."""
    config = request.app.state.config
    workspace = config.workspace_path
    if not workspace.is_dir():
        return JSONResponse({"files": []})

    workspace_resolved = workspace.resolve()
    collected = await asyncio.to_thread(_collect_recent_workspace_files, workspace_resolved)
    files: list[dict] = []
    for _mtime, fpath, rel_str in collected:
        try:
            files.append(_workspace_file_entry(fpath, rel_str))
        except OSError as exc:
            log.debug("workspace_recent: entry build failed for %s: %s", fpath, exc)
    return JSONResponse({"files": files})


# -- Router --------------------------------------------------------------------

workspace_router = APIRouter()

# Literal workspace routes must come before the {path:path} catch-all.
workspace_router.add_api_route(
    "/api/workspace",
    workspace_list,
    methods=["GET"],
    response_model=WorkspaceListingResponse,
    # Describe the optional query without changing legacy parsing
    # or the existing 404 response for invalid folders.
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
)
workspace_router.add_api_route("/api/workspace", workspace_create, methods=["POST"])
workspace_router.add_api_route(
    "/api/workspace/recent", workspace_recent, methods=["GET"], response_model=WorkspaceRecentResponse
)
workspace_router.add_api_route(
    "/api/workspace-file/{path:path}",
    workspace_read_json,
    methods=["GET"],
    response_model=WorkspaceTextResponse,
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
)
workspace_router.add_api_route(
    "/api/workspace/{path:path}",
    serve_workspace_file,
    methods=["GET"],
    response_class=FileResponse,
    openapi_extra={
        "parameters": [
            {
                "name": "path",
                "in": "path",
                "required": True,
                "schema": {"type": "string"},
            }
        ],
        "responses": {
            "200": {
                "description": "Workspace file bytes",
                "content": {
                    "application/octet-stream": {
                        "schema": {"type": "string", "format": "binary"},
                    }
                },
                "headers": {
                    "X-Content-Type-Options": {
                        "schema": {"type": "string"},
                        "description": "Always nosniff",
                    },
                    "Content-Disposition": {
                        "schema": {"type": "string"},
                        "description": "Attachment filename for non-safe-image content",
                    },
                },
            }
        },
    },
)
# Describe both existing PUT forms without moving parsing into FastAPI:
# saves have an optional JSON body schema, while renames send only the
# rename_to query. The handlers retain their legacy 400 responses.
workspace_router.add_api_route(
    "/api/workspace/{path:path}",
    workspace_write,
    methods=["PUT"],
    response_model=WorkspaceWriteResponse,
    openapi_extra={
        "parameters": [
            {"name": "path", "in": "path", "required": True, "schema": {"type": "string"}},
            {"name": "rename_to", "in": "query", "required": False, "schema": {"type": "string"}},
        ],
        "requestBody": {
            "required": False,
            "content": {
                "application/json": {
                    "schema": WorkspaceSaveRequest.model_json_schema(),
                },
            },
        },
    },
)
workspace_router.add_api_route(
    "/api/workspace/{path:path}",
    workspace_delete,
    methods=["DELETE"],
    response_model=WorkspaceDeleteResponse,
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
)
