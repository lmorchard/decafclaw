---
name: rss-ingest
description: Fetch new items from subscribed RSS/Atom feeds and record interesting content to the vault
effort: default
allowed-tools: shell($SKILL_DIR/fetch.sh*), vault_read, vault_write, vault_search, vault_list, vault_backlinks, vault_journal_append, current_time
user-invocable: true
---

# RSS/Atom Feed Ingestion

Fetch new items from subscribed RSS/Atom feeds and integrate interesting
content into the vault knowledge base. Fills the gap for sources without a
Linkding-style aggregator: blogs, YouTube channels via RSS, podcast shownotes,
newsletters.

## Output Folder

Write all RSS-derived pages **directly under `agent/pages/rss/`** as a flat
namespace — no topical subdirectories. Use `[[wiki-links]]` between pages to
express relationships, and link out to related pages elsewhere in the vault.
The garden skill handles promoting clusters to subdirectories when they earn
it.

## Managing feeds

Subscriptions live in a plain-text file at
`workspace/skill-state/rss-ingest/feeds.txt` — one feed per line, blank lines
and `#` comments ignored, with an optional `name|url` form to set a display
name:

```
# my subscriptions
https://example.com/blog/feed.xml
Simon Willison|https://simonwillison.net/atom/everything/
```

Manage subscriptions through the fetch script (no direct file editing needed):

```
$SKILL_DIR/fetch.sh list                 # show current subscriptions
$SKILL_DIR/fetch.sh add <url> [name]     # subscribe to a feed (idempotent)
$SKILL_DIR/fetch.sh remove <url>         # unsubscribe
```

When the user asks to **subscribe to a feed** (e.g. "add this blog's RSS" or
"follow <feed-url>"), run `$SKILL_DIR/fetch.sh add <url> "<name>"`. Pick a short
human name for the second argument when you can infer one. Confirm the result
back to the user. To unsubscribe, use `remove <url>`.

## Step 1: Fetch new items

Run the fetch script using the shell tool. It is bundled with this skill:

```
$SKILL_DIR/fetch.sh
```

**What this does (no-args mode):**
- Runs `fetch_feeds.py` via `uv run` (feedparser is resolved automatically as
  an isolated, cached dependency — no setup needed beyond `uv`).
- Reads the feed list from `workspace/skill-state/rss-ingest/feeds.txt`.
- For each feed, emits only items newer than the last successful run
  (per-feed state under `workspace/skill-state/rss-ingest/state.json`),
  deduped by entry id.
- On first sight of a feed, defaults to the last 24 hours so a new
  subscription doesn't flood the vault with its whole backlog.
- Advances the per-feed cursor on success, so the next run only sees new items.
- Outputs new items as markdown to stdout, grouped by feed, with a plain-text
  summary excerpt per item.

**Backfill / re-scan mode.** Passing any of `--since <dur>`, `--start`,
`--end` re-scans without advancing the stored cursor, so a backfill doesn't
clobber the scheduled cycle's state:

```
$SKILL_DIR/fetch.sh --since 7d
```

If the script fails (no feeds configured, `uv` missing, all feeds
unreachable), report the error and stop.

## Step 2: Review the output

Read through the fetched items. For each one, consider:
- Is it about a topic that has (or should have) a vault page?
- Does it introduce a project, person, tool, or recurring theme worth
  recording?
- Is it genuinely informative, or routine noise?

Skip low-signal items — pure link dumps, promotional posts, duplicate coverage
of something you already recorded this cycle, and shallow updates don't need
wiki entries.

## Step 3: Update the wiki

For each interesting item:
1. `vault_search` to find existing relevant pages.
2. If a page exists WITH frontmatter: `vault_read` it, revise with new context.
   PRESERVE the existing frontmatter as-is. APPEND a new entry to the
   `sources:` list for this item (don't modify earlier entries — they record
   when each source was first added), and append the item to the body
   `## Sources` section. `vault_write` the updated page.
3. If a page exists WITHOUT frontmatter: add a full frontmatter block on this
   write, seeding `sources:` with just this item. Don't backfill historical
   sources from the body.
4. If no page exists: create a new page with full YAML frontmatter (shape
   below), a body with `[[wiki-links]]`, and a `## Sources` section listing
   this item.

New-page frontmatter:

```yaml
---
tags: [<topic-tags>]
summary: one-line summary of the page
sources:
  - url: <item URL>
    date: <item date as YYYY-MM-DD>
    added_by: rss-ingest
---
```

In the `## Sources` section, note the item's publish date and its URL.

## Step 4: Finish

If you made vault changes, summarize what you added/updated.
If there was nothing interesting to ingest, respond with HEARTBEAT_OK.

## Rules

- Attribute third-party content — these are other people's writing; link to the
  source and don't reproduce it wholesale without attribution.
- Convert relative dates ("yesterday", "last week") to absolute dates.
- Only create vault pages for content revealing genuine information, projects,
  or recurring interests — not for every item in the feed.
