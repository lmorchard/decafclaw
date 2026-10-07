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

**Command autocomplete.** Typing `/` or `!` at the start of a line in the
composer opens a suggestion menu listing every user-invokable skill command
plus every MCP prompt (`mcp__<server>__<prompt>`), each with its
`argument-hint` and description. Matching is a fuzzy ordered subsequence, so
`/dsum` finds `mcp__demo__summarize`. Arrow keys move the highlight, Tab
commits it, Escape dismisses, Enter still sends.

The list arrives over the chat WebSocket: the client sends `list_commands` on
conversation-select and on every reconnect (so a newly connected MCP server
shows up), and the server answers `command_list`. It deliberately does *not*
ride on socket `open` — a reconnect with no conversation selected must put
nothing on the wire (#704). The consequence is that the menu is empty until a
conversation exists, which for a fresh session means the first message.

**Resource autocomplete.** Typing `@` requests file, vault-page, and MCP-resource
matches from `GET /api/autocomplete?q=...`. The composer uses the generated
browser method and keeps the generated discriminated result types through menu
rendering and insertion. Files insert as `@path`, vault pages as `@[[Page]]`, and
MCP resources as `@mcp/server/name`. A response for an older query is ignored
when the user has already changed the token.

**Input focus.** The composer takes focus on a conversation switch, and again
when the agent finishes a turn — but only if the user hasn't moved focus
somewhere else in the meantime (canvas terminal, wiki editor, a widget). It
never grabs focus mid-turn. The policy lives in `static/lib/chat-focus.js`;
`app.js` consults it from the conversation store's `change` handler, which
fires on *every* WebSocket message, so a looser condition there steals focus
on each streamed chunk.

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
- **Frontmatter editing**: a metadata strip above the body shows `summary` and
  tags in view mode; edit mode swaps it for typed controls plus a raw-YAML
  escape hatch. See [Editing frontmatter in the web UI](vault.md#editing-frontmatter-in-the-web-ui).

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
- **Metadata panel** (`<schedule-metadata>`): every frontmatter field the schedule format supports — cron, channel, model, enabled, and required skills in the main section; allowed tools, shell patterns, email recipients, and pre-script in a permissions group. Each field saves on `change`; no separate Save button. The permissions group is marked and grouped, because at admin or bundled tier these pre-approve actions that would otherwise need confirmation; at workspace or extra tier they still restrict but grant no pre-approval, and the panel shows a note saying so — see [Schedules](schedules.md#permissions-are-tier-dependent).
- **Raw section**: a read-only view of the frontmatter as it sits on disk, plus a warning naming any key the parser does not recognize. Read-only because the field set is closed — anything typed there that is not a known field would be dropped on the next write.
- **Body editor**: a full `<wiki-editor>` for the prompt body. Autosaves after 1 second of inactivity or on Ctrl+S / focus-out. The editor sends the file's `mtime` as a `modified` field, but the server does not enforce conflict detection — concurrent edits are last-write-wins. Refresh before editing if you need the latest version.
- **Workspace-tier schedules**: fully editable. Changes write in-place to `workspace/schedules/{name}.md`.
- **URL deep-linking**: opening a schedule sets `?schedule={name}` in the URL. Pasting the URL in a new tab opens the same schedule page directly.
- **Switching schedules**: one `<schedule-page>` element serves every schedule, so a response for an earlier selection can arrive late. The panel ignores it: detail loads, post-save refreshes, metadata saves (including their errors), and reset reloads update the panel only while their schedule is still selected. The earlier save itself still completes and still dispatches `schedule-saved`.

The tab auto-refreshes on activation. Save/reset actions dispatch a `schedule-saved` window event that triggers an immediate silent list refresh.

See [Schedules](schedules.md) for the full model, overlay semantics, and API.

### Tags tab

The **Tags** tab lists every tag in use across the vault, with usage counts, sorted by count descending:

- Click a tag to see the pages that carry it
- Click a page to open it in the wiki pane (same side panel as the Vault editor)
- The page open in the wiki pane is highlighted in any tag's page list that contains it; closing the pane clears the highlight
- Backed by `GET /api/vault/tags`

### Keyboard navigation

The sidebar list rows in the Chats, Vault, Files, Tags, and Schedules tabs work from the keyboard:

- **Tab** moves between rows
- **Enter** or **Space** opens the focused row, the same as a click
- A focus ring shows on the row that has keyboard focus

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
The panel's file list and reads, plus the shared editor's config read/write
operations, use the generated API client. Config paths are encoded one segment
at a time so allowed nested schedule paths retain their `/` separators. Missing
local prompt files still open their bundled defaults with a null modification
time; the first save creates the local override.

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
conversation-select via `GET /api/canvas/{conv_id}`. The panel state client and
the standalone canvas entry use the generated API contract for this snapshot;
tab identifiers, labels, widget types, and active-tab state stay typed while a
widget's `data` remains arbitrary JSON. User tab selection and close requests
use the generated body and path contracts as well.

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
The standalone entry participates in the normal JavaScript type check and uses
the same generated state contract as the embedded panel.

**Resize:** drag handle on the left edge of `#canvas-main`. Width persists
to `localStorage["canvas-width"]`.

**Mobile:** full-screen overlay (`position: fixed; inset: 0; z-index: 100`).
Mutually exclusive with the wiki overlay — most-recent-open wins. See
[Web UI — mobile conventions](web-ui-mobile.md#canvas-panel) for details.

See [Widgets — Phase 3](widgets.md#phase-3--canvas-panel-and-markdown_document)
and [Phase 4](widgets.md#phase-4--code_block-and-canvas-tabs) for the
widget mode contract and bundled widgets.

### Terminals

Type `/terminal [cwd]` in the chat input to open a real, interactive PTY
shell as a canvas tab. Human-only — the agent has no tools or code path to
spawn, attach to, or read from a terminal session. `/terminal` is a
server-side side-effect command: it spawns the shell and opens the tab
directly, with no LLM turn and no archive write. See [Web Terminal](web-terminal.md)
for the full architecture, security model, and configuration reference.

## Architecture

### Frontend

Lit web components in `src/decafclaw/web/static/`:

| Component | File | Purpose |
|-----------|------|---------|
| `chat-view` | `components/chat-view.js` | Main chat area with message list |
| `chat-input` | `components/chat-input.js` | Message input with file upload and command autocomplete |
| `chat-message` | `components/chat-message.js` | Individual message rendering |
| `conversation-sidebar` | `components/conversation-sidebar.js` | Conversation list, folders, vault browser, model picker |
| `wiki-editor` | `components/wiki-editor.js` | WYSIWYG markdown page editor (see [host contract](#the-wiki-editor-host-contract)) |
| `wiki-page` | `components/wiki-page.js` | Page viewer/renderer |
| `wiki-metadata` | `components/wiki-metadata.js` | Frontmatter strip/panel: view-mode summary + tags, edit-mode typed controls and raw YAML editor |
| `context-inspector` | `components/context-inspector.js` | Context diagnostics popover |
| `notification-inbox` | `components/notification-inbox.js` | Bell icon + dropdown inbox panel |
| `config-panel` | `components/config-panel.js` | Admin config file editor |
| `confirm-view` | `components/confirm-view.js` | Confirmation dialog for tool approvals |
| `login-view` | `components/login-view.js` | Login screen |
| `canvas-panel` | `components/canvas-panel.js` | Canvas side panel and resummon pill |
| `theme-toggle` | `components/theme-toggle.js` | Light/dark mode switch |

Service layer: `AuthClient`, `WebSocketClient`, `ConversationStore`, `MessageStore`, `ToolStatusStore`, `CanvasState`.

#### The wiki-editor host contract

`<wiki-editor>` is shared by three hosts, each pointing it at a different API
prefix via `save-endpoint`:

| Host | `save-endpoint` |
|------|-----------------|
| `wiki-page` | `/api/vault/` (default) |
| `schedule-page` | `/api/schedules/` |
| `config-panel` | `/api/config/files/` |

Every request the editor makes — autosave, force-save, and the conflict
banner's **Reload** — goes to `{saveEndpoint}{page}`. A host that sets
`save-endpoint` must therefore serve both verbs there, and both responses must
put the editor's fields at the **top level**, since the component knows nothing
about any per-endpoint envelope. All three branches call their generated
operations directly while retaining this host contract:

- Vault `GET` returns the editable markdown as `body` and its mtime as
  `modified`; the generated vault branch reads those fields directly.
- Config `GET` returns the editable markdown as `content` and its mtime as
  `modified`; the generated config branch reads those fields directly.
- Schedule `GET` returns `body` and `modified`; its generated detail type also
  exposes the nested `schedule` record used by `schedule-page`.
- `PUT` accepts `{content, modified}` and returns `modified`.

Force-save omits `modified` for vault, config, and schedules. Vault and config
use that omission to skip optimistic concurrency. The schedule server already
uses last-write-wins and accepts `modified` only as an editor compatibility
hint. Config reload consumes `content` directly; it does not share the vault
and schedule `body` field.

`/api/schedules/{name}` wraps its payload in `{"schedule": ...}` for
`schedule-page`, so it aliases `body` and `modified` to the top level for this
contract. Before [#666](https://github.com/lmorchard/decafclaw/issues/666)
Reload ignored `save-endpoint` entirely and always fetched `/api/vault/`,
which 404'd on a schedule or config file — or loaded a same-named vault page's
body into the wrong editor.

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

#### Subscriptions are per-socket, and re-established on reconnect

The server fans per-conversation events out to the sockets that sent
`select_conv` for that conversation. A reconnect produces a *new* socket, which
is subscribed to nothing — so `ConversationStore` re-sends `select_conv` for the
current conversation on every `open` (#704). Without it, everything on the
per-conversation stream (`canvas_update`, sticky updates, tool status, streamed
output) is silently dropped until a full page reload.

The resubscribe deliberately does **not** go through `selectConversation()`,
which clears the message store and re-issues `load_history` — that would blank
the transcript and refetch 50 messages on every transient blip. The TUI does the
same thing from its `__reconnected` handler (`tui/src/App.tsx`).

Note that the standalone canvas page (`/canvas/{conv_id}`) uses its own raw
socket with no reconnect logic at all, so it is not covered by this.

## REST API

### Auth

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/auth/login` | Log in with a token |
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

`GET /api/vault/{page}` returns `frontmatter` (parsed dict), `frontmatter_raw`
(the block's exact text, `""` when absent), and `body` (frontmatter-stripped).
It does **not** return `content`. `frontmatter_error` appears only when the
block fails to parse.

`PUT /api/vault/{page}` accepts `content`/`body` (body-only; the frontmatter
block is preserved verbatim), `frontmatter` (dict patch), or `frontmatter_raw`
(string, whole-block replace). The last two are mutually exclusive. The
response carries `modified` and the resulting `frontmatter` — `wiki-page` pushes
that `modified` into `<wiki-editor>` so the body autosave doesn't 409.

### Uploads

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/upload/{conv_id}` | Upload a multipart `file`; the generated browser contract returns `{filename, path, mime_type}` |
| `GET` | `/api/workspace/{path}` | Serve a workspace file (images, media) |

### Config

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/config/files` | List editable config files |
| `GET` | `/api/config/files/{path}` | Read a config file |
| `PUT` | `/api/config/files/{path}` | Write a config file |

These generated contracts preserve the list's bare-array shape, nullable
modification times, bundled-default fields, and the config writer's established
400/409 responses.

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

## Generated API clients

`AuthClient.checkSession()` calls the generated client for `GET /api/auth/me`.
It returns the username and stores it in `currentUser`. An unsuccessful request
returns `null` and clears `currentUser`, including after an earlier success.
`AuthClient.login()` also uses the generated client. Its request contains a
string `token`, and its response contains a string `username`. Login stores
and returns the username and emits the existing login event. HTTP failures
raise `Invalid token` before body decoding. Transport and JSON parsing failures
propagate without changing the user or emitting an event.

`AuthClient.logout()` uses the generated logout method with `true` to discard
the response. It clears the user and emits logout after any HTTP response,
including an error status. It does not decode the body. A transport failure
leaves the user unchanged and emits no event. The default generated method
still exposes the `{ok: boolean}` success contract for callers that read it.

The standalone vault page calls `lib/vault-auth.js:checkVaultSession()`.
This module uses the generated `/me` method with `true` to discard the response
and participates in frontend type checking. It redirects to `/` on HTTP
failure. Successful responses require no JSON decoding, and transport failures
propagate without a redirect. The main session check retains its existing behavior.

Login request metadata describes the existing token body without adding runtime
validation. The backend retains manual body parsing, token validation, response
shapes, status codes, and session cookie attributes. Logout deletes that cookie.

Run `make gen-api-client` to regenerate the OpenAPI schema, TypeScript client,
and browser bundle from the current backend. The build uses the existing
OpenAPI generator and esbuild. It writes `lib/api-client/index.js` beside
`index.ts`. Browser imports use the JavaScript bundle. The frontend type check
uses the adjacent TypeScript definitions, so changes to consumed response
fields fail at the caller. The generated JavaScript bundle is excluded from
TypeScript's input scan because its generated TypeScript source supplies the types.

`make check-js` regenerates the client before checking frontend types.
`make check` also runs the static module path tests after generation. These
commands rebuild missing or outdated client output. Commit the generated
schema, types, and bundle with changes to the API.

`sticky-state.js:setActiveConv()` also uses a generated method for
`GET /api/sticky/{conv_id}`. Its identifier is a required string. The response
has required, nullable `widget_type` and `data` properties; widget payloads
remain heterogeneous objects, with validation owned by the widget registry.
The generator installs `scripts/sticky_api_request.ts` as a transport adapter.
The adapter encodes the sticky identifier as one path segment and sends same-origin
session cookies. HTTP failures retain the cache silently. Transport and JSON
failures retain it and warn.

`ConversationStore.listConversations()`, `listArchivedConversations()`, and
`listSystemConversations()` use generated methods for their listing routes.
Each accepts an optional string `folder`. Empty and omitted folders select the
root without a query string. Other folder values use the generated query
serializer, including nested paths and names with spaces, Unicode, `&`, `+`, or `#`.

Each response contains `folder`, `folders`, and `conversations`. Regular items
contain `conv_id`, `title`, `created_at`, and `updated_at`. System items contain
`conv_id`, `title`, `conv_type`, and `updated_at`, without `created_at`.
Folder entries contain `name` and `path`. Active root virtual entries also
contain `virtual: true`. Generated types continue through the listing state,
getters, and sidebar. Successful responses pass through the listing models to validate required fields
before serialization. The backend preserves the existing wire fields and manual
folder validation, including whitespace trimming and HTTP 400 errors.

The listing reads also use the adapter for same-origin cookies and error handling.
Success replaces the corresponding listing state and publishes a change event.
HTTP failures leave state unchanged without a change event or error log.
Transport and JSON failures also leave state unchanged, with the existing error log.

`ConversationStore.renameConversation()` and `moveConversation()` use the generated
`PATCH /api/conversations/{id}` method. The identifier is a required string.
The JSON body accepts optional `title` and `folder` strings. Omitted and null
fields remain no-ops. The backend documents this body without replacing its
existing parser or coercion rules. Folder validation still returns HTTP 400,
and an invalid destination cannot rename the conversation first.

The response contains `conv_id`, `title`, `created_at`, and `updated_at`.
It includes `folder` only when the request supplies a non-null folder.
That value is trimmed, and an empty string selects the root. The handler keeps
its existing JSON response so schema defaults cannot add fields to it.
Rename merges the generated response into the typed listing and emits a change event.

Move passes `true` as the generated method's third argument to discard the
response body. This overload returns `void`, and its request types come from
the same generated signature. It refreshes the current listing after HTTP success,
even when the unused PATCH body is empty or malformed. The transport does not
decode that body. HTTP failures retain state silently. Network failures retain
the existing operation-specific error log. Rename also logs JSON decoding failures.
Neither failure path emits a change event.

The PATCH adapter sends same-origin cookies and encodes the identifier once as
one path segment. Authentication and ownership checks remain in the handler.
The folder and lifecycle operations below also use the adapter. Other operations use the
unmodified generated transport. Edit the adapter or generator source, then regenerate.

`ConversationStore.createFolder()`, `renameFolder()`, and `deleteFolder()` use
generated calls for `POST /api/conversations/folders` and
`PUT`/`DELETE /api/conversations/folders/{path}`. Create and rename require a
JSON `path` string; rename and delete require the original folder path.
The adapter encodes each path segment once, preserving nested paths, and sends
same-origin cookies. The backend keeps its existing parser, validation, status
codes, per-user storage, rename/merge behavior, and empty-only deletion.

Create returns `{ok: true, path: string}` with the trimmed path. Rename and
delete return `{ok: true}`. These response contracts remain available to
generated callers, but the store uses the derived `void` overloads: it only
needs HTTP success. Each store method returns a boolean and refreshes the
current listing after success. Failures log the server's JSON error, or the
network/JSON decoding error, and skip the refresh. Empty or malformed success
bodies remain ignored.

`ConversationStore.createConversation()` uses the generated `POST /api/conversations`
method. Its body contains a title and optional model and folder strings.
The backend documents these inputs but keeps its existing JSON parser, defaults,
coercions, and legacy `effort` fallback. Authentication runs before body parsing.
Creation returns HTTP 201 with `conv_id`, `title`, `created_at`, and `updated_at`.
The response includes `model` and `folder` only when they are nonempty.

The store inserts the typed metadata, selects the conversation, and sends any
queued message and attachments through the existing upload path.
`archiveConversation()`, `unarchiveConversation()`, and `deleteConversation()`
use generated calls with required string identifiers.
The adapter encodes each identifier once and sends same-origin cookies.
Their response contract is `{ok: true}`, but the store discards these unused
bodies through the generated `void` overloads.

Archive and delete clear selection and messages only for the selected conversation.
Archive refreshes the current listing. Restore and delete refresh the archived listing,
including its folder counts. Existing change events remain unchanged.
HTTP failures remain silent. Transport failures keep their operation-specific logs,
and creation also logs JSON decoding failures.
The backend preserves ownership checks, archive data, and folder assignments.
Deletion stops the target conversation's terminals before it removes files and assignments.

The context inspector and copy menu use generated methods for
`GET /api/conversations/{id}/context` and `GET /api/conversations/{id}/export`.
The inspector keeps the generated diagnostics type in its state and rendering helpers.
Its source records, detail records, memory candidates, and cache statistics retain their types.
Absent optional diagnostics stay absent. A 404 response still shows the empty state.
Other HTTP errors, network failures, and JSON decoding failures keep their existing messages.

Export requires a string identifier and a `jsonl` or `markdown` query value.
Both response formats are text. The generated export method uses a string-returning
transport in `scripts/sticky_api_request.ts`, so JSONL never goes through JSON decoding.
Clipboard text, success messages, and HTTP error messages remain unchanged.
The backend still returns 400 for missing or invalid formats and 404 for unavailable archives or conversations.
Both operations preserve authentication and conversation access rules.

Context and export contract tests change consumed diagnostics fields, identifiers,
and the format query. Each requires passing checks before the change and a type
error at the unchanged component afterward. Component tests cover optional data,
rendering, clipboard contents, and failures. Chromium runs the components against
real test routes and checks the received paths, queries, empty bodies, and session cookies.

The schedules sidebar, schedule page, metadata editor, shared body editor, and
model picker use generated operations for schedule list/detail/update/reset/run
and model list requests. Generated schedule records carry every rendered and
editable field through component state and helpers. The update request documents
both editor aliases (`content`, `modified`) and every patch accepted by
`write_overlay`; the handler still parses JSON manually, maps `content` to
`body`, rejects unknown keys with HTTP 400, and does not introduce FastAPI 422
responses.

Schedule requests encode the name as one path segment and send same-origin
session cookies. List and detail callers still treat malformed successful JSON
as failures. Toggle, run, and reset use generated `void` overloads where the UI
previously ignored successful bodies, so an empty or malformed 2xx body remains
successful. The page and editor still parse update responses because they use
the returned schedule or modification time. HTTP errors retain their existing
status or JSON-error messages; transport failures retain each caller's existing
warning, refresh, or UI-state behavior. Browser coverage verifies the real
backend routes, request bodies, 202 run responses, filesystem writes and reset,
editor remount, and model fallback without live credentials.

The browser integration tests in `tests/test_web_browser_scenarios.py` exercise
the generated client in real Chromium instances through `/static` against isolated
test servers with temporary data. They test session cookies, decoded folder queries,
PATCH paths, JSON bodies, conversation lifecycle, canvas tabs, vault editors, and
notifications. Static contract guards in `tests/test_api_client_contracts.py` enforce
that all backend API routes are represented in the generated client and that no
frontend files bypass the generated client with raw `fetch()` calls.
Route tests cover defaults, coercions, authentication, ownership, and lifecycle storage.
Before running `make test`, run `make install-js` and install the
browser with `uv run playwright install chromium`. CI installs its system dependencies with
`uv run playwright install --with-deps chromium`.
