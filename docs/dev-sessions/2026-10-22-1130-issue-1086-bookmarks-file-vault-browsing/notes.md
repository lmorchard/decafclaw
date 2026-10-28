# issue-1086-bookmarks-file-vault-browsing

## Resume here

- Issue: https://github.com/lmorchard/decafclaw/issues/1086 (Bookmarks for file and vault browsing)
- Worktree: .claude/worktrees/issue-1086-bookmarks-file-vault-browsing
- Branch: issue-1086-bookmarks-file-vault-browsing
- Base: origin/main at 75763910c2d04f0922af031e0029bdba58b4867b
- Baseline: `make test` → 4658 passed, 2 skipped at base. Green.
- Flow: implement → draft PR (never ready, no review request, no merge).
- Durable notes: ~/.local/state/ghflow/notes/2026-10-21-1100-burndown-ready-queue/notes-issue-1086.md

## Plan

Backend:
1. `src/decafclaw/web/bookmarks.py` — `BookmarkStore` over `data/{agent_id}/bookmarks.json`
   (atomic writes, asyncio.Lock, same shape as `ConversationFolderIndex`).
2. `http_server.py` — pydantic models (`BookmarkEntry`, `BookmarksResponse`,
   `BookmarkAddRequest`) + handlers `bookmarks_list/add/delete` + routes
   `GET/POST/DELETE /api/bookmarks`.
3. `tests/web/test_bookmarks_http.py` — endpoint + persistence + validation tests.
4. `make gen-api-client` (regenerates committed client from OpenAPI).

Frontend (Lit components under `src/decafclaw/web/static/`):
5. `lib/bookmarks-store.js` — shared cache + subscribe; add/remove/toggle.
6. `components/files-sidebar.js` — pinned Bookmarks section above the view
   toggle (file/dir bookmarks); ☆ toggle button on folder rows.
7. `components/file-page.js` — ☆/★ toggle in the file toolbar.
8. `components/vault-sidebar.js` — pinned Bookmarks section (page/folder
   bookmarks) above the view toggle; ☆ toggle on folder rows.
9. `components/tags-sidebar.js` — pinned Bookmarks section above the tag list
   (spec names this file for the Vault-side list).
10. `components/wiki-page.js` — ☆/★ toggle in the wiki toolbar.
11. `styles/wiki.css` / `styles/sidebar.css` — tag-qualified `.dc-icon-btn`
    overrides per web-ui-design.md; section header class.
12. JS tests: `lib/bookmarks-store.test.js`, `components/files-sidebar.test.js`,
    extend `tags-sidebar.test.js`, `wiki-page.test.js`.

Docs (same PR): `docs/web-ui.md` (Bookmarks feature + REST section),
`docs/files-tab.md` (Bookmarks section).

## Scope decisions (spec silent → repo conventions)

- `label` optional: defaults to the last path segment (basename).
- POST upsert: re-POSTing an existing (target, path) updates label/kind,
  never duplicates.
- `kind` must match `target`: files → file|dir, vault → page|folder (400 otherwise).
- Path validation: non-empty, relative, no `..` segments, no empty segments
  (mirrors `_validate_folder_param` / `conversation_folders._validate_path`).
- DELETE of a missing bookmark → 404 `{"error": ...}`.
- Bookmarks section renders only when that target has ≥1 bookmark (pinned
  position stays constant; empty section would be dead chrome).
- Dir/folder bookmarks are added from the sidebar folder rows (☆); file/page
  bookmarks from the viewer toolbar (per spec).

## Log

- 2026-10-22: worktree created (worktree create, uv sync OK, .env + HTTP_PORT=18930);
  board Ready → In progress (readback matched). Baseline green (4658 passed, 2 skipped).
- 2026-10-22: Implemented. Backend `web/bookmarks.py` (BookmarkStore, per-agent
  `data/{agent_id}/bookmarks.json`) + GET/POST/DELETE `/api/bookmarks` routes in
  `http_server.py`; 18 endpoint tests in `tests/web/test_bookmarks_http.py` (all pass).
  Frontend: `lib/bookmarks-store.js` (shared cache + subscribe, in-flight dedupe),
  pinned Bookmarks section in `files-sidebar.js` and `vault-sidebar.js` (above the
  view toggle), ☆/★ toggle on folder rows in both sidebars, ☆/★ toolbar buttons in
  `file-page.js` and `wiki-page.js`; styles in `sidebar.css`/`wiki.css` (tag-qualified
  `.dc-icon-btn` overrides per house style). 7 component tests in
  `components/bookmarks.test.js` (all pass).
- Deviation from plan item 9: the pinned Vault-bookmarks section lives in
  `vault-sidebar.js` (the Files/Vault tab sidebar), not `tags-sidebar.js` (the
  separate Tags tab); `wiki-page.js` gets the viewer toggle as planned.
- 2026-10-22: `make check` green (ruff, pyright, tsc via check-js, browser-assets);
  `make test` green (4676 passed, 2 skipped). Browser-scenario tests updated for the
  new `/api/bookmarks` GET on sidebar mount (exact request-list assertions +
  logout 401 allowed set).
- Status: draft PR published; left open, not marked ready, no review requested.
