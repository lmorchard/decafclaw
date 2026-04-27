# Widgets Phase 3 — Canvas panel + `markdown_document` widget

Tracks GitHub issue [#388](https://github.com/lmorchard/decafclaw/issues/388). Carved out of the Phase 1–4 epic [#256](https://github.com/lmorchard/decafclaw/issues/256).

## Goal

A persistent canvas surface in the web UI where the agent maintains a living document or visualization across multiple turns. First-class use case: a `markdown_document` widget the agent builds and revises across a conversation while chat continues alongside.

## Architecture

### Server-side state

Per-conversation sidecar at `workspace/conversations/{conv_id}.canvas.json`:

```json
{
  "schema_version": 1,
  "active_tab": "canvas_1",
  "tabs": [
    {
      "id": "canvas_1",
      "label": "Project Summary",
      "widget_type": "markdown_document",
      "data": { "content": "..." }
    }
  ]
}
```

In Phase 3, `tabs` has length 0 or 1. The tab-aware shape is preserved so Phase 4 can add multi-tab UI without a schema migration.

New module `src/decafclaw/canvas.py` — pure-data layer for read/write/atomic-rename, parallel to `persistence.py`. Sidecar pattern matches `{conv_id}.skills.json`. Fail-open on read errors.

### WebSocket event

`src/decafclaw/web/websocket.py` emits a new event on every set/update/clear:

```json
{
  "type": "canvas_update",
  "conv_id": "...",
  "kind": "set",
  "active_tab": "canvas_1",
  "tab": {
    "id": "canvas_1",
    "label": "Project Summary",
    "widget_type": "markdown_document",
    "data": { "content": "..." }
  }
}
```

- `kind: "set" | "update" | "clear"` — drives frontend dismiss-flag reset.
- `tab: null` for clear.

Subscribed by both the chat WS in the main UI and the standalone canvas page.

### REST endpoints

Added to `http_server.py`, all auth-gated by the existing web-auth middleware:

- `GET /api/canvas/{conv_id}` — load current canvas state for initial render and standalone-view bootstrap.
- `POST /api/canvas/{conv_id}/set` — body `{widget_type, data, label?}`. Same internal function as the `canvas_set` tool. Used by the inline widget's "Open in Canvas" button.
- `GET /canvas/{conv_id}` — serves the standalone canvas HTML page.

## Canvas tools (always-loaded)

New module `src/decafclaw/tools/canvas_tools.py`. All four tools are always-loaded with priority `"normal"` and the standard 180s timeout. Each is a thin wrapper around shared internal canvas-state functions.

### `canvas_set(widget_type, data, label?)`

Push a widget to the canvas, replacing any existing tab.

- Rejects with `[error: widget '{name}' not registered]` if `widget_type` is unknown.
- Rejects with `[error: widget '{name}' does not support canvas mode]` if widget descriptor's `modes` does not include `"canvas"`.
- Rejects with `[error: schema validation failed: {message}]` if `data` does not match the widget's `data_schema`.
- Generates `id = "canvas_1"` for now (single-tab).
- Default `label`: derived from first H1 in markdown content if widget is `markdown_document`; otherwise widget_type humanized.
- Writes `canvas.json`, emits WS event with `kind: "set"`.
- **Resets the dismiss flag on web clients** → re-shows the panel.
- Returns: `"canvas updated — view at /canvas/{conv_id}"`.

### `canvas_update(data)`

Replace the data of the current canvas widget. Same `widget_type`, label preserved.

- Errors `[error: no canvas widget set; call canvas_set first]` if `tabs[]` is empty.
- Validates `data` against the current tab's widget `data_schema`. Errors on mismatch.
- Writes `canvas.json`, emits WS event with `kind: "update"`.
- **Does NOT reset the dismiss flag** — stays silent if the user has hidden the panel; the resummon button's unread dot lights up.
- Returns: `"canvas updated"`.

### `canvas_clear()`

Remove the canvas widget; hides the panel for all watchers.

- No-op success if `tabs[]` is already empty: `"canvas already empty"`.
- Otherwise empties `tabs`, clears `active_tab`, writes file, emits WS event with `kind: "clear"`, `tab: null`.
- Returns: `"canvas cleared"`.

### `canvas_read()`

Return the current canvas tab as `{widget_type, label, data}` or `null`.

- For agent grounding after compaction or after the user's "Open in Canvas" click (which the agent otherwise can't observe).
- Returns text summary plus structured payload via `ToolResult.data`.

### Cross-transport behavior

All four tools persist regardless of transport. Mattermost and terminal users see the tool's text return value (which always includes the `/canvas/{conv_id}` URL); the panel is web-only display, but state is shared per-conversation. A user accessing the same conversation from web later will see the canvas state.

## Frontend — main UI

### Components

- `<canvas-panel>` — new Lit component, the right-side panel.
- `src/decafclaw/web/static/lib/canvas-state.js` — small client module managing per-conversation canvas state and dismiss flag (parallel to `widget-catalog.js`).

### Layout

Current layout: `conversation-sidebar | wiki-main? | chat-main`.

New layout: `conversation-sidebar | wiki-main? | chat-main | canvas-main?`. Both wiki and canvas can be open simultaneously on desktop, each independently dismissible and resizable.

Markup additions to `index.html`:

```html
<div id="chat-main">
  <div id="chat-main-header"><!-- new strip; only shows resummon pill --></div>
  <div id="mobile-header">...</div>
  <chat-view></chat-view>
  <chat-input></chat-input>
</div>
<div id="canvas-resize-handle" class="hidden"></div>
<div id="canvas-main" class="hidden">
  <canvas-panel></canvas-panel>
</div>
```

### Conversation load flow

1. User selects conversation → existing `select_conv` flow.
2. Frontend issues `GET /api/canvas/{conv_id}` once. If response has a tab, set local state and show panel.
3. Subscribe to `canvas_update` events on the existing per-conversation WS subscription.

### WebSocket update flow

When a `canvas_update` event arrives:

- `kind: "clear"` (or `tab: null`) → hide panel, clear state.
- `kind: "set"` → reset dismiss flag → mount fresh `<dc-widget-host>` for the new widget → show panel.
- `kind: "update"`:
  - If panel is visible: swap `.data` on the existing widget instance (no remount), preserve scroll position (clamped).
  - If panel is hidden by user: stay hidden, light up the resummon button's unread dot.

### Dismiss flag (in-memory only)

- Per conv: `canvasDismissed.set(conv_id, true)` on close-button click.
- Cleared on: any `kind: "set"` event, conversation switch, user click on resummon button, page reload.
- Not persisted to localStorage — by design, dismissal is session-ephemeral.

### Resummon UI

- Thin `#chat-main-header` strip above `chat-view`. Empty (no chrome) when no canvas state exists.
- When state exists: pill button `[📄 Canvas]`, with a `•` dot when there's an unread `canvas_update` since the user dismissed.
- Click → re-show panel + clear unread dot.
- Mobile: same pill lives in `#mobile-header` next to the hamburger.

### `<canvas-panel>` chrome

- Header: tab `label` + spacer + "Open in new tab" button (links to `/canvas/{conv_id}`) + close (X) button.
- Body: mounts `<dc-widget-host>` with `mode="canvas"` and the active tab's widget.
- CSS var `--canvas-width: 45%` default, `min-width: 280px`.
- Resize handle on left edge (`#canvas-resize-handle`), drag-to-resize, persists width to localStorage as pixels (mirrors wiki).
- `#chat-main` retains `flex: 1` with `min-width: 320px` so it doesn't disappear if both wiki and canvas are open and the screen is narrow.

### Mobile (≤639px)

- Canvas panel becomes `position: fixed; inset: 0` full-screen overlay, like wiki today.
- **Mutually exclusive with wiki** — opening canvas closes wiki, and vice versa. Most-recent-open wins.
- Resize handle hidden.
- Close (X) returns to chat.
- Respects `docs/web-ui-mobile.md` conventions (44px tap targets, no overflow, etc.).

## `markdown_document` widget

### Descriptor

`src/decafclaw/web/static/widgets/markdown_document/widget.json`:

```json
{
  "name": "markdown_document",
  "description": "A persistent markdown document the agent can build and revise across turns. Inline mode shows a collapsed preview; canvas mode is the primary surface.",
  "modes": ["inline", "canvas"],
  "accepts_input": false,
  "data_schema": {
    "type": "object",
    "required": ["content"],
    "properties": {
      "content": { "type": "string" }
    },
    "additionalProperties": false
  }
}
```

### Component (`widget.js`)

- LitElement, light-DOM root (matches existing pattern).
- Properties: `data` (object), `mode` (`"inline"` | `"canvas"`, default `"inline"`).
- The host (`<dc-widget-host>` for inline, `<canvas-panel>` for canvas) sets `mode` when mounting.
- Renders markdown via `renderMarkdown()` from `lib/markdown.js` — wiki-link and `workspace://` image support inherited.

### Inline mode

- Container: `max-height: 8rem` (~6 lines of body text), `overflow: hidden`, fade-out gradient on the bottom 2rem via `::after` with `linear-gradient(transparent, var(--bg))`.
- Two buttons in a footer row, **always visible**:
  - **Expand** — toggles a local `expanded: bool` that flips `max-height: none` and removes the fade.
  - **Open in Canvas** — `POST /api/canvas/{conv_id}/set` with `{widget_type: "markdown_document", data, label}`. Label: derived from first H1 in `content` (fallback `"Untitled"`).

### Canvas mode

- Full content rendered, no truncation, no buttons.
- Owns its own scroll container (`overflow: auto; height: 100%`).
- On `data` setter: capture `scrollTop`/`scrollLeft` in `willUpdate()`; restore in `updated()`, clamped to `min(scrollTop, scrollHeight - clientHeight)`.
- No diff/animation. Full replace each update.

## Standalone canvas view

### Route and auth

- `GET /canvas/{conv_id}` returns a static HTML page.
- Same web-auth middleware as `/` and `/api/*`. Unauthenticated users get the existing login flow.
- Mattermost users without a web token cannot follow the link — known limitation.

### Page

`src/decafclaw/web/static/canvas-page.html`:

```html
<!DOCTYPE html>
<html>
<head>
  <title>Canvas</title>
  <link rel="stylesheet" href="/static/styles/main.css">
  <link rel="stylesheet" href="/static/styles/canvas.css">
  <script type="importmap">...same as index.html...</script>
</head>
<body class="canvas-standalone">
  <div id="canvas-standalone-header">
    <h1 id="canvas-label">Canvas</h1>
    <a href="/?conv={conv_id}" class="back-link">← Back to chat</a>
  </div>
  <main id="canvas-standalone-body">
    <dc-widget-host></dc-widget-host>
  </main>
  <script type="module" src="/static/canvas-page.js"></script>
</body>
</html>
```

### Page controller (`canvas-page.js`)

1. Read `conv_id` from URL path.
2. `GET /api/canvas/{conv_id}` to load initial state. If empty, show `"No canvas content yet."` placeholder.
3. Mount the active tab's widget into `<dc-widget-host>` with `mode="canvas"`.
4. Open WebSocket to existing `/ws` endpoint, subscribe to that conv's events.
5. On `canvas_update` events: same routing as the in-app panel (swap data on same widget, mount fresh on widget_type change, clear on null).
6. Update the page `<title>` and `#canvas-label` to match the tab label.

### Reuse vs duplication

- `<dc-widget-host>` and `lib/widget-catalog.js` reused as-is.
- WS subscription: extract a small client module so both `app.js` and `canvas-page.js` use it. If extraction touches more than ~50 LOC of `app.js`, instead copy the minimal subset for the standalone page and file a follow-up to dedupe.

## Validation and error handling

| Condition | Behavior |
|---|---|
| `canvas_update` with no prior `canvas_set` | Tool returns `[error: no canvas widget set; call canvas_set first]` |
| Schema validation failure on `set` or `update` | Tool returns `[error: schema validation failed: ...]`; no state mutation |
| Unknown `widget_type` on `canvas_set` | Tool returns `[error: widget '{name}' not registered]` |
| Widget without `"canvas"` in `modes` | Tool returns `[error: widget '{name}' does not support canvas mode]` |
| `canvas_clear` on empty canvas | Success no-op: `"canvas already empty"` |
| `canvas.json` corruption / read failure | Fail-open: log warning, treat as empty canvas, continue |

## Acceptance criteria

- A skill or tool can call `canvas_set("markdown_document", {content: "..."})` and the canvas panel appears with the rendered markdown.
- Subsequent `canvas_update({content: "..."})` calls replace the content without re-mounting the widget; scroll position is preserved.
- `canvas_clear()` hides the panel.
- Reloading the page restores the canvas tab from `canvas.json` if a tab was set.
- Mobile (≤639px): canvas panel takes over the screen (analogous to current `#wiki-main` overlay behavior); tap-friendly resize / dismiss; respects `docs/web-ui-mobile.md` conventions.
- `/canvas/{conv_id}` standalone view loads the current canvas state and live-updates over WebSocket.
- "Open in Canvas" button on inline `markdown_document` pushes the same content to the canvas.
- `canvas_read()` returns the current canvas state to the agent.

## Test strategy

### Python unit tests

- `tests/test_canvas.py` — persistence layer:
  - `read_canvas_state` / `write_canvas_state` round-trip.
  - Atomic-rename write semantics.
  - Fail-open on missing or corrupt JSON.
  - Path traversal guard on `_canvas_sidecar_path`.
- `tests/test_canvas_tools.py` — all four tools end-to-end:
  - `canvas_set` happy path; rejects unknown widget; rejects widget without `"canvas"` in `modes`; rejects schema-invalid `data`.
  - `canvas_update` errors when `tabs[]` empty; rejects schema-invalid `data`; preserves label.
  - `canvas_clear` no-op when empty; clears active tab + emits WS event with `tab: null`.
  - `canvas_read` returns null on empty, structured dict on populated.
  - Dismiss-flag reset semantics (`set` resets, `update` does not — verify via WS event payload `kind`).
- `tests/test_web_canvas.py` — REST + WS:
  - `GET /api/canvas/{conv_id}` returns current state.
  - `POST /api/canvas/{conv_id}/set` is auth-gated, writes state, broadcasts WS event.
  - `GET /canvas/{conv_id}` is auth-gated, serves the standalone HTML.
  - WS `canvas_update` event shape (kind, conv_id, tab payload).
- Add `markdown_document` to the existing widget-registry test so the descriptor + schema validation are covered.

### Manual smoke test (Playwright MCP, run before declaring done)

1. Agent calls `canvas_set` with `markdown_document` → panel appears with rendered content + label.
2. `canvas_update` → content swaps, scroll preserved.
3. Dismiss panel → next `canvas_update` is silent, resummon button shows unread dot. Click resummon → panel back, dot gone.
4. New `canvas_set` after dismiss → panel auto-reveals.
5. Inline widget: collapsed by default, fade-out visible, "Expand" works, "Open in Canvas" pushes to canvas (verify WS event landed).
6. Resize handle drag persists width across reload.
7. `/canvas/{conv_id}` in second tab live-updates when agent does `canvas_update` in first tab.
8. Mobile (resize browser to 600px): canvas overlay full-screen, mutually exclusive with wiki, dismissible via X.
9. Conversation switch loads correct canvas; switching back restores dismiss state per conv.
10. `canvas_clear` hides panel.

No Playwright tests committed to the suite (matches project convention). Smoke list documents what manual verification covers.

## Documentation updates (in this PR)

Per CLAUDE.md "When changing a feature: update its `docs/` page as part of the same PR". Touch:

- `docs/widgets.md` — drop "Canvas panel out-of-scope" note in Phase 3 section; add canvas-mode rendering details for `markdown_document`; document `target` / canvas-mode descriptor expectations.
- `docs/web-ui.md` — add a Canvas panel section: layout, resummon UI, dismiss behavior, standalone view route.
- `docs/web-ui-mobile.md` — add a row to the breakpoint behaviors describing canvas full-screen overlay + wiki/canvas mutual exclusion.
- `docs/conversations.md` — note `{conv_id}.canvas.json` sidecar.
- `docs/context-composer.md` — add `canvas_set` / `canvas_update` / `canvas_clear` / `canvas_read` to the always-loaded tool list (and any related mention of system prompt / tool definitions).
- `docs/index.md` — link a new doc page if we choose to split canvas into its own page; otherwise leave the widget/web-ui pages as the canonical source.
- `CLAUDE.md` — add `canvas.py` and `tools/canvas_tools.py` to the key-files list under "Data and persistence" / "Tools" respectively.
- `README.md` — update any feature list that mentions widgets so canvas is reflected; keep concise.

## Out of scope (file as follow-on issues)

1. **Multi-tab canvas UI** — surfacing the `tabs[]` array as actual tabs in the panel header. Phase 4 from #256.
2. **`code_block` widget** — companion widget for canvas. Phase 4 from #256.
3. **Diff visualization / smooth animation** on `canvas_update` (currently full replace, no animation).
4. **In-browser editing** of canvas content (read-only for both panel and standalone view in Phase 3).
5. **Public-shareable canvas links** for Mattermost users without web auth (e.g., short-lived signed URLs).
6. **Persisted dismiss flag** via localStorage (currently in-memory only).
7. **Sandbox mode for agent-authored HTML/JS** in canvas — tracked in #358.
8. **Real-time collaborative editing** of canvas (multi-user write).
9. **Canvas state injected into agent context** automatically (currently only available via `canvas_read` tool).
10. **WebSocket reconnect/backoff hardening for the standalone canvas page** if the existing chat WS client doesn't already cover this case — investigate during implementation, file separately if non-trivial.
11. **Canvas history / undo-redo** within a tab (only current state persisted).
12. **Hot-reload of widget catalog without server restart** (existing limitation, not new).

## References

- #256 — original epic
- #388 — this issue
- #358 — agent-authored widget JS / sandbox mode
- `docs/widgets.md` — current widget infrastructure
- `docs/web-ui.md`, `docs/web-ui-mobile.md` — UI conventions
- `docs/conversations.md` — per-conversation persistence patterns
