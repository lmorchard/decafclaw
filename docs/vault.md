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

## Page Frontmatter

Vault pages may begin with a YAML frontmatter block. Frontmatter is optional and additive — the parser in `frontmatter.py` preserves unknown keys, and pages without frontmatter work the same as those with it. The schema is informal today; we may tighten it as patterns become clearer.

Fields the system recognizes today (parsed by `frontmatter.py` via `get_frontmatter_field`):

| Field | Type | Used for |
|-------|------|----------|
| `summary` | string | Prepended to body for semantic-search embeddings (via `build_composite_text`); surfaced in UI |
| `keywords` | list of strings | Prepended to body for embeddings (via `build_composite_text`) |
| `tags` | list of strings | Prepended to body for embeddings (via `build_composite_text`); loose categorization |
| `importance` | float in [0, 1] | Composite scoring weight in memory retrieval (not used by `build_composite_text`) |

Skill-authored conventions (preserved by the parser but not interpreted by core code):

| Field | Where | Shape |
|-------|-------|-------|
| `sources` | `linkding-ingest`, `mastodon-ingest` outputs | YAML list of `{url, date, added_by}` objects |

The `sources:` list records each source that contributed to a page, with the originating URL, date (`YYYY-MM-DD`), and skill name. Each ingest pass appends an entry rather than overwriting earlier ones, so the list accumulates page provenance over time. The body `## Sources` section mirrors it for human readability. Planned use: revalidation tooling (an addition to `garden` or its own scheduled task) that refetches each URL, compares to current page content, and flags staleness.

New skills producing structured page metadata are welcome to add their own conventions; document them here when they stabilize.

## Tools

The vault skill is **always loaded** — its tools are available in every conversation.

| Tool | Description |
|------|-------------|
| `vault_read(page)` | Read a page by name or path. Searches subdirectories. |
| `vault_write(page, content)` | Create or overwrite a page. Indexes in semantic search. |
| `vault_delete(page)` | Delete a page. Pages outside `agent/` trigger a user confirmation. |
| `vault_rename(from_page, to_page)` | Rename/move a page (preserves links). Pages outside `agent/` trigger a user confirmation. |
| `vault_grant_folder(folder, reason)` | Request per-conversation trust for a folder. After approval, vault_write/delete/rename under the folder skip confirmation. |
| `vault_journal_append(tags, content)` | Append timestamped entry to today's journal file. |
| `vault_search(query, source_type?, days?, folder?)` | Semantic + substring search across the vault. |
| `vault_list(folder?, pattern?)` | List pages with last-modified dates. |
| `vault_backlinks(page)` | Find pages linking to this page via `[[wiki-links]]`. |
| `vault_show_sections(page, section?)` | Show a page's section outline or a specific section's content with absolute line numbers. |
| `vault_move_lines(from_page, to_page, lines, to_section?, position?)` | Move specific lines (by line number) from one agent page to another. Both pages must be under `agent/`. |
| `vault_section(page, action, section?, title?, level?, after?, before?, parent?)` | Section ops: `add`, `remove`, `rename`, or `move`. Page must be under `agent/`. |
| `vault_update_frontmatter(page, fields, overwrite?)` | Merge frontmatter fields (`summary`, `importance`, `tags`, `keywords`, etc.) into a page's existing metadata without touching the body. Fills absent/empty fields by default; `overwrite=true` replaces existing values. Reindexes the page. Shared write primitive for the self-improving vault arc (#197) — [dream](dream-consolidation.md) calls it (`overwrite=false`) after every `vault_write` in its Consolidate phase; the `backfill-frontmatter` CLI and garden importance tuning build on the same primitive. Interactive callers writing outside `agent/` go through the same confirmation gate as `vault_write`; non-interactive callers (scheduled dream/garden) proceed without confirmation by design — the one mutating vault tool that doesn't error in non-interactive contexts. |

### Frontmatter merge (#197)

`vault_update_frontmatter` is a thin async wrapper around a pure helper,
`merge_frontmatter(existing: dict, fields: dict, overwrite: bool) -> dict` in
`skills/vault/tools.py`. The pure function does the field coercion (reusing
`get_frontmatter_field`'s rules so the merge and the parser agree on shape)
and the fill-vs-replace merge logic, with no ctx or I/O — other callers
(dream generation, the backfill CLI, garden importance tuning) import and
call it directly to compute merged frontmatter without going through the
tool or a running agent context.

### Backfill CLI (#197)

`make backfill-frontmatter` (`decafclaw-backfill-frontmatter`,
`src/decafclaw/backfill_frontmatter.py`) is a one-time CLI for vault pages
written before frontmatter generation existed. It walks all non-journal
vault pages, and for each one missing `summary`/`keywords`/`tags`/
`importance` makes a single forced-tool structured-output LLM call
(`generate_fields_for_page`) to generate the missing fields, then merges
them in via `merge_frontmatter(overwrite=False)` — a manually-set field is
never clobbered. Pages where all four fields are already present are
skipped for free, so the CLI is resumable and safe to re-run. `--dry-run`
prints planned changes without writing; it still makes a real LLM call per
page and costs the same tokens as a normal run — only the file write is
skipped. `--limit N` caps how many pages get an LLM call in one run (skips
don't count against it), which is the way to bound spend on `--dry-run`
too. It does not reindex itself — follow with `make reindex` so composite
embeddings pick up the new frontmatter.

### Backlink index (#197)

`vault_backlinks` no longer brute-force `rglob`s and regex-scans every page
on each call. `src/decafclaw/backlinks.py` maintains a persistent JSON
index at `{workspace}/backlinks.json` mapping `page -> [pages linking to
it]` (sorted, human-readable, crash-recoverable via tmp-file-then-rename).

- `rebuild_index(config)` — full scan: resolves every `[[link]]` (or
  `[[link|display]]`) in every page to an existing page, case-insensitively
  by full relative path or falling back to bare filename (stem). Dangling
  links (no matching page) and self-links are dropped — the index only
  tracks edges between real pages.
- `load_index(config)` — reads the persisted JSON, rebuilding lazily if
  missing or corrupt.
- `inbound_count(config, page)` — number of distinct pages linking to
  `page`. This is the raw signal Phase 5's importance formula folds in.
- `update_for_page(config, page)` — incremental update: re-scans only the
  changed page's current outbound links and adjusts the index's inbound
  entries (add new targets, drop stale ones) without re-reading every
  other page's content.

A `vault_changed`-event subscriber (`make_backlinks_subscriber`, wired in
`runner.py`) keeps the index current without an explicit rebuild step: for
create/update (and other same-identity events) it calls `update_for_page`
for the changed page only; for delete/rename — which change a page's own
identity in the index and can't be corrected incrementally (a rename event
only carries the new path) — it runs a full `rebuild_index` instead.
Delete/rename are rare relative to writes, so the full-scan cost is a
non-issue. Fail-open throughout — I/O or parse errors are logged at debug
level and never propagate into a tool call or event subscriber.

`vault_backlinks(page)` itself now just resolves `page`, looks up its
inbound linkers in the index, and re-reads only those specific linking
pages (not the whole vault) to pull a one-line quote for display context.

### Importance recompute (#197)

`importance` frontmatter starts as an LLM's subjective guess (backfill,
dream generation). `vault_recompute_importance` — a native tool from the
`garden` skill's `tools.py` (`src/decafclaw/skills/garden/tools.py`) —
replaces that guess weekly with a deterministic score, so importance
tracks measured usage instead of drifting further from it with every LLM
re-guess.

`compute_importance_scores(config)` in `skills/garden/tools.py` is pure
and config-driven (no `ctx`, no writes):

```
importance = clamp01(
    w_retrieval * norm(retrieval_freq)
    + w_inbound * norm(inbound_links)
)
```

`norm(x) = x / max(x across all vault pages)`, defined as 0 when that max
is 0 (no divide-by-zero on an empty or brand-new vault). `retrieval_freq`
comes from `retrieval_telemetry.aggregate`; `inbound_links` from
`backlinks.inbound_count`. `ImportanceConfig` also carries a `w_reference`
weight (default 0.0) that is **reserved / not yet computed** — no
explicit-reference signal exists yet, so it's omitted from the formula
above entirely rather than multiplied against an all-zero signal. Weights
resolve `w_retrieval=0.6`, `w_inbound=0.4`, `w_reference=0.0` via the same
dataclass-default → `config.json` → `IMPORTANCE_*` env resolution as every
other sub-config.

`tool_vault_recompute_importance(ctx, dry_run=False)` scores every
non-journal vault page, writes changed scores via
`vault_update_frontmatter(overwrite=True)`, and skips pages whose rounded
score is unchanged so a re-run only touches what actually moved.
`dry_run=true` reports the planned deltas without writing. It's the
weekly step in the `garden` skill's sweep (`skills/garden/SKILL.md`) —
review the reported deltas for outliers, and use `vault_backlinks` + the
recomputed score together to flag orphaned, rarely-retrieved pages as
split/merge/delete candidates.

### Ownership

- Agent writes to `agent/` by default
- Agent reads everything in the vault
- Agent writes outside `agent/` only when explicitly asked
- `vault_write` logs a notice when writing outside the agent folder

### Path Safety

All vault tools validate that paths stay within the vault root. Path traversal attempts are rejected.

## Writing to user pages

Writes/deletes/renames under the agent folder (`agent/`) execute directly. Operations on pages outside `agent/` go through a three-tier gate:

1. **Static allowlist.** Folders listed in `vault.user_writable_paths` are pre-approved. Path matching is prefix-based on vault-relative paths (no globs). Example: `["creative/", "notes/"]`.
2. **Per-conversation grants.** The agent can call `vault_grant_folder(folder, reason)` to request trust for a folder. After user approval, all writes/deletes/renames under that folder skip confirmation for the rest of the conversation. Grants persist as a sidecar at `{workspace}/conversations/{conv_id}/vault_grants.json` and reset between conversations.
3. **Per-call confirmation.** Anything else triggers a confirmation request showing the operation and a content preview. Approve to proceed; deny returns an error and no change is made.

Heartbeat / scheduled / child-agent contexts can't display confirmations, so writes outside the agent folder fail with an error in those contexts — with one exception: `vault_update_frontmatter` skips this gate entirely for non-interactive callers (scheduled `dream`/`garden`), proceeding ungated by design so weekly maintenance can touch frontmatter vault-wide without a human present. Interactive callers of `vault_update_frontmatter` are still gated exactly like `vault_write`.

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
- **Vault guide (`AGENTS.md`)**: A guide file at the vault root (default `AGENTS.md`) is auto-injected into every interactive turn as an always-loaded `system` section — before any tool decisions. Use it for vault-layout and protocol rules the agent must always follow. See [Vault guide](context-composer.md#vault-guide) in the context-composer docs for configuration and skip conditions.

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

## Retrieval telemetry (#197)

A fail-open EventBus subscriber records which retrieval candidates were
considered each interactive turn and which survived to injection —
measurement foundation for the self-improving vault arc (importance
formula, dream/garden tuning, and so on down the line). See
[Retrieval telemetry](context-composer.md#retrieval-telemetry-197) in the
context-composer docs for the event shape and `make retrieval-report`.

## Migration

For existing installations with `workspace/wiki/` and `workspace/memories/`:

```bash
make migrate-vault      # move files to vault structure
make reindex            # rebuild embeddings index
```

The migration script moves `workspace/wiki/**` → `agent/pages/` and `workspace/memories/**` → `agent/journal/`.
