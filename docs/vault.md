# Vault — Unified Knowledge Base

DecafClaw uses a unified vault — a shared Obsidian-compatible folder of markdown files with `[[wiki-links]]`. The vault replaces the previous separate wiki and memory systems.

The vault is shared between the agent and the user. The agent manages its own subfolder (`agent/`) while reading from the entire vault.

## Storage

The vault root is configurable (default: `workspace/vault/`). It can point at an existing Obsidian vault (e.g., synced via Syncthing).

Agent files live under `{vault_root}/agent/`:
- `agent/pages/` — curated wiki pages (living documents revised over time)
- `agent/journal/` — daily journal entries (timestamped observations, append-only)

Config options in `config.json`:
```json
{
  "vault": {
    "vault_path": "workspace/vault/",
    "agent_folder": "agent/"
  }
}
```

`agent_folder` is resolved relative to `vault_path`.

## Configuring the vault root

The default vault root is `workspace/vault/`, with agent content at `workspace/vault/agent/`. To point elsewhere — commonly the user's Obsidian vault — set `vault_path` in `data/<agent-id>/config.json`:

```json
{
  "vault": {
    "vault_path": "/absolute/path/to/obsidian-vault"
  }
}
```

When the vault root is an Obsidian vault, agent content lives at `<obsidian>/agent/` alongside the user's own folders. The agent can read everything in the vault; write tools (`vault_write`, `vault_move_lines`, `vault_section`) are gated to `agent/`.

To move the vault root to a new location, use `scripts/migrate_vault_root.py` to move `<old>/agent/` into the new root and patch `config.json`. Run `make reindex` after to rebuild the embedding index.

## Folders

The vault supports hierarchical folders. The API and web UI provide folder-aware browsing:

- **Sidebar navigation** — file-browser style with breadcrumbs. Click folders to navigate in, breadcrumbs to navigate up.
- **Editor breadcrumbs** — clickable folder path above each page.
- **Rename/move** — rename a page to change its folder path (e.g. `agent/pages/Foo` → `agent/pages/projects/Foo`). Parent directories are auto-created; empty directories are cleaned up.
- **New pages** — created in the currently browsed folder.

### API

`GET /api/vault?folder=agent/pages` returns `{folder, folders, pages}` — immediate subfolders and pages in that folder.

`PUT /api/vault/{page}` with `{"rename_to": "new/path"}` renames/moves a page. Returns 409 if target exists.

## Wiki Links

Standard Obsidian `[[wiki-links]]` connect pages:

```markdown
Works on [[DecafClaw]] and maintains a [[Blog]].
```

Pipe syntax for display text: `[[Tempest (arcade game)|Tempest arcade game]]`

Link resolution: closest match in the same folder subtree first, then any match across the vault. Explicit paths work too: `[[agent/pages/DecafClaw]]`.

## Tools

The vault skill is **always loaded** — its tools are available in every conversation.

| Tool | Description |
|------|-------------|
| `vault_read(page)` | Read a page by name or path. Searches subdirectories. |
| `vault_write(page, content)` | Create or overwrite a page. Indexes in semantic search. |
| `vault_journal_append(tags, content)` | Append timestamped entry to today's journal file. |
| `vault_search(query, source_type?, days?, folder?)` | Semantic + substring search across the vault. |
| `vault_list(folder?, pattern?)` | List pages with last-modified dates. |
| `vault_backlinks(page)` | Find pages linking to this page via `[[wiki-links]]`. |
| `vault_show_sections(page, section?)` | Show a page's section outline or a specific section's content with absolute line numbers. |
| `vault_move_lines(from_page, to_page, lines, to_section?, position?)` | Move specific lines (by line number) from one agent page to another. Both pages must be under `agent/`. |
| `vault_section(page, action, section?, title?, level?, after?, before?, parent?)` | Section ops: `add`, `remove`, `rename`, or `move`. Page must be under `agent/`. |

### Ownership

- Agent writes to `agent/` by default
- Agent reads everything in the vault
- Agent writes outside `agent/` only when explicitly asked
- `vault_write` logs a notice when writing outside the agent folder

### Path Safety

All vault tools validate that paths stay within the vault root. Path traversal attempts are rejected.

## Vault Gardening

The agent follows these principles (encoded in the vault skill's system prompt):

- **Search before create** — always search for existing pages before making new ones
- **Revise and rewrite** — restructure pages as understanding evolves, don't just append
- **Link liberally** — `[[Page Name]]` connects knowledge
- **Include sources** — `## Sources` section at the bottom of each page
- **Entity pages** — dedicated pages for people, projects, recurring topics
- **Merge related content** — consolidate scattered info into one page
- **Split when large** — break big pages into sub-pages with a summary parent
- **Update over duplicate** — edit existing pages rather than creating new ones

## Journal vs Pages

- **Journal entries** (`vault_journal_append`) are timestamped observations — append-only daily files
- **Pages** (`vault_write`) are curated knowledge — revised and restructured over time
- The [dream](dream-consolidation.md) process periodically reviews journal entries and distills insights into pages

## Chat Context Integration

Users can share vault pages directly into conversation context:

- **Open page in UI**: When a page is open in the web UI side panel, its content is automatically injected as context.
- **@[[PageName]] mentions**: Reference pages in message text using `@[[PageName]]` or `@[[folder/PageName]]` syntax. Works across all channels.

Each page is injected **once per conversation**. If a referenced page doesn't exist, the agent sees an error note.

## Semantic Search

Vault content is indexed in the embeddings database with per-type source types and boost weights:

| Source type | Content | Boost |
|-------------|---------|-------|
| `page` | Agent curated pages | 1.3x |
| `user` | User's Obsidian pages | 1.2x |
| `journal` | Agent journal entries | 1.0x |

- Pages are indexed incrementally when written via `vault_write` or `vault_journal_append`
- `make reindex` rebuilds the full index (`--vault`, `--journal` flags for subsets)
- `--concurrency N` controls parallel embedding API calls (default 4)
- Reindex includes 429 retry with exponential backoff

## Migration

For existing installations with `workspace/wiki/` and `workspace/memories/`:

```bash
make migrate-vault      # move files to vault structure
make reindex            # rebuild embeddings index
```

The migration script moves `workspace/wiki/**` → `agent/pages/` and `workspace/memories/**` → `agent/journal/`.
