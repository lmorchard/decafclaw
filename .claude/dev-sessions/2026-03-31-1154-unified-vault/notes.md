# Unified Vault — Session Notes

## Session started: 2026-03-31

### Context
- Issues: #175 (vault unification), #180 (memory → journal), #170 (wiki folders)
- Goal: Unify wiki, memory, and vault concepts; consider including agent prompts in the vault

### Decisions made during brainstorm
- Dedicated `workspace/vault/` subfolder (not vault-as-workspace-root)
- Agent files namespaced under `agent/` to avoid collisions with user's Obsidian vault
- `agent/pages/` for curated wiki, `agent/journal/` for daily entries
- Vault path configurable — can point at user's Syncthing-synced Obsidian vault
- `vault_journal_append` as explicit tool name (not generic `vault_append`)
- Dream/garden scoped to `agent/` only — no autonomous editing of user pages
- Embedding source types: `journal`, `page`, `user` with per-type boost weights
- Big-bang migration via post-deploy script
- Deferred: agent prompts in vault, folder tree nav, backlinks panel, graph view

### Implementation (8 phases, all completed)
1. VaultConfig dataclass + path properties on Config
2. 6 vault tools replacing wiki + memory tools; removed old modules
3. Embeddings: new source types, vault-aware reindex, per-type boosts
4. Memory context: updated source labels
5. Agent.py: wiki context resolves from vault root
6. Web UI: API routes, sidebar, frontend URLs, URL params
7. Dream/garden/AGENT.md updated for vault conventions
8. Migration script, cleanup, CLAUDE.md updates

### Bugs found and fixed during testing
- **UNIQUE constraint on embeddings** — `INSERT OR IGNORE` skips but `cursor.lastrowid` returns stale value. Fixed with `cursor.rowcount > 0` guard.
- **429 rate limiting during reindex** — Added retry with exponential backoff, lowered concurrency to 4.
- **Vault read 500 errors** — `resolve_page()` returns absolute paths but `_vault_root()` was relative. Fixed by resolving vault root. Added regression test with relative `data_home`.
- **`?vault=` URL param not read on startup** — `getWikiFromUrl()` and popstate handler still read `?wiki=`. Missed during rename.
- **Wiki link text duplication (pre-existing)** — Milkdown `toMarkdown.runner` emitted `[[text]]` but returned falsy, so the default handler also emitted the plain text. Fix: `return true` to suppress default.
- **Editor double-click navigation** — Used `textContent` (display text) instead of `data-wiki-page` attribute (target). Broke for pipe links.
- **agent_folder default desync** — Default was `workspace/vault/agent/` (full path) instead of `agent/` (relative to vault_root). Fixed per PR review.
- **Folder traversal in vault tools** — `folder` param in search/list wasn't validated. Added `_safe_folder()` helper.

### Attempted and reverted
- **Wiki link expand/collapse in editor** — Tried Obsidian-style live preview where `[[target|display]]` marks expand to raw syntax on cursor enter and collapse on leave. Three attempts (dispatch wrapping, selectionchange listener, RAF gating) all caused flickering or infinite loops. Fundamental issue: mutating the document on cursor move fights ProseMirror's transaction model. Filed #185 for future attempt (possibly using inline nodes or NodeView instead of marks).

### Conversation embedding removal
- Removed conversation embedding indexing entirely (~7000 entries, expensive, rarely surfaced)
- `conversation_search` rewritten as brute-force JSONL substring grep
- Proactive context already excluded conversation results
- Dream skill updated to journal noteworthy conversation findings

### Follow-up issues filed
- #184 — Load conversation list via REST API instead of WebSocket
- #185 — Wiki link editor: editable [[target|display]] pipe links

### Deferred items (need follow-up issues)
- Folder tree navigation in vault sidebar
- Backlinks panel in web UI
- Vault search UI
- Agent-assisted curation of user pages
- Agent prompts (SOUL.md, USER.md) in vault
- Page templates
- Recently edited pages list
- Frontmatter/tag-based filtering
- Move/rename pages in UI
- Chunking strategy for large pages in embeddings
- `memory_recent` behavioral regression (empty query returns file listings not entries)

### PR
- lmorchard/decafclaw#183 — squashed to single commit, ready for merge
- Post-deploy: `make migrate-vault` then `make reindex`
