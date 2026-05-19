---
name: mastodon-ingest
description: Fetch recent Mastodon posts and record interesting content to the vault
schedule: "30 */12 * * *"
effort: default
allowed-tools: shell($SKILL_DIR/fetch.sh*), vault_read, vault_write, vault_search, vault_list, vault_backlinks, vault_journal_append, current_time
user-invocable: true
---

# Mastodon Post Ingestion

Fetch recent Mastodon posts and integrate interesting content into the vault knowledge base.

## Output Folder

Write all Mastodon-derived pages **directly under `agent/pages/mastodon/`** as a flat namespace — no topical subdirectories. Use `[[wiki-links]]` between pages to express relationships, and link out to related pages elsewhere in the vault. The garden skill handles promoting clusters to subdirectories when they earn it.

## Configuration

Required environment variables (set in `.env` or `config.json` env section):

| Env Var | Description |
|---------|-------------|
| `MASTODON_SERVER` | Mastodon instance URL (e.g. `https://mastodon.social`) |
| `MASTODON_ACCESS_TOKEN` | API token (Settings > Development > New Application, `read:statuses` scope) |

## Step 1: Fetch posts

Run the fetch script using the shell tool. The script is bundled with this skill:

```
$SKILL_DIR/fetch.sh
```

**What this does (no-args mode):**
- Detects the current platform and runs the correct `mastodon-to-markdown` binary
- Reads Mastodon credentials from env vars
- Automatically fetches posts since the last successful run (timestamp stored under `workspace/skill-state/mastodon-ingest/last-run-time.txt`)
- On first run (no timestamp file), defaults to the last 24 hours
- Applies `--exclude-boosts` and `--exclude-replies` by default
- Updates the timestamp on success so the next run only fetches new posts
- Outputs the posts as formatted markdown to stdout

**Backfill mode.** When invoked with arguments, the script forwards them directly to the underlying `mastodon-to-markdown fetch` binary and skips the timestamp update — so a backfill doesn't clobber the scheduled-cycle state. The `--exclude-boosts` / `--exclude-replies` defaults are also dropped; pass them explicitly if you want them. Use this when the user asks for older posts or a specific date range:

```
$SKILL_DIR/fetch.sh --since 7d                      # last 7 days, ad-hoc
$SKILL_DIR/fetch.sh --start 2026-04-01 --end 2026-04-30
$SKILL_DIR/fetch.sh --since 7d --exclude-boosts --exclude-replies
```

Available flags (forwarded to the binary): `--since <duration>` (e.g. `24h`, `7d`), `--start YYYY-MM-DD`, `--end YYYY-MM-DD`, `--exclude-boosts`, `--exclude-replies`, `--exclude-favorites`, `--public-only`, `--visibility <list>`, `--sort-order asc|desc`. See `$SKILL_DIR/bin/<platform>/mastodon-to-markdown fetch --help` for the full list.

If the script fails (missing env vars, binary not found), report the error and stop.

## Step 2: Review the output

Read through the fetched posts. For each one, consider:
- Is it about a topic that has (or should have) a vault page?
- Does it express a preference, opinion, or decision worth recording?
- Does it mention a project, person, or recurring theme?

Skip boring posts — routine posts, casual replies, and low-signal content don't need wiki entries.

## Step 3: Update the wiki

For each interesting post:
1. `vault_search` to find existing relevant pages.
2. If a page exists with existing frontmatter: `vault_read` it, revise with new context. PRESERVE the existing frontmatter as-is. If this post has a URL, APPEND a new entry to the `sources:` list for it (don't modify earlier entries — they record when each source was first added); if there's no URL, leave `sources:` as-is. Either way, append the post to the body `## Sources` section. `vault_write` the updated page.
3. If a page exists WITHOUT frontmatter: add a full frontmatter block on this write. If this post has a URL, seed `sources:` with just this post; otherwise omit the `sources:` key. Don't backfill historical sources from the body `## Sources` section.
4. If no page exists: create a new page with full YAML frontmatter (see shape below), a body with `[[wiki-links]]`, and a `## Sources` section listing this post.

New-page frontmatter:

```yaml
---
tags: [<topic-tags>]
summary: one-line summary of the page
sources:
  - url: <post URL>
    date: <post date as YYYY-MM-DD>
    added_by: mastodon-ingest
---
```

`sources:` is a YAML list of objects keyed by URL — the list exists for revalidation tooling, which needs a URL to refetch. If the post has no URL, OMIT the `sources:` key entirely from the frontmatter (or leave any existing list unchanged) and just note the source in the body `## Sources` section.

In the `## Sources` section, note the Mastodon post date and include the post URL if available.

## Step 4: Finish

If you made vault changes, summarize what you added/updated.
If there was nothing interesting to ingest, respond with HEARTBEAT_OK.

## Rules

- Only ingest the user's OWN posts — do not quote or reproduce other people's content without attribution
- Convert relative dates ("yesterday", "last week") to absolute dates
- Don't create vault pages for throwaway posts — only for content revealing preferences, projects, or recurring interests
