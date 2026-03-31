# Knowledge Base (Wiki)

DecafClaw includes an Obsidian-compatible wiki for storing curated, evolving knowledge. Unlike [memory](memory.md) (append-only, timestamped entries), wiki pages are living documents the agent revises and improves over time.

The wiki directory works as an Obsidian vault — you can open it in Obsidian (or any markdown editor) and edit pages alongside the agent.

## Storage

Wiki pages live at `workspace/wiki/`. Each page is a markdown file where the filename is the page title (e.g. `Les Orchard.md`).

The directory starts flat. Subdirectories can be created as the wiki grows — Obsidian resolves `[[links]]` across subdirectories by default.

## Wiki Links

Use standard Obsidian `[[wiki-links]]` to connect pages:

```markdown
Works on [[DecafClaw]] and maintains a [[Blog]].
```

`[[DecafClaw]]` resolves to `DecafClaw.md` in the wiki directory (or any subdirectory).

### Linking to Memories

Wiki pages can reference memory files using relative paths:

```markdown
## Sources

- [2026-03-15 conversation](../memories/2026/2026-03-15.md) — mentioned drink preferences
```

Since `workspace/wiki/` and `workspace/memories/` are siblings, `../memories/...` paths resolve correctly in Obsidian too.

## Tools

The wiki skill is **always loaded** — its tools are available in every conversation without activation.

| Tool | Description |
|------|-------------|
| `wiki_read(page)` | Read a page by name. Searches subdirectories. |
| `wiki_write(page, content)` | Create or overwrite a page. Indexes in semantic search. |
| `wiki_search(query)` | Substring search across page titles and content. |
| `wiki_list(pattern?)` | List all pages with last-modified dates. Optional filter. |
| `wiki_backlinks(page)` | Find pages linking to this page via `[[wiki-links]]`. |

### Path Safety

Both `wiki_read` and `wiki_write` validate that paths stay within `workspace/wiki/`. Path traversal attempts (e.g. `../../../etc/passwd`) are rejected.

## Wiki Gardening

The agent follows these principles (encoded in the skill's system prompt):

- **Search before create** — always search for existing pages before making new ones
- **Revise and rewrite** — restructure pages as understanding evolves, don't just append
- **Link liberally** — `[[Page Name]]` connects knowledge
- **Include sources** — `## Sources` section at the bottom of each page
- **Entity pages** — dedicated pages for people, projects, recurring topics
- **Merge related content** — consolidate scattered info into one page
- **Split when large** — break big pages into sub-pages with a summary parent
- **Update over duplicate** — edit existing pages rather than creating new ones

## Chat Context Integration

Users can share wiki pages directly into conversation context:

- **Open page in UI**: When a wiki page is open in the web UI side panel and the user sends a message, the page content is automatically injected as context. The agent sees `[Currently viewing wiki page: PageName]` followed by the page content.
- **@[[PageName]] mentions**: Users can reference wiki pages in their message text using `@[[PageName]]` syntax. This works across all channels (web, Mattermost, terminal). The agent sees `[Referenced wiki page: PageName]` followed by the page content.

Each page is injected **once per conversation** — subsequent messages with the same page open or mentioned will not re-inject. This is tracked by scanning conversation history for existing `wiki_context` role messages.

If a referenced page doesn't exist, the agent sees `[Wiki page 'PageName' not found]`.

Wiki context messages use the `wiki_context` role internally, remapped to `user` for the LLM. They carry a `wiki_page` metadata field for tracking.

## Semantic Search

Wiki pages are indexed in the embeddings database as `source_type: "wiki"`. They receive a 1.2x score boost over memory and conversation results, since curated knowledge is higher signal.

- Pages are indexed incrementally when written via `wiki_write`
- `make reindex` rebuilds the full index including wiki pages
- `memory_search` (with embeddings enabled) and `conversation_search` return wiki content alongside memories and conversations

## Always-Loaded Skills

The wiki uses a new concept: **always-loaded skills**. Skills with `always-loaded: true` in their SKILL.md frontmatter are auto-activated at startup:

- Their SKILL.md body is appended to the system prompt (always in context)
- Their tools are registered globally (no `activate_skill` needed)
- Their tools are exempt from deferral (always available, not behind `tool_search`)
- No permission check — always-loaded skills are bundled and admin-trusted

The wiki is the first always-loaded skill. Other skills can use this mechanism by adding `always-loaded: true` to their frontmatter.

## Example Page

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

- [2026-03-15 conversation](../memories/2026/2026-03-15.md) — drink preferences
- [2026-03-20 conversation](../memories/2026/2026-03-20.md) — editor setup
```
