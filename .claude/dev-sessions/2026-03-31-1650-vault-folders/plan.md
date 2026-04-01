# Vault Folder Support — Plan

_Implementation plan for GitHub issue #170_

## Overview

The backend already supports folders — vault tools accept folder paths, resolve_page() searches subdirectories, embeddings store full relative paths. The work is primarily:

1. **API** — make `GET /api/vault` folder-aware (return folder contents, not flat list)
2. **UI** — file-browser sidebar navigation, breadcrumbs, rename/move
3. **Rename API** — `PUT` with `rename_to` field, embedding re-index
4. **Wiki links** — ensure `[[folder/Page]]` works end-to-end
5. **Prompts** — update SKILL.md and AGENT.md with folder guidance

---

## Phase 1: Folder-aware API

Update `GET /api/vault` to return pages and subfolders for a specific folder.

### Prompt 1.1: Update vault list endpoint

**Context:** `src/decafclaw/http_server.py`, lines 243-261. Currently returns a flat list of all `.md` files recursively.

**Task:**
- Add `folder` query parameter to `GET /api/vault` (optional, defaults to vault root)
- Validate the folder parameter: reject `..`, leading `/`, or paths outside the vault root (use the same safety checks as `_safe_folder()` in vault tools)
- When `folder` is provided, resolve it relative to the vault root
- Return a new response shape:
  ```json
  {
    "folder": "agent/pages",
    "folders": [
      { "name": "subfolder", "path": "agent/pages/subfolder" }
    ],
    "pages": [
      { "title": "My Page", "path": "agent/pages/My Page", "folder": "agent/pages", "modified": 1711900000 }
    ]
  }
  ```
- `folders`: immediate child directories of the target folder (only dirs that contain at least one `.md` file somewhere inside, to avoid showing empty dirs)
- `pages`: only `.md` files directly in the target folder (not recursive)
- Sort folders alphabetically, then pages alphabetically
- When `folder` is omitted or empty string, list the vault root

**Verify:** `make check` passes. Manual test: `curl localhost:PORT/api/vault` returns new shape, `curl localhost:PORT/api/vault?folder=agent` returns agent subfolders.

---

## Phase 2: Sidebar folder navigation

Convert the flat vault page list into a file-browser-style folder navigator.

### Prompt 2.1: Sidebar state and folder fetching

**Context:** `src/decafclaw/web/static/components/conversation-sidebar.js`. The vault tab currently fetches all pages and renders a flat list (lines 269-286). The `#fetchWikiPages()` method (around line 110) fetches from `/api/vault`.

**Task:**
- Add a `currentVaultFolder` reactive property (string, default `""` for root)
- Update `#fetchWikiPages()` to pass `?folder=${encodeURIComponent(this.currentVaultFolder)}` to the API
- Store both `folders` and `pages` from the response as separate properties
- Add a `#navigateToFolder(folderPath)` method that sets `currentVaultFolder` and re-fetches
- Re-fetch when navigating into or out of folders

**Verify:** `make check-js` passes. No visual changes yet — just wiring.

### Prompt 2.2: Render folder breadcrumbs and folder list

**Context:** Same file. The vault tab render section (around lines 269-286).

**Task:**
- Above the page list, render breadcrumbs for `currentVaultFolder`:
  - Split the folder path on `/` to get segments
  - Render each segment as a clickable link that calls `#navigateToFolder()` with the path up to that segment
  - First segment is always "vault" (or a home icon) linking to root (`""`)
  - Use ` / ` as separator between segments
  - Style: smaller text, muted color, clickable segments
- Below breadcrumbs, render folders first:
  - Each folder shows a folder icon (📁 or CSS) and folder name
  - Clicking a folder calls `#navigateToFolder(folder.path)`
  - Style folders distinctly from pages (slightly different background or icon)
- Then render pages as before (but now only pages in the current folder)
- The "+ New Page" button should create pages in `currentVaultFolder`

**Verify:** `make check-js` passes. Visually: vault tab shows breadcrumbs, folders are clickable and navigate into subfolders, pages show for current folder only.

### Prompt 2.3: Navigate sidebar when opening a page

**Context:** When a page is opened (clicked from list, or via wiki-link), the sidebar should navigate to that page's folder so the breadcrumbs and listing reflect the page's location.

**Task:**
- When a `wiki-open` event is dispatched with a page path (e.g. `agent/pages/Foo`), extract the folder portion and set `currentVaultFolder` to it
- This keeps sidebar and editor in sync — opening a deeply nested page navigates the sidebar there
- If the page is at root level, set folder to `""`

**Verify:** Click a page from a subfolder listing — sidebar stays in that folder. Open a page via wiki-link from the editor — sidebar navigates to the page's folder.

---

## Phase 3: Page editor breadcrumbs

### Prompt 3.1: Add breadcrumb bar to wiki-page component

**Context:** `src/decafclaw/web/static/components/wiki-page.js`. Currently shows the page title and edit/view toggle.

**Task:**
- Above the editor/viewer content, add a breadcrumb bar showing the page's full path
- Parse `this.page` (which is the full relative path like `agent/pages/My Page`) into folder segments + page name
- Render folder segments as clickable links that dispatch a `wiki-navigate-folder` event (or similar) with the folder path
- The page name (last segment) is shown but not clickable (it's the current page)
- Style: consistent with sidebar breadcrumbs (small text, muted, clickable segments)
- The parent component (or sidebar) should listen for folder navigation events and update the sidebar accordingly

**Verify:** `make check-js` passes. Opening a page shows `agent / pages / My Page` above the editor. Clicking `agent` navigates the sidebar to the `agent` folder.

---

## Phase 4: Rename/move API and UI

### Prompt 4.1: Add rename endpoint to vault API

**Context:** `src/decafclaw/http_server.py`, `PUT /api/vault/{page:path}` (lines 284-326). Currently handles content writes with conflict detection.

**Task:**
- When the request body includes a `rename_to` field (and no `content` field), treat it as a rename/move operation:
  - Validate `rename_to`: reject `..`, leading `/`, traversal (same safety as writes)
  - Resolve old path: `vault_root / page + ".md"`
  - Resolve new path: `vault_root / rename_to + ".md"`
  - Return 404 if old path doesn't exist
  - Return 409 if new path already exists
  - Create parent directories for new path (`mkdir(parents=True, exist_ok=True)`)
  - Move the file (`Path.rename()`)
  - Clean up empty parent directories from old location (walk up, `rmdir()` if empty, stop at vault root)
  - Update embedding index: delete old path entries, re-index new path
  - Return new page metadata (title, path, folder, modified)
- Import the embedding helpers needed for re-indexing

**Verify:** `make check` passes. Manual test: rename a page via curl, verify file moved, old location gone, embeddings updated.

### Prompt 4.2: Add rename UI to page editor

**Context:** `wiki-page.js`. Currently has edit/view mode toggle but no rename action.

**Task:**
- Add a rename button (pencil icon or "Rename" text) near the page title/breadcrumbs
- Clicking it shows an inline input pre-filled with the current page path (without `.md`)
- User can edit the path (including folder portions)
- On confirm (Enter or button), send `PUT /api/vault/{oldPage}` with `{ "rename_to": "new/path" }`
- On success: dispatch `wiki-open` event with the new path so the editor reloads at the new location
- On 409: show error that target already exists
- On cancel (Escape): hide the input, revert

**Verify:** `make check-js` passes. Rename a page, verify it appears at the new path in the sidebar. Rename to a new folder path, verify folder is created.

---

## Phase 5: Wiki link resolution for folder paths

### Prompt 5.1: Ensure resolve_page handles explicit folder paths

**Context:** `src/decafclaw/skills/vault/tools.py`, `resolve_page()` (lines 29-79). Already tries direct path match first, then falls back to stem search.

**Task:**
- Verify that `resolve_page(config, "agent/pages/Foo")` correctly resolves to `vault/agent/pages/Foo.md`
- The existing logic on line 44 (`candidate = vault / (page + ".md")`) should already handle this — but verify with a test
- Write a unit test in the appropriate test file that:
  - Creates a temp vault with `folder1/Page.md` and `folder2/Page.md`
  - Asserts `resolve_page(config, "folder1/Page")` returns `folder1/Page.md`
  - Asserts `resolve_page(config, "Page")` returns one of them (deterministic, sorted)
  - Asserts `resolve_page(config, "Page", from_page="folder1/Other")` prefers `folder1/Page.md`

**Verify:** `make test` passes including the new test.

### Prompt 5.2: Ensure @[[folder/Page]] mentions work in chat

**Context:** `src/decafclaw/agent.py`, line 640: `_WIKI_MENTION_RE = _re.compile(r'@\[\[([^\]]+)\]\]')`.

**Task:**
- The regex `[^\]]+` already matches any characters including `/`, so `@[[folder/Page]]` should already be captured
- Verify with a unit test that the regex matches `@[[agent/pages/Foo]]` and extracts `agent/pages/Foo`
- Also verify `@[[agent/pages/Foo|display text]]` works (the pipe syntax for display text)
- If the regex doesn't handle these cases, fix it

**Verify:** `make test` passes including the new test.

### Prompt 5.3: Ensure Milkdown wiki-link plugin handles folder paths

**Context:** `src/decafclaw/web/static/components/milkdown-wiki-link.js`. Parses `[[text]]` into wiki_link nodes.

**Task:**
- Check the regex that parses `[[...]]` — ensure it allows `/` in the target
- The current input rule regex and remark plugin should already handle this since they match between `[[` and `]]`
- Verify that typing `[[folder/Page]]` creates a proper wiki-link node with `data-wiki-page="folder/Page"`
- When clicking such a link, ensure the `wiki-open` event dispatches with `folder/Page`
- If any regex or handler strips slashes or breaks on them, fix it

**Verify:** `make check-js` passes.

---

## Phase 6: Prompt and documentation updates

### Prompt 6.1: Update vault SKILL.md with folder guidance

**Context:** `src/decafclaw/skills/vault/SKILL.md`.

**Task:**
- Add a section on folder organization:
  - Vault supports hierarchical folders for organizing pages
  - Prefer creating pages in relevant folders over flat root
  - Suggested conventions: `projects/`, `people/`, `resources/`, topic areas
  - When a topic area grows (3+ related pages), consider consolidating into a folder
  - Use `vault_write` with folder paths: `vault_write(page="projects/decafclaw/roadmap", ...)`
  - Use `vault_list` with folder filter to explore a specific area
- Add `[[folder/Page]]` link syntax to the wiki links section
- Keep guidance light — encourage patterns, don't prescribe rigid structure

### Prompt 6.2: Update AGENT.md with folder mention

**Context:** `data/decafclaw/AGENT.md` (or `src/decafclaw/prompts/AGENT.md` if bundled).

**Task:**
- Add brief mention that the vault supports folders for organization
- Reference the vault skill for details
- Mention `@[[folder/Page]]` syntax for chat mentions

### Prompt 6.3: File follow-up issue for garden auto-reorganization

**Task:**
- Create a GitHub issue for garden skill auto-reorganization into folders
- Reference #170 as the parent feature
- Describe: garden skill should be able to suggest or execute page moves into folders during maintenance sweeps
- Add to the project board as backlog

---

## Phase 7: Final verification and cleanup

### Prompt 7.1: End-to-end verification

**Task:**
- Run `make check` (lint + typecheck for Python and JS)
- Run `make test`
- Manual walkthrough in the web UI:
  - Navigate folders in sidebar
  - Create a page in a subfolder
  - Rename/move a page to a different folder
  - Click wiki links with folder paths
  - Use `@[[folder/Page]]` in chat
  - Verify breadcrumbs work in both sidebar and editor
- Update docs if any behavior differs from spec

### Prompt 7.2: Update CLAUDE.md and docs

**Task:**
- Update CLAUDE.md if any new conventions or key files were added
- Update relevant docs/ pages (vault docs, context-map if prompt assembly changed)
- Write session notes in `notes.md`
