# Codebase research: vault left-pane refresh

## 1. Vault sidebar component

**File:** `src/decafclaw/web/static/components/vault-sidebar.js`

- Lit element `<vault-sidebar>`. Renders two views: "browse" and "recent."
- **Data fetching:** REST only.
  - `#fetchWikiPages()` (lines 72-75) → `GET /api/vault[?folder=...]`. Response: `{folder, folders, pages}`.
  - `#fetchRecentPages()` (lines 94-96) → `GET /api/vault/recent`. Response: `{pages}` sorted by mtime desc.
- **Refresh triggers (today):**
  - `updated()` lifecycle hook (lines 54-66): re-fetches when `active` property goes false→true (user opens Vault tab).
  - `vault-page-deleted` custom DOM event (line 46): listened in `connectedCallback`, gated by `_everActivated` flag. Dispatched by `wiki-page.js:197` when the user deletes a page from the in-app editor.
  - **No** WebSocket listener, no polling, no route-change watcher.

## 2. REST endpoints

**File:** `src/decafclaw/http_server.py:1789-1795` (route registration), handlers at lines 1054-1374.

| Method | Path | Handler | Effect |
|---|---|---|---|
| GET | `/api/vault` | `vault_list` (1054) | List folders + pages in target dir. |
| GET | `/api/vault/recent` | `vault_recent` (1108) | Top-N agent pages by mtime. |
| GET | `/api/vault/{page}` | `vault_read` (1154) | Read page content. |
| POST | `/api/vault` | `vault_create` (1286) | Create new page. **Mutation.** |
| POST | `/api/vault/folders` | `vault_create_folder` (1320) | Create folder. **Mutation.** |
| PUT | `/api/vault/{page}` | `vault_write` (1232) + `_vault_rename` (1175) | Write/overwrite or rename. **Mutation.** |
| DELETE | `/api/vault/{page}` | `vault_delete` (1344) | Delete page + prune empty parents. **Mutation.** |

All decorated with `@_authenticated`. Mutations also call embedding `delete_entries` / `index_entry`.

## 3. WebSocket message types

**File:** `src/decafclaw/web/message_types.json`. Source of truth — generates `web/message_types.py` and `web/static/lib/message-types.js`.

Vault-relevant messages today: **none.** No `vault_changed`, `vault_updated`, `file_updated`, `workspace_changed`. The closest precedent is `canvas_update`.

Pattern for any new wire type (per CLAUDE.md "WebSocket message types are centralized"): edit `message_types.json`, run `make gen-message-types`, then reference `WSMessageType.X` in Python and `MESSAGE_TYPES.X` in JS.

## 4. Vault mutation surface (every place files change on disk)

### Agent tools (`src/decafclaw/skills/vault/tools.py`)

| Tool | Line | Effect |
|---|---|---|
| `tool_vault_write` | 290 | create/overwrite page |
| `tool_vault_delete` | 337 | delete page |
| `tool_vault_rename` | 389 | rename/move page |
| `tool_vault_journal_append` | 541 | append to today's journal file |
| `tool_vault_section` | 891 | insert/replace/delete/move sections within a page |
| `tool_vault_move_lines` | 961 | move lines between pages |

All call embedding index updates after the file change. Some (write/delete/rename per PR #443) gate on `_check_user_write_allowed`.

### HTTP REST handlers (`src/decafclaw/http_server.py`)

`vault_create`, `vault_create_folder`, `vault_write`, `_vault_rename`, `vault_delete` (line refs in §2 above). These are user-driven via the web UI, not agent-driven. Same kinds of mutations as the tool surface.

### Skills

`dream`, `garden` skills generate vault content via tool calls (`vault_write` etc.), so they hit the tool surface. No direct filesystem writes outside the tool/REST surfaces found in the scan.

## 5. Precedent: "tool changed disk → UI refreshes" via canvas

**Server side** (`src/decafclaw/canvas.py:163-184`):
```python
async def _emit_canvas_update(emit, conv_id, kind, **kwargs):
    payload = {"type": "canvas_update", "kind": kind, **kwargs}
    await emit(conv_id, payload)
```
Mutation functions (`open_or_update_tab`, `close_tab`, `clear_canvas`, etc.) call `_emit_canvas_update(...)` after writing the sidecar.

**Transport** (`src/decafclaw/web/websocket.py:555-566`):
```python
if event_type == "canvas_update":
    await ws.send_json({
        "type": WSMessageType.CANVAS_UPDATE.value,
        ...payload...
    })
```
Subscription is per-conversation: `manager.subscribe(conv_id, on_conv_event)` at line 680.

**Client side** (`web/static/app.js:439-441`):
```js
if (msg?.type === MESSAGE_TYPES.CANVAS_UPDATE) applyEvent(msg);
```
Then `canvas-state.js:applyEvent` updates local state and notifies subscribers; `canvas-panel.js` re-renders.

## 6. Vault sidebar lifecycle

- `connectedCallback` (lines 37-47): registers `vault-page-deleted` DOM listener; does NOT fetch.
- `updated` (lines 54-66): fetches only on `active` false→true transition. Gates further refreshes via `_everActivated`.
- No re-fetch on conversation switch, on page reload (other than the initial `active` activation), or on agent activity.

## Class-of-bug analogue note

The user's prompt says "created, edited, deleted." That covers `vault_write` (create + edit) and `vault_delete`. But the broader mutation surface (§4) includes `vault_rename`, `vault_journal_append`, `vault_section`, `vault_move_lines`, plus REST `vault_create_folder`. A "fire on every disk change" mechanism would catch all six tool paths and all five REST mutation paths uniformly. A "fire only on the original three tools" approach would miss journal entries, section edits, line moves, folder creation, and REST-driven edits — even though all of those change what the sidebar should show.

## Architectural question surfaced by the research

Canvas events are **conversation-scoped** (`emit(conv_id, ...)` → `manager.subscribe(conv_id, ...)`). Vault changes are **global** — they affect every connected client's view, not a single conversation. The existing per-conversation subscription model is a natural mismatch to think about: do we broadcast, or scope to user/session?
