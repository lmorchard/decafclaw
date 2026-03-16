# Interactive Buttons — Plan

## Ordering Rationale

We build bottom-up: config → HTTP server module → callback handler → wire into Mattermost → button UI. Each phase is independently testable. The HTTP server is fully functional before we touch the confirmation flow, so we can verify the infrastructure separately.

Each phase ends with `make check && make test`.

---

## Phase 1: Config & Dependencies

### Step 1.1: Add config fields and install starlette/uvicorn

**Prompt:**
> Add the HTTP server config fields to `src/decafclaw/config.py`:
>
> ```python
> # HTTP server settings
> http_enabled: bool = False
> http_host: str = "0.0.0.0"
> http_port: int = 18880
> http_secret: str = ""
> http_base_url: str = ""  # auto-detected if empty
>
> # Mattermost confirmation settings
> mattermost_disable_emoji_confirms: bool = False
> ```
>
> Add the corresponding `os.getenv()` / `_parse_bool()` lines in `load_config()`.
>
> Also add a computed property:
> ```python
> @property
> def http_callback_base(self) -> str:
>     """Base URL for HTTP callbacks. Auto-detected from host/port if not set."""
>     if self.http_base_url:
>         return self.http_base_url.rstrip("/")
>     return f"http://{self.http_host}:{self.http_port}"
> ```
>
> Install dependencies: `uv add starlette uvicorn`
>
> Run `make check` after.

### Step 1.2: Commit

> Commit: "Add HTTP server config fields and starlette/uvicorn dependencies"

---

## Phase 2: HTTP Server Module

### Step 2.1: Create the HTTP server module

**Prompt:**
> Create `src/decafclaw/http_server.py` with a Starlette app and lifecycle management:
>
> ```python
> """HTTP server — Starlette ASGI app for interactive callbacks and future web UI."""
>
> import logging
> from starlette.applications import Starlette
> from starlette.requests import Request
> from starlette.responses import JSONResponse
> from starlette.routing import Route
>
> log = logging.getLogger(__name__)
>
>
> def create_app(config, event_bus) -> Starlette:
>     """Create the Starlette ASGI app with routes."""
>
>     async def health(request: Request) -> JSONResponse:
>         return JSONResponse({"status": "ok"})
>
>     async def handle_confirm(request: Request) -> JSONResponse:
>         # Verify secret
>         secret = request.query_params.get("secret", "")
>         if secret != config.http_secret:
>             return JSONResponse({"error": "invalid secret"}, status_code=403)
>
>         body = await request.json()
>         user_id = body.get("user_id", "")
>         context = body.get("context", {})
>         action = context.get("action", "")
>         context_id = context.get("context_id", "")
>         tool_name = context.get("tool", "")
>         original_message = context.get("original_message", "")
>
>         # Map action to event fields
>         approved = action in ("approve", "always", "add_pattern")
>         always = action == "always"
>         add_pattern = action == "add_pattern"
>
>         # Publish confirmation event
>         await event_bus.publish({
>             "type": "tool_confirm_response",
>             "context_id": context_id,
>             "tool": tool_name,
>             "approved": approved,
>             **({"always": True} if always else {}),
>             **({"add_pattern": True} if add_pattern else {}),
>         })
>
>         # Determine result label
>         if action == "approve":
>             label = "✅ Approved"
>         elif action == "always":
>             label = "✅ Always approved"
>         elif action == "add_pattern":
>             label = "📓 Approved + pattern added"
>         else:
>             label = "👎 Denied"
>
>         # Resolve Mattermost username for display
>         # (user_id is available but username requires an API call —
>         #  just use the action label for now)
>
>         # Return update response — removes buttons, shows result
>         return JSONResponse({
>             "update": {
>                 "message": f"{original_message}\n\n**Result:** {label}",
>                 "props": {"attachments": []},
>             }
>         })
>
>     routes = [
>         Route("/health", health, methods=["GET"]),
>         Route("/actions/confirm", handle_confirm, methods=["POST"]),
>     ]
>
>     return Starlette(routes=routes)
>
>
> async def run_http_server(config, event_bus):
>     """Start the HTTP server as an asyncio task."""
>     import uvicorn
>     app = create_app(config, event_bus)
>     server_config = uvicorn.Config(
>         app,
>         host=config.http_host,
>         port=config.http_port,
>         log_level="info",
>     )
>     server = uvicorn.Server(server_config)
>     log.info(f"HTTP server starting on {config.http_host}:{config.http_port}")
>     await server.serve()
> ```
>
> Run `make check` after.

### Step 2.2: Tests for HTTP server

**Prompt:**
> Write tests for the HTTP server in `tests/test_http_server.py`:
>
> Use Starlette's `TestClient` (from `starlette.testclient`) or `httpx.AsyncClient` with the ASGI transport to test the routes without starting a real server.
>
> Tests:
> - GET /health returns {"status": "ok"}
> - POST /actions/confirm with valid secret publishes event and returns update
> - POST /actions/confirm with wrong secret returns 403
> - POST /actions/confirm with action "approve" publishes approved=True
> - POST /actions/confirm with action "deny" publishes approved=False
> - POST /actions/confirm with action "always" publishes approved=True, always=True
> - POST /actions/confirm with action "add_pattern" publishes approved=True, add_pattern=True
> - Response includes original_message in the update
>
> Use the existing conftest fixtures (config, ctx) and subscribe to the event bus to capture published events.
>
> Run `make check && make test` after.

### Step 2.3: Commit

> Commit: "Add HTTP server module with Starlette app and confirm handler"

---

## Phase 3: Wire HTTP Server into Bot Lifecycle

### Step 3.1: Start HTTP server in MattermostClient.run()

**Prompt:**
> In `src/decafclaw/mattermost.py`, start the HTTP server as an asyncio task in `run()` if `config.http_enabled` is true:
>
> 1. After `await init_mcp(app_ctx.config)` and before the websocket listener, add:
>    ```python
>    # Start HTTP server for interactive callbacks
>    http_task = None
>    if app_ctx.config.http_enabled:
>        from .http_server import run_http_server
>        http_task = asyncio.create_task(
>            run_http_server(app_ctx.config, app_ctx.event_bus)
>        )
>        log.info(f"HTTP server enabled on {app_ctx.config.http_host}:{app_ctx.config.http_port}")
>    ```
>
> 2. In the finally block, cancel the HTTP task alongside the heartbeat:
>    ```python
>    if http_task:
>        http_task.cancel()
>        try:
>            await http_task
>        except asyncio.CancelledError:
>            pass
>    ```
>
> 3. Store `http_enabled` and `http_callback_base` on the MattermostClient instance (or pass config through) so the confirmation message builder can access them later.
>
> Run `make check && make test` after.

### Step 3.2: Commit

> Commit: "Wire HTTP server into Mattermost bot lifecycle"

---

## Phase 4: Button-Based Confirmation Messages

### Step 4.1: Build button attachment helper

**Prompt:**
> In `src/decafclaw/http_server.py` (or a new `src/decafclaw/buttons.py` if you prefer), add a helper function that builds the Mattermost message attachment with interactive buttons:
>
> ```python
> def build_confirm_buttons(config, tool_name, command, suggested_pattern,
>                           context_id, original_message) -> list[dict]:
>     """Build Mattermost attachment with interactive action buttons.
>
>     Returns the attachments list to include in a post's props.
>     Returns [] if HTTP server is not enabled.
>     """
> ```
>
> The function should:
> - Return `[]` if `config.http_enabled` is false
> - Build the callback URL: `{config.http_callback_base}/actions/confirm?secret={config.http_secret}`
> - For shell tool: buttons are Approve, Deny, Allow Pattern (with pattern in label)
> - For other tools: buttons are Approve, Deny, Always
> - Each button has an `integration` field with the callback URL and `context` dict containing `action`, `context_id`, `tool`, `suggested_pattern`, and `original_message`
> - Use button styles: `primary` for Approve, `danger` for Deny, default for others
>
> Run `make check` after.

### Step 4.2: Integrate buttons into confirmation posting

**Prompt:**
> Modify `ConversationDisplay.on_confirm_request()` in `src/decafclaw/mattermost.py`:
>
> 1. Build the confirmation message text (same as now)
> 2. If `config.mattermost_disable_emoji_confirms` is false, keep the emoji instruction line
> 3. If `config.mattermost_disable_emoji_confirms` is true, omit the emoji line
> 4. For shell tool, remove "always" from emoji options (only approve/deny/pattern)
> 5. Build button attachments via `build_confirm_buttons()`
> 6. Post the message with attachments. The Mattermost API accepts attachments in the post body as `props.attachments`:
>    ```python
>    body = {"channel_id": channel_id, "message": msg}
>    if attachments:
>        body["props"] = {"attachments": attachments}
>    ```
>    You may need to add an optional `attachments` parameter to `MattermostClient.send()` or create a new method.
> 7. Still start `_poll_confirmation` for emoji reactions (unless emoji is disabled)
>
> The key insight: `on_confirm_request` now needs access to the config (for `http_enabled`, `http_callback_base`, `http_secret`, `mattermost_disable_emoji_confirms`). Pass config to `ConversationDisplay.__init__()` or access via a stored reference.
>
> Run `make check && make test` after.

### Step 4.3: Tests for button building

**Prompt:**
> Write tests for `build_confirm_buttons()` in `tests/test_buttons.py`:
>
> - Returns empty list when HTTP not enabled
> - Returns buttons with correct callback URL including secret
> - Shell tool gets Approve/Deny/Allow Pattern (no Always)
> - Other tools get Approve/Deny/Always
> - Context includes context_id, tool, original_message
> - Button styles: primary for Approve, danger for Deny
>
> Run `make check && make test` after.

### Step 4.4: Commit

> Commit: "Add interactive button confirmation UI for Mattermost"

---

## Phase 5: Shell Tool "Always" Removal

### Step 5.1: Remove "Always" from shell tool emoji flow

**Prompt:**
> In `MattermostClient._poll_confirmation()`, the emoji polling checks for ✅ (always) on all tools including shell. Per the spec, shell should only support approve/deny/pattern — not "always".
>
> Modify the emoji handling:
> - If the tool name is "shell", skip the ✅ always emoji check
> - The 📓 pattern emoji stays for shell
> - All other tools keep ✅ always and skip 📓 pattern
>
> Also update the emoji instruction text in `on_confirm_request()`:
> - Shell: `React: 👍 approve | 👎 deny | 📓 allow \`{pattern}\``
> - Others: `React: 👍 approve | 👎 deny | ✅ always`
>
> Run `make check && make test` after.

### Step 5.2: Commit

> Commit: "Remove 'always' option from shell tool confirmations"

---

## Phase 6: Integration Testing & Docs

### Step 6.1: Manual end-to-end test

**Prompt:**
> Test the full flow:
>
> 1. Set `HTTP_ENABLED=true`, `HTTP_SECRET=test123`, `HTTP_PORT=18880` in `.env`
> 2. Start `make dev`
> 3. Trigger a shell command confirmation
> 4. Verify buttons appear in Mattermost
> 5. Click Approve button
> 6. Verify the message updates (buttons removed, result shown)
> 7. Verify the shell command executes
> 8. Test Deny button
> 9. Test emoji reaction alongside buttons (both should work)
> 10. Test with `MATTERMOST_DISABLE_EMOJI_CONFIRMS=true` (no emoji line)
>
> Document any issues and fix them.

### Step 6.2: Update docs

**Prompt:**
> Update documentation:
>
> 1. `CLAUDE.md` — add HTTP server to key files list
> 2. `docs/installation.md` — add HTTP server config vars
> 3. `docs/deployment.md` — note about HTTP port and firewall if needed
> 4. `README.md` — mention interactive buttons in features list
> 5. Create `docs/http-server.md` — document the HTTP server, routes, configuration, and button confirmation flow
> 6. Session notes — write summary

### Step 6.3: Final commit

> Commit: "Document HTTP server and interactive button confirmation"

---

## Risk Notes

- **Phase 2 (HTTP server)** is low risk — Starlette + uvicorn are well-tested. The main question is whether uvicorn's `Server.serve()` plays nicely as an asyncio task alongside our websocket listener.
- **Phase 4 (button integration)** is medium risk — we're modifying the confirmation message format and `on_confirm_request`, which is active code. The parallel emoji+button path needs careful testing to ensure race conditions are handled.
- **Mattermost button callback format** — we're working from docs, not tested. The exact payload shape and response format may need adjustment during Phase 6 live testing. This is expected.
- **`original_message` in context** — Mattermost has a max size for button context. If the command text is very long, we may need to truncate. Handle this in `build_confirm_buttons`.
