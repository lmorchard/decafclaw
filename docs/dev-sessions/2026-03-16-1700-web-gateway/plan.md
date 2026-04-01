# Web Gateway UI — Plan

## Ordering Rationale

This is a large feature with a clear dependency chain:

1. **Restructure startup** (Phase 1) — must come first, everything else depends on it
2. **Auth** (Phase 2) — needed before WebSocket, since connections must be authenticated
3. **Conversation management** (Phase 3) — data layer for the web UI
4. **WebSocket handler** (Phase 4) — the real-time bridge between browser and agent
5. **Frontend** (Phase 5) — the web components and UI
6. **Integration** (Phase 6) — wire it all together, live testing, docs

Each phase is independently testable. The backend phases (1-4) can be fully tested with pytest and manual `curl`/`wscat` before any frontend exists.

---

## Phase 1: Restructure Startup — Top-Level Orchestrator

**Goal:** Extract MCP init, heartbeat, HTTP server, and signal handling from `MattermostClient.run()` into a shared orchestrator. The Mattermost client becomes a simple connect-and-listen task.

### Step 1.1: Create the orchestrator

**Prompt:**
> Create `src/decafclaw/runner.py` — the top-level orchestrator that manages all subsystems:
>
> ```python
> async def run_all(app_ctx):
>     """Run all subsystems: MCP, HTTP server, Mattermost, heartbeat."""
> ```
>
> The orchestrator should:
> 1. Set up signal handlers (SIGTERM, SIGINT) → set a shutdown_event
> 2. Init MCP servers
> 3. Start HTTP server task (if http_enabled)
> 4. Start Mattermost client task (if mattermost configured)
> 5. Start heartbeat timer task (if interval configured)
> 6. Wait for shutdown signal
> 7. Cancel all tasks gracefully in finally block
> 8. Shutdown MCP
>
> Update `__init__.py` `main()` to use the orchestrator:
> ```python
> if config.mattermost_url and config.mattermost_token:
>     asyncio.run(run_all(app_ctx))
> else:
>     asyncio.run(run_interactive(app_ctx))
> ```
>
> Actually — `run_all` should also work without Mattermost (just HTTP + heartbeat). So the logic becomes:
> ```python
> if config.mattermost_url or config.http_enabled:
>     asyncio.run(run_all(app_ctx))
> else:
>     asyncio.run(run_interactive(app_ctx))
> ```
>
> Run `make check && make test` after.

### Step 1.2: Slim down MattermostClient.run()

**Prompt:**
> Refactor `MattermostClient.run()` to remove the subsystems now handled by the orchestrator:
>
> - Remove MCP init/shutdown (now in runner.py)
> - Remove HTTP server start/stop (now in runner.py)
> - Remove heartbeat timer start/stop (now in runner.py)
> - Remove signal handler setup (now in runner.py)
> - Accept `shutdown_event` as a parameter instead of creating one
>
> The method should become roughly:
> ```python
> async def run(self, app_ctx, shutdown_event):
>     await self.connect()
>     # ... message handling setup ...
>     try:
>         await self.listen(on_message_sync, shutdown_event=shutdown_event)
>     finally:
>         if agent_tasks:
>             await asyncio.gather(*agent_tasks, return_exceptions=True)
>         await self.close()
> ```
>
> The heartbeat `_make_heartbeat_cycle` method can move to `runner.py` or stay on MattermostClient for now (it's only used in Mattermost mode).
>
> Run `make check && make test` after.

### Step 1.3: Verify interactive mode still works

**Prompt:**
> Verify that `run_interactive()` in `agent.py` still works independently. It has its own MCP init, heartbeat, and shutdown handling. That's fine for now — terminal mode is self-contained.
>
> Run `make check && make test` after. Manually test `make run` to verify interactive mode.

### Step 1.4: Commit

> Commit: "Restructure startup: extract top-level orchestrator from MattermostClient"

---

## Phase 2: Token Authentication

**Goal:** CLI token management and cookie-based session auth for the web UI.

### Step 2.1: Token management module

**Prompt:**
> Create `src/decafclaw/web/__init__.py` and `src/decafclaw/web/auth.py`:
>
> ```python
> # auth.py
> """Token-based authentication for the web gateway."""
>
> import json
> import secrets
> from pathlib import Path
>
> def tokens_path(config) -> Path:
>     return config.agent_path / "web_tokens.json"
>
> def load_tokens(config) -> dict[str, str]:
>     """Load {token: username} mapping."""
>
> def create_token(config, username: str) -> str:
>     """Generate and store a token for a user. Returns the token."""
>
> def validate_token(config, token: str) -> str | None:
>     """Validate a token. Returns the username or None."""
>
> def revoke_token(config, token: str) -> bool:
>     """Revoke a token. Returns True if found."""
> ```
>
> Tokens are `dfc_<32-char-urlsafe>`. Stored in `data/{agent_id}/web_tokens.json`.
>
> Also create a CLI entry point for token management:
> ```python
> def token_cli():
>     """CLI: decafclaw-token create <username> | list | revoke <token>"""
> ```
>
> Register `decafclaw-token` as a console script in `pyproject.toml`.
>
> Run `make check` after.

### Step 2.2: Auth routes in HTTP server

**Prompt:**
> Add auth routes to the Starlette app in `http_server.py`:
>
> - `POST /api/auth/login` — accepts `{"token": "dfc_..."}`, validates, sets HTTP-only cookie `decafclaw_session` (value is the token), returns `{"username": "..."}` or 401
> - `POST /api/auth/logout` — clears the cookie
> - `GET /api/auth/me` — returns current user from cookie, or 401
>
> Add a helper `get_current_user(request, config) -> str | None` that reads the cookie and validates the token. This will be used by all authenticated routes.
>
> Run `make check` after.

### Step 2.3: Tests for auth

**Prompt:**
> Write tests in `tests/test_web_auth.py`:
>
> - Create a token, validate it
> - Invalid token returns None
> - Revoke a token
> - Login sets cookie
> - Login with bad token returns 401
> - /api/auth/me returns username when authenticated
> - /api/auth/me returns 401 when not authenticated
> - Logout clears cookie
>
> Run `make check && make test` after.

### Step 2.4: Commit

> Commit: "Add token-based auth for web gateway"

---

## Phase 3: Conversation Management

**Goal:** REST API for managing per-user web conversations with persistent metadata.

### Step 3.1: Conversation index module

**Prompt:**
> Create `src/decafclaw/web/conversations.py`:
>
> ```python
> """Conversation index — lightweight metadata for web UI conversations."""
>
> @dataclass
> class ConversationMeta:
>     conv_id: str
>     user_id: str
>     title: str
>     created_at: str  # ISO timestamp
>     updated_at: str
>
> class ConversationIndex:
>     """Manages the conversation metadata index."""
>
>     def __init__(self, config):
>         self.path = config.agent_path / "web_conversations.json"
>
>     def list_for_user(self, user_id: str) -> list[ConversationMeta]
>     def create(self, user_id: str, title: str = "") -> ConversationMeta
>     def get(self, conv_id: str) -> ConversationMeta | None
>     def rename(self, conv_id: str, title: str) -> ConversationMeta | None
>     def touch(self, conv_id: str)  # update updated_at
>     def load_history(self, config, conv_id: str, limit: int = 50, before: str = "") -> tuple[list[dict], bool]
> ```
>
> `create()` generates conv_ids as `web-{user_id}-{uuid[:8]}`.
>
> `load_history()` reads from the existing archive system (JSONL), returns the last N messages and a `has_more` flag. If `before` timestamp is provided, only return messages before that point.
>
> Run `make check` after.

### Step 3.2: Conversation REST routes

**Prompt:**
> Add conversation routes to the Starlette app:
>
> - `GET /api/conversations` — list conversations for the authenticated user
> - `POST /api/conversations` — create a new conversation (optional `title`)
> - `GET /api/conversations/{id}` — get conversation metadata
> - `PATCH /api/conversations/{id}` — rename conversation
> - `GET /api/conversations/{id}/history?limit=50&before=` — load paginated history
>
> All routes require authentication (use `get_current_user`).
>
> Run `make check` after.

### Step 3.3: Tests for conversations

**Prompt:**
> Write tests in `tests/test_web_conversations.py`:
>
> - Create a conversation, verify metadata
> - List conversations returns user's only
> - Rename a conversation
> - Load history with pagination
> - Load empty conversation
> - Touch updates updated_at
> - Auth required on all endpoints
>
> Run `make check && make test` after.

### Step 3.4: Commit

> Commit: "Add conversation management for web gateway"

---

## Phase 4: WebSocket Chat Handler

**Goal:** Real-time chat over WebSocket, bridging browser to `run_agent_turn()` via the event bus.

### Step 4.1: WebSocket endpoint

**Prompt:**
> Create `src/decafclaw/web/websocket.py`:
>
> ```python
> """WebSocket handler for web gateway chat."""
>
> async def websocket_chat(websocket):
>     """Handle a WebSocket chat connection."""
> ```
>
> The handler should:
> 1. Authenticate from cookie (reject if no valid session)
> 2. Accept the WebSocket connection
> 3. Subscribe to the event bus for the user's conversations
> 4. Enter a receive loop:
>    - `type: "send"` → run_agent_turn in a task, stream events to the WebSocket
>    - `type: "create_conv"` → create conversation via ConversationIndex
>    - `type: "list_convs"` → return conversation list
>    - `type: "load_history"` → return paginated history
>    - `type: "rename_conv"` → rename via ConversationIndex
>    - `type: "confirm_response"` → publish tool_confirm_response on event bus
> 5. On disconnect, unsubscribe from event bus, clean up
>
> For `type: "send"`:
> - Fork a request context (like Mattermost does)
> - Set up a streaming callback that sends `chunk` events over WebSocket
> - Set up an event subscriber that forwards `tool_start`, `tool_status`, `tool_end`, `confirm_request` events
> - Call `run_agent_turn(ctx, text, history)`
> - Send `message_complete` when done
> - Handle errors gracefully
>
> Add the WebSocket route to the Starlette app: `WebSocketRoute("/ws/chat", websocket_chat)`
>
> Run `make check` after.

### Step 4.2: Tests for WebSocket handler

**Prompt:**
> Write tests in `tests/test_web_websocket.py`:
>
> Testing WebSocket is harder — use Starlette's `TestClient` which supports WebSocket testing, or mock the WebSocket interface.
>
> Tests:
> - Unauthenticated WebSocket rejected
> - list_convs returns conversations
> - create_conv creates and returns metadata
> - send message triggers agent turn (mock run_agent_turn)
> - Events are forwarded to WebSocket
> - confirm_response publishes to event bus
>
> Run `make check && make test` after.

### Step 4.3: Commit

> Commit: "Add WebSocket chat handler for web gateway"

---

## Phase 5: Frontend

**Goal:** Build the web UI with vanilla JS — client service layer first, then presentational web components on top.

### Frontend Architecture

All JS files use JSDoc type annotations for IDE support and type checking via `tsc --noEmit --checkJs`. Add a `make check-js` target and a `tsconfig.json` (checkJs mode, no compilation).

```
index.html                  # Shell — loads scripts, layout
style.css                   # Pico CSS + custom layout
lib/
  auth-client.js            # REST calls for login/logout/me
  websocket-client.js       # WebSocket connection, reconnect, message dispatch
  conversation-store.js     # State: conversations, messages, streaming. Talks to WebSocketClient.
components/
  login-view.js             # Token entry form (uses AuthClient)
  conversation-sidebar.js   # Conversation list (reads ConversationStore)
  chat-view.js              # Message list + streaming (reads ConversationStore)
  chat-message.js           # Single message (pure presentation)
  chat-input.js             # Text input (dispatches events up)
app.js                      # Top-level wiring: instantiate services, mount components
```

**Service layer** (plain JS classes, no DOM):
- `AuthClient` — REST calls for login/logout/session check
- `WebSocketClient` — manages WebSocket lifecycle, emits typed events, handles reconnection
- `ConversationStore` — holds all conversation state (list, messages, streaming buffer, busy flags). Provides methods (`send`, `loadHistory`, `create`, `rename`). Listens to WebSocketClient. Emits change events for components to react to.

**Component layer** (web components, presentational):
- Render state from ConversationStore
- Dispatch user actions as custom events
- Don't talk to the server directly

### Step 5.1: Static file serving and HTML shell

**Prompt:**
> Create the static file structure and add serving to the Starlette app:
>
> ```
> src/decafclaw/web/static/
>   index.html
>   style.css
>   lib/
>     auth-client.js
>     websocket-client.js
>     conversation-store.js
>   components/
>     login-view.js
>     conversation-sidebar.js
>     chat-view.js
>     chat-message.js
>     chat-input.js
>   app.js
> ```
>
> Add routes to the Starlette app:
> - `Route("/", serve_index)` — serves index.html
> - `Mount("/static", StaticFiles(directory=static_dir))` — serves assets
>
> Create `index.html` with:
> - Pico CSS from CDN
> - `<script type="module">` tags for lib/ and components/
> - Layout: sidebar + main content area
> - `<login-view>` shown initially, chat UI hidden
>
> Run `make check` after. Verify index.html loads in browser.

### Step 5.2: AuthClient + login component

**Prompt:**
> Implement `lib/auth-client.js`:
>
> ```js
> export class AuthClient extends EventTarget {
>   async login(token) { /* POST /api/auth/login, emit 'login' event */ }
>   async logout() { /* POST /api/auth/logout, emit 'logout' event */ }
>   async checkSession() { /* GET /api/auth/me, return user or null */ }
>   get currentUser() { ... }
> }
> ```
>
> Implement `components/login-view.js`:
> - Token input + submit button
> - On submit, calls `authClient.login(token)`
> - Shows error on failure
> - Receives AuthClient instance via property or constructor

### Step 5.3: WebSocketClient

**Prompt:**
> Implement `lib/websocket-client.js`:
>
> ```js
> export class WebSocketClient extends EventTarget {
>   constructor(url) { ... }
>   connect() { /* open WebSocket, set up onmessage/onclose */ }
>   disconnect() { ... }
>   send(message) { /* JSON.stringify and send */ }
>   // Emits: 'message' (parsed JSON), 'open', 'close', 'error'
>   // Auto-reconnect on close with backoff
> }
> ```
>
> This is a thin wrapper — just connection management and JSON serialization. All message interpretation happens in ConversationStore.

### Step 5.4: ConversationStore

**Prompt:**
> Implement `lib/conversation-store.js`:
>
> ```js
> export class ConversationStore extends EventTarget {
>   constructor(wsClient) { ... }
>
>   // State
>   get conversations() { ... }      // sorted by updated_at
>   get currentConvId() { ... }
>   get currentMessages() { ... }
>   get isBusy() { ... }             // agent responding?
>   get streamingText() { ... }      // partial response being streamed
>
>   // Actions
>   selectConversation(convId) { ... }
>   createConversation(title) { ... }
>   renameConversation(convId, title) { ... }
>   sendMessage(text) { ... }
>   loadHistory(before) { ... }
>   respondToConfirm(contextId, tool, approved, extra) { ... }
>   listConversations() { ... }
>
>   // Emits: 'change' (any state update — components re-render)
> }
> ```
>
> The store listens to WebSocketClient 'message' events and updates internal state:
> - `conv_list` → update conversations array
> - `conv_history` → prepend to current messages
> - `chunk` → append to streaming buffer
> - `message_complete` → finalize message, clear streaming buffer, set busy=false
> - `tool_start/status/end` → update tool state on current message
> - `confirm_request` → add to pending confirmations
> - `error` → surface error state
>
> On any state change, dispatch a 'change' event so components know to re-render.

### Step 5.5: Conversation sidebar component

**Prompt:**
> Implement `components/conversation-sidebar.js`:
>
> - Reads from ConversationStore.conversations
> - Renders list sorted by updated_at (newest first)
> - "New conversation" button at top
> - Click selects (calls store.selectConversation)
> - Active conversation highlighted
> - Rename via double-click or edit button (calls store.renameConversation)
> - Listens to store 'change' event to re-render

### Step 5.6: Chat view, message, and input components

**Prompt:**
> Implement `components/chat-view.js`:
> - Reads from ConversationStore.currentMessages and .streamingText
> - Renders `<chat-message>` elements for each message
> - Appends a streaming `<chat-message>` while isBusy with streamingText
> - Auto-scrolls on new content
> - Scroll-to-top triggers store.loadHistory()
> - Renders confirmation buttons inline from confirm_request events
> - Listens to store 'change' event
>
> Implement `components/chat-message.js`:
> - Pure presentation: role, content, timestamp
> - Whitespace-preserved text for v1 (or basic markdown)
> - Tool call indicators shown inline
>
> Implement `components/chat-input.js`:
> - Multi-line textarea
> - Enter to send, Shift+Enter for newline
> - Disabled when store.isBusy
> - Dispatches 'send' custom event with text

### Step 5.7: App wiring

**Prompt:**
> Implement `app.js` — the top-level coordinator:
>
> ```js
> import { AuthClient } from './lib/auth-client.js';
> import { WebSocketClient } from './lib/websocket-client.js';
> import { ConversationStore } from './lib/conversation-store.js';
>
> const auth = new AuthClient();
> const ws = new WebSocketClient(wsUrl);
> const store = new ConversationStore(ws);
>
> // Check session on load
> // On login → connect WebSocket, show chat UI
> // Wire component events: sidebar select → store, input send → store
> // On logout → disconnect, show login
> ```
>
> Test the full flow in a browser: login → create conversation → send message → see streaming response.

### Step 5.8: Commit

> Commit: "Add web gateway frontend with vanilla JS web components"

---

## Phase 6: Integration & Polish

### Step 6.1: Auto-generate conversation titles

**Prompt:**
> After the first agent response in a new conversation, auto-generate a title:
>
> - Use the first ~100 chars of the user's first message as the default title
> - Optionally, truncate at a word boundary and add "..."
>
> (Skip LLM-generated titles for v1 — keep it simple.)

### Step 6.2: Manual end-to-end test

**Prompt:**
> Test the full flow:
>
> 1. `decafclaw-token create lmorchard` — create a token
> 2. Start with `HTTP_ENABLED=true make dev`
> 3. Open `http://localhost:18880/` in browser
> 4. Enter token on login page
> 5. Create a new conversation
> 6. Send a message, see streaming response
> 7. Trigger a shell command, see confirmation buttons in chat
> 8. Approve/deny via buttons
> 9. Open sidebar, switch between conversations
> 10. Close tab, reopen — verify conversations persist
> 11. Verify Mattermost still works alongside the web UI
>
> Document issues and fix them.

### Step 6.3: Update docs

**Prompt:**
> Update documentation:
>
> 1. `CLAUDE.md` — add web/ to key files, add runner.py
> 2. `docs/http-server.md` — update to reflect web gateway routes and WebSocket
> 3. `README.md` — add web gateway to features list
> 4. `docs/installation.md` — add token management section
> 5. Session notes — write summary

### Step 6.4: Commit

> Commit: "Polish and document web gateway UI"

---

## Risk Notes

- **Phase 1 (restructure startup)** is the riskiest — it changes the process lifecycle for Mattermost mode. Test thoroughly. The `run_all` orchestrator must handle all the edge cases that `MattermostClient.run()` currently handles (graceful shutdown, in-flight task waiting, etc.).
- **Phase 4 (WebSocket handler)** is complex — it bridges the async event bus to a WebSocket connection, managing per-user conversation subscriptions. The event bus was designed for this, but we haven't done user-scoped subscriptions before.
- **Phase 5 (frontend)** is the least risky technically but the most time-consuming. Web components are straightforward but there's a lot of UI to build. Can be iterated on after the backend is solid.
- **Conversation history pagination** needs care — reading the last N lines from a potentially large JSONL file. For v1, reading the whole file and slicing is fine (files are under 2MB). Optimize later if needed.
