---
name: kindle
description: Sync highlights & notes from your Kindle library (read.amazon.com/notebook) into per-book vault pages.
schedule: "0 5 * * *"
user-invocable: true
context: inline
argument-hint: "[asin-or-title]"
allowed-tools: kindle_list_books, kindle_fetch_highlights, kindle_sync_book, kindle_sync_all, vault_read, vault_list, vault_write, vault_journal_append, current_time
required-skills: [kindle]
---

# Kindle sync

You sync highlights & notes from `read.amazon.com/notebook` into per-book vault pages under `agent/pages/kindle/`.

## When to run

This skill runs in two contexts:

1. **Scheduled (daily 5am UTC)** — full library sync. Call `kindle_sync_all`. The tool itself short-circuits to `kindle skill disabled; skipping` if `skills.kindle.enabled` is False (the default for fresh installs). It also fails fast if the cookies file is missing.

2. **User-invocable (`!kindle-sync` / `/kindle-sync`)** — on-demand. The `enabled` gate does NOT apply here; the user explicitly asked. Still requires cookies.

## Argument parsing

Argument: `$ARGUMENTS`

- **Empty** (`!kindle-sync`) — call `kindle_sync_all`. Summarize the result.
- **Non-empty** (`!kindle-sync <arg>`) — single-book mode:
  1. Call `kindle_list_books` to get the library.
  2. If `<arg>` looks like an ASIN (10 alphanumeric chars, all-caps), look it up directly.
  3. Otherwise, treat `<arg>` as a title substring. Lowercase-substring match against each book's title. If exactly one matches, use its ASIN. If multiple match, return a numbered list and ask the user to re-invoke with the ASIN. If zero match, return `No book matching '<arg>' in your Kindle library`.
  4. Call `kindle_sync_book(asin=...)` and summarize.

## Notes

- The per-book page is fully agent-owned. **Do not** manually edit `agent/pages/kindle/*.md` — your edits will be overwritten on the next sync. Use a separate hand-curated page for cross-links and synthesis (e.g., `agent/pages/<book>-notes.md` that wiki-links to the agent page).
- If a fetch fails with a 401/403, your cookies have probably expired. Re-export `cookies.txt` from a logged-in browser session and place it at the configured path.
