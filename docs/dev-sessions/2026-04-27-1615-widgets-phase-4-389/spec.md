# Widgets Phase 4 — `code_block` widget + canvas tabs + polish

Tracks GitHub issue [#389](https://github.com/lmorchard/decafclaw/issues/389). Builds on Phase 3 (#388, merged in `ca7561d`).

## Goal

Finish the canvas / widget catalog: surface the tab-aware data model from Phase 3 as actual tab UI, add a `code_block` widget with real syntax highlighting, and polish a11y / mobile.

## Architecture

### Tools API — explicit tab IDs

Phase 3 worked on a single implicit "active" tab. Phase 4 makes the agent address tabs by ID, with `active_tab` as informational/contextual state but never used as the implicit target for mutations. This avoids fragility around concurrent user clicks and stale agent assumptions.

Tab IDs are auto-generated as `canvas_1`, `canvas_2`, … using a monotonically increasing counter. **IDs are never reused after a tab is closed** — a closed tab's id stays burned.

Five always-loaded canvas tools (replacing Phase 3's three):

| Tool | Behavior |
|---|---|
| `canvas_new_tab(widget_type, data, label?)` | Append a new tab to `tabs[]`, set as `active_tab`, return new `tab_id` in `ToolResult.data["tab_id"]`. Validates widget+data via registry. |
| `canvas_update(tab_id, data)` | Find tab by id; replace `data` only (preserve widget_type + label). Errors `[error: tab '{id}' not found]` on bad id; revalidates against widget data_schema. |
| `canvas_close_tab(tab_id)` | Remove tab. If it was active, switch active to the left neighbor (else right; else clear active). Last tab closed → panel hides. |
| `canvas_clear()` | Empty `tabs[]`, clear `active_tab`. "I'm done; reset everything." Same as Phase 3. |
| `canvas_read()` | Returns full state `{active_tab, tabs: [{id, label, widget_type, data}, ...]}` via `ToolResult.data`. |

**Phase 3 tools `canvas_set` and the no-id `canvas_update` are removed.** Phase 3 only just shipped (`ca7561d`); the agent and the inline widget's "Open in Canvas" button are the only callers — both adapt naturally. Tests are rewritten to the new API rather than maintaining shims.

### Persistence shape

`workspace/conversations/{conv_id}.canvas.json` extends the Phase 3 schema with a `next_tab_id` counter (so closed tab IDs are never reused — `max(existing tabs)+1` is wrong because a closed-then-recreated id would silently rebind):

```json
{
  "schema_version": 1,
  "active_tab": "canvas_2",
  "next_tab_id": 3,
  "tabs": [
    { "id": "canvas_1", "label": "Plan", "widget_type": "markdown_document", "data": {...} },
    { "id": "canvas_2", "label": "snippet.py", "widget_type": "code_block", "data": {...} }
  ]
}
```

`next_tab_id` increments on every `new_tab`; close doesn't decrement. Phase 3 sidecars (no `next_tab_id` field) read fail-open: derive from `max(int(t["id"].split("_")[1]) for t in tabs) + 1` on first read, then persist on the next write.

Fail-open reads, atomic write, traversal-guarded path — all from Phase 3.

### WebSocket event — extended `kind` values

`canvas_update` event payload extended with new `kind` values to cover multi-tab operations:

| `kind` | Payload | When |
|---|---|---|
| `"new_tab"` | `tab` (new), `active_tab` (= new id) | `canvas_new_tab` |
| `"update"` | `tab` (updated; identifies by id), `active_tab` (unchanged) | `canvas_update` |
| `"close_tab"` | `closed_tab_id`, `active_tab` (new), `tab: null` if last | `canvas_close_tab` |
| `"set_active"` | `active_tab` (new) | User click in panel/list |
| `"clear"` | `tab: null`, `active_tab: null` | `canvas_clear` |

### REST endpoints

| Route | Notes |
|---|---|
| `GET /api/canvas/{conv_id}` | Unchanged; full state. |
| `POST /api/canvas/{conv_id}/new_tab` | Replaces Phase 3's `/set`. Body `{widget_type, data, label?}`. Returns `{ok, tab_id}`. Backs the inline widget's "Open in Canvas" button. |
| `POST /api/canvas/{conv_id}/active_tab` | Body `{tab_id}`. User-side tab-switch. Server validates the tab exists, updates `active_tab`, broadcasts `kind: "set_active"`. |
| `POST /api/canvas/{conv_id}/close_tab` | Body `{tab_id}`. User-side tab-close (clicking `[×]` on a tab). Server runs the same internal close path as the agent tool; broadcasts `kind: "close_tab"`. |
| `GET /canvas/{conv_id}` | Bare URL — backwards compat; renders active tab. |
| `GET /canvas/{conv_id}/{tab_id}` | Explicit tab-locked standalone view. |

Both standalone routes are auth-gated and conv-owner-checked (Phase 3 pattern).

## `code_block` widget

### Descriptor (`web/static/widgets/code_block/widget.json`)

```json
{
  "name": "code_block",
  "description": "A syntax-highlighted code block. Inline mode shows a collapsed preview with Expand and Open in Canvas buttons; canvas mode shows the full file with scroll preservation.",
  "modes": ["inline", "canvas"],
  "accepts_input": false,
  "data_schema": {
    "type": "object",
    "required": ["code"],
    "properties": {
      "code": { "type": "string" },
      "language": { "type": "string" },
      "filename": { "type": "string" }
    },
    "additionalProperties": false
  }
}
```

### Component (`web/static/widgets/code_block/widget.js`)

- LitElement, light-DOM (matches existing widget pattern).
- Properties: `data` (object), `mode` (`'inline' | 'canvas'`).
- Header bar: filename if present, else humanized language label, else "code". Right side: Copy button.
- Body: `<pre><code class="language-{lang || 'plaintext'}">{code}</code></pre>`. `hljs.highlightElement(codeEl)` called in `updated()` after mount.

**Inline mode** (mirrors `markdown_document`):
- Collapsed by default, `max-height: 12rem` (~8 lines, slightly taller than `markdown_document` since code is denser), fade-out gradient via `::after`.
- Two-button footer (always visible): **Expand** / **Collapse** + **Open in Canvas**. Open-in-Canvas POSTs to `/api/canvas/{conv_id}/new_tab` with `{widget_type: "code_block", data: {code, language, filename}, label}`. Label derivation: `filename` → `"{language} snippet"` → `"Code"`.

**Canvas mode**:
- Full code rendered, no truncation, header bar at top.
- Scroll container fills the canvas-body area (mirrors `markdown_document`'s flex chain).
- Scroll position preserved across `canvas_update` (capture in `willUpdate`, restore clamped in `updated`).

### highlight.js integration

**Vendor bundle.** Add to `make vendor`:
- Core: `highlight.js/lib/core` + ~20 common languages (python, js, ts, json, yaml, toml, markdown, bash, shell, dockerfile, html, xml, css, scss, sql, go, rust, ruby, java, kotlin, c, cpp, plaintext).
- Themes: `atom-one-dark.css` and `atom-one-light.css`, scoped under `:root[data-theme="dark"]` / `:root[data-theme="light"]` selectors.
- Single bundled module at `vendor/bundle/highlight.js`. Imported as `import hljs from 'hljs'` (importmap entry).

**Hljs hook for existing chat code blocks.** Update `assistant-message.js:updated()` — currently adds language-label + copy-btn but no actual highlighting. Add `hljs.highlightElement(codeEl)` immediately after the language-class is detected. Same visual treatment for chat fenced code and the new widget.

## Canvas panel UI

### Desktop tab strip

Tab strip lives at the top of `<canvas-panel>`, above the existing label/buttons header.

```
┌──────────────────────────────────────────────────────────┐
│ [Project Plan ✕] [Notes ✕] [snippet.py ✕]    +  scroll   │ ← strip
├──────────────────────────────────────────────────────────┤
│ Project Plan                       [↗ open]  [✕ close]   │ ← header (panel-level)
├──────────────────────────────────────────────────────────┤
│                                                          │
│   <active tab content>                                   │
│                                                          │
└──────────────────────────────────────────────────────────┘
```

- Strip: `display: flex; overflow-x: auto`. Each tab is a button: label (truncated with ellipsis at `min-width: 7.5rem; max-width: 14rem`) + `[×]` close.
- Active tab: `border-bottom: 2px solid var(--pico-primary)`. Non-active: muted text, transparent border.
- Click tab body → switch active (POSTs `/api/canvas/{conv_id}/active_tab`).
- Click `[×]` → `POST /api/canvas/{conv_id}/close_tab` with `{tab_id}`. Server runs the same internal close path as the agent's `canvas_close_tab` tool and broadcasts `kind: "close_tab"`.
- Header (existing): drop the now-redundant label (it's in the strip). Keep "open in new tab" link (now points at `/canvas/{conv_id}/{active_tab}`) and the panel-dismiss `[×]`.

### Mobile (≤639px) — vertical list disclosure

Replace the strip with a "Tabs (N) ▼" button in the compressed header. Tapping toggles a vertical list overlay anchored under the button.

```
┌──────────────────────────────────┐
│ [☰ Tabs (3) ▼]   [↗]  [✕]        │ ← compressed header
├──────────────────────────────────┤
│                                  │
│  <active tab content>            │
│                                  │
└──────────────────────────────────┘
```

- List entries: tab label + close `[×]` button + active indicator (filled dot or background tint).
- Tap a row → switch active, close the list. Tap `[×]` → close that tab (list stays open until empty).
- 44px tap-target heights on rows and close buttons.
- Active-tab indicator stays visible.

### A11y (A2 — roles + tab keyboard nav)

- `<canvas-panel>` — `role="region"` `aria-label="Canvas"`.
- Tab strip — `role="tablist"`.
- Each tab — `role="tab"` `aria-selected="..."` `aria-controls="canvas-tabpanel"` `tabindex={selected ? 0 : -1}` (roving tabindex).
- Tab content area — `role="tabpanel"` `id="canvas-tabpanel"` `aria-labelledby={active_tab_id}`.
- Resize handle — `role="separator"` `aria-orientation="vertical"` `aria-label="Resize canvas panel"`.
- Mobile vertical list — same tab roles, just rendered differently.

Keyboard nav (focused tab):
- `ArrowLeft` / `ArrowRight` — move focus to prev/next tab and activate (auto-activation pattern).
- `Home` / `End` — first / last tab.
- `Enter` / `Space` — activate (auto-activation makes this the same as moving focus, but works for assistive tech).
- `Delete` — close focused tab. Bonus, matches browser tab UX.

Keyboard resize on the drag handle is **out of scope** for v1.

### Polish

- Match border radii, padding, and header bar with the existing wiki/files panel chrome (so the canvas doesn't feel "different" from sibling surfaces).
- Mobile pass during smoke covers narrow-width touch targets and overflow.
- Don't enumerate exhaustively — let the implementer eyeball-review during the smoke test and patch as needed.

## Standalone canvas views

Two URL forms:

| URL | Behavior |
|---|---|
| `/canvas/{conv_id}` | Bare URL — backwards compat. Renders the **active** tab; follows server-side `active_tab` changes via WebSocket. |
| `/canvas/{conv_id}/{tab_id}` | Explicit tab-locked. Renders one specific tab; does NOT follow active-tab changes. If the tab is closed, shows a "Tab no longer exists" empty state with a link back to `/canvas/{conv_id}`. |

**Tools' return text uses the explicit form** — `tool_canvas_new_tab` returns `"tab created (id=canvas_2) — view at /canvas/{conv_id}/canvas_2"`. Stable links; tab id recoverable from URL alone.

Frontend (`canvas-page.js`):

- Path-parse: `/^\/canvas\/([^/?#]+)(?:\/([^/?#]+))?/`. Group 1 = conv_id, group 2 = optional tab_id.
- Initial load: `GET /api/canvas/{conv_id}`.
  - If `tab_id` in URL: find tab by id; if missing, "Tab no longer exists"; else render.
  - Else (bare URL): render active tab; if no active, "No canvas content yet."
- WebSocket message handler branches on `kind`:
  - `"new_tab"` — bare URL ignores (still showing active); explicit URL ignores (not our tab).
  - `"update"` — bare URL re-renders if event's tab matches active; explicit URL re-renders if event's tab matches our tab_id.
  - `"close_tab"` — bare URL re-renders with new active (or empty); explicit URL switches to "Tab no longer exists" if our tab_id was closed.
  - `"set_active"` — bare URL re-renders to follow new active; explicit URL ignores (we're tab-locked).
  - `"clear"` — both URLs show empty / no-longer-exists state.
- `<title>` and `#canvas-label` reflect the current rendered tab's label.

Standalone view does **not** show a tab strip — single-tab focus by design.

## Validation and error handling

| Condition | Behavior |
|---|---|
| `canvas_update` with unknown `tab_id` | Tool returns `[error: tab '{id}' not found]` |
| `canvas_close_tab` with unknown `tab_id` | Tool returns `[error: tab '{id}' not found]` |
| Schema validation failure on `canvas_new_tab` / `canvas_update` | Tool returns `[error: schema validation failed: {message}]` |
| Unknown `widget_type` on `canvas_new_tab` | Tool returns `[error: widget '{name}' not registered]` |
| Widget without `"canvas"` in `modes` | Tool returns `[error: widget '{name}' does not support canvas mode]` |
| `canvas_clear` on empty canvas | No-op success: `"canvas already empty"` |
| `canvas.json` corruption | Fail-open: log, treat as empty |
| `POST /api/canvas/{conv_id}/active_tab` with unknown `tab_id` | 400 with `{error: "tab '{id}' not found"}` |

## Acceptance criteria

- Agent can call `canvas_new_tab` and gets back a `tab_id`; subsequent calls with that id work.
- Multiple tabs render in a horizontal strip on desktop; tab clicks switch active; per-tab × closes.
- Mobile (≤639px): tab strip replaced by vertical list disclosure; tap to switch; 44px tap targets.
- `code_block` widget renders inline (collapsed + Expand + Open in Canvas) and canvas (full + scroll preserved) with hljs syntax highlighting.
- Existing chat code blocks now also have hljs syntax highlighting (same theme).
- `/canvas/{conv_id}/{tab_id}` URL renders that specific tab and stays locked when active changes elsewhere.
- `/canvas/{conv_id}` (bare URL) follows active-tab changes via WebSocket.
- ARIA tab pattern works: keyboard nav (Arrow / Home / End / Delete) cycles tabs and activates.
- `make check` and `make test` clean. `docs/widgets.md` reflects the final shape.

## Test strategy

### Python unit tests

- `tests/test_canvas.py` — multi-tab persistence:
  - `new_tab` appends with monotonic id; ids never reused.
  - `update_tab(tab_id)` finds by id; preserves widget_type + label; errors on unknown.
  - `close_tab` removes; updates active; emits event; last close clears active.
  - `set_active_tab` updates and emits.
  - `clear_canvas` unchanged.
  - `read_canvas_state` returns full state.
- `tests/test_canvas_tools.py` — five tools:
  - `tool_canvas_new_tab` returns `tab_id` in `ToolResult.data`; URL is the explicit form.
  - `tool_canvas_update(tab_id=...)` errors on unknown id.
  - `tool_canvas_close_tab(tab_id=...)` text reflects new active or panel-hidden.
  - `tool_canvas_clear` unchanged.
  - `tool_canvas_read` returns full state via `data`.
  - Phase 3's `canvas_set` and no-id `canvas_update` tests **rewritten**, not maintained.
- `tests/test_web_canvas.py` — REST + WS:
  - `POST /api/canvas/{conv_id}/new_tab` (renamed) — auth, owner, returns `tab_id`.
  - `POST /api/canvas/{conv_id}/active_tab` — auth, owner, broadcasts `kind: "set_active"`.
  - `POST /api/canvas/{conv_id}/close_tab` — auth, owner, broadcasts `kind: "close_tab"`.
  - `GET /canvas/{conv_id}/{tab_id}` — auth, owner, serves canvas-page.html.
  - WS event projection for each new `kind` value.
- `tests/test_widgets.py` — add `code_block` to expected widgets; validate descriptor.

### Manual smoke test (Playwright MCP, run before declaring done)

Drive both the chat and `/canvas/{conv_id}/{tab_id}` URLs from the worktree dev server on port 18881 (with `.env` copied from main).

1. `canvas_new_tab` creates a tab → strip shows it; active.
2. Second `canvas_new_tab` → second tab appears; new one becomes active.
3. Click a non-active tab → switches; `set_active` event broadcasts; bare-URL standalone follows.
4. `[×]` on active tab → switches to left neighbor; last close hides panel.
5. Inline `code_block` widget — collapsed; Expand works; Open in Canvas creates new tab.
6. Hljs highlighting visible on a Python sample (and existing chat code blocks now also highlighted).
7. `/canvas/{conv_id}/{tab_id}` URL — locks to that tab; doesn't follow active-tab changes; shows "tab no longer exists" if closed.
8. Mobile (resize to 600px): strip replaced by "Tabs (N) ▼" disclosure; vertical list works.
9. Keyboard tab nav: focus a tab, ArrowLeft/Right cycle, Home/End jump, Delete closes.
10. ARIA tree (Playwright accessibility snapshot): tablist/tab/tabpanel/region roles present; aria-selected accurate.

## Documentation updates (in this PR)

Per CLAUDE.md "When changing a feature: update its `docs/` page as part of the same PR":

- `docs/widgets.md` — Phase 4 section: tab API, `code_block` widget, hljs integration, multi-tab UI.
- `docs/web-ui.md` — replace single-tab section with multi-tab description; add tab-strip + mobile-disclosure UX; explicit-URL standalone view.
- `docs/web-ui-mobile.md` — vertical list pattern, tap-target sizes.
- `docs/conversations.md` — sidecar shape (mostly unchanged but multi-tab in practice).
- `docs/context-composer.md` — update always-loaded canvas tools list (5 tools now; old `canvas_set` / no-id `canvas_update` removed).
- `CLAUDE.md` — no changes needed (canvas.py + canvas_tools.py already listed).
- `README.md` — no changes (canvas already mentioned).

## Out of scope (file as follow-on issues)

1. **Diff view** for `code_block` widget.
2. **Line numbers / line highlighting** on `code_block`.
3. **Drag-to-reorder tabs.**
4. **Tab limit / cap** — currently unbounded; horizontal scroll handles overflow.
5. **Multi-tab in standalone view** — current standalone is single-tab focus by design.
6. **Keyboard resize** on the canvas drag handle.
7. **Hot-reload** of widget catalog / hljs themes.
8. **Sandbox mode** for agent-authored widget HTML/JS — still tracked in #358.
9. **In-browser editing** of canvas content.
10. **Public-shareable links** (no-auth tokens) for Mattermost users without web auth.
11. **Persistent tab order memory** across sessions — current order is insertion order in `canvas.json`.

## References

- #256 — original epic (closed)
- #388 — Phase 3 (canvas panel + markdown_document; merged in `ca7561d`)
- #358 — agent-authored widget JS / sandbox mode
- `docs/widgets.md` — current widget infrastructure
- `docs/web-ui.md`, `docs/web-ui-mobile.md` — UI conventions
