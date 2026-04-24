# Files sidebar tab — implementation plan

> **Spec:** [`spec.md`](./spec.md) — closes [#202](https://github.com/lmorchard/decafclaw/issues/202).

**Goal:** Ship a Files tab in the web UI sidebar for browsing, viewing, and editing agent-workspace content, and extract the existing wiki-tab code into its own `vault-sidebar.js` component along the way.

**Architecture:** Frontend adds three new Lit components (`vault-sidebar.js`, `files-sidebar.js`, `file-page.js`) plus a CodeMirror-6-backed `file-editor.js`; `conversation-sidebar.js` becomes a thin tab-switcher that embeds them. Backend adds `/api/workspace/*` REST endpoints paralleling `/api/vault/*`, with a stricter permission model (hardcoded secret/read-only patterns, path-escape guard, kind detection).

**Tech stack:** Python (Starlette handlers in `http_server.py`, pytest), Lit web components, CodeMirror 6, existing Milkdown wiki editor stays untouched.

---

## Pre-flight

- [ ] **Verify worktree + venv.** This plan executes in the `.claude/worktrees/files-sidebar-tab` worktree on branch `worktree-files-sidebar-tab`. Each worktree needs its own editable venv:

  ```bash
  make install           # from the worktree root
  ```

  Confirm `.venv/` now exists in the worktree, and `make test` (short run) passes on `main`-equivalent before any edits.

- [ ] **Confirm vendor bundle builds.** `make vendor` from the worktree — this pulls npm deps, rebuilds `vendor/bundle`. Baseline: works on `main` before we add any CodeMirror packages.

---

## Phase 1 — Extract vault-sidebar.js (pure refactor, no behavior change)

Lift the wiki-tab-specific state and rendering out of `conversation-sidebar.js` into a new `<vault-sidebar>` component. Same network calls, same events out, same keyboard and click behavior.

### Task 1.1 — Create `vault-sidebar.js`

**Files:**
- Create: `src/decafclaw/web/static/components/vault-sidebar.js`
- Reference source: `src/decafclaw/web/static/components/conversation-sidebar.js` (the wiki-related state, methods, and rendering sections)

- [ ] **Step 1: Identify moving pieces.** Scan `conversation-sidebar.js` for every piece of state, method, and render output tied to the wiki tab. At the time of writing this includes (verify against current file):
  - State: `_wikiPages`, `_wikiLoading`, `_vaultFolder`, `_vaultFolders`, `_openWikiPage`, `_vaultView`, `_recentPages`.
  - Methods: `#fetchWikiPages`, `#fetchRecentPages`, `#switchVaultView`, `#navigateToFolder`, `navigateToFolder`, `navigateToPageFolder`, `clearOpenPage`, `#handleWikiSelect`, `#createPageInFolder`, `#createVaultFolder`, `#renderVaultBrowse`, `#renderVaultRecent`, and any other method rendering vault folders/pages/breadcrumbs.
  - Window-event listener from #314: `vault-page-deleted`.

  The parent still needs these on the component:
  - Event out: `wiki-open` (bubbles, composed, `detail: {page}`).
  - Public methods: `navigateToPageFolder(path)`, `clearOpenPage()`, plus whatever `app.js` calls externally (e.g., `switchToWiki()` if present).

- [ ] **Step 2: Create the component.**

  ```js
  import { LitElement, html, nothing } from 'lit';

  export class VaultSidebar extends LitElement {
    static properties = {
      _wikiPages: { type: Array, state: true },
      // ...all moved state with their types...
    };

    createRenderRoot() { return this; }

    constructor() {
      super();
      // mirror current default values from conversation-sidebar.js
    }

    connectedCallback() {
      super.connectedCallback();
      this._onVaultPageDeleted = () => {
        if (this._vaultView === 'recent') this.#fetchRecentPages();
        else this.#fetchWikiPages();
      };
      window.addEventListener('vault-page-deleted', this._onVaultPageDeleted);
    }

    disconnectedCallback() {
      super.disconnectedCallback();
      window.removeEventListener('vault-page-deleted', this._onVaultPageDeleted);
    }

    // Paste the moved methods here.

    render() { /* moved render output */ }
  }

  customElements.define('vault-sidebar', VaultSidebar);
  ```

  Note: the old sidebar's listener was gated on `this._sidebarTab !== 'wiki'`. Since `<vault-sidebar>` renders only when that tab is active (and disconnects otherwise), drop the gate — the listener lifecycle handles it.

### Task 1.2 — Use `<vault-sidebar>` in `conversation-sidebar.js`

- [ ] **Step 1: Add the import** at the top of `conversation-sidebar.js`:

  ```js
  import './vault-sidebar.js';
  ```

- [ ] **Step 2: Replace the inline wiki-tab render** with the component:

  ```js
  html`<vault-sidebar
    @wiki-open=${(e) => this.#handleWikiOpen(e)}
  ></vault-sidebar>`
  ```

- [ ] **Step 3: Route public methods through a child lookup.**

  ```js
  navigateToPageFolder(path) {
    this._sidebarTab = 'wiki';
    this.updateComplete.then(() => {
      const vs = /** @type {any} */ (this.querySelector('vault-sidebar'));
      vs?.navigateToPageFolder(path);
    });
  }

  clearOpenPage() {
    const vs = /** @type {any} */ (this.querySelector('vault-sidebar'));
    vs?.clearOpenPage();
  }
  ```

- [ ] **Step 4: Delete the moved state and methods** from `conversation-sidebar.js`. Keep tab switching, collapse, and store wiring.

### Task 1.3 — Verify and commit

- [ ] **Step 1:** `make check-js` — expect clean.
- [ ] **Step 2: Manual smoke.** Hard reload the browser, then:
  - Browse the Vault tab — folders, subfolders, breadcrumb climb.
  - Create a page; confirm it appears in the listing.
  - Switch to Recent view; confirm recent pages render.
  - Open a page; close it; confirm the listing highlight clears.
  - Delete a page from the editor; confirm the sidebar refreshes (exercises the `vault-page-deleted` listener on the new component).
  - Click a wiki-link in a conversation; confirm the sidebar navigates to the right folder.
- [ ] **Step 3: Commit.**

  ```bash
  git add src/decafclaw/web/static/components/vault-sidebar.js \
          src/decafclaw/web/static/components/conversation-sidebar.js
  git commit -m "refactor(web): extract wiki tab into vault-sidebar component"
  ```

---

## Phase 2 — Backend: permission helpers + listing endpoints

### Task 2.1 — Permission-and-kind helpers (TDD)

**Files:**
- Create: `src/decafclaw/web/workspace_paths.py`
- Create: `tests/web/test_workspace_paths.py`

Keeps permission logic out of `http_server.py`, which is already large.

- [ ] **Step 1: Write the failing tests.**

  ```python
  # tests/web/test_workspace_paths.py
  from pathlib import Path
  from decafclaw.web.workspace_paths import (
      resolve_safe, is_secret, is_readonly, detect_kind,
  )


  def test_resolve_safe_allows_paths_under_root(tmp_path):
      (tmp_path / "sub").mkdir()
      (tmp_path / "sub" / "f.txt").write_text("x")
      assert resolve_safe(tmp_path, "sub/f.txt") == (tmp_path / "sub" / "f.txt").resolve()


  def test_resolve_safe_blocks_parent_escape(tmp_path):
      assert resolve_safe(tmp_path, "../etc/passwd") is None


  def test_resolve_safe_blocks_absolute(tmp_path):
      assert resolve_safe(tmp_path, "/etc/passwd") is None


  def test_is_secret_env_file():
      assert is_secret("config/.env") is True


  def test_is_secret_credentials_in_name():
      assert is_secret("some_credentials.json") is True


  def test_is_secret_key_file():
      assert is_secret("ssh/id_rsa.key") is True


  def test_is_secret_regular_file():
      assert is_secret("notes/draft.md") is False


  def test_is_readonly_jsonl_archive():
      assert is_readonly("conversations/abc123.jsonl") is True


  def test_is_readonly_db_file():
      assert is_readonly("embeddings.db") is True
      assert is_readonly("foo/bar.db-wal") is True


  def test_is_readonly_schedule_state():
      assert is_readonly(".schedule_last_run/task.txt") is True


  def test_is_readonly_regular_file():
      assert is_readonly("skills/foo/SKILL.md") is False


  def test_detect_kind_text_extension(tmp_path):
      p = tmp_path / "x.py"
      p.write_bytes(b"\x00garbage")  # extension wins over sniff
      assert detect_kind(p) == "text"


  def test_detect_kind_image_extension(tmp_path):
      p = tmp_path / "x.png"
      p.write_bytes(b"PNG")
      assert detect_kind(p) == "image"


  def test_detect_kind_unknown_text_sniff(tmp_path):
      p = tmp_path / "README"
      p.write_text("hello, world")
      assert detect_kind(p) == "text"


  def test_detect_kind_unknown_binary_sniff(tmp_path):
      p = tmp_path / "blob"
      p.write_bytes(b"abc\x00def")
      assert detect_kind(p) == "binary"
  ```

- [ ] **Step 2: Run the tests — expect failures.**

  ```bash
  make test -k test_workspace_paths
  ```

- [ ] **Step 3: Implement `src/decafclaw/web/workspace_paths.py`.**

  ```python
  from __future__ import annotations
  from fnmatch import fnmatch
  from pathlib import Path

  SECRET_PATTERNS = ("*.env", "*credentials*", "*.key")
  READONLY_PATTERNS = (
      "conversations/*.jsonl",
      "*.db",
      "*.db-wal",
      "*.db-shm",
      ".last_run",
      ".schedule_last_run/*",
      ".schedule_last_run/**",
  )

  TEXT_EXTENSIONS = frozenset({
      ".md", ".py", ".json", ".yaml", ".yml", ".sh",
      ".js", ".ts", ".css", ".html", ".txt",
      ".toml", ".ini", ".cfg", ".conf", ".log", ".csv", ".sql",
  })
  IMAGE_EXTENSIONS = frozenset({
      ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".bmp", ".ico",
  })


  def resolve_safe(root: Path, rel: str) -> Path | None:
      if not rel:
          return root
      candidate = (root / rel).resolve()
      try:
          candidate.relative_to(root.resolve())
      except ValueError:
          return None
      return candidate


  def is_secret(rel_path: str) -> bool:
      name = Path(rel_path).name.lower()
      return any(fnmatch(name, p.lower()) for p in SECRET_PATTERNS)


  def is_readonly(rel_path: str) -> bool:
      rel_lower = rel_path.lower().replace("\\", "/")
      return any(fnmatch(rel_lower, p.lower()) for p in READONLY_PATTERNS)


  def detect_kind(path: Path) -> str:
      ext = path.suffix.lower()
      if ext in TEXT_EXTENSIONS:
          return "text"
      if ext in IMAGE_EXTENSIONS:
          return "image"
      try:
          head = path.read_bytes()[:8192]
      except OSError:
          return "binary"
      if b"\x00" in head:
          return "binary"
      try:
          head.decode("utf-8")
      except UnicodeDecodeError:
          return "binary"
      return "text"
  ```

- [ ] **Step 4: Run tests — expect pass.**
- [ ] **Step 5: Commit.**

  ```bash
  git add src/decafclaw/web/workspace_paths.py tests/web/test_workspace_paths.py
  git commit -m "feat(web): workspace permission + kind helpers"
  ```

### Task 2.2 — `GET /api/workspace` listing (TDD)

**Files:**
- Modify: `src/decafclaw/http_server.py` (new `workspace_list` handler + route registration near line 1147)
- Create/modify: `tests/web/test_workspace_http.py`

Per-file response shape:

```json
{
  "name": "SKILL.md",
  "path": "skills/foo/SKILL.md",
  "size": 1234,
  "modified": 1730000000.0,
  "kind": "text",
  "readonly": false,
  "secret": false
}
```

Per-folder: `{"name": "skills", "path": "skills"}`.

- [ ] **Step 1: Write failing tests.** Cover: basic listing (folders first, then files, alphabetical); `?folder=sub` scopes to subdir; `secret:true` set for `.env` files; `readonly:true` set for `conversations/*.jsonl`; dotfiles are included in the response (filtering is frontend concern); unknown folder → 404; path-escape → 404. Use a Starlette `TestClient` fixture; mirror the pattern in any existing `tests/web/` test if present — otherwise see `tests/conftest.py` for app-factory helpers.
- [ ] **Step 2: Run tests — expect failures.**
- [ ] **Step 3: Implement `workspace_list`** in `http_server.py`. Reuses the auth decorator pattern from `vault_list` (line ~489). Uses `resolve_safe(config.workspace_path, folder)`. Enumerates folder contents, splits folders vs. files, sorts alphabetically, populates `kind/readonly/secret` via the helpers.
- [ ] **Step 4: Register the route** near line 1147:

  ```python
  Route("/api/workspace", workspace_list, methods=["GET"]),
  ```

- [ ] **Step 5: Run tests — expect pass.**
- [ ] **Step 6: Commit.**

### Task 2.3 — `GET /api/workspace/recent` (TDD)

- [ ] **Step 1: Write failing tests.** Returns up to 50 files sorted by mtime desc; secret/readonly flags set correctly; folders not included; empty workspace → `[]`; dotfile paths are included (frontend filter).
- [ ] **Step 2: Implement `workspace_recent`.** Walk workspace tree with `os.walk`, collect files with their mtimes, sort descending, slice to 50. Compose the same per-file payload as Task 2.2.
- [ ] **Step 3: Register route** at `/api/workspace/recent` (GET).
- [ ] **Step 4: Run tests — expect pass.**
- [ ] **Step 5: Commit.**

  ```bash
  git commit -m "feat(web): workspace listing endpoints (browse + recent)"
  ```

---

## Phase 3 — Backend: content, save, delete, rename

### Task 3.1 — File content endpoints (TDD)

**Plan deviation (discovered during Phase 2 execution):** `GET /api/workspace/{path:path}` already exists as `serve_workspace_file` — a raw-bytes file-serving endpoint used by `markdown.js` and `user-message.js` to display user-message attachments inline in the chat UI. Replacing it with a kind-branching handler (text → JSON) would break text attachments. Instead we split by purpose:

- `GET /api/workspace/{path:path}` — **augment** the existing raw-bytes handler with a secret-pattern check (403). Everything else stays as-is (continues to serve images inline, force-download other binaries with `Content-Disposition: attachment`).
- `GET /api/workspace-file/{path:path}` — **new** endpoint for the Files-tab editor. Returns JSON `{content, modified, readonly}` for text; 415 for non-text kinds; 403 for secret; 404 for missing / path-escape.

PUT/DELETE/rename on `/api/workspace/{path:path}` are new (different methods; no collision with the existing GET).

Task 3.1 splits into 3.1a and 3.1b:

**Task 3.1a — augment `serve_workspace_file` with secret check (TDD).**

- [ ] **Step 1: Tests** — secret file GET returns 403; non-secret file GET still serves bytes as before.
- [ ] **Step 2: Implement** the secret-pattern check at the top of `serve_workspace_file` (reuse `is_secret` from `workspace_paths.py`).
- [ ] **Step 3: Run tests — pass.**
- [ ] **Step 4: Commit** — `fix(web): 403 on secret-path file access`.

**Task 3.1b — new `GET /api/workspace-file/{path:path}` (TDD).**

Behavior:
- Text → JSON `{content, modified, readonly}`.
- Image / binary / unknown → 415 ("use /api/workspace/ for raw delivery"). The Files-tab editor only opens text files through this endpoint; image preview and binary download go through `serve_workspace_file`.
- Secret → 403.
- Missing → 404.
- Path-escape → 404.

- [ ] **Step 1: Tests** — text read, non-text 415, secret 403, missing 404, path-escape 404.
- [ ] **Step 2: Implement `workspace_read_json`** in `http_server.py`. Reuses `resolve_safe`, `is_secret`, `is_readonly`, `detect_kind`.
- [ ] **Step 3: Register route** `Route("/api/workspace-file/{path:path}", workspace_read_json, methods=["GET"])`.
- [ ] **Step 4: Run tests — pass.**
- [ ] **Step 5: Commit** — `feat(web): JSON text-content endpoint for Files tab`.

### Task 3.2 — `PUT /api/workspace/{path:path}` (TDD)

Body: `{"content": str, "modified": float}`. Mtime-conflict check mirrors vault write.

- Secret / readonly → 403.
- Non-text kind → 415.
- Stale `modified` (differs from current by more than a small epsilon) → 409.
- Creates intermediate directories if needed.

- [ ] **Step 1: Write failing tests** — new file creation; overwrite with correct mtime; stale mtime 409; readonly path 403; secret path 403; binary-kind path 415.
- [ ] **Step 2: Implement `workspace_write`**. Return `{"ok": true, "modified": <new_mtime>}` on success.
- [ ] **Step 3: Register route.** `Route("/api/workspace/{path:path}", workspace_write, methods=["PUT"])`.
- [ ] **Step 4: Run tests — pass.**
- [ ] **Step 5: Commit.**

### Task 3.3 — `DELETE /api/workspace/{path:path}` (TDD)

- Secret / readonly → 403.
- Missing → 404.
- Prune empty parent dirs (same pattern as `vault_delete`, line ~777).
- Success → `{"ok": true}`.

- [ ] **Step 1: Tests.**
- [ ] **Step 2: Implement `workspace_delete`.**
- [ ] **Step 3: Register route** at `DELETE /api/workspace/{path:path}`.
- [ ] **Step 4: Commit.**

### Task 3.4 — `PUT /api/workspace/{path:path}?rename_to=<new>` (TDD)

Folds into `workspace_write` by checking for `rename_to` query param first (same pattern as `_vault_rename` at line ~666).

- Rejects if either old or new path is secret or readonly (403).
- Rejects if new path already exists (409).
- Creates intermediate directories for the new path.

- [ ] **Step 1: Tests.**
- [ ] **Step 2: Implement** the branch + new `_workspace_rename` helper.
- [ ] **Step 3: Commit.**

  ```bash
  git commit -m "feat(web): workspace content read/save/delete/rename endpoints"
  ```

---

## Phase 4 — Backend: folder operations

### Task 4.1 — `POST /api/workspace` (create file or folder) (TDD)

Body: `{"type": "file"|"folder", "path": "...", "content": "..."}`.

- File create: writes content (empty string if omitted); secret/readonly paths → 403.
- Folder create: `mkdir` under workspace (auto-create parents).
- Path escape → 404.

- [ ] **Step 1: Tests.**
- [ ] **Step 2: Implement `workspace_create`.**
- [ ] **Step 3: Register route.** `Route("/api/workspace", workspace_create, methods=["POST"])`.
- [ ] **Step 4: Commit.**

### Task 4.2 — Folder delete (TDD)

Delete empty folders via the existing `workspace_delete` by detecting that the target is a directory.

- Non-empty folder → 409 (no `force` in v1).
- Secret/readonly paths → 403 (the folder could itself match; keep the check consistent).

- [ ] **Step 1: Tests** for folder delete behavior.
- [ ] **Step 2: Extend `workspace_delete`** — branch on `path.is_dir()`; `Path.rmdir()` for empty folders, return 409 if `OSError: Directory not empty`.
- [ ] **Step 3: Commit.**

  ```bash
  git commit -m "feat(web): workspace folder create/delete endpoints"
  ```

---

## Phase 5 — Frontend: CodeMirror 6 + file-editor

### Task 5.1 — Add CodeMirror deps

**Files:**
- Modify: `src/decafclaw/web/static/package.json`
- Run: `npm install` (from `src/decafclaw/web/static/`)
- Run: `make vendor`

- [ ] **Step 1:** Add to `package.json` `dependencies`:

  ```json
  "@codemirror/state": "^6.0.0",
  "@codemirror/view": "^6.0.0",
  "@codemirror/commands": "^6.0.0",
  "@codemirror/language": "^6.0.0",
  "@codemirror/search": "^6.0.0",
  "codemirror": "^6.0.0",
  "@codemirror/lang-markdown": "^6.0.0",
  "@codemirror/lang-python": "^6.0.0",
  "@codemirror/lang-json": "^6.0.0",
  "@codemirror/lang-yaml": "^6.0.0",
  "@codemirror/lang-javascript": "^6.0.0"
  ```

- [ ] **Step 2:** `cd src/decafclaw/web/static && npm install`.
- [ ] **Step 3:** `make vendor` — rebuilds the vendor bundle. Expect bundle size to grow ~80–150 KB (gzipped numbers are smaller).
- [ ] **Step 4:** Commit `package.json`, `package-lock.json`, and regenerated bundle (match the project's existing vendoring-commit convention — inspect a prior dep-add commit to see what's tracked).

  ```bash
  git commit -m "chore(web): add codemirror 6 deps for file-editor"
  ```

### Task 5.2 — `file-editor.js` Lit component

**Files:**
- Create: `src/decafclaw/web/static/components/file-editor.js`

Behavior:

- Props: `path` (String), `content` (String), `modified` (Number), `kind` (String), `readonly` (Boolean), `saveEndpoint` (String, default `/api/workspace/`).
- Creates a CodeMirror 6 `EditorView` targeting a local `<div>`. Initial content from `this.content`.
- Extension → language lookup:
  - `.md` → markdown
  - `.py` → python
  - `.json` → json
  - `.yaml` / `.yml` → yaml
  - `.js` / `.ts` → javascript
  - else → plain text (no language extension).
- `readonly=true`: include `EditorState.readOnly.of(true)` and `EditorView.editable.of(false)`.
- Auto-save: subscribe via `EditorView.updateListener.of`. On doc change, debounce 800 ms then PUT to `${saveEndpoint}${encodePagePath(path)}` with `{content, modified}`. On 200, store new `modified` and dispatch `saved`. On 409, dispatch `conflict`. On error, dispatch `error`.
- Status field reflects `saving / saved / conflict / error` states for UI surfacing.

- [ ] **Step 1: Create the file** modeled on `wiki-editor.js` (toolbar slot, status labels) but without Milkdown. Aim for under ~250 lines.
- [ ] **Step 2:** `make check-js` — pass.
- [ ] **Step 3: Manual smoke.** Temporarily mount `<file-editor>` via a dev route or test harness (e.g., an inline `<file-editor path="test.md" content="..." modified="..." kind="text" readonly="false"></file-editor>` in `index.html` during development — remove before commit). Verify rendering, typing, debounced save, language highlighting on a `.py` or `.json` file.
- [ ] **Step 4: Commit.**

  ```bash
  git commit -m "feat(web): file-editor component built on codemirror 6"
  ```

---

## Phase 6 — Frontend: file-page

### Task 6.1 — `file-page.js` Lit component

**Files:**
- Create: `src/decafclaw/web/static/components/file-page.js`

Mirrors `wiki-page.js` structure:

- Props: `path`, `kind`, `readonly`, `standalone` (for opening in a new tab/URL).
- On `path` change: fetches `/api/workspace/{encoded path}`.
  - Text → stores `_content`, renders `<file-editor>` in edit mode or `<pre>` in view mode (toggle via view/edit button, same as wiki-page).
  - Image → renders `<img src="/api/workspace/{encoded path}">`.
  - Binary → renders a download button linking to `/api/workspace/{encoded path}?download=1` plus file metadata.
- Toolbar: breadcrumb (path segments, clickable for nav), rename icon (text-editable files only), delete icon (non-readonly, non-secret, all kinds), view/edit toggle (text-editable files), close.
- Emits:
  - `file-close` (bubbles, composed).
  - `file-navigate-folder` (bubbles, composed, `detail: {folder}`) — mirror of `wiki-navigate-folder`.

On successful DELETE, dispatch `window.dispatchEvent(new CustomEvent('workspace-file-deleted', {detail: {path: this.path}}))` before `_close()` (pattern from #314).

- [ ] **Step 1: Create component.**
- [ ] **Step 2:** `make check-js`.
- [ ] **Step 3: Commit.**

  ```bash
  git commit -m "feat(web): file-page component with view/edit/download modes"
  ```

---

## Phase 7 — Frontend: files-sidebar

### Task 7.1 — `files-sidebar.js` Lit component

**Files:**
- Create: `src/decafclaw/web/static/components/files-sidebar.js`

Mirrors `vault-sidebar.js`:

- State: `_files`, `_folders`, `_currentFolder`, `_view` (`'browse' | 'recent'`), `_recentFiles`, `_showHidden`, `_loading`, `_openFilePath`.
- Methods: `#fetchBrowse`, `#fetchRecent`, `#navigateToFolder`, `#toggleView`, `#toggleHidden`, `#handleFileSelect`, `clearOpenFile()`, `navigateToFileFolder(path)`.
- Event out: `file-open` (bubbles, composed, `detail: {path, kind}`).
- Hidden-file filter: when `_showHidden=false`, skip entries whose `name` starts with `.`. Persist `_showHidden` in `localStorage` under `files-show-hidden`.
- Window listener: `workspace-file-deleted` → refetch current view.
- Refresh button invokes the fetch for the current view.

Row behavior:
- Folder → navigate into.
- File → click emits `file-open` **unless `secret`**.
- Secret → click no-op, tooltip "This file is hidden from the UI by policy."
- Icons: lightweight inline SVG or text glyphs for folder / text / image / binary. Lock overlay for `readonly`; solid lock for `secret`.

- [ ] **Step 1: Create component.**
- [ ] **Step 2:** `make check-js`.
- [ ] **Step 3: Commit.**

  ```bash
  git commit -m "feat(web): files-sidebar component (browse + recent + hidden toggle)"
  ```

---

## Phase 8 — Integration in conversation-sidebar.js + app root

### Task 8.1 — Add Files tab

**Files:**
- Modify: `src/decafclaw/web/static/components/conversation-sidebar.js`
- Modify: `src/decafclaw/web/static/app.js` (or wherever `<wiki-page>` is mounted in the main content area — grep for `wiki-page` to find it)

- [ ] **Step 1: Tab bar button.** Add a "Files" button after the Vault button in the tab bar. Use `_sidebarTab = 'files'`.
- [ ] **Step 2: Import + render `<files-sidebar>`** in the tab-content branch when `_sidebarTab === 'files'`. Wire `@file-open` up to a handler on the sidebar that re-dispatches or passes through to the app root.
- [ ] **Step 3: App-root integration.** Where `<wiki-page>` mounts today in the main content area, add mutual-exclusion with a new `<file-page>` mount:
  - Opening a file (from the Files sidebar) closes any open `<wiki-page>`.
  - Opening a wiki page closes any open `<file-page>`.
  - `file-close` unmounts the `<file-page>` and calls `sidebar.clearOpenFile()`.
- [ ] **Step 4:** `make check-js`.
- [ ] **Step 5: Commit.**

  ```bash
  git commit -m "feat(web): wire files tab + file-page into sidebar and app root"
  ```

---

## Phase 9 — Auto-refetch on turn-complete

### Task 9.1 — Identify the signal (discovery)

The spec assumes the store exposes a turn-complete event. The frontend doesn't clearly expose one under that exact name today.

- [ ] **Step 1: Investigate.**

  ```bash
  grep -rn "turn_complete\|turnComplete\|turn-complete" src/decafclaw/web/static/
  grep -rn "'turn'\|dispatchEvent.*turn" src/decafclaw/web/static/
  ```

  Likely candidates:
  1. A `change` event on the store that fires after every websocket message, including turn-end.
  2. A direct websocket message the store surfaces as a dedicated signal.
  3. Nothing clean — we add a new `turn-complete` event on the store.

- [ ] **Step 2: Record the decision** by appending one sentence to this plan under this task ("Chose option X because Y") before moving to Task 9.2.

### Task 9.2 — Subscribe in files-sidebar

- [ ] **Step 1: Wire the subscription** in `files-sidebar.js` `connectedCallback` per the option chosen in 9.1. On each signal, if the tab is active, silently refetch the current view.
- [ ] **Step 2: Unsubscribe** in `disconnectedCallback`.
- [ ] **Step 3:** `make check-js`.
- [ ] **Step 4: Manual smoke.** Ask the agent in a conversation to write a file under workspace (e.g., `echo hello > workspace/scratch/test.txt` via shell tool). Without touching the UI, confirm the file appears in the Files tab Recent view.
- [ ] **Step 5: Commit.**

  ```bash
  git commit -m "feat(web): auto-refetch files sidebar on turn-complete"
  ```

---

## Phase 10 — Documentation

### Task 10.1 — `docs/files-tab.md`

**Files:**
- Create: `docs/files-tab.md`
- Modify: `docs/index.md`

- [ ] **Step 1: Write `docs/files-tab.md`.** Cover: what the tab is, the permission model, the secret/readonly patterns, the CodeMirror editor choice, the REST endpoint family, and references to the components.
- [ ] **Step 2: Add entry in `docs/index.md`.**
- [ ] **Step 3: Commit.**

### Task 10.2 — CLAUDE.md + README updates

- [ ] **Step 1:** In `CLAUDE.md` "Key files" under Transports, add `vault-sidebar.js`, `files-sidebar.js`, `file-page.js`, `file-editor.js`. Also add `src/decafclaw/web/workspace_paths.py` under Data.
- [ ] **Step 2:** In `README.md` "Key features:" line, add `[Files](docs/files-tab.md)` alongside `[Vault & memory](docs/vault.md)`.
- [ ] **Step 3: Commit.**

  ```bash
  git commit -m "docs(files-tab): feature documentation and key-files update"
  ```

---

## Phase 11 — Self-review + pre-PR polish

### Task 11.1 — Full check

- [ ] `make check` — lint + typecheck (Python + JS) clean.
- [ ] `make test` — all pass.

### Task 11.2 — Manual verification against the spec test plan

Run through every item under **Testing → Frontend** in `spec.md`. Record any deviations as issues worth filing or fix inline if trivial.

### Task 11.3 — Branch self-review

- [ ] `git diff origin/main..HEAD`. Scan for:
  - Bugs or incomplete changes (renamed method in one place but missed another; removed function still referenced elsewhere).
  - Edge cases (dotfile filter, symlink behavior, empty listing, zero-byte file, filename with `?` or `#`).
  - Doc gaps (new endpoints documented; any new config not documented).
  - Convention violations (bare error strings instead of `ToolResult`, imports inside functions, undeclared attributes on Config).
- [ ] Fix anything found before PR.

### Task 11.4 — Hand off to `/dev-session pr`

When the branch is ready, use `/dev-session pr` to squash, push, open the PR, and run the Copilot review cycle.

---

## Self-review of this plan

- **Spec coverage:** all v1 "In" items covered by phases 1–9; docs by phase 10; testing by phase 11 ✓.
- **Out-of-scope items** remain out: no server websocket broadcast, no upload, no multi-select, no git history, no Monaco.
- **Placeholder scan:** Phase 9 Task 9.1 is a discovery task with explicit grep commands and a written decision gate — intentional, not a placeholder.
- **Task granularity:** each task is 30–60 minutes of focused work; steps inside are fine-grained. Full code is shown for the non-obvious pieces (workspace_paths helpers, file-editor shape); pattern-matching tasks (e.g., new REST endpoints paralleling vault) reference concrete line numbers in `http_server.py`.
- **Type/name consistency:** `vault-sidebar`, `files-sidebar`, `file-page`, `file-editor` used consistently; events `wiki-open`, `file-open`, `file-close`, `workspace-file-deleted` are distinct.
