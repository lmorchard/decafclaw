# Vault Folder Support — Spec

_Session for GitHub issue #170_

## Summary

Add hierarchical folder support to the vault system. The backend already stores pages in subdirectories and returns folder metadata — this session focuses on building folder-aware UI navigation, updating the API, enhancing wiki link resolution, and adding agent prompt guidance.

## Decisions

- **Sidebar navigation:** File-browser style (navigate into/out of folders), not a collapsible tree. Better for narrow sidebar.
- **Rename = move:** Renaming a page can change its path, which effectively moves it between folders. No drag-and-drop.
- **Wiki links:** Keep global stem search (`[[PageName]]`), add explicit path syntax (`[[folder/PageName]]`). Relative paths (`[[./Sibling]]`) deferred.
- **Ambiguity resolution:** Prefer pages closer to the linking page's folder (existing `from_page` proximity logic).
- **API:** Folder-aware endpoint — returns pages in a specific folder + list of immediate subfolders. Client doesn't need to build the tree.
- **Editor breadcrumbs:** Show clickable folder path above the editor/viewer.
- **Agent guidance:** Light encouragement to use folders for organization. No rigid prescribed structure. Garden auto-reorganization deferred (follow-up issue).

## UI Changes

### Sidebar (file-browser navigation)

- **Current path breadcrumbs** at the top of the vault list (e.g. `vault / agent / pages /`). Each segment is clickable to navigate up.
- **Folders listed first**, sorted alphabetically, with a folder icon. Pages listed after, sorted alphabetically.
- Clicking a folder navigates into it (replaces the list contents).
- **"+ New Page" creates in the current folder** being viewed.
- Root level shows all top-level folders and pages.

### Page editor/viewer

- **Breadcrumb bar above the editor** showing the page's folder path (e.g. `agent / pages / My Page`). Folder segments are clickable and navigate the sidebar to that folder.

### Rename (with path change)

- Rename action allows changing the full path (e.g. `old/name` → `new/folder/name`).
- Parent directories are auto-created on rename.
- Empty directories are cleaned up after a page is moved out (optional, nice-to-have).

## API Changes

### `GET /api/vault` (updated)

Add query parameter: `folder` (optional, defaults to root).

Response changes:
```json
{
  "folder": "agent/pages",
  "folders": [
    { "name": "subfolder", "path": "agent/pages/subfolder" }
  ],
  "pages": [
    {
      "title": "My Page",
      "path": "agent/pages/My Page",
      "folder": "agent/pages",
      "modified": 1711900000
    }
  ]
}
```

- `folders`: immediate child folders of the requested folder
- `pages`: only pages directly in the requested folder (not recursive)

### `PUT /api/vault/{page:path}` (rename/move)

Support a `rename_to` field in the request body. When present:
- Move the file from old path to new path
- Auto-create parent directories for new path
- Return the new page metadata
- Return 409 if target already exists

## Wiki Link Resolution

### Current behavior (preserved)
- `[[PageName]]` searches all directories by stem, first match wins (sorted, with proximity preference)

### New: explicit path syntax
- `[[folder/PageName]]` resolves to a specific path
- Works in both the Milkdown editor (wiki-link plugin) and agent-side resolution (`resolve_page()`)
- `resolve_page()` already supports paths — just need to ensure the link parsing handles `/` in targets

### Ambiguity
- When multiple pages share a stem, prefer the one closest to the linking page's folder
- Already implemented via `from_page` parameter in `resolve_page()`

## Agent Prompt Changes

### Vault skill SKILL.md
- Add guidance: prefer creating pages in relevant folders over flat root
- Suggest conventions: `projects/`, `people/`, `resources/`, topic areas
- When a topic area grows (3+ related pages), consider consolidating into a folder
- Keep it light — encourage the pattern, don't prescribe rigid structure

### AGENT.md
- Brief mention that the vault supports folders for organization

## Edge Cases

### Embedding index on rename/move
When a page is renamed/moved, its path in the sqlite-vec embedding index becomes stale. On rename, delete old embeddings and re-index the page at its new path. The `reindex_page()` or equivalent should handle this.

### `@[[folder/Page]]` mentions in chat
The `@[[...]]` mention syntax should support folder paths, not just bare stems. The regex already captures the content between `[[` and `]]` — just ensure slashes are allowed and passed through to `resolve_page()`.

### Stale references in conversation archives
If a page is injected as wiki context and later moved, the old reference in archived conversations is stale. This is acceptable — archives are historical snapshots. No action needed.

### Path validation on rename
Reject paths with `..`, leading `/`, or other traversal attempts (consistent with existing `_safe_folder()` checks in vault tools).

## Out of Scope

- Relative wiki links (`[[./Sibling]]`) — future enhancement
- Garden skill auto-reorganization into folders — follow-up issue to file (#TBD)
- Drag-and-drop page moving
- Folder-level permissions or metadata
- Nested folder depth limits
