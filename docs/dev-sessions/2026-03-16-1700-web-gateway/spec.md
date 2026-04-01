# Web Gateway UI — Spec

## Goal

Add a web-based chat interface to DecafClaw, running on the existing Starlette HTTP server. The web UI is a peer to Mattermost and terminal — another source of agent conversations using the same `run_agent_turn()` and event bus. Works alongside Mattermost or standalone.

## Architecture

### HTTP Server as Top-Level Concern

Restructure startup so the HTTP server is started from the main orchestrator, not nested inside `MattermostClient.run()`:

```
main()
  ├── load config
  ├── assemble system prompt
  ├── create event bus + context
  ├── init MCP servers
  ├── start HTTP server (if enabled) ── web UI, button callbacks, WebSocket
  ├── start Mattermost client (if configured) ── websocket listener
  ├── start heartbeat timer
  └── wait for shutdown signal
```

Extract from `MattermostClient.run()`:
- Signal handling (SIGTERM, SIGINT) → top-level orchestrator
- MCP init/shutdown → top-level
- Heartbeat timer → top-level
- HTTP server start/stop → top-level

`MattermostClient.run()` becomes just: connect, listen, dispatch messages. The Mattermost client and HTTP server are parallel asyncio tasks.

### Web UI Stack

- **Backend:** Starlette routes on the existing HTTP server (same port as button callbacks)
- **Frontend:** Vanilla JS, web components/custom elements, Pico CSS for minimal styling
- **Transport:** WebSocket for real-time chat, same event types as the internal event bus
- **No build step:** No React, no bundlers, no npm. Plain HTML/JS/CSS served from `src/decafclaw/web/static/`

### WebSocket Protocol

JSON messages over WebSocket, mirroring the internal event bus types.

**Client → Server:**
```json
{"type": "send", "conv_id": "abc123", "text": "fix the bug in agent.py"}
{"type": "create_conv", "title": "Bug fix session"}
{"type": "list_convs"}
{"type": "load_history", "conv_id": "abc123", "before": "2026-03-16T10:00:00", "limit": 50}
{"type": "rename_conv", "conv_id": "abc123", "title": "New title"}
{"type": "confirm_response", "context_id": "ctx1", "tool": "shell", "approved": true}
```

**Server → Client:**
```json
{"type": "chunk", "conv_id": "abc123", "text": "Here's"}
{"type": "message_complete", "conv_id": "abc123", "role": "assistant", "text": "full response"}
{"type": "tool_start", "conv_id": "abc123", "tool": "shell", "args": {...}}
{"type": "tool_status", "conv_id": "abc123", "tool": "shell", "message": "Running..."}
{"type": "tool_end", "conv_id": "abc123", "tool": "shell"}
{"type": "confirm_request", "conv_id": "abc123", "context_id": "ctx1", "tool": "shell", "command": "ls ~", "suggested_pattern": "ls *"}
{"type": "conv_list", "conversations": [{"conv_id": "abc", "title": "...", "updated_at": "..."}]}
{"type": "conv_history", "conv_id": "abc123", "messages": [...], "has_more": true}
{"type": "error", "message": "..."}
```

The WebSocket handler subscribes to the event bus for the user's active conversations and forwards events to the browser. Confirmations flow back over WebSocket (no HTTP callback needed for the web UI).

## Conversations

### Per-User, Persistent

- Each web UI user has their own conversation list
- Conversations use the existing archive system (JSONL per conv_id)
- Conv IDs are prefixed for web conversations: `web-{user_id}-{uuid}`
- Conversation metadata (title, created_at, updated_at) stored in a lightweight index file

### Titles

- Auto-generated from the first message or LLM-generated summary after first exchange
- User can rename via the sidebar

### History Loading

- On opening a conversation, load the most recent ~50 messages from the archive
- Scroll-up to load more (paginated, `load_history` with `before` cursor)
- Don't load the full archive into the browser — conversations can be 2MB+

## Authentication

### Token-Based (v1)

Minimal auth — enough to identify users and protect the web UI.

1. Admin runs CLI command: `decafclaw create-token <username>`
2. Gets a token: `dfc_<random>`
3. User visits web UI, enters token on a login page
4. Server validates, sets an HTTP-only cookie (`decafclaw_session`)
5. WebSocket upgrade and all requests authenticated via cookie
6. Tokens stored in `data/{agent_id}/web_tokens.json` (outside workspace)

### No Passwords, No OAuth

This is intentionally bare-bones. The assumption is:
- Few users (1-2)
- Accessed over LAN, VPN, or Tailscale
- The token is the identity

## Web Components

### `<chat-view>`
The main chat component. Manages the WebSocket connection, renders the message list, handles streaming.

- Connects to `ws://<host>:<port>/ws/chat`
- Subscribes to events for the current conversation
- Renders messages as `<chat-message>` elements
- Auto-scrolls on new messages
- Scroll-up triggers history loading

### `<chat-message>`
A single message (user or assistant).

- Displays role, content, timestamp
- Renders markdown in assistant messages (use a lightweight renderer or just `<pre>` for v1)
- Shows tool call indicators inline
- Renders confirmation buttons for `confirm_request` events

### `<chat-input>`
Text input with send button.

- Multi-line textarea
- Enter to send, Shift+Enter for newline
- Disabled while waiting for response

### `<conversation-sidebar>`
List of conversations with create/rename.

- Shows conversation titles, sorted by last activity
- Click to switch conversations
- "New conversation" button
- Rename on double-click or edit button

### `<login-view>`
Token entry form.

- Single text input + submit
- Shown when no valid session cookie

## HTTP Routes (New)

Added to the existing Starlette app:

| Route | Method | Description |
|-------|--------|-------------|
| `/` | GET | Serve the web UI (index.html) |
| `/static/{path}` | GET | Serve static assets (JS, CSS) |
| `/ws/chat` | WebSocket | Chat WebSocket endpoint |
| `/api/auth/login` | POST | Validate token, set cookie |
| `/api/auth/logout` | POST | Clear cookie |
| `/api/conversations` | GET | List user's conversations |
| `/api/conversations/{id}` | GET | Get conversation metadata |
| `/api/conversations/{id}` | PATCH | Rename conversation |
| `/api/conversations` | POST | Create new conversation |

## Configuration

Reuses existing HTTP server config:

| Variable | Default | Description |
|----------|---------|-------------|
| `HTTP_ENABLED` | `false` | Enable HTTP server (required for web UI) |
| `HTTP_HOST` | `0.0.0.0` | Bind address |
| `HTTP_PORT` | `18880` | Listen port |

No new config vars for v1. Auth tokens managed via CLI command.

## File Layout

```
src/decafclaw/
  http_server.py          # Existing — add new routes
  web/
    __init__.py            # Web gateway: WebSocket handler, auth, conversation management
    static/
      index.html           # Main page
      style.css            # Pico CSS + custom styles
      chat.js              # Web components and WebSocket logic
data/{agent_id}/
  web_tokens.json          # Auth tokens (admin-managed)
  web_conversations.json   # Conversation index (titles, metadata)
```

## Out of Scope (v1)

- File editing (HEARTBEAT.md, USER.md, etc.) — follow-up
- Multi-user shared conversations — per-user only
- OAuth / password auth — token only
- Mobile optimization — desktop-first
- Media/file attachments in web UI — text only for v1
- Stop button on in-progress turns (#55) — follow-up
- Full admin panel
