---
name: blog-ideas
description: Daily scheduled brainstorm (also runnable on demand via /blog-ideas) that reviews the week-so-far across ingested activity, the daily journal, and the blog archive, and maintains a living weekly page of blog-post ideas.
effort: strong
required-skills:
  - vault
user-invocable: true
context: fork
---

# Blog-idea brainstorm

Once a day, review the week so far across everything I've been reading, writing,
and thinking, and maintain a single living page of blog-post ideas for the week.
The page accumulates and sharpens across the week; you rewrite it in place each
run. Read and write only within `agent/`.

## Phase 1: Orient

1. Call `blog_ideas_week` to get this week's `week` key, `days_so_far`, and the
   `page_path`. NEVER compute the ISO week yourself.
2. `vault_read` the `page_path`. If it exists, this is the living page you'll
   refine — it's also your within-week dedup memory. If it doesn't exist yet,
   you'll create it this run.
3. Call `blog_ideas_week` with `offset_weeks=-1` and `offset_weeks=-2` and
   `vault_read` those pages if present — just enough to notice themes I keep
   circling week to week.

## Phase 2: Gather (read at snippet level — do NOT bulk-read article bodies)

`vault_search`'s `days=N` is a **rolling** lookback (`now − N days`), NOT a
calendar-week boundary. Use `days=days_so_far` to be sure you cover the whole
week so far — but that window reaches a little into last week, so when you read
results, **disregard anything dated before `monday`** (the Monday returned by
`blog_ideas_week`). That keeps the brainstorm to this week's material and avoids
re-surfacing last week's items.

1. **Ingested activity** — `vault_search` across the ingest folders with
   `days=<days_so_far>`: `agent/pages/bookmarks`, `agent/pages/mastodon`,
   `agent/pages/youtube`, `agent/pages/github`, `agent/pages/podcasts`,
   `agent/pages/music`, `agent/pages/kindle`. These are things I consumed.
2. **Journal** — `vault_search` with `source_type=user` over my daily journal
   (`journals/…`) for the same window. ALL of it is fair game — TIL bullets,
   session notes, offhand musings. Notes tagged `*(blog candidate)*` are a
   stronger signal but are not the only material.
3. **Stalled drafts** — `vault_list` `blog/drafts` and `vault_search` it against
   this week's themes. When fresh material supplies the missing piece for an
   abandoned draft, that's a high-value idea.
4. **Published archive** (on demand) — `vault_search` `blog/daily` and
   `blog/weeknotes` only to check a candidate: skip near-duplicates of things
   I've already written; frame genuine extensions as follow-ups; keep angles in
   my actual voice.

Read full bodies (`vault_read`) only for the handful of drafts/posts tied to an
idea you're actively developing.

## Phase 3: Synthesize

- Treat journal notes and ingested activity as EQUAL peers — an idea may spring
  from a striking bookmark with no journal note behind it, or from a half-thought
  in the journal.
- Connect across sources: a journal note + a bookmark + a stalled draft can be
  one idea.
- Hold a high bar for headliners; everything else is a seed.

## Phase 4: Write the living page

`vault_write` the `page_path` with this structure (rewrite in place — refine and
merge existing entries, add new ones only if they clear the bar, demote faded
ones; do NOT just append):

    ---
    tags: [blog-ideas, brainstorm]
    summary: Blog post ideas for the week of <monday> (<week>)
    week: <week>
    updated: <today's date>
    ---

    # Blog ideas — week of <monday>

    ## Headliners
    ### <hook title>
    - **angle:** why this, why now
    - **sources:** [[page]], journal <date>, connects to draft "<name>"
    - **shape:** outline bullets / the missing piece
    - **status:** new | sharpened <date> | follow-up to <published post>

    ## Seeds
    - <one-liner> — source hint

    ## Recurring
    - <theme> — circled ~N weeks running; maybe it's ripe

Keep 3–5 headliners. Surface cross-week repeats under **Recurring** as signal —
do NOT re-pitch them cold in Headliners.

## Finishing up

End with a short narrative summary that doubles as the newsletter's raw material:
the headliner titles, a one-line hook each, and a link to the full
`[[<page_path without the .md>]]`. If there was nothing new worth surfacing this
run, begin your summary with `HEARTBEAT_OK` on its own line followed by a brief
quiet-cycle note.
