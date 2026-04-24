# Files tab

The Files tab is a sidebar in the web UI that browses, views, and edits the agent's workspace directory (`config.workspace_path`). It parallels the Vault tab, but targets agent-produced files — skill sources, scratch notes, schedule state, logs, conversation sidecars — rather than curated markdown pages. Developers and power users reach for it when they want to see and tweak what the agent has been writing, without SSHing into the host.

## What you can do

- **Browse the workspace folder-at-a-time** with a breadcrumb trail (no expand/collapse tree).
- **View text files** in a CodeMirror 6 editor with syntax highlighting.
- **View images** inline.
- **Download binaries** via a download button (no in-browser preview).
- **Edit text files** — auto-save on debounce, manual force-save with Ctrl/Cmd+S.
- **Rename/move files** (destination folders are auto-created; empty source folders pruned).
- **Delete files** (empty parent folders auto-prune afterwards).
- **Show/hide dotfiles** via a toggle (persisted in `localStorage`).
- **Switch between Browse and Recent views** — Recent lists the 50 most recently modified files across the workspace.

> Create-file, create-folder, and empty-folder deletion are available via the REST API (see the table below) but are not wired to UI controls in v1 — the UI may expose them later.

## Permission model

Every workspace request passes through five layers, in order. Each layer makes one binary decision and either passes through or refuses:

1. **Path-escape → 404.** `resolve_safe()` resolves the requested relative path against the workspace root and confirms the result still lies inside. Symlink escapes and `../` traversal return 404 (existence leakage is avoided by never distinguishing from "not found").
2. **Secret → 403 on content access, lock icon in listings.** Files whose basename matches a secret pattern appear in folder listings with a lock icon (no preview, no open, no click action), but the raw bytes, editor JSON, save, delete, rename, and create endpoints all return 403. The secret patterns are hardcoded in `workspace_paths.py`:
   ```
   *.env
   *credentials*
   *.key
   ```
   Basename match, case-insensitive. Visibility in listings is intentional: if the agent writes something sensitive into your workspace by accident, you want to see it sitting there.
3. **Readonly → 403 on mutation, GET allowed.** Files that the system writes out-of-band (conversation archives, embedding DBs, schedule state) are visible and readable but refuse save/delete/rename. Patterns match against the full posix-slashed relative path, case-insensitive:
   ```
   conversations/*.jsonl
   *.db
   *.db-wal
   *.db-shm
   .last_run
   .schedule_last_run/*
   ```
   If you need to hand-edit one of these, SSH in. There is no "edit anyway" override.
4. **Binary / unknown kind → implicitly read-only.** The editor JSON endpoint returns 415 for non-text kinds. The PUT endpoint similarly refuses. Binaries are still GETable via the raw-bytes endpoint.
5. **Text → editable.** The Files-tab editor mounts, auto-save is live, and save-on-blur/debounce/Ctrl-S all work.

## File kinds

Kind detection happens in `detect_kind(path)` in `workspace_paths.py`. Extension check wins; unknown extensions fall through to an 8 KiB content sniff.

**Text extensions** (`TEXT_EXTENSIONS`):
```
.md .py .json .yaml .yml .sh .js .ts .css .html .txt
.toml .ini .cfg .conf .log .csv .sql
```

**Image extensions** (`IMAGE_EXTENSIONS`):
```
.png .jpg .jpeg .gif .svg .webp .bmp .ico
```

**Unknown extension** — read the first 8 KiB. NUL byte present, or UTF-8 decode fails → binary. Otherwise → text.

This means a `.log` file edits as text, a `.gif` previews inline, and a file named `notes` with no extension gets sniffed and usually classed as text.

## Auto-refetch

The Files sidebar listens for two events and re-fetches its current view:

- **`workspace-file-deleted`** (window-level) — dispatched by `<file-page>` after a successful delete. Fires only after the Files tab has been activated at least once in this session.
- **`turn-complete`** (window-level) — fanned out by `app.js` from the WebSocket `turn_complete` message. The Files sidebar re-fetches silently (no "Loading…" placeholder) but **only when the Files tab is the active sidebar tab**. Other tabs don't fire spurious fetches, and the refetch never clobbers a listing the user is looking at.

There is no live file-watcher or websocket-push for external changes — edits made via SSH or another tool won't surface until the next refetch trigger or manual folder re-navigation.

## Editor (CodeMirror 6)

`<file-editor>` wraps CodeMirror 6 in a Lit component. Language packs bundled:

- Markdown (`.md`, `.markdown`)
- Python (`.py`)
- JSON (`.json`)
- YAML (`.yaml`, `.yml`)
- JavaScript / TypeScript (`.js`, `.mjs`, `.cjs`, `.ts`)

Anything else renders as plain text with line numbers, search, folding, and bracket matching.

### Save flow

- Typing starts an 800 ms debounce.
- The debounce fires `PUT /api/workspace/{path}` with `{"content": str, "modified": float}`.
- **Ctrl/Cmd+S** flushes the debounce immediately.
- The server compares the client's `modified` against the file's current mtime. Drift > 1 ms → 409 conflict with the current mtime in the response body.

### Conflict recovery

On 409, the editor dispatches a `conflict` event. The component does not latch — it will keep trying and keep 409-ing until the host remounts it. `<file-page>` handles this by refetching `GET /api/workspace-file/{path}` and re-keying the editor so it mounts fresh with the server's current `{content, modified}`.

## REST API surface

All routes require auth (see [Web UI](web-ui.md#setup)). Paths are relative to the workspace root.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/workspace?folder={path}` | Folder listing: `{folder, folders, files}`. Each file carries `{name, path, size, modified, kind, readonly, secret}`. |
| GET | `/api/workspace/recent` | 50 most-recently-modified files across the workspace tree. Prunes `conversations/`, `.schedule_last_run/`, and `attachments/` during descent. |
| GET | `/api/workspace/{path}` | Raw file bytes (images inline; everything else `Content-Disposition: attachment`). 403 on secret. |
| GET | `/api/workspace-file/{path}` | Editor JSON: `{content, modified, readonly}`. Non-text kinds → 415. |
| PUT | `/api/workspace/{path}` | Save text file. Body `{content, modified}`. Stale mtime → 409 with current mtime. Secret/readonly → 403. Non-text → 415. |
| PUT | `/api/workspace/{path}?rename_to=...` | Rename/move. Target exists → 409. Secret/readonly on either side → 403. |
| DELETE | `/api/workspace/{path}` | Delete file or empty folder. Non-empty folder → 409. Secret/readonly → 403. Empty parents auto-prune. |
| POST | `/api/workspace` | Create file or folder. Body `{type: "file"\|"folder", path, content?}`. Exists → 409. |

## Components involved

Frontend (all under `src/decafclaw/web/static/`):

- `components/vault-sidebar.js` — Vault tab, extracted from the old inline sidebar code as part of this feature's refactor.
- `components/files-sidebar.js` — Files tab: browse/recent views, hidden-file toggle, breadcrumbs, auto-refetch listeners.
- `components/file-page.js` — File content pane: routes to text editor / image preview / binary download based on `kind`; owns rename, delete, conflict recovery.
- `components/file-editor.js` — CodeMirror 6 editor with debounced auto-save and conflict events.
- `codemirror-entry.js` — Bundling barrel for the CodeMirror language packs.

Backend:

- `src/decafclaw/web/workspace_paths.py` — Path resolution, pattern constants, kind detection.
- `src/decafclaw/http_server.py` — Handlers: `workspace_list`, `workspace_recent`, `workspace_read_json`, `workspace_write`, `workspace_delete`, `workspace_create`; helpers `_workspace_file_entry`, `_can_write_as_text`, `_workspace_rename`, `_prune_empty_parents`.

## Configuration

`config.workspace_path` controls what the Files tab sees. No other Files-tab-specific config knobs in v1 — the secret and readonly patterns are hardcoded in `workspace_paths.py`.

The Files tab and Vault tab are independent mounts (`config.workspace_path` vs `config.vault_path`) and can fully overlap, partially overlap, or be disjoint. The default layout has the vault at `workspace/vault/`, so there's overlap there; the Files tab simply exposes the full workspace tree while the Vault tab focuses on curated markdown.

## Out-of-scope / follow-ups

Explicitly not shipped in v1:

- Websocket broadcast for cross-tab updates — other open tabs don't see your edits until they refetch.
- File upload from the UI — create a text file with the create-file action; binary upload isn't wired.
- Multi-select (batch rename / delete).
- Git history / blame / diff view.
- Swap CodeMirror 6 for Monaco (or any other editor).
- User-configurable secret / readonly patterns (currently hardcoded).
- An "edit anyway" override for readonly paths — SSH in if you need to hand-edit a conversation archive.
