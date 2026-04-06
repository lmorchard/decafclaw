"""HTTP server — Starlette ASGI app for interactive callbacks and future web UI."""

import functools
import logging
import re
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, RedirectResponse
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles

from .mattermost_ui import get_token_registry

log = logging.getLogger(__name__)

_CONFIG_FILES = [
    {"name": "SOUL.md", "path": "SOUL.md", "description": "Core identity prompt", "scope": "admin"},
    {"name": "AGENT.md", "path": "AGENT.md", "description": "Behavioral instructions", "scope": "admin"},
    {"name": "USER.md", "path": "USER.md", "description": "User-specific context", "scope": "admin"},
    {"name": "HEARTBEAT.md", "path": "HEARTBEAT.md", "description": "Heartbeat check sections", "scope": "admin"},
    {"name": "COMPACTION.md", "path": "COMPACTION.md", "description": "Compaction prompt override", "scope": "admin"},
]


def create_app(config, event_bus, app_ctx=None) -> Starlette:
    """Create the Starlette ASGI app with routes."""

    async def health(request: Request) -> JSONResponse:
        from .tools.health import get_health_data
        return JSONResponse(get_health_data(config))

    async def handle_confirm(request: Request) -> JSONResponse:
        """Handle Mattermost interactive button callbacks for tool confirmation."""
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

        log.info(f"Confirm callback: action={action} tool={tool_name} context={context_id[:8]}")

        # Map action to event fields
        approved = action in ("approve", "always", "add_pattern")
        always = action == "always"
        add_pattern = action == "add_pattern"

        # Publish confirmation event on the event bus
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

    # -- Auth routes -----------------------------------------------------------

    async def auth_login(request: Request) -> JSONResponse:
        """Validate token, set session cookie."""
        from .web.auth import validate_token
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
        """Clear session cookie."""
        response = JSONResponse({"ok": True})
        response.delete_cookie("decafclaw_session")
        return response

    async def auth_me(request: Request) -> JSONResponse:
        """Return current authenticated user."""
        from .web.auth import get_current_user
        username = get_current_user(request, config)
        if not username:
            return JSONResponse({"error": "not authenticated"}, status_code=401)
        return JSONResponse({"username": username})

    # -- Conversation routes ---------------------------------------------------

    def _validate_folder_param(folder_param: str) -> str | None:
        """Validate a folder query parameter. Returns error message or None."""
        if not folder_param:
            return None
        if folder_param.startswith("/"):
            return "invalid folder path"
        segments = folder_param.split("/")
        if any(not seg or seg == ".." for seg in segments):
            return "invalid folder path"
        return None

    def _require_auth(request):
        """Helper: get username from cookie or None."""
        from .web.auth import get_current_user
        return get_current_user(request, config)

    def _authenticated(handler):
        """Decorator that extracts username from auth, returns 401 if not authenticated."""
        @functools.wraps(handler)
        async def wrapper(request):
            username = _require_auth(request)
            if not username:
                return JSONResponse({"error": "not authenticated"}, status_code=401)
            return await handler(request, username)
        return wrapper

    @_authenticated
    async def list_conversations(request: Request, username: str) -> JSONResponse:
        """List conversations and subfolders for a specific folder.

        Query params:
            folder — folder path (default: top-level)

        Returns ``{folder, folders, conversations}`` mirroring vault_list pattern.
        """
        from .web.conversation_folders import ConversationFolderIndex
        from .web.conversations import ConversationIndex
        folder_param = request.query_params.get("folder", "").strip()
        err = _validate_folder_param(folder_param)
        if err:
            return JSONResponse({"error": err}, status_code=400)
        index = ConversationIndex(config)
        folder_index = ConversationFolderIndex(config, username)
        convs = index.list_for_user(username)
        assignments = await folder_index.get_all_assignments()
        # Filter conversations to requested folder
        filtered = [
            c for c in convs
            if assignments.get(c.conv_id, "") == folder_param
        ]
        # Get child folders
        child_names = await folder_index.list_folders(folder_param)
        folders: list[dict] = [
            {"name": name, "path": f"{folder_param}/{name}" if folder_param else name}
            for name in child_names
        ]
        # At top level, append virtual folders
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
        """List archived conversations, optionally filtered by folder.

        Query params:
            folder — folder path (default: top-level)
        """
        from .web.conversation_folders import ConversationFolderIndex
        from .web.conversations import ConversationIndex
        folder_param = request.query_params.get("folder", "").strip()
        err = _validate_folder_param(folder_param)
        if err:
            return JSONResponse({"error": err}, status_code=400)
        index = ConversationIndex(config)
        folder_index = ConversationFolderIndex(config, username)
        convs = index.list_for_user(username, include_archived=True)
        archived = [c for c in convs if c.archived]
        assignments = await folder_index.get_all_assignments()
        # Filter to requested folder
        filtered = [
            c for c in archived
            if assignments.get(c.conv_id, "") == folder_param
        ]
        # Derive child folders from archived conversation assignments.
        # Extract the immediate child segment — e.g. if folder_param="" and a
        # conversation is in "projects/bot-redesign", emit "projects".
        prefix = f"{folder_param}/" if folder_param else ""
        child_names = set()
        for c in archived:
            folder = assignments.get(c.conv_id, "")
            if not folder:
                continue
            if folder_param == "":
                # Top level: extract first segment
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
        """List system conversations, grouped by type sub-folders.

        Query params:
            folder — sub-folder type: heartbeat, schedule, delegated (default: top-level)
        """
        from .web.conversations import list_system_conversations as list_sys
        folder_param = request.query_params.get("folder", "").strip()
        all_sys = list_sys(config, username=username)
        if not folder_param:
            # Top level: show type sub-folders, no conversations
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
        # Filter by conv_type
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
        """Create a new conversation, optionally in a folder with an effort level."""
        body = await request.json()
        folder = str(body.get("folder", "")).strip()
        effort = str(body.get("effort", "")).strip()
        # Validate effort level
        if effort:
            from .config import EFFORT_LEVELS
            if effort not in EFFORT_LEVELS:
                return JSONResponse({"error": f"Unknown effort level: {effort}"}, status_code=400)
        # Validate folder exists before creating conversation
        if folder:
            from .web.conversation_folders import ConversationFolderIndex
            folder_index = ConversationFolderIndex(config, username)
            if not await folder_index.folder_exists(folder):
                return JSONResponse({"error": "Folder does not exist"}, status_code=400)
        from .web.conversations import ConversationIndex
        index = ConversationIndex(config)
        conv = index.create(username, title=body.get("title", ""))
        # Assign to folder
        if folder:
            await folder_index.set_folder(conv.conv_id, folder)
        # Record initial effort level
        if effort and effort != "default":
            from .archive import append_message
            append_message(config, conv.conv_id,
                           {"role": "effort", "content": effort})
        result = conv.to_dict()
        if folder:
            result["folder"] = folder
        if effort:
            result["effort"] = effort
        return JSONResponse(result, status_code=201)

    @_authenticated
    async def get_conversation(request: Request, username: str) -> JSONResponse:
        """Get conversation metadata."""
        conv_id = request.path_params["id"]
        from .web.conversations import ConversationIndex
        index = ConversationIndex(config)
        conv = index.get(conv_id)
        if not conv or conv.user_id != username:
            return JSONResponse({"error": "not found"}, status_code=404)
        return JSONResponse(conv.to_dict())

    @_authenticated
    async def rename_conversation(request: Request, username: str) -> JSONResponse:
        """Rename and/or move a conversation to a different folder."""
        conv_id = request.path_params["id"]
        body = await request.json()
        from .web.conversations import ConversationIndex
        index = ConversationIndex(config)
        conv = index.get(conv_id)
        if not conv or conv.user_id != username:
            return JSONResponse({"error": "not found"}, status_code=404)
        # Validate folder before applying any changes
        folder = body.get("folder")
        if folder is not None:
            folder = str(folder).strip()
            from .web.conversation_folders import ConversationFolderIndex
            folder_index = ConversationFolderIndex(config, username)
            if folder != "":
                if not await folder_index.folder_exists(folder):
                    return JSONResponse({"error": "Folder does not exist"}, status_code=400)
        # Rename title if provided
        title = body.get("title")
        if title is not None:
            updated = index.rename(conv_id, title)
            if not updated:
                return JSONResponse({"error": "not found"}, status_code=404)
            conv = updated
        # Move to folder if provided (already validated above)
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
        conv_id = request.path_params["id"]
        from .web.conversations import ConversationIndex
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
        conv_id = request.path_params["id"]
        from .web.conversations import ConversationIndex
        index = ConversationIndex(config)
        conv = index.get(conv_id)
        if not conv or conv.user_id != username:
            return JSONResponse({"error": "not found"}, status_code=404)
        from .context_composer import read_context_sidecar
        data = read_context_sidecar(config, conv_id)
        if data is None:
            return JSONResponse({"error": "no context data"}, status_code=404)
        return JSONResponse(data)

    @_authenticated
    async def serve_workspace_file(request: Request, username: str):
        """Serve a file from the agent workspace (authenticated, read-only)."""
        file_path = request.path_params.get("path", "")
        if not file_path:
            return JSONResponse({"error": "path required"}, status_code=400)
        # Resolve and sandbox to workspace
        import mimetypes
        workspace = config.workspace_path.resolve()
        resolved = (workspace / file_path).resolve()
        if not str(resolved).startswith(str(workspace)):
            return JSONResponse({"error": "path outside workspace"}, status_code=403)
        if not resolved.is_file():
            return JSONResponse({"error": "not found"}, status_code=404)
        content_type = mimetypes.guess_type(str(resolved))[0] or "application/octet-stream"
        # Only allow inline display for safe image types; force download for
        # everything else (including SVG) to prevent XSS.
        safe_inline = content_type.startswith("image/") and content_type != "image/svg+xml"
        headers = {"X-Content-Type-Options": "nosniff"}
        if not safe_inline:
            headers["Content-Disposition"] = f'attachment; filename="{resolved.name}"'
        return FileResponse(str(resolved), media_type=content_type, headers=headers)

    # -- Vault routes --------------------------------------------------------------

    def _vault_root():
        return config.vault_root

    def _resolve_vault_page(page_name: str):
        """Resolve a vault page name to a file path."""
        from .skills.vault.tools import resolve_page
        return resolve_page(config, page_name)

    def _vault_source_type(filepath):
        """Determine source type for a vault file."""
        from .skills.vault.tools import _source_type_for_path
        return _source_type_for_path(config, filepath)

    @_authenticated
    async def vault_list(request: Request, username: str) -> JSONResponse:
        """List vault pages and subfolders for a specific folder.

        Query params:
            folder — relative path within vault (default: root)

        Returns ``{folder, folders, pages}`` where *folders* are immediate
        child directories that contain at least one ``.md`` file and *pages*
        are ``.md`` files directly in the requested folder.
        """
        vault = _vault_root()
        if not vault.is_dir():
            return JSONResponse({"folder": "", "folders": [], "pages": []})

        folder_param = request.query_params.get("folder", "").strip()
        # Validate folder path
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

        # Collect .md files directly in the target folder (not recursive)
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

        # Build folder list from all child directories
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
        """List recently modified vault pages sorted by mtime descending."""
        vault = _vault_root()
        if not vault.is_dir():
            return JSONResponse({"pages": []})

        vault_resolved = vault.resolve()
        limit = config.vault.recent_changes_limit
        try:
            limit = int(request.query_params.get("limit", limit))
        except (ValueError, TypeError):
            pass
        limit = max(0, limit)

        pages = []
        for md_file in vault_resolved.rglob("*.md"):
            if not md_file.is_file():
                continue
            if not md_file.resolve().is_relative_to(vault_resolved):
                continue
            # Skip hidden directories (.obsidian, .git, .trash, etc.)
            if any(part.startswith('.') for part in md_file.relative_to(vault_resolved).parts[:-1]):
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
        page_name = request.path_params.get("page", "")
        if not page_name:
            return JSONResponse({"error": "page name required"}, status_code=400)
        resolved = _resolve_vault_page(page_name)
        if not resolved:
            return JSONResponse({"error": "not found"}, status_code=404)
        content = resolved.read_text(encoding="utf-8")
        stat = resolved.stat()
        vault = _vault_root().resolve()
        rel = resolved.relative_to(vault)
        return JSONResponse({
            "title": resolved.stem,
            "path": str(rel.with_suffix("")),
            "content": content,
            "modified": stat.st_mtime,
        })

    @_authenticated
    async def vault_write(request: Request, username: str) -> JSONResponse:
        """Create or update a vault page, or rename/move it."""
        page_name = request.path_params.get("page", "")
        if not page_name:
            return JSONResponse({"error": "page name required"}, status_code=400)
        if ".." in page_name or page_name.startswith("/"):
            return JSONResponse({"error": "invalid page path"}, status_code=400)
        vault = _vault_root()
        vault.mkdir(parents=True, exist_ok=True)
        target = (vault / f"{page_name}.md").resolve()
        if not target.is_relative_to(vault.resolve()):
            return JSONResponse({"error": "path outside vault directory"}, status_code=403)
        body = await request.json()

        # --- Rename/move operation ---
        rename_to = body.get("rename_to")
        if rename_to is not None:
            if "content" in body:
                return JSONResponse(
                    {"error": "cannot combine rename_to with content"},
                    status_code=400,
                )
            return await _vault_rename(vault, target, page_name, rename_to)

        # --- Content write ---
        content = body.get("content")
        if content is None or not isinstance(content, str):
            return JSONResponse({"error": "content (string) required"}, status_code=400)
        # Conflict detection
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
        # Update semantic search index
        source_type = _vault_source_type(target)
        try:
            from .embeddings import delete_entries, index_entry
            rel_path = str(target.relative_to(vault.resolve()))
            delete_entries(config, rel_path, source_type=source_type)
            await index_entry(config, rel_path, content, source_type=source_type)
        except Exception as e:
            log.warning(f"Failed to index vault page '{page_name}': {e}")
        new_mtime = target.stat().st_mtime
        return JSONResponse({"ok": True, "modified": new_mtime})

    async def _vault_rename(vault: Path, old_file: Path, old_name: str, rename_to: str) -> JSONResponse:
        """Rename/move a vault page."""
        if not isinstance(rename_to, str) or not rename_to.strip():
            return JSONResponse({"error": "rename_to must be a non-empty string"}, status_code=400)
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
        # Move the file
        new_file.parent.mkdir(parents=True, exist_ok=True)
        old_file.rename(new_file)
        # Clean up empty parent directories from old location
        old_dir = old_file.parent
        vault_resolved = vault.resolve()
        while old_dir.resolve() != vault_resolved:
            try:
                old_dir.rmdir()  # only succeeds if empty
            except OSError:
                break
            old_dir = old_dir.parent
        # Update embedding index
        try:
            from .embeddings import delete_entries, index_entry
            old_rel = f"{old_name}.md"
            old_source_type = _vault_source_type(old_file)
            delete_entries(config, old_rel, source_type=old_source_type)
            new_rel = str(new_file.relative_to(vault_resolved))
            new_content = new_file.read_text(encoding="utf-8")
            new_source_type = _vault_source_type(new_file)
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
    async def vault_create(request: Request, username: str) -> JSONResponse:
        """Create a new vault page."""
        body = await request.json()
        name = body.get("name")
        if not name or not isinstance(name, str):
            return JSONResponse({"error": "name (string) required"}, status_code=400)
        name = name.strip()
        if not name:
            return JSONResponse({"error": "name (string) required"}, status_code=400)
        if ".." in name or name.startswith("/"):
            return JSONResponse({"error": "invalid page name"}, status_code=400)
        vault = _vault_root()
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
        # Update semantic search index
        source_type = _vault_source_type(target)
        try:
            from .embeddings import delete_entries, index_entry
            rel_path = str(target.relative_to(vault.resolve()))
            await index_entry(config, rel_path, content, source_type=source_type)
        except Exception as e:
            log.warning(f"Failed to index new vault page '{name}': {e}")
        new_mtime = target.stat().st_mtime
        return JSONResponse({"ok": True, "page": name, "modified": new_mtime})

    @_authenticated
    async def vault_create_folder(request: Request, username: str) -> JSONResponse:
        """Create a new empty folder in the vault."""
        body = await request.json()
        folder = body.get("folder")
        if not folder or not isinstance(folder, str):
            return JSONResponse({"error": "folder (string) required"}, status_code=400)
        folder = folder.strip()
        if not folder:
            return JSONResponse({"error": "folder (string) required"}, status_code=400)
        if ".." in folder or folder.startswith("/"):
            return JSONResponse({"error": "invalid folder path"}, status_code=400)
        vault = _vault_root()
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
        page_name = request.path_params.get("page", "")
        if not page_name:
            return JSONResponse({"error": "page name required"}, status_code=400)
        if ".." in page_name or page_name.startswith("/"):
            return JSONResponse({"error": "invalid page path"}, status_code=400)
        vault = _vault_root()
        target = (vault / f"{page_name}.md").resolve()
        if not target.is_relative_to(vault.resolve()):
            return JSONResponse({"error": "path outside vault directory"}, status_code=403)
        if not target.exists():
            return JSONResponse({"error": "not found"}, status_code=404)
        target.unlink()
        # Clean up empty parent directories
        parent = target.parent
        vault_resolved = vault.resolve()
        while parent.resolve() != vault_resolved:
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent
        # Remove from embedding index
        try:
            from .embeddings import delete_entries
            rel_path = f"{page_name}.md"
            source_type = _vault_source_type(target)
            delete_entries(config, rel_path, source_type=source_type)
        except Exception as e:
            log.warning(f"Failed to remove embeddings for '{page_name}': {e}")
        return JSONResponse({"ok": True})

    async def serve_vault_page(request: Request):
        """Serve the vault page HTML shell."""
        username = _require_auth(request)
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
        conv_id = request.path_params["conv_id"]
        # Verify conversation belongs to user
        from .web.conversations import ConversationIndex
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
        # Parse multipart form
        try:
            form = await request.form()
        except RuntimeError:
            # python-multipart not installed or request is not multipart
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
        from .attachments import save_attachment
        result = save_attachment(config, conv_id, filename, data, content_type)
        return JSONResponse(result, status_code=201)

    @_authenticated
    async def archive_conversation(request: Request, username: str) -> JSONResponse:
        """Archive a conversation (hide from list, keep data)."""
        conv_id = request.path_params["id"]
        from .web.conversations import ConversationIndex
        index = ConversationIndex(config)
        conv = index.get(conv_id)
        if not conv or conv.user_id != username:
            return JSONResponse({"error": "not found"}, status_code=404)
        index.archive(conv_id)
        return JSONResponse({"ok": True})

    @_authenticated
    async def unarchive_conversation(request: Request, username: str) -> JSONResponse:
        """Unarchive a conversation (restore to active list)."""
        conv_id = request.path_params["id"]
        from .web.conversations import ConversationIndex
        index = ConversationIndex(config)
        conv = index.get(conv_id)
        if not conv or conv.user_id != username:
            return JSONResponse({"error": "not found"}, status_code=404)
        index.unarchive(conv_id)
        return JSONResponse({"ok": True})

    @_authenticated
    async def delete_conversation(request: Request, username: str) -> JSONResponse:
        """Permanently delete a conversation and all associated files."""
        conv_id = request.path_params["id"]
        from .web.conversations import ConversationIndex
        index = ConversationIndex(config)
        conv = index.get(conv_id)
        if not conv or conv.user_id != username:
            return JSONResponse({"error": "not found"}, status_code=404)

        # Delete files on disk (with path sandboxing)
        conv_dir = config.workspace_path / "conversations"
        for suffix in [".jsonl", ".compacted.jsonl", ".context.json"]:
            path = (conv_dir / f"{conv_id}{suffix}").resolve()
            if not path.is_relative_to(conv_dir.resolve()):
                continue  # path traversal guard
            if path.exists():
                path.unlink()

        # Delete uploads directory
        from .attachments import delete_conversation_uploads
        delete_conversation_uploads(config, conv_id)

        # Remove from conversation index
        index.delete(conv_id)

        # Remove from folder index
        from .web.conversation_folders import ConversationFolderIndex
        folder_index = ConversationFolderIndex(config, username)
        await folder_index.remove_assignment(conv_id)

        return JSONResponse({"ok": True})

    # -- Conversation folder routes -----------------------------------------------

    @_authenticated
    async def create_conv_folder(request: Request, username: str) -> JSONResponse:
        """Create a conversation folder."""
        body = await request.json()
        path = body.get("path", "")
        if not path or not isinstance(path, str):
            return JSONResponse({"error": "path (string) required"}, status_code=400)
        from .web.conversation_folders import ConversationFolderIndex
        folder_index = ConversationFolderIndex(config, username)
        ok, err = await folder_index.create_folder(path.strip())
        if not ok:
            status = 409 if "already exists" in err else 400
            return JSONResponse({"error": err}, status_code=status)
        return JSONResponse({"ok": True, "path": path.strip()})

    @_authenticated
    async def delete_conv_folder(request: Request, username: str) -> JSONResponse:
        """Delete an empty conversation folder."""
        path = request.path_params.get("path", "")
        if not path:
            return JSONResponse({"error": "path required"}, status_code=400)
        from .web.conversation_folders import ConversationFolderIndex
        folder_index = ConversationFolderIndex(config, username)
        ok, err = await folder_index.delete_folder(path)
        if not ok:
            status = 409 if "contains" in err else 404
            return JSONResponse({"error": err}, status_code=status)
        return JSONResponse({"ok": True})

    @_authenticated
    async def rename_conv_folder(request: Request, username: str) -> JSONResponse:
        """Rename/move a conversation folder. Merges if target exists."""
        old_path = request.path_params.get("path", "")
        if not old_path:
            return JSONResponse({"error": "path required"}, status_code=400)
        body = await request.json()
        new_path = body.get("path", "")
        if not new_path or not isinstance(new_path, str):
            return JSONResponse({"error": "path (string) required in body"}, status_code=400)
        from .web.conversation_folders import ConversationFolderIndex
        folder_index = ConversationFolderIndex(config, username)
        ok, err = await folder_index.rename_folder(old_path, new_path.strip())
        if not ok:
            status = 404 if "not found" in err else 400
            return JSONResponse({"error": err}, status_code=status)
        return JSONResponse({"ok": True})

    # -- Config file routes ----------------------------------------------------

    def _resolve_config_path(path_str: str) -> tuple:
        """Resolve a config file path to (filesystem_path, scope).
        Returns (None, None) if invalid."""
        # Check static config files
        for f in _CONFIG_FILES:
            if f["path"] == path_str:
                if path_str.startswith("workspace/"):
                    return config.workspace_path / path_str.removeprefix("workspace/"), f["scope"]
                return config.agent_path / path_str, f["scope"]
        # Check schedules pattern
        if re.match(r"^schedules/[^/]+\.md$", path_str):
            return config.agent_path / path_str, "admin"
        if re.match(r"^workspace/schedules/[^/]+\.md$", path_str):
            return config.workspace_path / path_str.removeprefix("workspace/"), "workspace"
        return None, None

    @_authenticated
    async def config_list_files(request: Request, username: str) -> JSONResponse:
        """List editable config files."""
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
        # Discover schedule files
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
        """Read a config file."""
        path_str = request.path_params.get("path", "")
        fpath, scope = _resolve_config_path(path_str)
        if fpath is None:
            return JSONResponse({"error": "invalid config path"}, status_code=400)
        is_default = False
        if fpath.exists():
            content = fpath.read_text(encoding="utf-8")
            modified = fpath.stat().st_mtime
        else:
            # Check for bundled default in prompts directory
            prompts_dir = Path(__file__).parent / "prompts"
            bundled = prompts_dir / Path(path_str).name
            if bundled.exists():
                content = bundled.read_text(encoding="utf-8")
                modified = None
                is_default = True
            else:
                # File doesn't exist yet — return empty so editor can create it
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
        """Write a config file."""
        path_str = request.path_params.get("path", "")
        fpath, scope = _resolve_config_path(path_str)
        if fpath is None:
            return JSONResponse({"error": "invalid config path"}, status_code=400)
        body = await request.json()
        content = body.get("content")
        if content is None or not isinstance(content, str):
            return JSONResponse({"error": "content (string) required"}, status_code=400)
        # Conflict detection
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
        new_mtime = fpath.stat().st_mtime
        return JSONResponse({"ok": True, "modified": new_mtime})

    # -- WebSocket route -------------------------------------------------------

    async def ws_chat(websocket):
        from .web.websocket import websocket_chat
        await websocket_chat(websocket, config, event_bus, app_ctx)

    async def handle_cancel(request: Request) -> JSONResponse:
        """Handle Mattermost interactive button callback for stop/cancel."""
        token = request.query_params.get("token", "")
        token_data = get_token_registry().consume(token)

        if not token_data:
            log.warning("Cancel callback rejected: invalid or expired token")
            return JSONResponse({"error": "unauthorized"}, status_code=403)

        conv_id = token_data["context_id"]
        log.info(f"Cancel button pressed for conversation {conv_id[:8]}")

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
        Route("/api/upload/{conv_id}", handle_upload, methods=["POST"]),
        Route("/api/workspace/{path:path}", serve_workspace_file, methods=["GET"]),
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
        Route("/wiki/{page:path}", lambda r: RedirectResponse(
            f"/vault/{r.path_params['page']}", status_code=301), methods=["GET"]),
        WebSocketRoute("/ws/chat", ws_chat),
    ]

    # Static file serving for web UI
    static_dir = Path(__file__).parent / "web" / "static"
    if static_dir.is_dir():
        async def serve_index(request: Request):
            return FileResponse(static_dir / "index.html")

        routes.append(Route("/", serve_index, methods=["GET"]))
        routes.append(Mount("/static", StaticFiles(directory=str(static_dir)), name="static"))

    return Starlette(routes=routes)


_http_server = None  # uvicorn.Server instance, set by run_http_server


async def run_http_server(config, event_bus, app_ctx=None) -> None:
    """Start the HTTP server as an asyncio task."""
    global _http_server
    import uvicorn
    app = create_app(config, event_bus, app_ctx=app_ctx)
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
