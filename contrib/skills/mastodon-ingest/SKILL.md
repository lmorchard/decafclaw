---
name: mastodon-ingest
description: Fetch recent Mastodon posts and record interesting content to the vault
schedule: "30 */4 * * *"
effort: default
allowed-tools: shell($SKILL_DIR/fetch.sh), vault_read, vault_write, vault_search, vault_list, vault_backlinks, vault_journal_append, current_time
user-invocable: true
---

# Mastodon Post Ingestion

Fetch recent Mastodon posts and integrate interesting content into the vault knowledge base.

## Output Folder — READ THIS FIRST

**All new Mastodon-derived pages MUST be created under `agent/pages/mastodon/`.** Never call `vault_write` with a page path that doesn't start with `agent/pages/mastodon/`. Example valid paths:

- `agent/pages/mastodon/Coffee Ritual`
- `agent/pages/mastodon/projects/decafclaw-notes`

Use sub-organization by topic when it makes sense. Include `[[wiki-links]]` back to related pages elsewhere in the vault.

If `vault_search` finds a relevant page that's already under `agent/pages/` but outside `agent/pages/mastodon/`, update it in place — don't create a duplicate. If you find a relevant page *outside* `agent/pages/` (e.g. the user's own notes under a different vault root), do NOT modify it — create a new page under `agent/pages/mastodon/` and optionally link to the user's page with a `[[wiki-link]]`.

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
- Is it about a topic that has (or should have) a vault page?
- Does it express a preference, opinion, or decision worth recording?
- Does it mention a project, person, or recurring theme?

Skip boring posts — routine posts, casual replies, and low-signal content don't need wiki entries.

## Step 3: Update the wiki

For each interesting post:
1. `vault_search` to find existing relevant pages.
2. If a page exists: `vault_read` it, revise with new context, `vault_write` the updated page. Keep the existing path — don't move the page.
3. If no page exists: `vault_write(page="agent/pages/mastodon/<Page Name>", content=...)` with `[[wiki-links]]` and a `## Sources` section. The `page` argument MUST start with `agent/pages/mastodon/` — never the vault root.
4. In Sources, note the Mastodon post date and include the post URL if available.

## Step 4: Finish

End with a short narrative summary of what you added or updated in the vault. If there was nothing interesting to ingest this cycle, begin your summary with `HEARTBEAT_OK` on its own line followed by a brief quiet-cycle note — the leading marker lets the scheduler log a tidy line, and the narrative keeps the archive readable for the newsletter.

## Rules

- **New pages go under `agent/pages/mastodon/`** — never create a new page at the vault root.
- Only ingest the user's OWN posts — do not quote or reproduce other people's content without attribution
- Convert relative dates ("yesterday", "last week") to absolute dates
- Don't create vault pages for throwaway posts — only for content revealing preferences, projects, or recurring interests
