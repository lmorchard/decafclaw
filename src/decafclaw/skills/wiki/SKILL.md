---
name: wiki
description: Obsidian-compatible knowledge base for curated, evolving knowledge
always-loaded: true
---

# Knowledge Base (Wiki)

You have a wiki at `workspace/wiki/` for storing curated knowledge. Unlike memories (append-only, timestamped), wiki pages are **living documents** you revise and improve over time.

The wiki is Obsidian-compatible — the user may also edit pages directly.

## Wiki Gardening Rules

**Search before create.** ALWAYS use `wiki_search` before making a new page. Look for existing pages to add to rather than creating duplicates.

**Revise and rewrite.** Don't just append facts to the bottom of a page. Restructure, condense, and rewrite as understanding evolves. New information should improve the whole page.

**Link liberally.** Use `[[Page Name]]` to connect related concepts. Links are how the knowledge graph grows.

**Include sources.** Add a `## Sources` section at the bottom of pages noting where information came from. Link to memory files with relative paths: `[2026-03-15](../memories/2026/2026-03-15.md)`.

**Create entity pages.** For people, projects, and recurring topics, create dedicated pages that accumulate facts over time.

**Merge related content.** When you find scattered information about a topic, consolidate into one well-organized page.

**Split when large.** When a page grows unwieldy, break it into sub-pages with a summary parent that links to them.

**Update over duplicate.** If new information contradicts existing wiki content, edit the existing page. The wiki should reflect current understanding, not a history of changes.

## Boundaries

- Wiki tools only modify files in `workspace/wiki/`. Never use wiki tools to edit memory files.
- Memories are read-only context. Reference them in Sources sections but don't modify them.
- Use `wiki_read` before `wiki_write` when updating existing pages — `wiki_write` overwrites the entire page.

## Navigating the Knowledge Graph

**Follow links.** When you read a wiki page, note any `[[wiki-links]]` in the content. If the linked pages are relevant to your current task, read them too — context builds through connections.

**Check backlinks.** After reading a page, use `wiki_backlinks` to see what other pages reference it. This reveals related context that might not be obvious from the page itself.

**Explore before answering.** When a user asks about a topic, don't just read one page — follow the links and backlinks to build a fuller picture before responding.

## When to Consult the Wiki

- A user asks about a person, project, or topic → `wiki_search` first, then `wiki_read` matching pages
- You need context for a task → check if there's a wiki page with relevant background
- You're about to give advice or make a decision → see if the wiki has recorded preferences or prior decisions
- You're unsure about something the user told you before → the wiki may have the curated answer
- Don't rely solely on conversation history — the wiki may have information from past conversations you don't have in context

## When to Update the Wiki

- Someone tells you a fact, preference, or decision worth remembering long-term → wiki page
- You notice scattered information about a topic across conversations → consolidate into a wiki page
- A project or person comes up repeatedly → create an entity page
- You learn something that corrects or updates existing knowledge → revise the wiki page
