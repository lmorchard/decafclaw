# Vault Recent Changes — Plan

## Step 1: Config + Backend

- Add `recent_changes_limit: int = 50` to `VaultConfig` in config_types.py
- Add `GET /api/vault/recent` endpoint in http_server.py
  - Recursively walk vault, collect all .md files with mtime
  - Sort by mtime descending, limit to config value
  - Return `[{title, path, folder, modified}]`

## Step 2: Frontend — view toggle + recent list

- Add `_vaultView` state property ('browse' | 'recent') to conversation-sidebar.js
- Add toggle buttons in the vault tab header (Browse / Recent)
- When 'recent' is selected, fetch `/api/vault/recent` and render flat list
- Each item shows title + relative time (e.g., "2h ago")
- Clicking opens the page (same as browse view)
- Load recent view on toggle (not eagerly)

## Step 3: Tests + check

- Test the REST endpoint
- Run make check, make test
