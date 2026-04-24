# Files sidebar tab — spec

Closes: [#202](https://github.com/lmorchard/decafclaw/issues/202)

## Summary

Add a Files tab to the web UI sidebar (alongside Conversations and Vault) for browsing, viewing, and editing files in the agent workspace directory. Parallel to the existing Vault tab in shape, but targeting `config.workspace_path` (independent of the vault root), with a stricter permission model for system-managed paths and secrets.

Also extract the existing wiki-tab code from `conversation-sidebar.js` into its own `vault-sidebar.js` component so the sidebar's tab content is consistently externalized. This is a targeted cleanup justified by the new-component work happening in parallel.

## Motivation

With increased use of Claude Code sessions, agent-edited skills under `workspace/skills/`, and scratch files produced by heartbeat / background tools, users frequently need visibility and edit access to workspace content from the UI they're already in. SSH-ing to the host for a one-line edit is friction. The Vault tab already proves the pattern; this extends it to the workspace.

## Scope — v1

### In

- New Files tab in the sidebar, Conversations • Vault • Files tab order.
- Folder-at-a-time browse with breadcrumbs (matches Vault). Recent view with last-N-modified flat listing.
- Text file viewing and editing with syntax highlighting (CodeMirror 6).
- Inline image preview.
- Download for binary / unknown types.
- Read-only defaults for system-managed paths, no UI override (truly read-only — SSH if you need to edit).
- Secrets shown in listing as opaque (locked icon, no content access, no edit, no delete).
- Auto-refetch on agent turn-complete when Files tab is active.
- Hardcoded secret and readonly pattern lists (configurable is a follow-up).
- Wiki tab extracted from `conversation-sidebar.js` into `vault-sidebar.js`. Pure lift-and-shift, no behavior change.

### Out (follow-up issues if needed)

- Server-broadcast websocket `workspace-changed` / `vault-changed` events for multi-tab / multi-client updates.
- File upload from the UI.
- Multi-select / bulk operations.
- Git history / file diffs.
- Monaco / richer editor features beyond what CodeMirror 6 gives us.
- Configurable secret / readonly patterns via `config.json`.
- "Edit anyway" override for read-only system files.

## Architecture

### Frontend components

1. **`conversation-sidebar.js`** (modified).
   - Adds a "Files" tab button in the tab bar.
   - Vault tab content is replaced by `<vault-sidebar>`.
   - Files tab content hosted by `<files-sidebar>`.
   - Conversations tab content stays inline (not part of this scope).
   - Forwards the existing `turn-complete` / store events down into the child components.

2. **`vault-sidebar.js`** (new, extracted from the inline wiki-tab code in `conversation-sidebar.js`).
   - Owns wiki browsing, recent view, folder navigation, and the `wiki-open` dispatch.
   - Continues to listen for the `vault-page-deleted` window event shipped in #314.
   - Pure lift-and-shift — no behavior changes in this extraction.

3. **`files-sidebar.js`** (new). Structural mirror of `vault-sidebar.js`:
   - Browse ↔ Recent toggle.
   - Folder-at-a-time with breadcrumbs.
   - "Show hidden" toggle (default off, dotfiles skipped). State persisted in `localStorage`.
   - Refresh button.
   - Auto-refetch on turn-complete when this is the active sidebar tab.
   - Fetches exclusively from `/api/workspace/*`.

4. **`file-page.js`** (new). Mirrors `wiki-page.js`:
   - Fetches content and metadata from the backend.
   - For text: hosts `<file-editor>` in edit mode; plain `<pre>` in view mode.
   - For image: inline `<img>` with the image endpoint URL.
   - For binary / unknown: a download button + file metadata, no content pane.
   - Surfaces `readonly: true` with a lock icon and a disabled editor toolbar.
   - Delete button follows the same guardrails as the backend (only shown when the file is neither readonly nor secret).

5. **`file-editor.js`** (new). Built on **CodeMirror 6**:
   - Extension-based language detection for the following packs: markdown, json, yaml, python, shell, javascript. Plain-text fallback for everything else.
   - Same auto-save debounce and mtime-conflict handling as `wiki-editor.js`.
   - Dispatches a `saved` event with the new mtime on successful write.
   - Read-only mode honored when `readonly: true` is passed in.

### Backend — new endpoints at `/api/workspace/*`

Live in `src/decafclaw/http_server.py`, reusing the existing auth guard. Paralleling the vault endpoints where it makes sense.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/workspace?folder={path}` | Folder listing. Returns `{folder, folders:[{name,path}], files:[{name,path,size,modified,kind,readonly,secret}]}`. |
| GET | `/api/workspace/recent` | Flat list of the 50 most-recently-modified files. Same per-file schema. |
| GET | `/api/workspace/{path}` | Raw file content (existing endpoint, pre-dates this spec). Images served inline; other types forced to download. Used by the chat UI for attachment display (`markdown.js`, `user-message.js`). This spec adds a 403-on-secret check; all other behavior preserved. |
| GET | `/api/workspace-file/{path}` | JSON file content for the Files-tab editor. Text → `{content, modified, readonly}`. Non-text kinds → 415 (use the raw endpoint). Secret → 403. |
| PUT | `/api/workspace/{path}` | Save text file. Mtime-based conflict check (same shape as vault). 403 if readonly or secret. |
| DELETE | `/api/workspace/{path}` | Delete file. 403 if readonly or secret. |
| POST | `/api/workspace` | Create file or folder (body `{type: "file"|"folder", path, content?}`). Folder creation path mirrors vault. |
| PUT | `/api/workspace/{path}?rename_to=...` | Rename file or folder. 403 if readonly or secret (on either old or new path). |
| DELETE | `/api/workspace/{folder}` | Delete empty folder. 409 if non-empty and no `force` flag. |

### Permission rules

Evaluated in this order; first match wins. Shared helper in `http_server.py` so listings and direct access agree.

1. **Path escape** — any path resolving outside `config.workspace_path`: 404 at every endpoint. Uses the same `_resolve_safe` helper already in `workspace_tools.py`.
2. **Secret patterns** (hardcoded v1): `*.env`, `*credentials*`, `*.key`. Case-insensitive fnmatch on the filename.
   - In listings → `secret: true`.
   - Content GET / PUT / DELETE / rename all return 403.
3. **Read-only patterns** (hardcoded v1): `conversations/*.jsonl`, `*.db`, `*.db-wal`, `*.db-shm`, `.last_run`, `.schedule_last_run/**`.
   - In listings → `readonly: true`.
   - Content GET returns `{content, modified, readonly: true}` for text; bytes stream for other kinds.
   - PUT / DELETE / rename → 403.
4. **Binary / unknown kind** — treated as implicitly read-only. GET serves bytes for download; no PUT endpoint semantics apply.
5. **Text** — editable.

### Kind detection

- Extension allowlist for text: `.md .py .json .yaml .yml .sh .js .ts .css .html .txt .toml .ini .cfg .conf .log .csv .sql`.
- Extension allowlist for image: `.png .jpg .jpeg .gif .svg .webp .bmp .ico`.
- Anything else → sniff the first 8 KB of the file. Null byte or non-decodable UTF-8 → binary. Otherwise → text.
- Extension list is authoritative when matched; sniff only runs for unknown extensions.

## UI behavior

### Sidebar tab

- Tab bar order: Conversations • Vault • Files.
- Tab button icon: a folder / document glyph, consistent with the existing tab style.
- Selected-tab state persists via the existing `_sidebarTab` mechanism in `conversation-sidebar.js`.

### Files tab content

- Breadcrumb row at the top (Files / sub / sub), each segment clickable to navigate to that level.
- Browse ↔ Recent toggle (same shape as Vault).
- "Show hidden" checkbox; dotfiles hidden by default; state per-session in `localStorage`.
- Refresh button (manual refetch).
- Listing: folders first (sorted by name), then files (sorted by name by default; mtime sort in Recent view).
- Each file row: type icon, name, mtime (relative), size (short, e.g. "1.2 KB"). A lock icon overlay indicates readonly; a solid-lock icon indicates secret.

### Click behavior

- Folder → navigates into the folder.
- Text file → opens `<file-page>` in the main content area (same slot the wiki pane uses; the two are mutually exclusive and close each other).
- Image file → opens `<file-page>` with an inline `<img>`.
- Binary / unknown → triggers a direct download (no content pane).
- Secret → no-op with a tooltip explaining it can't be viewed.

### Auto-refetch on turn-complete

- `files-sidebar.js` subscribes to the conversations store's `turn-complete` signal on connect.
- On each `turn-complete`, if `_sidebarTab === 'files'`, silently re-fetch the current view (Browse or Recent).
- On inactive tab, no refetch — avoids wasted fetches. The next time the user switches to the Files tab, the normal tab-switch fetch fires.

### Read-only / secret surfacing in the UI

- Read-only file in a `<file-page>`: editor loads in disabled mode; toolbar buttons grayed; save path suppressed.
- Secret file: clicking the row in the listing does nothing; tooltip reads "This file is hidden from the UI by policy."

## Testing

### Backend

pytest covering each permission rule and endpoint behavior:

- Path escape — `..`, absolute paths, symlinks pointing outside workspace: all 404.
- Secret — listing shows `secret: true`; GET / PUT / DELETE / rename all return 403; writing to a secret path still 403.
- Readonly — listing shows `readonly: true`; GET returns content; PUT / DELETE / rename return 403.
- Text kind detection — extension allowlist picks up expected files; sniff correctly identifies UTF-8 text with no extension and binary with null bytes.
- Mtime conflict on PUT — stale mtime returns 409.
- Folder ops — create, rename, delete-empty all succeed; delete non-empty returns 409 without `force`.
- Recent listing — returns up-to-N files sorted by mtime desc.

### Frontend

- No automated test infrastructure exists. Verification via the PR test plan:
  - Browse workspace from root down through a few folders.
  - Open a text file; edit; save; reopen; confirm persisted.
  - Open an image file; verify inline preview.
  - Trigger a binary download; verify file saved.
  - Attempt to open a secret file; verify tooltip and no navigation.
  - Attempt to edit a readonly file; verify editor disabled.
  - Run an agent turn that writes a file under workspace; verify the file appears in Recent without manual refresh.
  - Switch tabs, reopen Files, confirm state restored.
  - "Show hidden" toggle — dotfiles appear / disappear.

## References

- Issue [#202](https://github.com/lmorchard/decafclaw/issues/202)
- Precedent for vault listing / editor: `src/decafclaw/http_server.py` (`/api/vault/*`), `src/decafclaw/web/static/components/conversation-sidebar.js` (wiki tab), `wiki-page.js`, `wiki-editor.js`.
- Path-escape helper precedent: `src/decafclaw/tools/workspace_tools.py` (`_resolve_safe`).
- Related recent work: [#314](https://github.com/lmorchard/decafclaw/pull/314) (sidebar vault listing refresh on delete — the `vault-page-deleted` window event pattern).
