"""HTTP server — Starlette ASGI app for interactive callbacks and future web UI."""

import functools
import logging
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
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
        """List conversations for the authenticated user."""
        from .web.conversations import ConversationIndex
        index = ConversationIndex(config)
        convs = index.list_for_user(username)
        return JSONResponse([c.to_dict() for c in convs])

    @_authenticated
    async def create_conversation(request: Request, username: str) -> JSONResponse:
        """Create a new conversation."""
        body = await request.json()
        from .web.conversations import ConversationIndex
        index = ConversationIndex(config)
        conv = index.create(username, title=body.get("title", ""))
        return JSONResponse(conv.to_dict(), status_code=201)

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
        """Rename a conversation."""
        conv_id = request.path_params["id"]
        body = await request.json()
        from .web.conversations import ConversationIndex
        index = ConversationIndex(config)
        conv = index.get(conv_id)
        if not conv or conv.user_id != username:
            return JSONResponse({"error": "not found"}, status_code=404)
        updated = index.rename(conv_id, body.get("title", ""))
        if not updated:
            return JSONResponse({"error": "not found"}, status_code=404)
        return JSONResponse(updated.to_dict())

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

    # -- Wiki routes --------------------------------------------------------------

    def _wiki_dir():
        return config.workspace_path / "wiki"

    def _resolve_wiki_page(page_name: str):
        """Resolve a wiki page name to a file path (reuses wiki tool logic)."""
        if ".." in page_name or page_name.startswith("/"):
            return None
        wiki_root = _wiki_dir().resolve()
        if not wiki_root.is_dir():
            return None
        # Direct path first
        direct = (wiki_root / f"{page_name}.md").resolve()
        if direct.is_relative_to(wiki_root) and direct.exists():
            return direct
        # Search subdirectories by stem
        for path in wiki_root.rglob("*.md"):
            if path.stem == page_name and path.resolve().is_relative_to(wiki_root):
                return path
        return None

    @_authenticated
    async def wiki_list(request: Request, username: str) -> JSONResponse:
        """List all wiki pages with titles and modified dates."""
        wiki_root = _wiki_dir()
        if not wiki_root.is_dir():
            return JSONResponse([])
        pages = []
        for path in sorted(wiki_root.rglob("*.md")):
            if not path.resolve().is_relative_to(wiki_root.resolve()):
                continue
            stat = path.stat()
            pages.append({
                "title": path.stem,
                "modified": stat.st_mtime,
            })
        pages.sort(key=lambda p: p["title"].lower())
        return JSONResponse(pages)

    @_authenticated
    async def wiki_read(request: Request, username: str) -> JSONResponse:
        """Read a single wiki page as JSON."""
        page_name = request.path_params.get("page", "")
        if not page_name:
            return JSONResponse({"error": "page name required"}, status_code=400)
        resolved = _resolve_wiki_page(page_name)
        if not resolved:
            return JSONResponse({"error": "not found"}, status_code=404)
        content = resolved.read_text(encoding="utf-8")
        stat = resolved.stat()
        return JSONResponse({
            "title": resolved.stem,
            "content": content,
            "modified": stat.st_mtime,
        })

    @_authenticated
    async def wiki_write(request: Request, username: str) -> JSONResponse:
        """Create or update a wiki page."""
        page_name = request.path_params.get("page", "")
        if not page_name:
            return JSONResponse({"error": "page name required"}, status_code=400)
        # Validate page path
        if ".." in page_name or page_name.startswith("/"):
            return JSONResponse({"error": "invalid page path"}, status_code=400)
        wiki_root = _wiki_dir()
        wiki_root.mkdir(parents=True, exist_ok=True)
        target = (wiki_root / f"{page_name}.md").resolve()
        if not target.is_relative_to(wiki_root.resolve()):
            return JSONResponse({"error": "path outside wiki directory"}, status_code=403)
        # Parse body
        body = await request.json()
        content = body.get("content")
        if content is None or not isinstance(content, str):
            return JSONResponse({"error": "content (string) required"}, status_code=400)
        # Conflict detection: if modified timestamp provided and file exists,
        # check that the file hasn't been modified since the client read it
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
        # Write
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        # Update semantic search index
        try:
            from .embeddings import delete_entries, index_entry
            rel_path = str(target.relative_to(config.workspace_path.resolve()))
            delete_entries(config, rel_path, source_type="wiki")
            await index_entry(config, rel_path, content, source_type="wiki")
        except Exception as e:
            log.warning(f"Failed to index wiki page '{page_name}': {e}")
        new_mtime = target.stat().st_mtime
        return JSONResponse({"ok": True, "modified": new_mtime})

    @_authenticated
    async def wiki_create(request: Request, username: str) -> JSONResponse:
        """Create a new wiki page."""
        body = await request.json()
        name = body.get("name")
        if not name or not isinstance(name, str):
            return JSONResponse({"error": "name (string) required"}, status_code=400)
        name = name.strip()
        if not name:
            return JSONResponse({"error": "name (string) required"}, status_code=400)
        # Validate name: block path traversal and absolute paths
        if ".." in name or name.startswith("/"):
            return JSONResponse({"error": "invalid page name"}, status_code=400)
        wiki_root = _wiki_dir()
        wiki_root.mkdir(parents=True, exist_ok=True)
        target = (wiki_root / f"{name}.md").resolve()
        if not target.is_relative_to(wiki_root.resolve()):
            return JSONResponse({"error": "path outside wiki directory"}, status_code=403)
        if target.exists():
            return JSONResponse({"error": "page already exists"}, status_code=409)
        content = body.get("content")
        if content is None or not isinstance(content, str):
            content = f"# {name}\n"
        # Create parent directories and write file
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        # Update semantic search index
        try:
            from .embeddings import delete_entries, index_entry
            rel_path = str(target.relative_to(config.workspace_path.resolve()))
            await index_entry(config, rel_path, content, source_type="wiki")
        except Exception as e:
            log.warning(f"Failed to index new wiki page '{name}': {e}")
        new_mtime = target.stat().st_mtime
        return JSONResponse({"ok": True, "page": name, "modified": new_mtime})

    async def serve_wiki_page(request: Request):
        """Serve the standalone wiki page HTML shell."""
        username = _require_auth(request)
        if not username:
            # Redirect to login
            return JSONResponse({"error": "not authenticated"}, status_code=401)
        wiki_html = Path(__file__).parent / "web" / "static" / "wiki.html"
        if not wiki_html.exists():
            return JSONResponse({"error": "wiki page not found"}, status_code=404)
        return FileResponse(str(wiki_html))

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
        import re
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
        Route("/api/conversations", create_conversation, methods=["POST"]),
        Route("/api/conversations/{id}", get_conversation, methods=["GET"]),
        Route("/api/conversations/{id}", rename_conversation, methods=["PATCH"]),
        Route("/api/conversations/{id}/history", get_conversation_history, methods=["GET"]),
        Route("/api/conversations/{id}/archive", archive_conversation, methods=["POST"]),
        Route("/api/upload/{conv_id}", handle_upload, methods=["POST"]),
        Route("/api/workspace/{path:path}", serve_workspace_file, methods=["GET"]),
        Route("/api/config/files", config_list_files, methods=["GET"]),
        Route("/api/config/files/{path:path}", config_read_file, methods=["GET"]),
        Route("/api/config/files/{path:path}", config_write_file, methods=["PUT"]),
        Route("/api/wiki", wiki_create, methods=["POST"]),
        Route("/api/wiki", wiki_list, methods=["GET"]),
        Route("/api/wiki/{page:path}", wiki_write, methods=["PUT"]),
        Route("/api/wiki/{page:path}", wiki_read, methods=["GET"]),
        Route("/wiki/{page:path}", serve_wiki_page, methods=["GET"]),
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
