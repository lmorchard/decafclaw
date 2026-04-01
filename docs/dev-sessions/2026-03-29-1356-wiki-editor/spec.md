# Wiki Editor — Milkdown WYSIWYG Integration

**Branch:** `feat/wiki-editor`
**Date:** 2026-03-29
**Issue:** #169
**Goal:** Add inline WYSIWYG markdown editing to wiki pages using Milkdown, wrapped in Lit web components.

---

## Architecture Overview

### Components

```
wiki-page.js (existing)          — read-only viewer, gets edit button
wiki-editor.js (new)             — Lit component wrapping Milkdown editor
lib/milkdown-entry.js (new)      — bundled Milkdown entry point for import map
```

### Data Flow

```
User clicks "Edit" on wiki-page
  → wiki-page switches to wiki-editor component
  → wiki-editor initializes Milkdown with page markdown
  → User edits in WYSIWYG mode
  → User clicks "Save"
  → PUT /api/wiki/{page} with markdown body
  → Server writes file, updates embeddings index
  → wiki-editor emits "saved" event
  → wiki-page switches back to read-only view
```

---

## 1. Backend: Wiki Write API

### `PUT /api/wiki/{page}`
- **Auth:** Required (via `@_authenticated` decorator)
- **Body:** JSON `{ "content": "markdown text" }`
- **Behavior:**
  - Validate page path (reuse `_safe_write_path` from wiki tools)
  - Create parent directories if needed
  - Write markdown to `workspace/wiki/{page}.md`
  - Update embeddings index (reuse logic from `wiki_write` tool)
  - Return `{ "ok": true, "modified": <unix timestamp> }`
- **Conflict detection:** Include `If-Modified-Since` or a `modified` field in the PUT body. Server checks file mtime and returns 409 if file was modified since the client last read it.

### `POST /api/wiki` (create new page)
- **Body:** JSON `{ "name": "PageName", "content": "initial markdown" }`
- **Behavior:** Same as PUT but creates new file. Returns 409 if page already exists.

---

## 2. Frontend: Milkdown Vendor Bundle

### Package
- `@milkdown/kit` — all-in-one package (~438KB minified / ~134KB gzipped with GFM)
- Includes: commonmark, GFM (tables, task lists), history (undo/redo), listener, clipboard

### Bundle Setup
- Add `@milkdown/kit` to `package.json`
- Create `milkdown-entry.js` that re-exports needed sub-paths
- Add esbuild entry in `build-vendor.mjs`
- Add to import map in `index.html` and `wiki.html`

### What stays
- `marked` + `dompurify` remain for read-only rendering in chat messages
- Milkdown is only used in the editor component

---

## 3. Frontend: `wiki-editor` Web Component

### `<wiki-editor>` — Lit component

**Properties:**
- `page` (String) — page name for save API
- `content` (String) — initial markdown content
- `modified` (Number) — file mtime from last read (for conflict detection)

**Internal state:**
- `_dirty` — content has been modified
- `_saving` — save in progress
- `_error` — last error message
- `_editor` — Milkdown Editor instance

**Lifecycle:**
- `firstUpdated()` — initialize Milkdown editor in a container div
  - Load commonmark + GFM presets
  - Load history plugin (undo/redo)
  - Load listener plugin to track dirty state
  - Load custom wiki-link plugin (render `[[links]]` as clickable in editor)
  - Set `defaultValueCtx` from `this.content`
- `disconnectedCallback()` — destroy editor instance to prevent leaks

**Auto-save:**
- Debounced save after 1 second of idle time (no keystrokes)
- On each content change (via listener plugin), reset debounce timer
- When timer fires: extract markdown via `getMarkdown()`, PUT to API
- Track save state: `idle` → `dirty` → `saving` → `saved` (or `error`)
- Also save on blur (editor loses focus) and before disconnect

**Methods:**
- `_scheduleSave()` — reset debounce timer (called on every content change)
- `_save()` — extract markdown, PUT to API, update `modified` timestamp
- `close()` — flush pending save, emit `close` event

**Events emitted:**
- `saved` — after successful save (detail: `{ page, modified }`)
- `close` — user clicked close/done button (parent switches back to read-only)

**UI:**
- Header: page title, close button (X or "Done"), save status indicator
- Formatting toolbar: Bold, Italic, Strikethrough | Heading (dropdown or cycle) | Bullet list, Ordered list, Task list | Code, Code block | Link, Image | Blockquote, Horizontal rule
- Save status: subtle text — "Editing", "Saving...", "Saved", "Error: ..."
- Editor area: Milkdown root div
- No explicit save button — auto-save handles it
- Ctrl+S / Cmd+S forces immediate save (skips debounce)
- Standard keyboard shortcuts from Milkdown/ProseMirror (Ctrl+B bold, Ctrl+I italic, etc.)

### Wiki Link Plugin

Custom Milkdown plugin for `[[wiki-link]]` syntax:
1. Remark plugin to parse `[[target]]` in markdown AST
2. ProseMirror inline node that renders as `<a class="wiki-link">` in the editor
3. Input rule to transform `[[text]]` as user types the closing `]]`
4. Serializer to round-trip back to `[[target]]` in markdown output

---

## 4. Frontend: `wiki-page` Modifications

### Edit Mode Toggle
- Add "Edit" button to `wiki-page-header` (next to "open in new tab")
- Clicking "Edit" switches rendering from read-only markdown to `<wiki-editor>`
- Pass current `content`, `page`, and `modified` to the editor
- Listen for `saved` event → refresh page content, switch back to read-only
- Listen for `cancel` event → switch back to read-only (discard changes)

### Navigation Guard
- If editor has unsaved changes (dirty + save not yet flushed), flush save before switching
- On page switch: auto-save current page, then load new page into editor
- On close: auto-save, then switch back to read-only view
- On tab switch to chat: auto-save in background, keep editor state for return

---

## 5. Conflict Detection

### Simple optimistic locking
1. `wiki-page` fetches page and records `modified` timestamp
2. `wiki-editor` includes `modified` in each auto-save PUT request
3. Server compares request `modified` with file mtime
4. If match → save succeeds, return new `modified` timestamp, editor updates its tracked mtime
5. If mismatch → 409 Conflict response
6. Editor shows inline warning: "Page was modified externally" with "Reload" and "Overwrite" options
7. Auto-save pauses until conflict is resolved

---

## 6. Styling

- Milkdown v7 is headless (no built-in styles beyond `prosemirror.css`)
- Style the editor to match Pico CSS / existing wiki-page styling
- Editor content area should look similar to the read-only rendered view
- Formatting toolbar: icon buttons in a horizontal bar above the editor, grouped by type
- Toolbar buttons should show active state when cursor is inside a formatted range (e.g. bold button highlighted when in bold text)
- Use existing CSS variables for colors and typography

---

## Phases

### Phase 1: Foundation
- [ ] Add `@milkdown/kit` to package.json and vendor bundle
- [ ] Create `wiki-editor.js` Lit component with basic Milkdown (commonmark + GFM + history + listener)
- [ ] Add `PUT /api/wiki/{page}` endpoint
- [ ] Wire edit button in `wiki-page.js` to toggle between viewer and editor

### Phase 2: Polish
- [ ] Add wiki-link plugin for `[[link]]` round-trip support
- [ ] Add conflict detection (mtime-based, 409 on stale save)
- [ ] Add Ctrl+S / Cmd+S for immediate save
- [ ] Navigation guard (flush save on page switch, close, tab switch)
- [ ] Style editor to match existing wiki page appearance

### Phase 3: New Pages & System Prompt Editing
- [ ] `POST /api/wiki` endpoint for creating new pages
- [ ] "New page" button in sidebar wiki tab → prompts for name, opens in editor
- [ ] `GET /api/config/files` — list editable config files with metadata
- [ ] `GET /api/config/files/{path}` — read config file content
- [ ] `PUT /api/config/files/{path}` — write config file content (with mtime conflict detection)
- [ ] "Agent Config" section in sidebar — lists editable files: SOUL.md, AGENT.md, USER.md, HEARTBEAT.md, COMPACTION.md, schedule files
- [ ] Reuse `<wiki-editor>` component for config file editing (same Milkdown editor, different save endpoint)
- [ ] Config files use `data/{agent_id}/` paths — distinct from wiki workspace paths
- [ ] Show which files are admin-level (read-only to agent) vs workspace-level (agent-writable)

---

## 7. System Prompt File Editing

### Editable files
| File | Location | Description |
|------|----------|-------------|
| `SOUL.md` | `data/{agent_id}/SOUL.md` | Core identity prompt |
| `AGENT.md` | `data/{agent_id}/AGENT.md` | Behavioral instructions |
| `USER.md` | `data/{agent_id}/workspace/USER.md` | User-specific context |
| `HEARTBEAT.md` | `data/{agent_id}/HEARTBEAT.md` | Heartbeat check sections |
| `COMPACTION.md` | `data/{agent_id}/COMPACTION.md` | Compaction prompt override |
| `schedules/*.md` | `data/{agent_id}/schedules/` | Scheduled task definitions |

### API Design
- `GET /api/config/files` — returns list of `{ path, name, description, modified, writable }` for all editable config files
- `GET /api/config/files/{path}` — returns `{ content, modified }` for a specific file
- `PUT /api/config/files/{path}` — saves content with mtime-based conflict detection
- Path validation: only allow known config file patterns (no arbitrary file access)

### UI Placement
- Gear/settings button next to the theme toggle (bottom of sidebar or header area)
- Clicking opens a config file list in the main content panel (reuses wiki panel area)
- Each file clickable → opens in `<wiki-editor>` with appropriate save endpoint
- Visual distinction from wiki pages (gear icon, "Agent Config" header)
- Show whether file exists on disk or would use bundled default
- Not a sidebar tab — config is infrequent access, doesn't need persistent navigation
