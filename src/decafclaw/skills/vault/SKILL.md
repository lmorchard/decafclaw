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

## Journal vs Pages

- **Journal entries** (`vault_journal_append`) are for timestamped observations — things that happened, things you learned, raw notes. Append-only.
- **Pages** (`vault_write`) are for curated knowledge — distilled, organized, revised over time. Living documents.
- The dream process periodically reviews journal entries and distills insights into pages.

## Page Naming and Cross-Platform Safety

Vault pages sync across Linux, macOS, Windows, and mobile devices (Android / iOS).
- **Never use forbidden filename characters:** `:`, `?`, `*`, `"`, `<`, `>`, or `|` in page titles or paths. These cause immediate sync failures and "out of sync" errors on Android and Windows filesystems.
- **Convert colons in titles:** When naming pages based on articles, topics, or subtitles with colons (e.g., `Topic: Subtopic`), always replace `:` with a spaced dash (`Topic - Subtopic`).
- Keep page titles concise, descriptive, and filesystem-friendly.

## Boundaries

- `vault_write` modifies files in the vault. Default to writing in `agent/pages/`.
- Prefer `vault_edit` for surgical changes — it replaces an exact block of text without rewriting the page. Use `vault_read` (optionally with `start_line`/`end_line`) to get the exact text and the page's version token; pass that token as `expected_version` so a stale edit is rejected.
- Use `vault_write` for a new page or a substantial rewrite; it overwrites the entire page, so `vault_read` first when updating an existing one.
- `vault_append` adds text to the end of a page (creating it if missing) without a prior read.
- `vault_replace_lines` replaces a 1-based line range; `vault_read` with `start_line`/`end_line` shows the numbers.
- `vault_rename` renames or moves an existing page (updates the embedding index). Prefer it over delete + rewrite when the content stays the same.
- `vault_delete` permanently removes a page. Only for pages that are definitively wrong, duplicate, or no longer reachable — prefer editing over deleting when the page can be salvaged. It refuses pages with inbound links unless `allow_broken_links=true` (then reports the pages left dangling), and unattended calls default to dry-run. Use `vault_delete_plan` for batches. Pages outside `agent/` will trigger a user confirmation.
- `vault_update_frontmatter` merges metadata fields (summary, keywords, tags, importance) into a page without touching the body. Pages outside `agent/` will trigger a user confirmation in interactive conversations; scheduled maintenance (dream/garden) can update frontmatter vault-wide without confirmation since it can't prompt.
- User pages outside `agent/` are writable on explicit user request. Each `vault_write` / `vault_edit` / `vault_append` / `vault_replace_lines` / `vault_delete` / `vault_rename` / `vault_update_frontmatter` triggers a user confirmation. For batch operations (3+ pages in the same folder), call `vault_grant_folder` first to trust the folder for the rest of the conversation and skip per-page confirmations.

## Editing a Page

Pick the smallest tool that does the job:

- `vault_edit(page, old_text, new_text)` — the default for typo fixes, sentence rewrites, and multi-line block changes. Copy `old_text` from a fresh `vault_read` so the match is exact; it fails if the text is missing or matches more than once (`replace_all=true` for bulk).
- `vault_replace_lines(page, start_line, end_line, content)` — edits by line number (boundary rewrites, deletions by range).
- `vault_append(page, content)` — additions at the end of a page, no prior read needed.
- `vault_write(page, content)` — new pages or rewrites of most of the page.

Structural edits (inserting a heading in the middle, moving a section) are exact-match edits: `vault_edit` with the surrounding heading and body as `old_text`.

## Organizing with Folders

The vault supports hierarchical folders. Use them to keep related pages together as topics grow.

- **Prefer folders over flat lists.** When a topic area accumulates 3+ related pages, group them into a folder (e.g. `agent/pages/projects/decafclaw/`).
- **Suggested conventions:** `projects/`, `people/`, `resources/`, or topic-specific areas. These aren't rigid — let the content guide the structure.
- **Use folder paths in vault tools:** `vault_write(page="agent/pages/projects/decafclaw/roadmap", ...)`.
- **Use `vault_list` with folder filter** to explore a specific area: `vault_list(folder="agent/pages/projects")`.
- **`vault_list` is the enumeration tool, not `vault_search`.** A `vault_search` call that narrows on nothing — no `query`, and no `tags`, folder or date filter either — is refused rather than run, because it would return every page in the vault instead of searching it. Any one narrowing argument is enough to make it a search.
- **Link with folder paths (vault-root-relative):** `[[agent/pages/projects/decafclaw/roadmap]]` for explicit links, or `[[roadmap]]` for stem-based resolution (picks the closest match).

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
