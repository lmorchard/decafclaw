# Spec — `rss-ingest` contrib skill (#285)

## Goal

A contrib skill that pulls new items from RSS/Atom feeds and integrates
interesting content into the vault — parallel to the existing
`linkding-ingest` and `mastodon-ingest` skills. Fills the gap for sources
without a Linkding-style aggregator in front of them: blogs, YouTube channels
via RSS, podcast shownotes, newsletters.

## Non-goals (v1)

- No `delegate_task`-per-item pipeline (YAGNI — add later only if feeds prove
  noisy). The agent filters + integrates inline via SKILL.md prose.
- No per-feed output subdirectories. Flat namespace; the `garden` skill
  promotes clusters when they earn it.
- No Go binary / separate repo. Everything lives in-repo under the skill dir.

## Shape

Mirrors `mastodon-ingest` — prose-driven, no `tools.py`:

```
contrib/skills/rss-ingest/
  SKILL.md          # agent instructions (fetch → review → vault; manage feeds)
  SCHEDULE.md       # cron sidecar (every 4h)
  fetch.sh          # thin wrapper: fetch + feed-management subcommands
  fetch_feeds.py    # PEP 723 inline-deps script (feedparser), run via `uv run`
  tests/
    fixtures/       # static RSS + Atom sample files (no network)
    test_fetch_feeds.py
```

## Dependency management

`fetch_feeds.py` declares its own dependencies with PEP 723 inline script
metadata and is executed via **`uv run --no-project`**, which resolves and
caches an isolated environment on first run (~6ms warm). No `setup.sh`, no
manual venv, no vendoring, and no coupling to the core project's dependency
set — contrib skills are opt-in.

```python
# /// script
# requires-python = ">=3.11"
# dependencies = ["feedparser"]
# ///
```

`--no-project` is required so uv builds a standalone script env rather than
using decafclaw's `pyproject.toml`.

## Data flow (division of labor)

Same as `mastodon-ingest`: the script fetches and emits **new** feed items as
markdown to stdout; the **agent** reads that output and performs all vault
integration via SKILL.md prose (`vault_search` / `vault_read` / `vault_write`).
The script never touches the vault.

## Configuration — feed list

Feed subscriptions live at **`workspace/skill-state/rss-ingest/feeds.txt`**
(runtime workspace — user-editable, persists across repo updates, not in git).
Format: one feed per line; blank lines and `#` comments ignored; optional
`name|url` to override the derived feed name.

```
# my subscriptions
https://example.com/blog/feed.xml
Simon Willison|https://simonwillison.net/atom/everything/
```

The agent manages this file through `fetch.sh` subcommands (below) so no broad
`workspace_write` grant is needed in `allowed-tools`.

## Incremental state

Per-feed last-seen tracked under `workspace/skill-state/rss-ingest/` (same
location convention as mastodon's `last-run-time.txt`). State is a JSON map
keyed by feed URL:

```json
{ "https://example.com/blog/feed.xml": {"last_published": "2026-07-23T12:00:00Z", "seen_guids": ["..."]} }
```

Emit an entry when its published timestamp is newer than `last_published`,
deduped by entry `guid`/`id` (guards against feeds with unreliable or missing
dates). `seen_guids` is capped (most-recent N) to bound file growth. **First
run per feed defaults to the last 24h** (mirrors mastodon) so a newly added
feed doesn't flood the vault with its entire backlog. State is updated only on
a successful scheduled/auto run.

## `fetch.sh` interface

```
fetch.sh                      # auto: emit new items across all feeds since last run
fetch.sh --since 7d           # backfill, ad-hoc; does NOT update last-seen state
fetch.sh --start YYYY-MM-DD --end YYYY-MM-DD
fetch.sh list                 # print current feeds.txt
fetch.sh add <url> [name]     # append a feed to feeds.txt (idempotent)
fetch.sh remove <url>         # remove a feed from feeds.txt
```

- Platform-agnostic; wraps `uv run --no-project "$SCRIPT_DIR/fetch_feeds.py"`.
- Backfill mode (`--since` / `--start`/`--end`) skips the last-seen update so it
  doesn't clobber the scheduled cycle's state — same ergonomics as mastodon.
- Feed-management subcommands read/write `feeds.txt` in the workspace
  skill-state dir. `add` is idempotent (no duplicate URLs); `add`/`remove`
  report what changed.
- Fails loudly (nonzero exit + stderr message) on: missing `uv`, unreadable
  feeds.txt with no feeds configured, all-feeds-unreachable.

## Output markdown shape

Per emitted entry: title, source feed name, published date (absolute), item
URL, and a cleaned summary/content excerpt. Grouped/labeled by feed so the
agent can see provenance. Exact formatting mirrors the readability of
mastodon-ingest's output.

## SKILL.md contents

Frontmatter `allowed-tools`: `shell($SKILL_DIR/fetch.sh*)`, `vault_read`,
`vault_write`, `vault_search`, `vault_list`, `vault_backlinks`,
`vault_journal_append`, `current_time`. `user-invocable: true`.

Sections:
1. **Output folder** — flat `agent/pages/rss/`, `[[wiki-links]]`, garden
   promotes clusters (copied from mastodon-ingest).
2. **Managing feeds** — documents `feeds.txt` (location + format) and the
   `fetch.sh list|add|remove` commands, so when the user says "subscribe to
   this blog's RSS" the agent knows to run `fetch.sh add <url>`. *(Added per
   review — the agent must know how to subscribe, not just fetch.)*
3. **Step 1: Fetch** — run `$SKILL_DIR/fetch.sh`; describe auto vs backfill
   modes.
4. **Step 2: Review** — signal-filtering guidance ("skip low-signal items":
   routine link-dumps, pure promo, etc.), mirroring mastodon's "skip boring
   posts."
5. **Step 3: Update the wiki** — vault page create/update rules, frontmatter
   shape with `sources:` (`added_by: rss-ingest`), `## Sources` section. Copied
   and adapted from mastodon-ingest.
6. **Step 4: Finish** — summarize changes, or `HEARTBEAT_OK` if nothing.
7. **Rules** — attribution for third-party content; convert relative dates to
   absolute; only create pages for genuinely interesting items.

## SCHEDULE.md

Cron `0 */4 * * *`, `required-skills: [rss-ingest]`, same `allowed-tools`,
prompt "Time for the scheduled RSS ingestion. Follow the rss-ingest skill
instructions to completion." (Per contrib convention, the scheduled task is
opt-in — the user enables it via the copy-on-write overlay; the sidecar itself
is not auto-activated.)

## Testing

**Design constraint:** `fetch_feeds.py` runs under `uv run` with feedparser in
an *isolated* env, so it is **not importable in the project venv** that
`make test` uses (feedparser isn't a project dependency — that's the point of
the uv isolation). To keep the high-value logic covered by the normal
`make test` run without coupling feedparser into the project, structure the
code so the testable logic is **pure-Python (no feedparser)** and the
feedparser call is a single thin adapter:

- `parse_feed(raw_xml) -> list[Entry]` — the ONLY function that touches
  feedparser; imported lazily so importing the module doesn't require
  feedparser. Kept deliberately thin (feedparser does the parsing).
- Pure-Python logic operating on normalized `Entry` dicts, all testable in
  `make test` with no feedparser and no network:
  - `select_new_entries(entries, state, now)` — incremental filtering: emit
    entries newer than `last_published`; dedup by `guid`/`id`; first-run (no
    state) → last-24h window.
  - `render_markdown(entries_by_feed)` — normalized entries → output markdown.
  - `parse_feeds_txt(text)` — comments, blank lines, `name|url` override.
  - state load/save + `seen_guids` cap.

Colocated tests at `contrib/skills/rss-ingest/tests/` run in the default
`make test` (importlib-loaded, per the contrib colocation convention) because
they exercise only the pure functions. The thin `parse_feed` feedparser
adapter is covered by a single fixture-backed check gated on feedparser being
importable (`pytest.importorskip("feedparser")`) so it's exercised in envs that
have it (and in manual smoke) without breaking `make test` where it's absent.

Fixtures: static RSS 2.0 + Atom sample files under `tests/fixtures/`.

## Verification

- Unit tests green, no network.
- Manual smoke: add a real feed via `fetch.sh add`, run `fetch.sh`, confirm
  markdown output; run again, confirm no re-emission (incremental works).
- Not LLM-visible routing → no eval. (The skill catalog entry is a new
  description; if it risks overlap with mastodon/linkding ingest, add a
  `tool_choice`/theme eval — decide at plan time.)
