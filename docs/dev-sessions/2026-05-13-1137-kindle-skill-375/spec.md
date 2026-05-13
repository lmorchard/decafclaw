# Kindle highlights & notes ingest skill — Spec

**Goal:** Periodically scrape Kindle highlights & notes from `read.amazon.com/notebook` into per-book vault pages, with on-demand and scheduled sync modes, plus archived-section handling for deleted highlights. Replaces the manual Obsidian Kindle Highlights plugin workflow for the agent host.

**Source:** [issue #375](https://github.com/lmorchard/decafclaw/issues/375).

## Current state

- No Kindle ingest exists today. The user manually exports highlights via the [Obsidian Kindle plugin](https://github.com/hadynz/obsidian-kindle-plugin) on a separate host.
- The closest prior art is the bundled `ingest` skill (`src/decafclaw/skills/ingest/SKILL.md`) — one-shot URL/file ingestion via `tabstack_extract_markdown` + LLM-driven page composition. It does *not* support upsert/dedup or periodic re-sync.
- Bundled skill anatomy that this skill will mirror: `src/decafclaw/skills/newsletter/tools.py:19-48` (`SkillConfig` dataclass + `init(config, skill_config)` + `TOOL_DEFINITIONS` export) and `src/decafclaw/skills/newsletter/SKILL.md:1-65` (frontmatter with `schedule:` + `user-invocable: true` + `allowed-tools`).
- Bundled-skill schedule discovery already wired: `src/decafclaw/schedules.py:99-160` rescans bundled skill SKILL.md files for `schedule:` frontmatter every poll tick; `run_schedule_task` (lines 216-295) dispatches with conv ID `schedule-{name}-{timestamp}`.
- Vault primitives: `frontmatter.py:19-55` (parse/serialize YAML frontmatter); `skills/vault/tools.py:300` (`vault_write` does full-page overwrite); `skills/vault/tools.py:563` (`vault_journal_append` for episodic per-run logging).
- Existing HTTP clients: `httpx` throughout (`tools/http_tools.py:9`, etc.) but **no existing pattern for cookie-authenticated HTML scraping** — this skill establishes it.
- Sibling issue #374 (YouTube transcript ingest) is open but unimplemented. It shares the cross-host cookie problem; the cookie-storage convention defined here is intended for #374 to adopt later.

## Desired end state

A bundled `kindle` skill at `src/decafclaw/skills/kindle/` with three operating modes:

1. **On-demand full sync** — `!kindle-sync` (Mattermost) / `/kindle-sync` (web UI) → list books with new activity since last sync → re-fetch each book's highlights → upsert into per-book vault page.
2. **On-demand single-book sync** — `!kindle-sync <asin-or-title>` → fetch just that book's highlights and upsert. Title fuzzy match falls through to ASIN if ambiguous.
3. **Scheduled sync** — daily cron (default `0 5 * * *`) runs the full-sync path. Gated by `skill_config.enabled` + presence of cookies file; silently skips otherwise. Logs a `vault_journal_append` entry tagged `[ingested, kindle]` per run.

Per-book vault page at `agent/pages/kindle/<asin>-<title-slug>.md` is **fully agent-owned**, mechanically overwritten on every sync:

```markdown
---
asin: B0XXXXXX
title: "Book Title"
author: "Author Name"
cover_url: https://...
tags: [ingested, kindle]
summary: "<one-line description for embedding retrieval>"
keywords: [...]
importance: 0.5
highlight_count: 42
archived_count: 3
last_synced: 2026-05-13T05:00:00Z
---

## Highlights

<!-- annotation-id: ABC123 -->
**Location 412** · *yellow*

> Highlight text here.

*Note:* Optional user note.

<!-- annotation-id: DEF456 -->
...

## Archived

<!-- annotation-id: GHI789 -->
~~**Location 102** · *yellow*~~ *(archived 2026-05-12)*

> ~~Deleted highlight text.~~
```

Upsert model: on each sync, parse the existing page (if any), index existing entries by `annotation-id`, then re-emit. New IDs are inserted in location order; edited highlights overwrite the text in place; missing-from-Amazon IDs move to `## Archived` with a strikethrough + archive date.

Synthesis (cross-linking, wiki-links, prose summaries) is the **user's responsibility on a separate hand-curated page** — the agent never touches the synthesis page. This is the explicit boundary between mechanical ingest and curated knowledge.

## Design decisions

- **Decision:** Ship all three phases (on-demand single + on-demand all + scheduled with archive handling) in this one PR.
  - **Why:** The work isn't actually three phases of effort — Phase 1 (on-demand single-book) does 90% of the implementation (auth, fetch, parse, upsert), and adding "loop over all books" and "archive missing IDs" is incremental on top of that. Splitting into three PRs would be three rebases for not much review benefit.
  - **Rejected:** Phase 1 only first. Mirrors `ingest` but leaves the user with no scheduled sync until later — a real loss of value for the size of effort saved.

- **Decision:** Use `curl_cffi` (TLS-impersonating Python HTTP client) as the primary fetch client.
  - **Why:** Amazon flags vanilla `requests`/`httpx` clients via TLS fingerprinting (mid-2023+). `curl_cffi` impersonates a real Chrome/Firefox TLS handshake. Async-capable, accepts standard cookie jars, lightweight. Same approach as the Node.js `kindle-api` library uses (via external TLS-client proxy).
  - **Rejected:** Raw `httpx` — known to be blocked. Tabstack — black-box for auth, unclear if per-request cookie injection is supported. Playwright — heavy (~100MB install) for what is a single static HTML page.
  - **Fallback:** If `curl_cffi` stops working (Amazon escalates detection), pivot to Playwright with persistent profile. Keep the fetch layer thin enough that swapping the client is a localized change — the parser and upsert logic don't care which client supplied the HTML.

- **Decision:** Cookies live at `data/{agent_id}/secrets/kindle.cookies.txt` in Netscape `cookies.txt` format, path configurable via `KINDLE_COOKIES_PATH` env var or `skills.kindle.cookies_path` JSON config.
  - **Why:** Netscape format is the de-facto standard every browser-cookie-exporter extension produces ("Get cookies.txt LOCALLY", "cookies.txt", etc.). Loads cleanly into `curl_cffi` via `http.cookiejar.MozillaCookieJar`. Admin path (`data/{agent_id}/`) is agent-readable but not agent-writable — appropriate for credentials.
  - **Rejected:** Workspace path — agent could overwrite the cookies (undesirable for secrets). JSON blob — no de-facto exporter exists. Generic shared `cookies/{domain}.txt` directory — speculative over-engineering before we know what #374 actually needs.
  - **Coordination with #374:** This skill defines the convention (Netscape format, admin secrets directory). #374 follows the same path scheme later, e.g. `data/{agent_id}/secrets/youtube.cookies.txt`. No shared abstraction is built yet — each skill loads its own cookies file directly. If a second source proves the pattern useful, we extract a helper at that point.

- **Decision:** Per-book pages are fully agent-owned mechanical overwrites. Synthesis lives on a separate user-curated page.
  - **Why:** The vault has no existing "agent-managed sections vs user-preserved regions" convention. Building one would be a separate sub-project (parse + preserve outside-region content + define the delimiter syntax + handle conflicts), all of which is unrelated to Kindle ingest. The clean boundary — agent owns the highlights page entirely; user owns the synthesis page entirely — sidesteps the whole problem.
  - **Rejected:** Single page with `<!-- BEGIN agent-managed -->` delimiters (requires new vault primitive); append-only with dedup (loses the ability to detect edited highlights).

- **Decision:** Deleted highlights move to an `## Archived` section in the same per-book page, with strikethrough markdown and the archive date. The `archived_count` field on frontmatter tracks the running total.
  - **Why:** Preserves history (the user invested in those highlights at one point and may want them back). One file per book stays the unit of organization — no separate "archive store" to maintain. Strikethrough + archive date makes the human-readable diff between current and lost obvious.
  - **Rejected:** Hard-delete (loses history); separate archive store (more files to manage).

- **Decision:** Per-run observability via `vault_journal_append` — one entry per sync run, tagged `[ingested, kindle]`, with a structured summary line (e.g., `Synced 3 books: 12 new, 2 edited, 1 archived`).
  - **Why:** Same pattern `newsletter` and `dream` use. Cheap. The journal is already wikilinked, searchable, and indexed for embedding retrieval. Issue #289 (structured ingest log) might supersede this later; if so, both consumers can read the journal entries directly.
  - **Rejected:** Workspace JSONL ingest log (premature; #289 isn't built yet); return-text-only (not searchable, lost outside the scheduled-task conv archive).

- **Decision:** Scheduled run is gated by `skill_config.enabled` (default `False`) AND presence of the cookies file. Schedule frontmatter is always present in SKILL.md; the gate happens inside the prompt body.
  - **Why:** Fresh installs shouldn't fire failing Amazon scrapes daily. `enabled=False` default means scheduled runs no-op until the user has set up cookies and flipped the toggle. On-demand `!kindle-sync` still works regardless of `enabled` (gated only by cookies file presence) — useful for one-off backfills.
  - **Rejected:** Omitting `schedule:` until enabled — would require config-aware skill discovery, which doesn't exist.

- **Decision:** Highlight color is captured as metadata on each highlight entry (Amazon's yellow/blue/pink/orange).
  - **Why:** Trivial to capture during parsing; lets the user grep/filter later. Free signal.

- **Decision:** Multi-domain support is config-driven from day one via `amazon_domain` (default `amazon.com`), but v1 is single-domain-per-install — no per-book domain detection.
  - **Why:** Single-account users only ever hit one domain. Building per-book detection without a user with multiple Amazon accounts is over-engineering.

## Patterns to follow

- **Skill structure:** Mirror `src/decafclaw/skills/newsletter/`:
  - `tools.py`: `SkillConfig` dataclass with `env_alias` metadata on each field (`src/decafclaw/skills/newsletter/tools.py:19-42`); `init(config, skill_config)` signature stores module-level `_config` and `_skill_config` (lines 44-48); `TOOL_DEFINITIONS` dict mapping tool names → callables.
  - `SKILL.md`: frontmatter with `name`, `description`, `schedule`, `user-invocable: true`, `context: inline`, `allowed-tools`, `required-skills`. Mirror `src/decafclaw/skills/newsletter/SKILL.md:1-65`.
- **Tool signatures:** All native tools take `ctx` as first param (`src/decafclaw/CLAUDE.md` convention). Async, return `ToolResult` (errors as `ToolResult(text="[error: ...]")`).
- **Config resolution:** `load_sub_config(SkillConfig, json_data, env_prefix="KINDLE")` per `config.py:88-145`. Env precedence: `KINDLE_FIELD_NAME` → `env_alias` → JSON `skills.kindle.field_name` → dataclass default.
- **Frontmatter:** Use `frontmatter.parse_frontmatter()` and `frontmatter.serialize_frontmatter()` (`frontmatter.py:19-55`) for read-modify-write. The vault page body string is what `vault_write` accepts.
- **Vault writes:** Call `tool_vault_write(ctx, page, content)` (`skills/vault/tools.py:300`) directly from within the kindle tools; it handles path validation and write permissions.
- **Vault journal:** Call `tool_vault_journal_append(ctx, tags=[...], content="...")` (`skills/vault/tools.py:563`) for per-run summaries.
- **Scheduled-task body:** The SKILL.md body is the LLM prompt the scheduled run sees. Write it so the LLM (a) checks `enabled` via reading skill config / cookies file, (b) calls `kindle_sync_all`, (c) appends a journal entry. Mirror `src/decafclaw/skills/newsletter/SKILL.md` structure.
- **Test patterns:**
  - Mock at the HTTP-client boundary (the `curl_cffi` Session). Capture fixture HTML from `read.amazon.com/notebook` (one book list page + one highlights page per fixture) under `tests/fixtures/kindle/`.
  - Per `CLAUDE.md` testing rules: patch `decafclaw.schedules.run_schedule_task` in any test that touches the scheduler (bundled scheduled skills will otherwise fire real fetches).
  - Test the upsert logic end-to-end via the parsed-HTML → page-string path; don't network in CI.

## What we're NOT doing

- **Building a generic "agent-managed sections + user-preserved regions" vault primitive.** Filed mentally as a future option if a second skill needs it. Single-page agent ownership is the boundary for now.
- **Downloading book cover images.** Cover URL goes in frontmatter; the image stays on Amazon's CDN. Defer to a separate issue if image-on-disk ever matters.
- **Supporting `My Clippings.txt` (USB device) fallback.** Out of scope; device-only and sparse metadata. File as follow-up if needed.
- **Readwise / Send-to-Kindle / official Amazon API integrations.** Out of scope.
- **Solving cross-host cookie *transport*.** User is responsible for getting `cookies.txt` to the agent host (scp, sync, manual paste). This skill consumes the file; it does not produce it.
- **Building #374's YouTube ingest.** Sibling skill, separate session. We only define the cookie-path convention #374 can adopt.
- **Implementing issue #289's structured ingest log.** We use `vault_journal_append` for now; #289 can read those entries if/when it ships.
- **Per-book domain detection.** Single Amazon domain per install via `amazon_domain` config.
- **Automatic 2FA / re-auth.** When cookies expire, user re-exports manually. No retry loops, no captcha solving, no headless-browser login flow.
- **Synthesis / cross-linking pages.** Highlights pages are mechanical; cross-linking and prose summaries are the user's hand-curated work on separate pages.
- **A wrapped notification tool for the agent to emit `NotificationRecord`s directly.** The scheduled task surfaces status via `vault_journal_append` + the schedule conv archive; no direct notification emission.

## Open questions

- **Title-fuzzy-match strategy for `!kindle-sync <arg>`.** Default answer: substring match on lowercased title; if ambiguous (>1 match), return a list and ask the user to pass the ASIN instead. Simple; no fuzzy-string library needed for v1.
- **Rate-limit cadence between book fetches.** Default answer: `sync_min_interval_seconds=60` (one book/minute). Conservative; can tune downward later if Amazon doesn't object.
- **Handling of un-highlighted books.** Default answer: skip silently. The notebook page only lists books with at least one highlight, so this should be a non-issue in practice.
- **Cookies-file age warning threshold.** Default answer: warn (via journal entry) when cookies file is older than 300 days. Amazon cookies are ~365-day TTL, so 300 days gives the user time to re-export before failures start.
- **Tool timeouts.** Default answer: `kindle_sync_all` and `kindle_list_books` opt out of the 180s default timeout (set `timeout=None` or `timeout=600`). Per-book `kindle_sync_book` and `kindle_fetch_highlights` keep the default 180s.
