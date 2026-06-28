---
name: meta-ingest
description: Fetch recent activity across all me-to-markdown sources (Mastodon, Linkding, GitHub, Spotify, YouTube, Pocket Casts), analyze each source in a child agent, and record insights to the vault
effort: default
required-skills:
  - tabstack
allowed-tools: shell($SKILL_DIR/fetch.sh), shell($SKILL_DIR/fetch.sh *), workspace_read, vault_read, vault_write, vault_search, vault_list, vault_backlinks, vault_journal_append, tabstack_extract_markdown, current_time, delegate_tasks
user-invocable: true
---

# Meta Ingestion

Fetch recent activity across **all** `me-to-markdown` sources in one pass, hand each source to its own child agent for analysis (so heavy content like full article text never enters this context), then apply their planned vault writes here.

This is the unified successor to `linkding-ingest` and `mastodon-ingest`: instead of one skill per source, `me-to-markdown` fetches every source over a shared time window, and you fan out one child per source.

**Children CANNOT write to the vault.** They research and return a structured plan; the parent (you) applies the writes.

## Prerequisites

- `me-to-markdown` installed and on `$PATH`.
- `me-to-markdown install` has populated its per-source binaries.
- `me-to-markdown auth` has been run for the desired sources.

Per-source credentials live in `me-to-markdown`'s own config / env (e.g. `MASTODON_SERVER`, `LINKDING_URL`, `GITHUB_TOKEN`, …) — this skill does not read them directly.

## Step 1: Fetch all sources

Run the fetch script:

```
$SKILL_DIR/fetch.sh
```

With no args it fetches everything since the last successful run (or the past 24h on first run) and updates a workspace-side timestamp on a clean run. This is what scheduled cycles use.

**Backfill mode.** Any arguments are forwarded directly to `me-to-markdown export` and the timestamp is left untouched, so a backfill doesn't clobber scheduled-cycle state:

```
$SKILL_DIR/fetch.sh --since 7d
$SKILL_DIR/fetch.sh --since 2026-04-01 --until 2026-04-30
$SKILL_DIR/fetch.sh --since 168h --include mastodon,linkding
$SKILL_DIR/fetch.sh --since 30d --exclude spotify,youtube
```

The script writes **one file per source** into the workspace and prints a manifest — workspace-relative paths plus byte sizes, **not** the content:

```
Per-source files (workspace-relative paths for workspace_read):
  github       skill-state/meta-ingest/export/github.md       (2316 bytes)
  linkding     skill-state/meta-ingest/export/linkding.md     (1115 bytes)
  mastodon     skill-state/meta-ingest/export/mastodon.md     (641 bytes)
  pocketcasts  skill-state/meta-ingest/export/pocketcasts.md  (3763 bytes)
  spotify      skill-state/meta-ingest/export/spotify.md      (152 bytes)
  youtube      skill-state/meta-ingest/export/youtube.md      (92 bytes)
```

You do **not** read these files yourself — each is handed to its own child agent, which reads it directly so source text never enters your context. Use the manifest only to decide which sources to delegate: skip files that are obviously just an empty header (very small — roughly `< 200 bytes`, like the `spotify`/`youtube` rows above when there's no activity). Delegate a child for every remaining file; the child decides whether its content is worth recording.

If the manifest is empty or every file is below the empty-header threshold, start your final summary with `HEARTBEAT_OK` on its own line and stop.

## Step 2: Delegate one child per source with `delegate_tasks`

One `delegate_tasks` call, one task per source file. The children run **in parallel** and each writes to a **different** vault folder, so there are no cross-source conflicts. Call `delegate_tasks` with:

- `allow_vault_read: true` — children must browse the vault for context.
- `return_schema`: the shape shown below.
- `tasks`: a flat JSON **array of plain strings** — one string per delegated source, each being the fully-substituted per-source prompt from the template below. It is **NOT** a list of objects: pass `["<prompt for github>", "<prompt for linkding>", …]`, never `[{"prompt": "…"}]`. At most 6 sources — well under the 10-task cap — so a single call covers them all.

The call looks like this (only `tasks` varies per source; the other three params are shared across the whole batch):

```
delegate_tasks(
    allow_vault_read=true,
    return_schema={ …the shape shown below… },
    tasks=[
        "<github prompt, fully substituted>",
        "<linkding prompt, fully substituted>",
        "<mastodon prompt, fully substituted>",
        …one string per remaining source…
    ],
)
```

Each child reads its own source file with `workspace_read(<path from the manifest>)`; you never load the source content into this turn.

### `return_schema`

```json
{
  "writes": [
    {
      "page": "agent/pages/<folder>/example-slug",
      "content": "---\ntags: [ingested, example]\nsummary: one-line summary of the page\nsources:\n  - url: https://example.com\n    date: 2026-05-12\n    added_by: meta-ingest\n---\n\nBody synthesizing the source with [[wiki-links]].\n\n## Sources\n- https://example.com (2026-05-12)"
    }
  ],
  "notes": "one-line note about what you wrote or why you skipped"
}
```

### Per-source routing

Each source writes to its own flat folder and warrants a different analysis depth. Substitute the row's folder and focus into the task template.

| Source slug | Output folder | What to capture | Depth |
|-------------|---------------|-----------------|-------|
| `mastodon` | `agent/pages/mastodon/` | Your own posts that reveal preferences, opinions, decisions, projects, or recurring themes | Light — analyze inline, no article fetching |
| `linkding` | `agent/pages/bookmarks/` | Bookmarks worth a page | **Heavy** — fetch each notable bookmark's article with `tabstack_extract_markdown` |
| `github` | `agent/pages/github/` | Notable repos, issues, PRs, releases — project activity and direction | Medium — work from the activity summary; fetch a linked page only if essential |
| `pocketcasts` | `agent/pages/podcasts/` | Shows and episodes, especially recurring topics/interests | Light–medium |
| `spotify` | `agent/pages/music/` | Notable artists / listening trends — be **very** selective | Light |
| `youtube` | `agent/pages/youtube/` | Liked videos revealing interests or topics worth tracking | Light |

### Per-source task description

Substitute `{source_slug}`, `{source_path}` (the workspace-relative path from the manifest), `{output_folder}`, and `{focus}` and send verbatim to the child:

```
Analyze recent {source_slug} activity and return a vault-write plan in the structured JSON shape requested by the return_schema. You CANNOT call vault_write — the parent will apply your plan.

Your source data is in a workspace file. Read it first:
  workspace_read("{source_path}")

Focus: {focus}
Output folder: {output_folder} (flat namespace — no subdirectories; use [[wiki-links]] for relationships)

Tools available to you:
- workspace_read(path) — read your source file (above), and re-read ranges if it's large
- vault_search(query) — search the vault for related pages
- vault_read(page) — read an existing vault page
- vault_backlinks(page) — find pages linking to a page
- tabstack_extract_markdown(url) — fetch full content for a URL (use ONLY if your focus says "Heavy"; otherwise work from the source file)

Steps:
1. Read your source file (workspace_read above) and pick the items worth recording. Drop low-signal, ephemeral, or routine items. If the file is empty / reports no activity, or nothing is worth a page, return writes: [] with a one-line notes explaining why.
2. For each item worth keeping, search the vault (vault_search) for a related page. Read strong matches (vault_read) so you extend them rather than create duplicates.
3. Decide the writes:
   - Prefer extending an existing page over creating a new one. If a strong match exists under agent/pages/, return its existing path and the FULL revised content.
   - Otherwise create a new page directly under {output_folder} — flat, no subdirectories.
   - Cap total writes at ~3 per item and ~6 for this source. Mention anything else in notes instead of writing it.
   - Do NOT touch pages outside agent/pages/. If a strong match lives elsewhere (user notes, admin pages), leave it alone and link to it.
4. Each page's content must include:
   - YAML frontmatter — see provenance rules below.
   - A body that synthesizes in your own words (short attributed quotes only) with [[wiki-links]] to related pages.
   - A ## Sources section. NEW pages: list this item's URL + date. UPDATED pages: KEEP existing entries and append the new one.
5. Keep your prose response to one line — the JSON block is what matters.

Frontmatter rules:
- NEW pages — include all three top-level fields:
  - tags: list of topic tags
  - summary: one-line summary
  - sources: YAML list seeded with ONE entry for this item (shape below)
- UPDATED pages WITH existing frontmatter — PRESERVE everything; just APPEND a new entry to the sources list. Don't modify earlier entries (they record when each source was first added). You may refresh summary or extend tags if the new content materially changes them.
- UPDATED pages WITHOUT frontmatter — add a full frontmatter block. Seed sources with just this item. Do NOT backfill historical sources from the body ## Sources section.

sources entry shape:

  - url: https://example.com
    date: 2026-05-12
    added_by: meta-ingest

date is YYYY-MM-DD. If an item has no URL, omit its sources entry and just note it in the body ## Sources section.
```

## Step 3: Apply the writes

`delegate_tasks` returns a `ToolResult` whose `data` field has the shape `{summary: {...}, results: [...]}`. Each entry in `results` is one child: `{index, ok, text, data}`, with `data.writes` and `data.notes` matching the return schema.

Iterate `data.results`. For each entry where `ok` is true, walk that entry's `data.writes` and call `vault_write(page=<page>, content=<content>)` for each item. The child already synthesized and merged with existing content — don't second-guess.

For entries where `ok` is false, note the failure (`entry.error`) in your summary and move on.

If two writes target the same page, the later one wins. Acceptable; mention it in the summary.

## Step 4: Summarize

```
Meta ingest: processed N sources.
Wrote: [[Page A]], [[Page B]], [[Page C]]
Skipped sources: <slugs with no activity>
Failed: <slug — reason, if any>
```

If every source was empty or everything was skipped, start your summary with `HEARTBEAT_OK` on its own line followed by a brief note.

## Rules

- **Children cannot write to the vault.** They have read access (`allow_vault_read: true`); the parent applies all writes.
- **Don't fetch article content in this turn.** That's the linkding child's job — your context stays clean.
- **All writes go under `agent/pages/`.** Children proposing paths elsewhere are buggy; skip those writes and note them.
- **Only ingest the user's OWN content.** Don't reproduce other people's posts without attribution.
- Convert relative dates to absolute (`YYYY-MM-DD`).
- This skill overlaps `linkding-ingest` and `mastodon-ingest`. While all three are active, expect duplicate analysis of the same bookmarks/posts — the vault-merge logic dedupes, but plan to retire the single-source schedules once this one is validated (see SCHEDULE.md).
