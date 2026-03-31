---
name: vault
description: Unified knowledge base — shared Obsidian-compatible vault for curated pages, journal entries, and user notes
always-loaded: true
---

# Vault — Unified Knowledge Base

You have a vault — a shared Obsidian-compatible folder of markdown files with `[[wiki-links]]`. The vault contains your curated pages, daily journal entries, and the user's own notes.

## Your Home Folder

Your files live under `agent/` in the vault:

- `agent/pages/` — curated wiki pages (living documents you revise and improve over time)
- `agent/journal/` — daily journal entries (timestamped observations, append-only)

**Write to `agent/` by default.** You can read anything in the vault, but only write outside `agent/` when the user explicitly asks.

## Vault Gardening Rules

**Search before create.** ALWAYS use `vault_search` before making a new page. Look for existing pages to add to rather than creating duplicates.

**Revise and rewrite.** Don't just append facts to the bottom of a page. Restructure, condense, and rewrite as understanding evolves. New information should improve the whole page.

**Link liberally.** Use `[[Page Name]]` to connect related concepts. Links are how the knowledge graph grows.

**Include sources.** Add a `## Sources` section at the bottom of pages noting where information came from. Link to journal entries with relative paths when appropriate.

**Create entity pages.** For people, projects, and recurring topics, create dedicated pages in `agent/pages/` that accumulate facts over time.

**Merge related content.** When you find scattered information about a topic, consolidate into one well-organized page.

**Split when large.** When a page grows unwieldy, break it into sub-pages with a summary parent that links to them.

**Update over duplicate.** If new information contradicts existing content, edit the existing page. The vault should reflect current understanding, not a history of changes.

**tl;dr summaries.** Pages longer than ~20 lines should have a blockquote summary immediately after the `# Title`: `> tl;dr: One or two sentence summary.` Keep these concise. Update them when the page content changes significantly. Short pages don't need them.

## Journal vs Pages

- **Journal entries** (`vault_journal_append`) are for timestamped observations — things that happened, things you learned, raw notes. Append-only.
- **Pages** (`vault_write`) are for curated knowledge — distilled, organized, revised over time. Living documents.
- The dream process periodically reviews journal entries and distills insights into pages.

## Boundaries

- `vault_write` modifies files in the vault. Default to writing in `agent/pages/`.
- Use `vault_read` before `vault_write` when updating existing pages — `vault_write` overwrites the entire page.
- The user's files are readable but not yours to modify autonomously. Only edit user files when asked.

## Navigating the Knowledge Graph

**Follow links.** When you read a page, note any `[[wiki-links]]` in the content. If the linked pages are relevant to your current task, read them too — context builds through connections.

**Check backlinks.** After reading a page, use `vault_backlinks` to see what other pages reference it. This reveals related context that might not be obvious from the page itself.

**Explore before answering.** When a user asks about a topic, don't just read one page — follow the links and backlinks to build a fuller picture before responding.

## When to Consult the Vault

- A user asks about a person, project, or topic → `vault_search` first, then `vault_read` matching pages
- You need context for a task → check if there's a page with relevant background
- You're about to give advice or make a decision → see if the vault has recorded preferences or prior decisions
- You're unsure about something the user told you before → the vault may have the curated answer
- Don't rely solely on conversation history — the vault may have information from past conversations

## When to Update the Vault

- Someone tells you a fact, preference, or decision worth remembering long-term → page in `agent/pages/`
- An observation or event worth recording → `vault_journal_append`
- You notice scattered information about a topic → consolidate into a page
- A project or person comes up repeatedly → create an entity page
- You learn something that corrects or updates existing knowledge → revise the page
