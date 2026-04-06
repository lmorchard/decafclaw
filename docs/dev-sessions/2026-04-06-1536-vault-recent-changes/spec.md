# Vault Recent Changes — Spec

## Summary

Add a "Recent Changes" view to the vault sidebar tab, toggled via a widget that switches between the existing folder/browse view and a new recency-sorted list.

## Backend

- New REST endpoint: `GET /api/vault/recent?limit=50`
- Returns vault pages sorted by file mtime (most recent first)
- Response: `{ "pages": [{ "title", "path", "folder", "modified" }, ...] }`
- Limit configurable via `vault.recent_changes_limit` (default 50)

## Frontend

- Add a toggle/switch in the vault sidebar header to swap between "Browse" and "Recent"
- "Browse" is the existing folder tree view (default)
- "Recent" shows a flat list of recently modified pages sorted by mtime
- Each entry shows page name and relative timestamp (e.g., "2 hours ago")
- Clicking a page opens it in the vault viewer (same as browse view)
- Persist the toggle state in the component (no need for server persistence)

## Config

- `vault.recent_changes_limit` (int, default 50) in `VaultConfig`

## Non-goals

- No change summaries or diffs
- No git integration
- No pagination (scroll for more)
