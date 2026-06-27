---
name: blog-ideas
description: Daily scheduled brainstorm (also runnable on demand via /blog-ideas) that reviews the week-so-far across ingested activity, the daily journal, and the blog archive, and maintains a living weekly page of blog-post ideas.
effort: strong
required-skills:
  - vault
user-invocable: true
context: inline
---

# Blog-idea brainstorm

Once a day, review the week so far across everything I've been reading, writing,
and thinking, and maintain a single living page of blog-post ideas for the week.
The page accumulates and sharpens across the week; you rewrite it in place each
run. **Write** only within `agent/` (the living page is the only thing you
write); **read broadly** across the vault as Phase 2 enumerates — `journals/`,
`blog/`, and `agent/pages/` are all in scope for reading.

## Phase 1: Orient

1. Call `blog_ideas_week` to get this week's `week` key, `days_so_far`, and the
   `page_path`. NEVER compute the ISO week yourself.
2. `vault_read` the `page_path`. If it exists, this is the living page you'll
   refine — it's also your within-week dedup memory; note its `updated:` date
   (the watermark for incremental reads in Phase 2). If it doesn't exist yet,
   this is the week's first run and you'll create it.
3. Call `blog_ideas_week` with `offset_weeks=-1` and `offset_weeks=-2` and
   `vault_read` those pages if present — just enough to notice themes I keep
   circling week to week.
4. `vault_read agent/pages/blog-ideas/parking-lot.md` if it exists — the rolling
   set of seeds from the last ~month that haven't ripened. Consider these
   alongside this week's material: a parked seed that connects to something new
   can finally graduate into a headliner.

## Phase 2: Gather

The goal is to read **everything from this week**, not to search for the words
"blog ideas." `vault_recent` is the right tool — it lists what changed by date.
Do **NOT** use `vault_search` to enumerate: it requires a content query and only
returns pages containing that exact text, so it misses almost everything (it's
for the dedup check in step 4 only).

1. **Everything that changed this week** — call `vault_recent` with
   `days=<days_so_far>` (from `blog_ideas_week`). It returns every vault page
   modified this week, newest first, each with its modified date and
   `source_type`: across all my ingest + distilled sources (`agent/pages/…`:
   bookmarks, mastodon, youtube, github, podcasts, music, kindle, plus topical
   pages) **and** my Obsidian daily journal (`journals/…`, `source_type=user`).
   Ignore `agent/pages/notifications/…` (system noise) and the
   `agent/pages/blog-ideas/…` pages themselves.
2. `vault_read` the entries worth considering. ALL of it is fair game: ingested
   bookmarks/posts/podcasts/videos/repos, distilled topical pages
   (`source_type=page`), and my daily-journal notes (`source_type=user` — TIL
   bullets, session notes, offhand musings; `*(blog candidate)*` is a strong
   signal but not the only material). These pages are already distilled
   summaries — read them directly, don't re-fetch source URLs.
   - **Read incrementally.** On the week's first run (no existing page), read
     everything dated on/after `monday`. On later runs, only `vault_read`
     entries modified **after** the page's `updated:` date — the living page
     already captures everything earlier this week, so trust it and merge the
     new findings in. This keeps daily runs cheap instead of re-reading the
     whole week every time.
3. **Stalled drafts** — `vault_list folder=blog/drafts` and `vault_read` any
   whose title relates to this week's themes. When fresh material supplies the
   missing piece for an abandoned draft, that's a high-value idea.
4. **Published archive** (on demand) — here, and only here, use `vault_search`
   with a topic query against `blog/daily` and `blog/weeknotes` to check a
   candidate: skip near-duplicates of things I've already written; frame genuine
   extensions as follow-ups; keep angles in my actual voice.

Keep reads focused: enumerate broadly, but only `vault_read` this week's entries
plus the handful of drafts/posts tied to an idea you're actively developing.

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

Keep 3–5 headliners, **ordered strongest first** — headliner #1 is the week's
lead (a downstream consumer like the weeknotes composer treats it that way).
Surface cross-week repeats under **Recurring** as signal — do NOT re-pitch them
cold in Headliners.

Keep this page structure **stable and parseable**: the section headings
(`Headliners` / `Seeds` / `Recurring`) and the headliner field set (hook title,
`angle`, `sources`, `shape`, `status`) are a contract other skills read — don't
rename or drop them.

## Phase 5: Tend the parking lot

The parking lot (`agent/pages/blog-ideas/parking-lot.md`, under `agent/` so you
can write it) is how **slow-burning themes** survive — ideas that develop across
notes and bookmarks over a week or two before they're clearly worth writing.
A single week rarely makes a trend obvious; this is where evidence accumulates.
After updating the weekly page, `vault_write` the parking lot:

- **Reinforce:** if this week's material adds evidence to a parked theme, append
  the new sources and bump its `reinforced:` date. Accumulating evidence is the
  signal it's ripening.
- **Graduate:** when a parked theme has gathered enough to be worth writing,
  promote it to a Headliner on this week's page and remove it from the lot.
- **Add:** park this week's genuinely-new seeds that didn't become headliners,
  each tagged `seen: <today>`.
- **Prune:** drop entries that haven't gained traction in **~1 month** (≈4 weeks
  since `seen`/`reinforced`) — if it hasn't ripened in a month, let it go. If
  one is *still actively accumulating* at the month mark, keep it and flag it as
  a persistent slow-burn rather than dropping it silently.

Entry shape (keep the page a simple list under a short header):

    - <theme / seed one-liner> — sources: [[a]], [[b]], journal <date>
      (seen <YYYY-MM-DD>, reinforced <YYYY-MM-DD>)

## Finishing up

End with a short narrative summary that doubles as the newsletter's raw material:
the headliner titles, a one-line hook each, and a link to the full
`[[<page_path without the .md>]]`. If there was nothing new worth surfacing this
run, begin your summary with `HEARTBEAT_OK` on its own line followed by a brief
quiet-cycle note.
