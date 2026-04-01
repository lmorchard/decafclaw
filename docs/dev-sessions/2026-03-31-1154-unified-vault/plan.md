# Unified Vault — Implementation Plan

## Overview

This plan breaks the vault unification into 8 phases, each ending with a working (if incomplete) system. Phases build on each other — no orphaned code. Each phase has a commit checkpoint.

Key files touched across all phases:
- `src/decafclaw/config.py` / `config_types.py` — vault config
- `src/decafclaw/skills/wiki/` → new `src/decafclaw/skills/vault/` skill
- `src/decafclaw/tools/memory_tools.py` → removed
- `src/decafclaw/memory.py` → removed
- `src/decafclaw/memory_context.py` → updated
- `src/decafclaw/embeddings.py` — new source types, incremental indexing
- `src/decafclaw/http_server.py` — vault API routes
- `src/decafclaw/agent.py` — vault context injection
- `src/decafclaw/web/static/` — vault UI components
- `src/decafclaw/skills/dream/` / `garden/` — updated paths
- Migration script

---

## Phase 1: Vault config and directory helpers

**Goal:** Add vault config fields, create vault path helpers, wire into config loading. No behavioral changes yet — just the foundation.

### Prompt 1.1: Add VaultConfig dataclass and config loading

Read `src/decafclaw/config_types.py` and `src/decafclaw/config.py`.

Add a `VaultConfig` dataclass to `config_types.py` with:
- `vault_path: str = "workspace/vault/"` — root of the vault
- `agent_folder: str = "workspace/vault/agent/"` — agent's subfolder

Add a `vault: VaultConfig` field to the `Config` dataclass. Load it in `load_config()` using the existing `load_sub_config` pattern (env vars + config.json `vault` section). The paths should be resolved relative to `data_home` the same way `workspace_path` is — i.e., if they're relative, they resolve from `data_home`.

Add helper properties on `VaultConfig` or as standalone functions in a new `src/decafclaw/vault.py` module:
- `vault_root(config) -> Path` — resolved vault path
- `agent_dir(config) -> Path` — resolved agent folder
- `agent_pages_dir(config) -> Path` — `agent_dir / "pages"`
- `agent_journal_dir(config) -> Path` — `agent_dir / "journal"`

Ensure the directories are created on startup if they don't exist (mkdir -p style), similar to how `workspace_path` is handled.

Run `make check` to verify no type errors. Commit.

---

## Phase 2: Vault tools (replace wiki + memory tools)

**Goal:** Create the new vault tool module with all 6 tools. Wire them as an always-loaded skill replacing the wiki skill. Remove old memory tools.

### Prompt 2.1: Create vault skill with read/write/list tools

Create `src/decafclaw/skills/vault/` directory with `__init__.py`, `SKILL.md`, and `tools.py`.

Write `SKILL.md` based on the existing wiki `SKILL.md` but updated for vault conventions:
- Frontmatter: `name: vault`, `always-loaded: true`
- Describe the vault concept: shared knowledge base, agent folder conventions
- Gardening rules adapted from wiki skill (search before create, revise, link, etc.)
- Ownership guidance: agent writes to `agent/pages/` by default, reads everywhere, writes elsewhere only when asked

Write `tools.py` with these tools, using the new vault path helpers from Phase 1:

**`vault_read(ctx, page: str)`** — Read any page in the vault by name or path. Resolution logic:
1. Try exact path match (relative to vault root)
2. Search all subdirectories for stem match
3. For ambiguous matches, prefer closest match to the page doing the lookup (or first alphabetical if no context)
4. Return page content or error

**`vault_write(ctx, page: str, content: str)`** — Create or overwrite a page. Default path is under `agent/pages/` when no folder specified. Log a notice if writing outside agent folder. Auto-index in embeddings after write (carry over from wiki_write). Use `_safe_write_path` pattern — validate path stays within vault root.

**`vault_list(ctx, folder: str = "")`** — List pages under a folder (or entire vault). Return paths with last-modified dates. Support nested folder display.

Register as `TOOLS` dict and `TOOL_DEFINITIONS` list in the standard skill pattern.

Run `make check`. Commit.

### Prompt 2.2: Add vault_journal_append tool

In `src/decafclaw/skills/vault/tools.py`, add:

**`vault_journal_append(ctx, tags: list[str], content: str)`** — Append a timestamped entry to today's journal file at `{agent_dir}/journal/YYYY/YYYY-MM-DD.md`. Format:

```markdown
## YYYY-MM-DD HH:MM

- **channel:** {channel_name} ({channel_id})
- **thread:** {thread_id}
- **tags:** tag1, tag2

{content}
```

Pull channel/thread info from `ctx` the same way `memory_save` does. Create parent directories if needed. Auto-index the new entry in embeddings with `source_type="journal"`.

Add to `TOOLS` dict and `TOOL_DEFINITIONS`.

Run `make check`. Commit.

### Prompt 2.3: Add vault_search and vault_backlinks tools

In `src/decafclaw/skills/vault/tools.py`, add:

**`vault_search(ctx, query: str, source_type: str = "", days: int = 0, folder: str = "")`** — Unified search:
- If semantic search is configured: embed query, search via `embeddings.search_similar()` with optional `source_type` filter
- Apply `days` filter: only return results from files modified in the last N days
- Apply `folder` filter: only return results from files under that vault subfolder
- Fallback to substring search if semantic returns nothing (carry over from memory_search)
- When `query` is empty and `days` is set, return recent entries (replacing `memory_recent`)

**`vault_backlinks(ctx, page: str)`** — Find all vault pages that contain `[[page]]` links. Scan the entire vault (not just agent folder). Adapted from wiki_backlinks but searches vault root.

Add to `TOOLS` dict and `TOOL_DEFINITIONS`.

Run `make check`. Commit.

### Prompt 2.4: Wire vault skill, remove wiki skill and memory tools

Read `src/decafclaw/skills/wiki/__init__.py` and `src/decafclaw/tools/memory_tools.py` to understand registration.

1. Create `src/decafclaw/skills/vault/__init__.py` mirroring wiki's `__init__.py`
2. Update the skill scan/discovery to find the vault skill (it should auto-discover from `src/decafclaw/skills/vault/SKILL.md`)
3. Remove `src/decafclaw/skills/wiki/` directory entirely
4. Remove `src/decafclaw/tools/memory_tools.py`
5. Remove `memory_tools` from the tool registry in `src/decafclaw/tools/__init__.py` (or wherever `MEMORY_TOOLS` is imported)
6. Remove `src/decafclaw/memory.py` module
7. Update any imports that reference the old modules — grep for `memory_tools`, `wiki/tools`, `from decafclaw.memory import`, `from decafclaw.skills.wiki`

Run `make check` and `make test` to find and fix all broken references. Commit.

---

## Phase 3: Embeddings updates

**Goal:** Update source types, add incremental indexing, add vault-wide reindexing.

### Prompt 3.1: Update source types and search weighting

Read `src/decafclaw/embeddings.py`.

Changes:
1. Replace `WIKI_BOOST = 1.2` with a `SOURCE_BOOSTS` dict:
   - `"page": 1.3` (agent curated pages — highest signal)
   - `"user": 1.2` (user Obsidian pages — high signal)
   - `"journal": 1.0` (raw observations — baseline)
   - `"conversation": 0.9` (contextual — slightly lower)
2. Update `search_similar_sync()` to apply per-source-type boosts from the dict
3. Update `search_similar()` — the auto-reindex on empty should call vault reindex instead of memory reindex

Run `make check` and `make test`. Commit.

### Prompt 3.2: Add incremental indexing

Read `src/decafclaw/embeddings.py`, focusing on the schema and reindex functions.

Add a `file_hash` column to `memory_embeddings` table (add to schema init, handle migration for existing DBs that lack it).

Add `reindex_vault_incremental(config)` function:
1. Scan all `.md` files under `vault_root(config)`
2. For each file, compute SHA256 of its content
3. Check if the file_path + hash already exists in the DB — skip if so
4. Determine source_type from path:
   - Under `agent_journal_dir(config)` → `"journal"` (split on `##` headers, index each entry)
   - Under `agent_pages_dir(config)` → `"page"` (index full page)
   - Everything else → `"user"` (index full page)
5. Delete old embeddings for files whose hash changed, then re-embed
6. Delete embeddings for files that no longer exist on disk
7. Log stats: skipped, added, updated, deleted

Replace `reindex_all()` and `reindex_wiki()` with:
- `reindex_vault(config, full: bool = False)` — if `full`, delete all vault entries and re-embed everything; otherwise incremental
- Keep `reindex_conversations()` as-is (conversations aren't in the vault)

Update `reindex_cli()` to use the new functions. Update `make reindex` if needed.

Run `make check` and `make test`. Commit.

---

## Phase 4: Update memory context injection

**Goal:** Update proactive context to use vault search with new source types.

### Prompt 4.1: Update memory_context.py for vault

Read `src/decafclaw/memory_context.py`.

Changes:
1. Update `SOURCE_LABELS` dict:
   - `"page"` → `"📄 Agent page"`
   - `"user"` → `"📝 User page"`
   - `"journal"` → `"📓 Journal"`
   - `"conversation"` → `"💬 Conversation"`
2. `retrieve_memory_context()` — the function queries `embeddings.search_similar()` which already searches across all source types. The main change is that old `source_type="memory"` results won't exist anymore; they'll be `"journal"`. Update any filtering logic that checks for `"memory"` or `"wiki"`.
3. Remove the conversation exclusion filter if it checks `source_type == "conversation"` — keep the exclusion but update the condition if needed.
4. Verify `format_memory_context()` works with the new source type labels.

Run `make check` and `make test`. Commit.

---

## Phase 5: Update agent.py for vault context

**Goal:** Update `@[[page]]` context injection and wiki_page handling to use vault paths and resolution.

### Prompt 5.1: Update agent.py wiki context to vault context

Read `src/decafclaw/agent.py`, focusing on `_parse_wiki_references()`, `_read_wiki_page()`, `_get_already_injected_pages()`, and `_prepare_messages()`.

Changes:
1. `_parse_wiki_references()` — update to use vault's `resolve_page()` instead of wiki's. The `@[[PageName]]` syntax stays the same.
2. `_read_wiki_page()` — use vault read logic (resolve from vault root, not wiki dir). Import from the new vault tools module.
3. Variable naming: rename `ctx.wiki_page` → `ctx.vault_page` (or keep as-is if too many touchpoints — search for all references first and decide).
4. `_prepare_messages()` — update the `wiki_context` role to `vault_context` in the message injection. Update `ROLE_REMAP` dict.
5. Update event name from `"wiki_context"` to `"vault_context"` if published.

Search for all references to `wiki_page`, `wiki_context`, `_wiki` in agent.py and update consistently.

Run `make check` and `make test`. Commit.

---

## Phase 6: Web UI and API updates

**Goal:** Rename wiki → vault in the API layer and frontend. Add folder tree navigation.

### Prompt 6.1: Update HTTP server API endpoints

Read `src/decafclaw/http_server.py`, focusing on the wiki endpoints and routes.

Changes:
1. Rename endpoint functions: `wiki_list` → `vault_list`, `wiki_read` → `vault_read`, `wiki_write` → `vault_write`, `wiki_create` → `vault_create`
2. Update routes: `/api/wiki` → `/api/vault`, `/api/wiki/{page}` → `/api/vault/{page}`, `/wiki/{page:path}` → `/vault/{page:path}`
3. Update `_resolve_wiki_page()` → `_resolve_vault_page()` — use vault path helpers and resolution logic
4. `vault_list` — update to return folder structure. Return flat list of paths with metadata (client builds the tree). Include a `folder` query param to list a specific subfolder.
5. `vault_read` — resolve page from vault root using closest-match resolution
6. `vault_write` / `vault_create` — write to vault root, auto-index with correct source_type based on path
7. Update `serve_wiki_page()` → `serve_vault_page()`, serve the same HTML shell but at `/vault/` URL
8. Update the `_authenticated` routes array

Also update `src/decafclaw/web/__init__.py` if it has wiki references.

Search the entire `src/decafclaw/web/` directory for "wiki" references and update.

Run `make check`. Commit.

### Prompt 6.2: Update WebSocket handler for vault context

Read `src/decafclaw/web/` for WebSocket handling — look for where `wiki_page` is sent from the client and received on the server.

Update the WebSocket message handling:
1. Client sends `vault_page` (or `wiki_page` — check what the current protocol uses) when user has a page open
2. Server sets `ctx.vault_page` (updated from Phase 5)
3. Update any message type names if they reference "wiki"

Run `make check`. Commit.

### Prompt 6.3: Update frontend — rename wiki to vault, basic wiring

Read the frontend JS files:
- `src/decafclaw/web/static/components/wiki-page.js`
- `src/decafclaw/web/static/components/wiki-editor.js`
- `src/decafclaw/web/static/lib/milkdown-wiki-link.js`

Changes:
1. Rename `wiki-page.js` → `vault-page.js`, update component name/tag
2. Rename `wiki-editor.js` → `vault-editor.js`, update component name/tag
3. Update all API calls from `/api/wiki/` to `/api/vault/`
4. Update URL routing from `/wiki/` to `/vault/`
5. Update `milkdown-wiki-link.js` — the `[[link]]` syntax stays the same, but navigation URLs change from `/wiki/PageName` to `/vault/PageName`
6. Update the sidebar tab label from "Wiki" to "Vault"
7. Update any parent components that import/reference the wiki components
8. Grep all JS files for "wiki" and update remaining references

Run `make check-js` if available. Test manually in browser. Commit.

### Prompt 6.4: Add folder tree navigation to vault sidebar

Read the current sidebar/page-list rendering in the vault page component (formerly wiki-page).

Currently the sidebar shows a flat list of pages. Replace with a collapsible folder tree:

1. **Data transformation:** The vault list API returns flat paths like `agent/pages/People/Alice.md`. Transform into a tree structure client-side:
   ```js
   { name: "agent", type: "folder", children: [
     { name: "pages", type: "folder", children: [
       { name: "People", type: "folder", children: [
         { name: "Alice", type: "page", path: "agent/pages/People/Alice" }
       ]}
     ]}
   ]}
   ```
2. **Rendering:** Each folder node is expandable/collapsible (click to toggle). Pages are clickable to navigate. Use indentation or disclosure triangles.
3. **State:** Track expanded/collapsed state per folder. Default: collapse all except the currently-viewed page's ancestor path.
4. **Breadcrumb:** Above the editor, show the current page's folder path as clickable breadcrumb segments (e.g., `vault > agent > pages > People > Alice`).
5. **Create page in folder:** When creating a new page, pre-fill the folder path if the user is currently viewing a folder or a page within a folder.

Keep styling consistent with the existing UI. Use Lit component patterns.

Run `make check-js`. Test manually. Commit.

---

## Phase 7: Update dream/garden skills and prompts

**Goal:** Update autonomous skills to use vault tools and paths.

### Prompt 7.1: Update dream skill

Read `src/decafclaw/skills/dream/SKILL.md` and `src/decafclaw/skills/dream/tools.py` (if it exists).

Changes:
1. Update SKILL.md:
   - Change `required-skills: wiki` → `required-skills: vault`
   - Update all references from `wiki_*` tools to `vault_*` tools
   - Update paths: "reads memories" → "reads `agent/journal/`", "writes to wiki" → "writes to `agent/pages/`"
   - Add explicit boundary: "Only read and write within the `agent/` folder"
2. If there's a `tools.py`, update imports and function calls

Run `make check`. Commit.

### Prompt 7.2: Update garden skill

Read `src/decafclaw/skills/garden/SKILL.md` and `src/decafclaw/skills/garden/tools.py` (if it exists).

Same pattern as dream:
1. Update SKILL.md: tool names, paths, vault references, agent-folder boundary
2. Update tools.py if present

Run `make check`. Commit.

### Prompt 7.3: Update AGENT.md and system prompts

Read `src/decafclaw/prompts/AGENT.md`.

Update:
1. Replace wiki/memory references with vault concepts
2. Describe the vault as the primary knowledge interface
3. Describe agent folder conventions (`agent/pages/`, `agent/journal/`)
4. Describe ownership model (reads everywhere, writes to agent/ by default)
5. Remove separate memory/wiki sections — unify under "Vault"

Also check `src/decafclaw/prompts/SOUL.md` for any wiki/memory references.

Run `make check`. Commit.

---

## Phase 8: Migration script and cleanup

**Goal:** Create the migration script, clean up dead code, update docs.

### Prompt 8.1: Create migration script

Create `scripts/migrate_to_vault.py` (or a management command).

The script:
1. Read config to determine vault path and agent folder
2. Move `workspace/wiki/**` → `{agent_folder}/pages/` preserving directory structure
3. Move `workspace/memories/**` → `{agent_folder}/journal/` preserving directory structure
4. Scan moved pages for `[[wiki-links]]` — links shouldn't need updating since they reference page names not paths, but verify
5. Scan for relative memory path references (e.g., `../../memories/2026/...`) in wiki pages and update to new relative paths
6. Delete old empty directories
7. Run incremental vault reindex (or flag that a full reindex is needed)
8. Print summary: files moved, links updated, errors

Make it idempotent: skip files that already exist at destination, handle partial runs gracefully.

Add `make migrate-vault` target to Makefile.

Test with a dry-run flag first. Commit.

### Prompt 8.2: Clean up dead code and imports

Search the entire codebase for remaining references to:
- `memory_save`, `memory_search`, `memory_recent`
- `wiki_read`, `wiki_write`, `wiki_search`, `wiki_list`, `wiki_backlinks`
- `from decafclaw.memory import`
- `from decafclaw.skills.wiki`
- `workspace/wiki`
- `workspace/memories`

Fix any remaining references. Remove dead imports, dead config fields, dead test fixtures.

Run `make check` and `make test`. Commit.

### Prompt 8.3: Update documentation

Update the following docs:
1. Create `docs/vault.md` — covering the unified vault concept, folder structure, tools, configuration
2. Update `docs/index.md` — add vault doc, remove separate wiki/memory entries if they exist
3. Update `CLAUDE.md`:
   - Key files list (remove memory.py, wiki skill; add vault.py, vault skill)
   - Conventions (vault replaces wiki + memory, new source types, incremental indexing)
   - Remove wiki/memory-specific conventions
4. Update `README.md` — tool table, config table, project structure

Run `make check`. Commit.

### Prompt 8.4: File follow-up issues

Using `gh issue create`, file issues for deferred work:
1. Vault backlinks panel in web UI
2. Vault search UI (search box in sidebar)
3. Agent-assisted curation of user pages
4. Graph view for vault page connections
5. Agent prompts (SOUL.md, USER.md) editable via vault
6. Page templates for common patterns
7. Recently edited pages list in sidebar
8. Frontmatter/tag-based filtering
9. Move/rename pages in UI (drag-and-drop or action menu)
10. Chunking strategy for large pages in embeddings

Reference #175, #180, #170 as parent/related issues. Add to project board as backlog.

---

## Phase summary

| Phase | Description | Key files | Est. complexity |
|-------|-------------|-----------|-----------------|
| 1 | Vault config + path helpers | config.py, config_types.py, vault.py | S |
| 2 | Vault tools (6 tools, remove old) | skills/vault/*, tools/memory_tools.py | L |
| 3 | Embeddings (source types, incremental) | embeddings.py | M |
| 4 | Memory context injection | memory_context.py | S |
| 5 | Agent context (@@[[page]]) | agent.py | S |
| 6 | Web UI + API | http_server.py, web/static/* | L |
| 7 | Dream/garden/prompts | skills/dream/*, garden/*, prompts/* | M |
| 8 | Migration + cleanup + docs | scripts/*, docs/*, CLAUDE.md | M |
