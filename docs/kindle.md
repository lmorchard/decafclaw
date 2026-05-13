# Kindle

Periodically ingest Kindle highlights and notes from `read.amazon.com/notebook` into per-book vault pages.

This is a **contrib skill** — it lives at `contrib/skills/kindle/` and is not bundled by default. See [Installation](#installation) below to enable it.

## Installation

Add the skill directory to `extra_skill_paths` in your agent config. First, expose the repo path as an env var in `.env`:

```bash
DECAFCLAW_REPO=/absolute/path/to/decafclaw-repo
```

Then in `data/{agent_id}/config.json`:

```json
{
  "extra_skill_paths": [
    "$DECAFCLAW_REPO/contrib/skills/kindle"
  ]
}
```

See `contrib/skills/README.md` for detailed installation options (reference vs. copy, env var vs. absolute path).

## Operating modes

**On-demand:** `!kindle-sync` (Mattermost) / `/kindle-sync` (web UI) syncs all books in your Kindle library that have highlights. Pass a book title or ASIN to sync a single book:

- `/kindle-sync` — sync all books
- `/kindle-sync "The Pragmatic Programmer"` — sync by title
- `/kindle-sync B0XXXXXXXX` — sync by ASIN

**Scheduled:** daily at 5am UTC via `schedule: "0 5 * * *"` in SKILL.md. Gated by `skills.kindle.enabled` (default `false`) so fresh installs don't fire failing scrapes.

## Setup

1. **Export `cookies.txt`** from a logged-in Amazon session. Recommended: the "Get cookies.txt LOCALLY" browser extension (Chrome, Firefox). Make sure your browser is signed in to amazon.com (or your local TLD) at the time of export.

2. **Place the file** at `data/{agent_id}/secrets/kindle.cookies.txt` on the agent host. The path is configurable via `KINDLE_COOKIES_PATH` env var or `skills.kindle.cookies_path` config.

3. **Opt in to scheduled sync** by setting `skills.kindle.enabled = true` in `data/{agent_id}/config.json` (under the `skills` key). On-demand sync works regardless of this flag — you just need cookies present.

Cookies are long-lived (typically ~1 year for Amazon). The skill warns when the cookies file is older than `cookies_warn_after_days` (default 300 days). When you see the warning — or when sync fails with a 401/403 — re-export `cookies.txt` and replace the file.

## Vault layout

The skill writes one page per book at `agent/pages/kindle/<asin>-<title-slug>.md`. The page is **fully agent-owned** — running sync overwrites the page on each run. Do NOT manually edit these pages — your edits will be overwritten on the next sync. For synthesis, cross-links, and prose notes, create a separate hand-curated page (e.g., `agent/pages/<book>-notes.md`) that the skill never touches.

Frontmatter:

```yaml
asin: B0XXXXXXXX
title: "Book Title"
author: "Author Name"
cover_url: https://...
tags: [ingested, kindle]
summary: "Kindle highlights from <Title> by <Author>"
keywords: []
importance: 0.5
highlight_count: 42
archived_count: 3
last_synced: 2026-05-13T05:00:00+00:00
```

Body has two sections:

- **`## Highlights`** — current highlights in document order. Each highlight is preceded by an HTML comment marker (`<!-- annotation-id: ... -->`) used for upsert tracking.
- **`## Archived`** — highlights that have been deleted on Amazon's side since the last sync. Rendered with strikethrough markdown and an "archived YYYY-MM-DD" stamp.

## Per-run observability

Each `kindle_sync_all` run appends a journal entry tagged `[ingested, kindle]` under `agent/journal/YYYY/YYYY-MM-DD.md` summarizing books processed, new highlights, archived count, and any failures.

## Configuration

All keys live under `skills.kindle` in `data/{agent_id}/config.json`, or as env vars with the `KINDLE_*` prefix:

| Key | Default | Description |
|---|---|---|
| `enabled` | `false` | Gates the scheduled run. On-demand works regardless. |
| `cookies_path` | `""` (resolves to `data/{agent_id}/secrets/kindle.cookies.txt`) | Path to the Netscape-format cookies file. Relative paths resolve against `agent_path`. |
| `amazon_domain` | `"amazon.com"` | Configurable TLD for non-US accounts (`amazon.co.uk`, `amazon.de`, etc.). |
| `vault_subfolder` | `"agent/pages/kindle"` | Vault-relative folder for per-book pages. |
| `sync_min_interval_seconds` | `60` | Delay between books in a multi-book sync. |
| `archive_deleted` | `true` | If false, deleted highlights are dropped instead of moved to the Archived section. |
| `user_agent` | (Chrome 131 UA) | Override the User-Agent header. Defaults to a realistic recent Chrome string. |
| `cookies_warn_after_days` | `300` | When cookies file is older than this, the sync summary includes a "consider re-exporting" warning. |

## Tools

The skill provides four native tools:

- **`kindle_list_books(ctx)`** — fetch the books-with-highlights index. Returns ASIN, title, author, and cover_url per book.
- **`kindle_fetch_highlights(ctx, asin)`** — fetch all highlights for one book. Returns annotation_id, location, color, text, and note per highlight.
- **`kindle_sync_book(ctx, asin)`** — fetch and upsert a single book's vault page.
- **`kindle_sync_all(ctx)`** — orchestrate a full library sync with rate-limiting and per-run journal entry.

## Architecture notes

- **HTTP client:** `curl_cffi` with Chrome TLS impersonation. Amazon flags vanilla `requests`/`httpx` via TLS fingerprinting; `curl_cffi` mimics a real Chrome handshake.
- **Playwright fallback hook:** the two network functions (`_fetch_books_list_html`, `_fetch_book_highlights_html`) are the only places that touch the network. If `curl_cffi` stops working, swap their bodies to use a Playwright headless session — everything else (parsing, upsert, vault writes, tool defs) stays unchanged.
- **Upsert model:** stable Amazon annotation IDs survive across syncs. Edits in place (same ID, different text); deletions move to `## Archived` with today's date stamp. Previously-archived entries keep their original archive date.

## Troubleshooting

- **`[error: Kindle cookies file not found ...]`** — Export cookies.txt and place at the configured path.
- **`[error: failed to fetch books list: ...]` after cookies are in place** — Cookies probably expired (401/403). Re-export.
- **Sync runs but pages don't update** — Check that the `kindle_sync_book` tool isn't being blocked by vault user-write confirmation (agent-owned `agent/pages/kindle/*` should bypass the gate automatically).
- **The scheduled run silently does nothing** — Confirm `skills.kindle.enabled` is `true` in config.

## Related

- Filed as part of [issue #375](https://github.com/lmorchard/decafclaw/issues/375).
- Sibling pattern with the (still-open) [YouTube transcript ingest](https://github.com/lmorchard/decafclaw/issues/374) — both consume the `data/{agent_id}/secrets/<source>.cookies.txt` convention introduced here.
- Inspired by the [Obsidian Kindle Highlights plugin](https://github.com/hadynz/obsidian-kindle-plugin), which proves the differential-sync model against `read.amazon.com/notebook`.
