# Vault Folder Support — Notes

## Summary

Implemented hierarchical folder support for the vault system (GitHub issue #170). The backend already supported folders in the file system — this session added folder-aware API, UI navigation, rename/move, and prompt guidance.

## Changes Made

### Phase 1: Folder-aware API
- Updated `GET /api/vault` to accept `?folder=` query param
- New response shape: `{folder, folders, pages}` instead of flat array
- Folders only listed if they contain at least one `.md` file
- Path validation rejects traversal attempts

### Phase 2: Sidebar folder navigation
- Added `_vaultFolder` and `_vaultFolders` state to sidebar component
- Breadcrumb navigation (vault / folder / subfolder)
- Folder listing with folder icons, pages listed after
- New pages created in current folder
- Auto-sync sidebar when opening a page via wiki-link

### Phase 3: Editor breadcrumbs
- Clickable folder path above wiki page content (edit and view modes)
- Dispatches `wiki-navigate-folder` event to sync sidebar

### Phase 4: Rename/move API and UI
- `PUT /api/vault/{page}` with `rename_to` field moves the file
- Auto-creates parent dirs, cleans up empty old dirs
- Re-indexes embeddings at new path
- Inline rename input in page editor with confirm/cancel

### Phase 5: Wiki link resolution
- Confirmed `resolve_page` handles explicit folder paths
- Confirmed `@[[folder/Page]]` regex matches correctly
- Confirmed Milkdown wiki-link plugin allows `/` in targets
- Added 7 new tests covering folder resolution and mention parsing

### Phase 6: Prompt updates
- SKILL.md: folder organization section with conventions
- AGENT.md: folder mention syntax and vault_list guidance

### Phase 7: Verification
- 902 tests pass (17 new tests added)
- Updated docs/vault.md with folders section

## Deferred

- Garden auto-reorganization into folders (follow-up issue to file)
- Relative wiki links (`[[./Sibling]]`)
