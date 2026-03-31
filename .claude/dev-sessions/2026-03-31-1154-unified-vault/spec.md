# Unified Vault — Spec

## Context

Issues: #175, #180, #170

Replace the separate wiki and memory systems with a unified "vault" — a shared folder of markdown files with `[[wiki-links]]`, used by both the user and the agent. The vault is the user's existing Obsidian vault (synced via Syncthing), with the agent managing its own subfolder within it.

## Architecture

### Vault path and structure

The vault root is configurable (default: `workspace/vault/`). In practice, it points at the user's synced Obsidian vault (e.g., `workspace/obsidian/main/`). The agent's folder is also configurable (default: `workspace/vault/agent/`).

Config options in `config.json`:
- `vault_path` — root of the vault (default: `workspace/vault/`)
- `agent_folder` — agent's subfolder within the vault (default: `workspace/vault/agent/`)

Hardcoded conventions within the agent folder:
- `{agent_folder}/pages/` — agent's curated wiki pages
- `{agent_folder}/journal/` — agent's daily journal entries (replaces memories)

Example layout:
```
workspace/obsidian/main/          # vault root (user's Obsidian vault)
  agent/                          # agent's home folder
    journal/                      # daily entries (replaces memories/)
      2026/
        2026-03-31.md
    pages/                        # curated wiki pages (replaces wiki/)
      People/
      Topics/
      Projects/
  journal/                        # user's personal journal
  projects/                       # user's existing folders
  ...                             # rest of user's Obsidian vault
```

### Ownership model

- **Agent reads everything** in the vault — user pages, user journal, agent pages, all of it.
- **Agent writes to `agent/` by default** — journal entries and curated pages.
- **Agent can write elsewhere when explicitly asked** by the user.
- **Autonomous processes (dream, garden) are scoped to `agent/` only.** They do not read from or write to user pages. Agent-assisted curation of user content is a separate future feature.

## Tools

### New vault tools

| Tool | Purpose |
|------|---------|
| `vault_read` | Read any page in the vault by name/path |
| `vault_write` | Create or overwrite a page (full content) |
| `vault_journal_append` | Append a timestamped entry to today's agent journal page (`agent/journal/YYYY/YYYY-MM-DD.md`) |
| `vault_search` | Unified semantic + substring search across the entire vault |
| `vault_list` | List pages/folders, with folder tree support |
| `vault_backlinks` | Find all pages linking to a given page via `[[wiki-links]]` |

### Removed tools

| Old tool | Replaced by |
|----------|-------------|
| `memory_save` | `vault_journal_append` |
| `memory_search` | `vault_search` |
| `memory_recent` | `vault_search` with recency filter |
| `wiki_read` | `vault_read` |
| `wiki_write` | `vault_write` |
| `wiki_search` | `vault_search` |
| `wiki_list` | `vault_list` |
| `wiki_backlinks` | `vault_backlinks` |

### `vault_write` ownership convention

Writing outside `agent/` is prompt-guided, not enforced by the tool. The tool description instructs the agent to default to `agent/pages/` and only write elsewhere when explicitly asked by the user. No hard block — this is a soft convention, consistent with the "soft ownership" model. The tool logs a notice when writing outside the agent folder for visibility.

### `vault_search` parameters

`vault_search` accepts:
- `query` (required) — search text (semantic + substring fallback)
- `source_type` (optional) — filter to `journal`, `page`, `user`, or `conversation`
- `days` (optional) — limit to entries from the last N days (replaces `memory_recent` use case)
- `folder` (optional) — limit search to a vault subfolder path

When `days` is specified without `source_type`, it applies across all vault types. This covers the `memory_recent` use case: `vault_search(query="", days=3, source_type="journal")`.

### Workspace file tools

Workspace file tools (`workspace_read`, `workspace_write`, etc.) remain for non-vault files — scripts, temp files, working state. The vault tools carry semantic meaning (knowledge management) and are preferred for vault content.

### `vault_journal_append` behavior

Appends to `{agent_folder}/journal/YYYY/YYYY-MM-DD.md`. Entry format preserves current memory format for search self-containedness:

```markdown
## YYYY-MM-DD HH:MM

- **channel:** name (id)
- **thread:** id
- **tags:** tag1, tag2

Content here with [[wiki-links]] to topic pages.
```

Full date in the header (not just time) so matched entries carry their own context without needing the filename. Tags and channel/thread metadata preserved for search filtering and provenance.

## Embeddings and search

### Source types

| Source type | Content | Indexing granularity |
|-------------|---------|---------------------|
| `journal` | Agent journal entries (`agent/journal/`) | Split on `##` headers |
| `page` | Agent curated pages (`agent/pages/`) | Full page |
| `user` | User's Obsidian pages (everything outside `agent/`) | Full page |
| ~~`conversation`~~ | Removed — conversations searched via substring grep, not embeddings | N/A |

### Search weighting

Curated pages get higher weight than raw journal entries. Approximate priority:
1. Agent pages (`page`) — distilled, high signal
2. User pages (`user`) — human-curated, high signal
3. Journal entries (`journal`) — raw observations, noisy but recent
4. Conversation entries (`conversation`) — contextual

Exact weights tuned via existing boost mechanism in `embeddings.py`.

### Incremental indexing

The current "delete all, re-embed" strategy doesn't scale to 3000+ pages. New approach:
- Track file content hash in the embeddings DB
- On index update: scan vault files, compare hashes, only embed new/modified files
- Delete embeddings for files that no longer exist
- Full reindex (`make reindex`) remains available for model changes, dimension changes, or major restructures

### Proactive context injection

`memory_context.py` updated to search the full vault via unified `vault_search`. Source type weighting applies. No structural changes to the injection pipeline — it already queries embeddings and formats results.

## Wiki link resolution

`[[PageName]]` resolution across the full vault:
- **Closest match first** — prefer pages in the same folder subtree as the linking page
- **Fallback to any match** — search all folders if no local match
- **Explicit paths supported** — `[[agent/pages/Some Topic]]` for unambiguous references
- **Agent tools warn on ambiguous links** — when writing a page with a `[[link]]` that matches multiple pages

## Dream and garden skills

Both scoped to `agent/` directory only:
- **Dream**: reads `agent/journal/`, writes to `agent/pages/`. Same distillation logic, updated paths.
- **Garden**: maintains `agent/pages/` structure (cross-linking, cleanup, merges). Updated paths.
- Neither reads from nor writes to user pages during autonomous operation.

## Web UI changes

### In scope
- Rename "Wiki" tab → "Vault" tab
- **Folder tree navigation** — collapsible folder nodes showing full vault structure (#170)
- **Breadcrumb navigation** — clickable path showing current page's location
- Update REST API endpoints: `/api/wiki/*` → `/api/vault/*` (list, read, write)
- List endpoint returns nested folder structure (or flat paths for client-side tree building)
- Create page in specific folder (path parameter on write endpoint)
- `[[wiki-link]]` navigation works across full vault (updated resolution in both backend and frontend)
- Update `@[[page]]` WebSocket context sharing to use vault paths

### Deferred
- Backlinks panel
- Vault search UI
- Recently edited pages list
- Frontmatter/tag-based filtering
- Graph view

## Migration

Big-bang migration via a post-deploy script:

1. Move `workspace/wiki/**` → `{vault_path}/agent/pages/`
2. Move `workspace/memories/**` → `{vault_path}/agent/journal/`
3. Update `[[wiki-links]]` in moved pages (if paths changed)
4. Rebuild embeddings index with new paths and source types
5. Clean up old directories

The script is idempotent — safe to re-run if interrupted.

## Config changes

New fields in config (with defaults):
```json
{
  "vault_path": "workspace/vault/",
  "agent_folder": "agent/"
}
```

These replace the implicit `workspace/wiki/` and `workspace/memories/` paths. Existing wiki/memory config fields removed.

## Prompt changes

- Wiki skill `SKILL.md` updated: references vault conventions, `agent/pages/` as default write location, folder organization guidance
- `AGENT.md` updated: vault as primary knowledge interface, agent folder conventions
- Dream/garden skill prompts updated for new paths

## Docs updates

- New `docs/vault.md` covering the unified vault concept
- Update `docs/index.md`
- Update `CLAUDE.md` key files, conventions, known gaps
- Update `README.md` tool table, config table

## Deferred (file as follow-up issues)

- Backlinks panel in web UI
- Vault search UI
- Agent-assisted curation of user pages (separate permissions model)
- Graph view
- Agent prompts (SOUL.md, USER.md) in the vault
- Page templates
- Recently edited pages list
- Frontmatter/tag-based filtering
- Move/rename pages in UI
- Chunking strategy for large pages (embedding quality degrades on very long documents)
