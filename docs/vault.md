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
- **@[[PageName]] mentions**: Reference pages in message text using `@[[PageName]]` syntax. Works across all channels.

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
