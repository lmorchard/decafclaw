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

You are syncing my Kindle highlights & notes from `read.amazon.com/notebook` into per-book vault pages under `agent/pages/kindle/`. Do it now — don't ask for confirmation.

Argument: `$ARGUMENTS`

## How to run

**Empty argument** — call `kindle_sync_all` and summarize the result. This is the path taken by both the daily 5am UTC scheduled run and bare `!kindle` / `/kindle` invocations.

**Non-empty argument** — single-book mode:

1. Call `kindle_list_books` to fetch the library.
2. If `<arg>` looks like an ASIN (10 alphanumeric characters, all-caps, typically starting with `B0`), call `kindle_sync_book(asin=<arg>)` directly.
3. Otherwise treat `<arg>` as a title substring. Lowercase-substring match against each book's title.
   - **Exactly one match:** call `kindle_sync_book(asin=<that book's asin>)`.
   - **Multiple matches:** list them as `<asin> · <title>` and tell me to re-invoke with a specific ASIN. Do NOT pick one yourself.
   - **Zero matches:** respond with `No book matching '<arg>' in your Kindle library`.
4. Summarize the result of the sync.

## Notes about behavior

- The `kindle_sync_all` tool short-circuits with `kindle skill disabled; skipping` when the scheduled run hits it with `skills.kindle.enabled = False`. That gate doesn't apply to user-invoked runs — you should sync regardless.
- If a fetch fails with a 401/403 or returns 0 books unexpectedly, the cookies have probably expired or the deployed host's IP isn't authenticated. Surface a message telling me to re-export `cookies.txt` from a logged-in browser session and place it at the configured path.
- Per-book pages at `agent/pages/kindle/*.md` are fully agent-owned. **Do not** manually edit them — they're overwritten on every sync. Cross-links and prose summaries belong on a separate hand-curated page (e.g., `agent/pages/<book>-notes.md` that wiki-links to the agent page).
