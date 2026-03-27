---
name: mastodon-ingest
description: Fetch recent Mastodon posts and record interesting content to the wiki
schedule: "30 */4 * * *"
effort: default
required-skills:
  - wiki
allowed-tools: shell($SKILL_DIR/fetch.sh), wiki_read, wiki_write, wiki_search, wiki_list, wiki_backlinks, memory_recent, memory_search, current_time
user-invocable: true
---

# Mastodon Post Ingestion

Fetch recent Mastodon posts and integrate interesting content into the wiki knowledge base.

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

**What this does:**
- Detects the current platform and runs the correct `mastodon-to-markdown` binary
- Reads Mastodon credentials from env vars
- Automatically fetches posts since the last successful run (stored in `$SKILL_DIR/last-run-time.txt`)
- On first run (no `.last_run` file), defaults to the last 24 hours
- Updates `.last_run` on success so the next run only fetches new posts
- Outputs the posts as formatted markdown to stdout

If the script fails (missing env vars, binary not found), report the error and stop.

## Step 2: Review the output

Read through the fetched posts. For each one, consider:
- Is it about a topic that has (or should have) a wiki page?
- Does it express a preference, opinion, or decision worth recording?
- Does it mention a project, person, or recurring theme?

Skip boring posts — routine posts, casual replies, and low-signal content don't need wiki entries.

## Step 3: Update the wiki

For each interesting post:
1. `wiki_search` to find existing relevant pages
2. If a page exists: `wiki_read` it, revise with new context, `wiki_write` the updated page
3. If no page exists: create a new page with `[[wiki-links]]` and a `## Sources` section
4. In Sources, note the Mastodon post date and include the post URL if available

## Step 4: Finish

If you made wiki changes, summarize what you added/updated.
If there was nothing interesting to ingest, respond with HEARTBEAT_OK.

## Rules

- Only ingest the user's OWN posts — do not quote or reproduce other people's content without attribution
- Convert relative dates ("yesterday", "last week") to absolute dates
- Don't create wiki pages for throwaway posts — only for content revealing preferences, projects, or recurring interests
