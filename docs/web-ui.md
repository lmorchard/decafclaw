# Web UI

DecafClaw includes a browser-based chat interface with a vault wiki editor, conversation management, model selection, and context inspection. The web UI runs alongside the Mattermost bot and interactive terminal as a third transport.

## Setup

Enable the HTTP server in `.env` or `config.json`:

```bash
HTTP_ENABLED=true
HTTP_PORT=18880
HTTP_SECRET=your-random-secret-here
```

Create a login token:

```bash
uv run decafclaw-token create myusername
```

This prints a token (`dfc_...`). Open `http://localhost:18880` in a browser and log in with your username and token.

Tokens are stored in `data/{agent_id}/web_tokens.json` (admin-managed, outside the workspace). Manage them with:

```bash
uv run decafclaw-token list              # show all tokens
uv run decafclaw-token revoke dfc_...    # revoke a token
```

## Features

### Chat

Real-time streaming chat over WebSocket. Messages stream token-by-token as the LLM generates them. Tool calls show inline progress with status indicators.

- Send messages, receive streamed responses
- File uploads (images, documents) attached to messages
- Cancel in-progress turns
- Confirmation prompts for shell commands and skill activation

### Conversations

The sidebar lists conversations organized into folders:

- **Create** new conversations
- **Rename** and **move** conversations between folders
- **Archive** / **unarchive** conversations
- **Virtual folders**: Archived (preserving folder structure) and System (heartbeat, scheduled, delegated)
- Folder structure is per-user metadata — archive files stay in place

### Vault editor

WYSIWYG markdown editor for vault pages, accessible from the sidebar:

- **Browse** vault pages with folder navigation and breadcrumbs
- **Create**, **edit**, **rename/move**, and **delete** pages
- **Recent pages** list for quick access
- Open pages are automatically injected as context in the active conversation
- `@[[PageName]]` mentions in messages also inject page content

### Files tab

The **Files** tab in the sidebar exposes the agent's workspace as a browsable file tree. See [Files tab](files-tab.md) for in-depth coverage.

- **Browse** workspace files and folders with breadcrumb navigation
- **Recent files** list sorted by last modification time
- Click a file to open it in the file viewer (text, image, markdown)
- Show/hide dotfiles with the "Show hidden" toggle
- Auto-refreshes on agent turn completion so newly-written files appear without manual reload

### Schedules tab

The **Schedules** tab lists all discovered scheduled tasks and lets you manage them without touching files directly:

- **List view**: each row shows the schedule name, a source tier badge (`bundled` / `admin` / `extra` / `workspace`), an "overridden" pill when a copy-on-write overlay is active, the cron expression, and the next estimated run time.
- **Enabled toggle**: a checkbox on each row lets you enable or disable a schedule instantly. Toggling writes a copy-on-write overlay at `data/{agent_id}/schedules/{name}.md` for skill-sourced schedules, or edits the standalone file in place for admin-standalone and workspace-tier schedules.
- **Row click → side panel editor**: clicking a row name opens the schedule in the `#wiki-main` side panel (the same surface as vault pages, workspace files, and agent config). The panel is mutually exclusive with those other views.

**Side panel editor** (`<schedule-page>`):
- **Header**: back arrow (closes the panel), name, source tier badge, "overridden" pill, a **"Run now"** button (fires the task immediately, bypassing the enabled flag and cron timer), and a "Reset to default" button when an overlay is shadowing a skill SCHEDULE.md.
- **Form row**: cron expression input, channel input, and an enabled checkbox. Each field saves on `change` — no separate Save button needed.
- **Body editor**: a full `<wiki-editor>` for the prompt body. Autosaves after 1 second of inactivity or on Ctrl+S / focus-out. The editor sends the file's `mtime` as a `modified` field, but the server does not enforce conflict detection — concurrent edits are last-write-wins. Refresh before editing if you need the latest version.
- **Workspace-tier schedules**: fully editable. Changes write in-place to `workspace/schedules/{name}.md`.
- **URL deep-linking**: opening a schedule sets `?schedule={name}` in the URL. Pasting the URL in a new tab opens the same schedule page directly.

The tab auto-refreshes on activation. Save/reset actions dispatch a `schedule-saved` window event that triggers an immediate silent list refresh.

See [Schedules](schedules.md) for the full model, overlay semantics, and API.

### Model picker

When multiple model configs are defined, a dropdown in the sidebar lets you switch models per-conversation. See [Model Selection](model-selection.md).

### Context inspector

Click the context usage bar in the sidebar to see a popover with:
- Waffle chart showing token allocation by source
- Summary stats (estimated vs actual tokens, window size, compaction threshold)
- Source breakdown table
- Memory candidates with composite scores

See [Context Composer](context-composer.md#context-inspection) for details.

### Copy conversation

A floating `📋 Copy ▾` button in the upper-right of the chat area opens a
small menu with two items: **Copy as JSONL** and **Copy as markdown**. JSONL
is the raw archive bytes (lossless — paste into another LLM for diagnosis).
Markdown is a rendered transcript suitable for Obsidian, sharing, or PR
descriptions. Both are server-rendered via
`GET /api/conversations/{id}/export?format=jsonl|markdown` and written to
the clipboard via `navigator.clipboard`. A toast confirms success or
surfaces the failure reason. The menu hides when no conversation is active.

The markdown form includes `user`, `assistant`, and `tool` turns plus a
short `> [background event]` blockquote for scheduled-task wakes;
metadata-only roles (system prompt, model markers, reflection,
confirmation prompts/responses, cancel/wake markers) are skipped.

### Config editor

Edit admin config files (`SOUL.md`, `AGENT.md`, `HEARTBEAT.md`, etc.) directly in the browser. Changes are written to `data/{agent_id}/`.

### Theme

Light/dark mode toggle.

### Notifications

A bell icon in the sidebar footer renders a red badge when the agent has
emitted noteworthy events (heartbeat completion, scheduled task finish,
background process exit, compaction, reflection rejection). Updates arrive
in real time over the authenticated WebSocket — no polling — so multiple
open tabs stay in sync when one of them marks a notification read. The
bell seeds itself via `GET /api/notifications/unread-count` on mount and on
every WebSocket reconnect. Click the bell to see the last 20 records;
click a row to mark it read and jump to the associated conversation or
vault page. See [Notifications](notifications.md) for the full model, API,
and WebSocket event shapes.

### Canvas panel

A persistent side panel for living documents. The agent drives it with
always-loaded canvas tools (`canvas_new_tab`, `canvas_update`,
`canvas_close_tab`, `canvas_clear`, `canvas_read`); each tool call emits a
`canvas_update` event over WebSocket and the panel re-renders without a page
reload.

**Layout:** `conversation-sidebar | (wiki-main?) | chat-main | (canvas-main?)`.
The wiki panel and canvas panel can both be open simultaneously on desktop;
each occupies a draggable column to the right of chat.

**State:** per-conversation, persisted in
`workspace/conversations/{conv_id}/canvas.json` (sidecar). Loaded on
conversation-select via `GET /api/canvas/{conv_id}`.

**Tabs (Phase 4 multi-tab):** the panel holds multiple tabs. The agent
opens tabs with `canvas_new_tab` (returns a `tab_id`), updates by
`tab_id` with `canvas_update`, and closes by `tab_id` with
`canvas_close_tab`. `canvas_read` returns the full state including all
tabs. See [Widgets — canvas tools](widgets.md#canvas-tools-always-loaded-1)
for the full tool descriptions.

**Tab strip (desktop):** horizontal strip above the content area.
Each tab has a label (truncated) + `[×]` close button. Click a tab body
→ switch active; click `[×]` → close that tab. Active tab highlighted with
a bottom border.

**Tab list (mobile ≤639px):** the strip is replaced by a "Tabs (N) ▼"
disclosure button. Tapping it toggles a vertical list overlay; each row has
a label, active indicator, and `[×]` close. 44px tap targets on rows and
close buttons. See [Web UI — mobile conventions](web-ui-mobile.md#canvas-tabs) for details.

**Lifecycle:**
- `canvas_new_tab(widget_type, data, label?)` — append a tab; set active;
  return `tab_id`. Reveals the panel.
- `canvas_update(tab_id, data)` — replace data on the identified tab;
  preserves panel-hidden state.
- `canvas_close_tab(tab_id)` — remove the identified tab; activates
  neighbor or hides panel if last.
- `canvas_clear()` — empty all tabs; hide the panel.
- `canvas_read()` — return `{active_tab, tabs: [{id, label, widget_type, data}, ...]}`.

**Resummon UI:** when canvas state exists but the panel has been dismissed,
a "📄 Canvas" pill appears in `#chat-main-header` (mirrored to
`#mobile-header` on mobile). Clicking it re-opens the panel. An unread dot
lights up on the pill if a `canvas_update` event arrived while the panel
was hidden.

**Dismiss behavior:** dismissing the panel persists to localStorage
per-conversation (key `canvas-dismissed.{conv_id}`); the canvas sidecar
itself is unaffected. The dismissed state is cleared on `canvas_new_tab`
events, `canvas_clear` events, and resummon click. It is preserved
across page reload and conversation-switch so the user's intent
sticks.

**Standalone views:**
- `/canvas/{conv_id}` — full-screen render of the active tab. Follows
  `active_tab` changes via WebSocket (bare URL, backwards-compat).
- `/canvas/{conv_id}/{tab_id}` — tab-locked view of one specific tab.
  Does not follow active-tab changes; shows "Tab no longer exists" if
  the tab is closed.

Both are auth-gated with the same web-auth as the main UI. Useful for
sharing persistent links (e.g. to a Mattermost user who has a web token).

**Resize:** drag handle on the left edge of `#canvas-main`. Width persists
to `localStorage["canvas-width"]`.

**Mobile:** full-screen overlay (`position: fixed; inset: 0; z-index: 100`).
Mutually exclusive with the wiki overlay — most-recent-open wins. See
[Web UI — mobile conventions](web-ui-mobile.md#canvas-panel) for details.

See [Widgets — Phase 3](widgets.md#phase-3--canvas-panel-and-markdown_document)
and [Phase 4](widgets.md#phase-4--code_block-and-canvas-tabs) for the
widget mode contract and bundled widgets.

## Architecture

### Frontend

Lit web components in `src/decafclaw/web/static/`:

| Component | File | Purpose |
|-----------|------|---------|
| `chat-view` | `components/chat-view.js` | Main chat area with message list |
| `chat-input` | `components/chat-input.js` | Message input with file upload |
| `chat-message` | `components/chat-message.js` | Individual message rendering |
| `conversation-sidebar` | `components/conversation-sidebar.js` | Conversation list, folders, vault browser, model picker |
| `wiki-editor` | `components/wiki-editor.js` | WYSIWYG markdown page editor |
| `wiki-page` | `components/wiki-page.js` | Page viewer/renderer |
| `context-inspector` | `components/context-inspector.js` | Context diagnostics popover |
| `notification-inbox` | `components/notification-inbox.js` | Bell icon + dropdown inbox panel |
| `config-panel` | `components/config-panel.js` | Admin config file editor |
| `confirm-view` | `components/confirm-view.js` | Confirmation dialog for tool approvals |
| `login-view` | `components/login-view.js` | Login screen |
| `canvas-panel` | `components/canvas-panel.js` | Canvas side panel and resummon pill |
| `theme-toggle` | `components/theme-toggle.js` | Light/dark mode switch |

Service layer: `AuthClient`, `WebSocketClient`, `ConversationStore`, `MessageStore`, `ToolStatusStore`, `CanvasState`.

### Backend

The HTTP server (`src/decafclaw/http_server.py`) serves both the web UI and the Mattermost button callbacks. It uses Starlette/uvicorn and runs as an asyncio task in the same process as the bot.

- **REST API** — all conversation and vault management
- **WebSocket** (`/ws/chat`) — real-time chat streaming, history loading, model changes, turn cancellation
- **Static files** — serves the frontend from `src/decafclaw/web/static/`

### REST vs WebSocket

Conversation management is REST-only. WebSocket handles only real-time operations:

**REST** (stateless, standard HTTP):
- Conversation CRUD, folders, archiving
- Vault page CRUD, folder management
- File uploads
- Auth (login/logout/me)
- Config file management

**WebSocket** (persistent connection, real-time):
- Chat messages (send + streamed responses)
- History loading with pagination
- Conversation selection
- Model switching
- Turn cancellation
- Tool confirmation responses

## REST API

### Auth

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/auth/login` | Log in with username + token |
| `POST` | `/api/auth/logout` | Log out (revoke session) |
| `GET` | `/api/auth/me` | Get current user info |

### Conversations

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/conversations?folder=` | List conversations + subfolders in a folder |
| `GET` | `/api/conversations/archived?folder=` | List archived conversations by folder |
| `GET` | `/api/conversations/system?folder=` | List system conversations by type |
| `POST` | `/api/conversations` | Create conversation (optional: folder, model) |
| `GET` | `/api/conversations/{id}` | Get conversation metadata |
| `PATCH` | `/api/conversations/{id}` | Rename and/or move to a folder |
| `DELETE` | `/api/conversations/{id}` | Delete a conversation |
| `GET` | `/api/conversations/{id}/history` | Get conversation history (paginated) |
| `GET` | `/api/conversations/{id}/context` | Get context diagnostics sidecar |
| `GET` | `/api/conversations/{id}/export?format=jsonl\|markdown` | Export raw archive or rendered transcript |
| `POST` | `/api/conversations/{id}/archive` | Archive a conversation |
| `POST` | `/api/conversations/{id}/unarchive` | Unarchive a conversation |

### Conversation folders

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/conversations/folders` | Create a folder |
| `DELETE` | `/api/conversations/folders/{path}` | Delete an empty folder |
| `PUT` | `/api/conversations/folders/{path}` | Rename/move a folder (merges on collision) |

Folder structure is per-user metadata stored in `data/{agent_id}/web/users/{username}/conversation_folders.json`. Archive files stay in place.

### Vault

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/vault?folder=` | List pages and subfolders |
| `GET` | `/api/vault/recent` | Recently modified pages |
| `POST` | `/api/vault` | Create a new page |
| `GET` | `/api/vault/{page}` | Read a page |
| `PUT` | `/api/vault/{page}` | Write/rename a page |
| `DELETE` | `/api/vault/{page}` | Delete a page |
| `POST` | `/api/vault/folders` | Create a vault folder |

### Uploads

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/upload/{conv_id}` | Upload a file attachment |
| `GET` | `/api/workspace/{path}` | Serve a workspace file (images, media) |

### Config

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/config/files` | List editable config files |
| `GET` | `/api/config/files/{path}` | Read a config file |
| `PUT` | `/api/config/files/{path}` | Write a config file |

### Notifications

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/notifications` | List inbox records (newest first) with joined read-state |
| `GET` | `/api/notifications/unread-count` | Count of unread records — seed on bell mount + WebSocket reconnect (see [notifications.md](notifications.md#websocket-push)) |
| `POST` | `/api/notifications/{id}/read` | Mark a single record read (idempotent) |
| `POST` | `/api/notifications/read-all` | Mark all currently-visible records read |

### Canvas

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/canvas/{conv_id}` | Get full canvas state (active_tab + tabs array) |
| `POST` | `/api/canvas/{conv_id}/new_tab` | Append a new tab (widget_type, data, label?); returns `{ok, tab_id}` |
| `POST` | `/api/canvas/{conv_id}/active_tab` | Switch active tab; body `{tab_id}` |
| `POST` | `/api/canvas/{conv_id}/close_tab` | Close a tab; body `{tab_id}` |
| `GET` | `/canvas/{conv_id}` | Standalone view — renders active tab; follows active-tab changes via WebSocket |
| `GET` | `/canvas/{conv_id}/{tab_id}` | Standalone tab-locked view — renders one tab; doesn't follow active changes |

### Other

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/health` | Health check |
| `POST` | `/actions/confirm` | Mattermost button callback |
| `POST` | `/actions/cancel` | Mattermost cancel callback |

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `HTTP_ENABLED` | `false` | Enable the HTTP server (required for web UI) |
| `HTTP_HOST` | `0.0.0.0` | Bind address |
| `HTTP_PORT` | `18880` | Listen port |
| `HTTP_SECRET` | `""` | Shared secret for Mattermost button callbacks |
| `HTTP_BASE_URL` | `""` | External URL (auto-detected from host/port if empty) |

See [Configuration Reference](config.md#http) for the full `http` config group.

## Key files

- `src/decafclaw/http_server.py` — HTTP server, all REST routes, WebSocket endpoint
- `src/decafclaw/web/auth.py` — Token-based auth, `decafclaw-token` CLI
- `src/decafclaw/web/conversations.py` — Conversation index metadata
- `src/decafclaw/web/conversation_folders.py` — Per-user folder management
- `src/decafclaw/web/websocket.py` — WebSocket message handlers
- `src/decafclaw/web/static/` — Frontend components and service layer
