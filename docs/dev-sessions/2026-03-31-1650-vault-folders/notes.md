# Vault Folder Support — Notes

## Summary

Implemented hierarchical folder support for the vault system (GitHub issue #170). Merged as single squashed commit.

## Changes Made

### API
- `GET /api/vault?folder=` — returns `{folder, folders, pages}` scoped to a directory
- `PUT /api/vault/{page}` with `rename_to` — moves/renames pages with embedding re-index
- `POST /api/vault/folders` — creates empty directories
- `DELETE /api/vault/{page}` — deletes pages with empty dir cleanup and embedding removal
- Path validation, conflict detection, ambiguous request rejection (rename_to + content)

### UI
- Sidebar: file-browser navigation with breadcrumbs, folder icons, "+ Page" / "+ Folder" buttons, open page highlighting, stale data clearing on error
- Editor toolbar: breadcrumbs replace format buttons (saves vertical space), inline rename/move input, delete button with confirmation
- Auto-sync sidebar when opening a page via wiki-link

### Wiki Links
- Confirmed resolve_page, @[[folder/Page]] mentions, and Milkdown plugin all handle folder paths (no changes needed — just tests added)

### Prompts
- SKILL.md: folder organization section with conventions
- AGENT.md: folder mention syntax and vault_list guidance

### Accessibility (from PR review)
- Breadcrumb spans → buttons for keyboard/screen reader support
- :focus-visible styles on breadcrumb buttons
- aria-labels on icon-only buttons (rename, delete, confirm, cancel)

### Bug fixes (from PR review and testing)
- encodePagePath() — encode per segment preserving `/` (was 404ing nested pages)
- Flush editor auto-save before rename (race condition)
- Always re-fetch sidebar after page navigation (stale list after rename)
- Reject trailing slash and normalize .md suffix in rename paths
- Case-insensitive folder sorting

## Issues Filed
- #187 — Chat sidebar: folder-style navigation for archived/system conversations (linked to #184)

## Deferred
- Garden auto-reorganization into folders (follow-up issue not yet filed)
- Relative wiki links (`[[./Sibling]]`)
- Format toolbar as collapsible/optional (removed in favor of breadcrumbs for now)

## Observations
- Copilot review caught several real issues (path encoding bug, rename race condition, accessibility gaps). Worth having on PRs.
- The squash workflow with merge commits from prior PRs was messy — `git reset --soft origin/main` was the cleanest approach.
