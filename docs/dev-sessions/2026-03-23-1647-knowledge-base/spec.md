# Knowledge Base (Obsidian-style Wiki)

## Overview

Add an Obsidian-compatible wiki as a structured knowledge base alongside episodic memory. The wiki stores curated, evolving knowledge — facts, preferences, project context, entity profiles — that the agent actively maintains through wiki gardening practices.

Unlike memory (append-only, timestamped entries), wiki pages are living documents the agent revises, restructures, and condenses as understanding evolves.

## References

- Issue: https://github.com/lmorchard/decafclaw/issues/15
- Related: #81 (Dream memory consolidation — needs a destination for distilled facts)
- Related: #89 (Selective memory loading — wiki pages as another context source)

## Storage

### Location

`workspace/wiki/` — inside the agent's workspace, writable by the agent.

### Obsidian Compatibility

The wiki directory should work as an Obsidian vault that the user can open and edit manually:

- **Filenames are page titles** — `Les Orchard.md`, not `les-orchard.md`. Obsidian uses the filename as the display name.
- **`[[wiki-links]]`** — standard Obsidian-style links. `[[Les Orchard]]` resolves to `Les Orchard.md`.
- **Flat structure to start** — all pages in `workspace/wiki/`. Subdirectories are not forbidden but should emerge organically as the wiki grows. Obsidian resolves `[[links]]` across subdirectories by default.

### Page Format

Free-form markdown. Some pages will be entity-oriented (people, projects) with consistent sections, but there's no enforced template. Pages should include a `## Sources` section at the bottom linking to conversations/dates where information originated.

Example:

```markdown
# Les Orchard

Software engineer. Owner and primary user of this DecafClaw instance.

## Preferences

- Drinks: Boulevardier, Old Fashioned
- Editor: VS Code
- Prefers pragmatic solutions over clever ones

## Projects

- [[DecafClaw]] — AI agent project (this bot)
- [[Blog]] — personal blog, weeknotes

## Sources

- 2026-03-15 conversation: mentioned drink preferences
- 2026-03-20 conversation: discussed editor setup
```

## Skill Implementation

### Native Skill

The wiki is implemented as a bundled native skill at `src/decafclaw/skills/wiki/`:

- `SKILL.md` — wiki gardening guidance and tool descriptions
- `tools.py` — Python tool implementations

### Always-Loaded Skill

This introduces a new concept: **always-loaded skills**. The wiki skill's tools and SKILL.md prompt are injected at startup without needing `activate_skill`. This ensures the agent always has wiki awareness and can proactively reference/update knowledge.

Implementation:
- New frontmatter field `always-loaded: true` in SKILL.md
- During skill discovery, skills with `always-loaded: true` are collected separately
- At system prompt assembly time (not per-conversation), these skills are auto-activated: their SKILL.md body is appended to the system prompt and their tools are registered globally
- **No permission check** — always-loaded skills are bundled and admin-trusted
- **Tool budget exemption** — always-loaded skill tools count as always-loaded tools (like `set_effort`, `health_status`), not deferred
- This is a general mechanism — wiki is the first user, but other skills could use it later

## Tools

### `wiki_read(page)`

Read a wiki page by name. Returns the page content, or an error if the page doesn't exist.

- `page`: Page name (filename without `.md`)
- Searches `workspace/wiki/` and subdirectories for the file
- If multiple files match (same name in different subdirs), return the first match and log a warning. Obsidian has the same behavior.

### `wiki_write(page, content)`

Create or overwrite a wiki page. The agent should use this for both new pages and revisions.

- `page`: Page name (becomes the filename)
- `content`: Full markdown content of the page
- Creates `workspace/wiki/{page}.md`
- If the page exists, overwrites it entirely (the agent should read first to preserve what it wants to keep)

### `wiki_search(query)`

Search wiki page titles and contents using substring matching (case-insensitive). Critical for the "search before create" gardening practice.

- `query`: Search term
- Returns matching page names and relevant excerpts
- Also searches page titles (filenames)
- This is distinct from `semantic_search`: `wiki_search` is exact substring matching for finding specific pages by name/content (the gardening tool). `semantic_search` finds conceptually related content across wiki, memories, and conversations (the retrieval tool).

### `wiki_list()`

List all wiki pages. Helps the agent discover what exists.

- Returns page names, optionally with last-modified dates
- Supports an optional `pattern` parameter for filtering (glob-style)

### `wiki_backlinks(page)`

Find all pages that link to the given page via `[[wiki-links]]`.

- `page`: Page name to find backlinks for
- Scans all wiki files for `[[page]]` references
- Returns list of linking page names with the context line

## SKILL.md Guidance

The SKILL.md body contains wiki gardening guidance that's always in the agent's system prompt:

### Core Principles

- **Search before create** — always `wiki_search` before making a new page. Look for existing pages to add to rather than creating duplicates.
- **Revise and rewrite** — wiki pages are living documents. Don't just append — restructure, condense, and rewrite as understanding evolves. New information should improve the whole page, not just pile up at the bottom.
- **Link liberally** — use `[[Page Name]]` to connect related concepts. Links are how the knowledge graph grows.
- **Sources section** — include a `## Sources` section at the bottom of pages noting where information came from (conversation dates, memory entries).
- **Entity pages** — for people, projects, and recurring topics, create dedicated pages that accumulate facts over time.
- **Merge related content** — when you find scattered information about a topic across multiple pages, consolidate into one well-organized page.
- **Split when large** — when a page grows unwieldy, break it into sub-pages with a summary parent page that links to them.
- **Update over duplicate** — if new information contradicts or updates existing wiki content, edit the existing page. The wiki should reflect current understanding, not a history of changes.

## Linking to Memories

Wiki pages can reference memory files using relative paths from the wiki root:

```markdown
## Sources

- [2026-03-15 conversation](../memories/2026/2026-03-15.md) — mentioned drink preferences
- [2026-03-20 conversation](../memories/2026/2026-03-20.md) — discussed editor setup
```

Since `workspace/wiki/` and `workspace/memories/` are siblings, `../memories/...` paths resolve correctly — and work in Obsidian too (Obsidian supports relative links).

**Important boundary**: wiki tools must only create/edit files within `workspace/wiki/`. Memory files are read-only context — the agent should never modify them through wiki operations. The SKILL.md guidance and `wiki_write` tool should enforce this (reject paths that resolve outside the wiki root).

## Semantic Search Integration

Wiki pages are indexed in the embeddings database alongside memories and conversations.

- **Source type**: `"wiki"` (new, alongside existing `"memory"` and `"conversation"`)
- **Higher weight**: wiki results should rank higher than memory/conversation results in semantic search, since they represent curated knowledge. Implementation: apply a score multiplier (e.g. 1.2x) to wiki results when ranking search output. This is a tunable parameter — start with a modest boost and adjust based on experience.
- **Reindexing**: the `make reindex` pipeline should scan `workspace/wiki/` for pages to index
- **Incremental updates**: when a wiki page is written via `wiki_write`, update its embeddings entry

## Configuration

No new config initially. The wiki is always available once the skill is loaded.

Future enhancements:
- `wiki_path` config override (default: `workspace/wiki/`)
- Max page size warnings

## What's NOT in Scope (v1)

- **Git-backed version control** — the workspace is inside a git repo, so git history is naturally available. A formal `wiki_history(page)` tool or auto-commit-on-write is deferred.
- **`wiki_append` tool** — the agent can `wiki_read` + `wiki_write` to achieve the same thing. Add if the read-modify-write pattern proves too expensive.
- **Templates/schemas** — no enforced page structure. Entity pages will develop consistent patterns organically through the SKILL.md guidance.
- **Wiki-specific UI** — no Obsidian-like viewer in the web UI. The user opens the directory in Obsidian (or any markdown editor) for browsing. The agent uses tools.
- **Cross-wiki links** — only one wiki per agent instance.
