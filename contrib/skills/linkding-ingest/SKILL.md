---
name: linkding-ingest
description: Fetch recent Linkding bookmarks, analyze their content in child agents, and record insights to the vault
schedule: "45 */4 * * *"
effort: default
required-skills:
  - tabstack
allowed-tools: shell($SKILL_DIR/fetch.sh), vault_read, vault_write, vault_search, vault_list, vault_backlinks, vault_journal_append, tabstack_extract_markdown, current_time, delegate_tasks
user-invocable: true
---

# Linkding Bookmark Ingestion

Fetch recent bookmarks from Linkding, delegate per-bookmark analysis to child agents (so the heavy article text never enters this context), then apply their planned vault writes here.

**Children CANNOT write to the vault.** They research and return a structured plan; the parent applies the writes.

## Output Folder

All bookmark-derived pages live **directly under `agent/pages/bookmarks/`** as a flat namespace — no topical subdirectories. Children acting independently can't coordinate categorization and tend to create one-page subdirs that don't pay off. Use `[[wiki-links]]` between pages instead of folder structure to express relationships, and link out to related pages elsewhere in the vault.

## Configuration

| Env Var | Description |
|---------|-------------|
| `LINKDING_URL` | Linkding instance URL (e.g. `https://links.example.com`) |
| `LINKDING_TOKEN` | API token (Linkding Settings > Integrations) |

## Step 1: Fetch the bookmark list

Run the fetch script:

```
$SKILL_DIR/fetch.sh
```

This outputs ALL recent bookmarks as markdown. Read the entire output to get the full list. Drop obviously low-signal bookmarks (duplicates, ephemeral content) before delegating — don't waste a child on them.

If the output is empty (no bookmarks since the last run), start your final summary with `HEARTBEAT_OK` and stop.

## Step 2: Delegate analysis with `delegate_tasks`

Call `delegate_tasks` with all of these:

- `allow_vault_read: true` — children must browse the vault for context.
- `return_schema`: the shape shown below.
- `tasks`: an array of per-bookmark task descriptions using the template below. One entry per bookmark.

The plural cap is 10 tasks per call. If you have more than 10 bookmarks, call `delegate_tasks` again with the next batch — don't try to cram everything into one call. Concurrent execution is capped server-side; you just submit batches.

### `return_schema`

```json
{
  "writes": [
    {
      "page": "agent/pages/bookmarks/example-slug",
      "content": "---\ntags: [ingested, example]\nsummary: one-line summary of the page\nsources:\n  - url: https://example.com\n    date: 2026-05-12\n    added_by: linkding-ingest\n---\n\nBody synthesizing the source with [[wiki-links]].\n\n## Sources\n- https://example.com (2026-05-12)"
    }
  ],
  "notes": "one-line note about what you wrote or why you skipped"
}
```

### Per-bookmark task description

Substitute the bookmark fields into this template. Send it verbatim to the child:

```
Analyze this bookmark and return a vault-write plan in the structured JSON shape requested by the return_schema. You CANNOT call vault_write — the parent will apply your plan.

URL: {bookmark_url}
Title: {bookmark_title}
Tags: {bookmark_tags}
Description: {bookmark_description}
Date: {bookmark_date}

Tools available to you:
- tabstack_extract_markdown(url) — fetch full article content
- vault_search(query) — search the vault for related pages
- vault_read(page) — read an existing vault page
- vault_backlinks(page) — find pages linking to a page

Steps:
1. Fetch the article with tabstack_extract_markdown. If it fails (paywall, dead link, error stub), work from the title/tags/description only.
2. Search the vault for related pages (vault_search). Read any strong matches (vault_read) so you can extend them rather than creating duplicates.
3. Decide what should be written:
   - Primary: ONE page for this bookmark. If a strong match already exists under `agent/pages/`, return its existing path and the FULL revised content. Otherwise create a new page directly under `agent/pages/bookmarks/` — flat namespace, no subdirectories.
   - Secondary (optional): a small number of related pages under `agent/pages/` to extend with a sentence or cross-link. Return the FULL updated content for each.
   - Cap total writes at ~3. If more pages seem relevant, mention them in `notes` instead of writing them.
   - Do NOT touch pages outside `agent/pages/`. If a strong match lives elsewhere (user notes, admin pages), leave it alone and link to it from your primary page.
4. Each page's content must include:
   - YAML frontmatter — see provenance rules below.
   - Body that synthesizes in your own words (short quotes only, attributed inline).
   - `[[wiki-links]]` to related pages you found.
   - A `## Sources` section. NEW pages: list this bookmark's URL + date. UPDATED pages: KEEP existing entries and append the new bookmark.

Frontmatter rules:
- NEW pages — include all three top-level fields:
  - `tags:` — list of topic tags
  - `summary:` — one-line summary
  - `sources:` — YAML list seeded with ONE entry for this bookmark (shape below)
- UPDATED pages WITH existing frontmatter — PRESERVE everything; just APPEND a new entry to the `sources:` list for this bookmark. Don't modify earlier entries; they record when each source was first added. You may refresh `summary` or extend `tags` if the new content materially changes them.
- UPDATED pages WITHOUT frontmatter — add a full frontmatter block. Seed `sources:` with just this bookmark. DO NOT backfill historical sources from the body `## Sources` section.

`sources:` entry shape:

```yaml
- url: https://example.com
  date: 2026-05-12
  added_by: linkding-ingest
```

`date` is YYYY-MM-DD. If the bookmark has no URL (rare), omit the `sources:` entry and just note the source in the body `## Sources` section.
5. If the bookmark isn't worth a page (low-signal duplicate, throwaway content), return `writes: []` with a one-line `notes` explaining why.
6. Keep your prose response short — one line is enough. The JSON block is what matters.
```

## Step 3: Apply the writes

`delegate_tasks` returns a `ToolResult` whose `data` field has the shape `{summary: {...}, results: [...]}`. Each entry in `results` represents one child and has `{index, ok, text, data}` — with `data.writes` and `data.notes` matching the per-child return schema you specified.

Iterate `data.results`. For each entry where `ok` is true, walk that entry's `data.writes` and call `vault_write(page=<page>, content=<content>)` for each item. The child has already synthesized and merged with existing content — don't second-guess.

For entries where `ok` is false, note the failure in your summary and move on. (The error message is in `entry.error`.)

If two writes target the same page (rare — two bookmarks on the same topic), the later one overwrites the earlier. Acceptable; mention it in the summary.

## Step 4: Summarize

Report what you processed and what changed:

```
Linkding ingest: processed N bookmarks.
Wrote: [[Page A]], [[Page B]], [[Page C]]
Skipped: M (low-signal)
Failed: K (see error)
```

If every bookmark was skipped or there were no bookmarks at all, start your summary with `HEARTBEAT_OK` on its own line followed by a brief note.

## Rules

- **Children cannot write to the vault.** They have read access (with `allow_vault_read: true`) but vault writes are categorically blocked for child agents. The parent applies all writes.
- **Don't fetch article content in this turn.** That's what children are for — your context stays clean.
- **All writes go under `agent/pages/`.** Children that propose paths elsewhere are buggy; skip those writes and note them.
- Convert relative dates to absolute dates.
