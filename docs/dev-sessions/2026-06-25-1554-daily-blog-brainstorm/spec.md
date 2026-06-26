# Spec — Daily blog-idea brainstorm skill

**Date:** 2026-06-25
**Branch:** `feat-blog-ideas-skill`
**Status:** Design approved; pending spec review → implementation plan.

## Problem

Les ingests a lot daily — bookmarks, Mastodon posts, liked YouTube videos, GitHub
activity, podcasts, Kindle highlights — and also keeps a daily Obsidian journal of rough
notes, plus a backlog of published posts and stalled drafts. There's no routine that
steps back across all of it and asks: *given what's been on my mind lately, what's worth
writing about?* The raw material for blog posts accumulates and goes cold.

## Goal

A scheduled skill that, once a day, reviews the week-so-far across all of Les's
content sources and maintains a living "blog ideas for this week" page — a small set of
developed ideas plus a list of lighter seeds — that sharpens as the week fills in. The
daily newsletter surfaces it so Les gets nudged to go read the page as it shapes up.

## Key insight: zero new ingest plumbing

Everything the skill needs already lives in one indexed vault on the deployment:

- **External activity** is pulled in by the existing `meta-ingest` skill (Mastodon,
  Linkding bookmarks, YouTube, GitHub, Pocket Casts, Spotify) → `agent/pages/{mastodon,
  bookmarks,youtube,github,podcasts,music}/`, plus **Kindle highlights** from the separate
  `kindle` skill → `agent/pages/kindle/`.
- **The daily journal** is Les's Obsidian vault, synced to the deployment and configured
  as decafclaw's `vault_path` (`obsidian/main/`). Journal entries live at `journals/`.
- **The blog archive** is in the same vault: `blog/drafts/` (stalled drafts),
  `blog/daily/` and `blog/weeknotes/` (published posts).

All are reachable through the normal `vault_search` / `vault_read` / `vault_list` tools.
The skill is therefore **almost prompt-only** (`SKILL.md` + `SCHEDULE.md`), mirroring
`dream` / `garden`, with **one small `tools.py`** carrying a single deterministic helper.

### Why the one helper tool

`current_time` returns a plain timestamp (`%Y-%m-%d %H:%M:%S (%A)`) with no ISO-week
number. The living-page model hinges on a correct, stable ISO-week key: a wrong key means
the daily run can't find yesterday's page and silently creates a duplicate instead of
refining it. ISO-week math (and "N weeks ago" for the cross-week lookback) is fiddly and
year-boundary-prone — exactly the deterministic mechanic that belongs in code, not in the
LLM. So the skill ships a single helper, `blog_ideas_week(offset_weeks=0)`, returning the
week key, that week's Monday, days-so-far, and the page path. The LLM never does week math;
page identity is guaranteed. (This is a deliberate, narrow deviation from the pure-prompt
`dream`/`garden` shape, approved during planning.)

## Requirements

### Inputs (all via existing vault tools)

| Source | Vault location | Role | Read depth |
|--------|----------------|------|-----------|
| External ingests | `agent/pages/{bookmarks,mastodon,youtube,github,podcasts,music,kindle}/` | Idea fuel (peer to journal) | Search snippets; full read only for an idea being developed |
| Daily journal | `journals/` | Idea source (peer to ingests); **all** sections fair game, `*(blog candidate)*`-flagged notes weighted higher | Snippets + targeted reads |
| Stalled drafts | `blog/drafts/` | Resurface when this week's material connects to an abandoned draft | Read matched drafts |
| Published posts | `blog/daily/`, `blog/weeknotes/` | Dedup (skip already-covered), continuity (spot follow-ups), voice grounding | On-demand search; read only to verify a candidate |

### Cadence & window

- Runs **daily**, early morning, **before** the newsletter's 07:00 UTC compose. Target
  `0 6 * * *` (06:00 UTC).
- Each run covers the **week so far** — an accumulating window from Monday 00:00 through
  now. Monday's run sees ~1 day; Sunday's sees the full week.
- Model: `strong` (heavy synthesis, matches `dream` / `garden`).
- `required-skills: [vault]`.

### Synthesis behavior

- Journal notes and external ingests are **equal peers** — an idea may originate from a
  striking bookmark with no journal note behind it, or from a journal half-thought.
- Connect across sources (a journal note + a bookmark + a stalled draft → one idea).
- Match this week's material against `blog/drafts/`; surface a stalled draft when fresh
  material supplies its missing piece.
- Check a candidate against published posts before promoting it: skip near-duplicates,
  frame genuine extensions as follow-ups.

### Output: a living weekly page

- Path: `agent/pages/blog-ideas/{ISO-week}.md` (e.g. `agent/pages/blog-ideas/2026-W26.md`).
  Writes under `agent/` to avoid the `vault_write` out-of-agent confirmation gate (a
  scheduled run can't satisfy interactive confirmation). One page per ISO week.
- **Rewritten in place** each day (overwrite, not append) — the page *is* the durable
  within-week state and dedup memory.
- Tiered structure:
  - **Headliners** — 3–5 developed ideas (angle, sources, rough shape, status). High bar.
  - **Seeds** — lightweight bullets, half-formed; may graduate to headliners or fade.
  - **Recurring** — themes circled across recent weeks, surfaced as signal ("you've
    returned to X three weeks running — maybe it's ripe"), not re-pitched cold.
- Frontmatter: `tags`, `summary`, `week`, `updated`.

Page shape:

```markdown
---
tags: [blog-ideas, brainstorm]
summary: Blog post ideas for the week of 2026-06-22 (W26)
week: 2026-W26
updated: 2026-06-25
---

# Blog ideas — week of 2026-06-22

## Headliners
### <hook title>
- **angle:** why this, why now
- **sources:** [[bookmark-x]], journal 06-23, connects to draft "handwriting recognition"
- **shape:** outline bullets / the missing piece
- **status:** new | sharpened 06-25 | follow-up to [published post]

## Seeds
- <one-liner> — source hint

## Recurring
- <theme> — surfaced ~3 weeks running; maybe it's ripe
```

### Cross-week memory

- Glance at the last ~2–3 `agent/pages/blog-ideas/*` pages.
- Pitch genuinely new ideas; for ideas Les keeps circling, surface them under
  **Recurring** as signal rather than re-listing them cold. Don't hard-suppress —
  a ripening idea may finally be ready.

### Newsletter delivery (no newsletter changes)

- The newsletter already aggregates **all** scheduled-task activity generically via
  `newsletter_list_scheduled_activity` (which exposes each run's `final_message` and
  `vault_pages_touched`) and `newsletter_list_vault_changes` (mtime). A scheduled skill
  contributes simply by running and ending with a good summary. **No edits to the
  newsletter skill.**
- **Design lever — the final summary message.** On a productive day the run ends with a
  compact digest: headliner titles + a one-line hook each + a link to the full
  `[[agent/pages/blog-ideas/{week}]]` page. That rich `final_message` is what the
  newsletter narrates from, giving a good "blog ideas" presence in the email and the
  nudge to go read the living page. Quiet days emit `HEARTBEAT_OK` and the newsletter
  skips them.
- Timing: brainstorm 06:00 → its conversation is well inside the newsletter's lookback
  at 07:00.
- Deferred (YAGNI): a guaranteed fixed-format "Blog ideas this week" section would be a
  small newsletter-prompt nudge later; not in scope now. Keeps the two skills decoupled.

## Architecture

Single scheduled agent turn (`dream`-style), no fan-out, no workflow engine. The core
value is **cross-source synthesis**, which wants all material in one reasoning context;
fan-out (meta-ingest-style) would fragment exactly the connection-making that's the
point, and the workflow engine's durability/suspend-resume buys nothing for a daily
idempotent page refresh (the living page already is the durable state).

Context stays bounded because reads default to search **snippets** (meta-ingest already
did the heavy article fetching upstream); full-body `vault_read` happens only for the
handful of drafts/posts tied to a candidate being actively developed.

### Daily run algorithm (encoded in the prompt)

1. Establish current ISO week → target page path; compute week-so-far window (days since
   Monday, min 1).
2. Gather as snippets: `vault_search` over the ingest folders (bookmarks, mastodon,
   youtube, github, podcasts, music, kindle; `days=<window>`); journal
   over `journals/` (all sections, blog-flagged weighted); `vault_list` `blog/drafts/` +
   targeted search to match stalled drafts; published archive on-demand for dedup/continuity.
3. Read the current weekly page (running state + within-week dedup memory).
4. Glance at recent prior-week pages for recurring themes.
5. Synthesize: peers (journal + ingests), connect across sources, match drafts, dedup vs
   published.
6. Rewrite the weekly page in place — refine/merge headliners, graduate/add seeds, demote
   faded ones, refresh `updated` and the Recurring note.
7. End with the summary: productive day → digest + page link; quiet day → `HEARTBEAT_OK`.

### Naming

Working name `blog-ideas` (sibling to `dream` / `garden` / `newsletter`). `muse` is an
alternative that fits the evocative-name family; clarity-first convention favors
`blog-ideas`. Final name decided at implementation.

### Enablement

> **Relocated to contrib (post-implementation).** The skill lives at
> `contrib/skills/blog-ideas/`, not as a core bundled skill — it's personal and
> vault-layout-specific. Consequences: it's discovered only when the deployment's
> `extra_skill_paths` includes `$CONTRIB/skills/blog-ideas`; its `SCHEDULE.md` is
> **force-disabled** (contrib skills don't auto-activate cron), so the daily run is
> opted into via a copy-on-write overlay at `data/{agent_id}/schedules/blog-ideas.md`
> (copy the SCHEDULE.md, set `enabled: true`). The `/blog-ideas` command works on demand
> regardless. The behavior eval was dropped (a contrib skill isn't on the default eval
> skill path); validation is the colocated importlib unit tests + the manual deployment
> smoke. See `notes.md`.

## Testing & validation

- **Eval (LLM-visible):** one behavior case in `evals/blog-ideas.yaml` — an explicit run
  (the scheduled/`​/blog-ideas` invocation) computes the week via `blog_ideas_week` and
  writes the weekly page under `agent/pages/blog-ideas/`. Bound with `max_tool_calls` /
  `max_tool_errors` (the latter intentionally allows the expected "page not found" reads on
  a fresh week). *Natural-language routing is explicitly not tested or pursued* — see
  Non-goals.
- **Unit (deterministic, no LLM):** the `blog_ideas_week` helper — ISO-week key, Monday
  date, days-so-far, and page path, including year-boundary and prior-week (`offset_weeks`)
  cases — is a pure function with cheap, exhaustive tests.
- **Real-data smoke:** trigger the skill manually (user-invocable) once against the
  deployment's actual vault (real week of ingests + journal + drafts) and eyeball the
  page — this is where output *quality* is judged, which no unit test covers. Manual
  trigger before letting cron run it.

## Non-goals / out of scope

- No new ingest sources or fetch code (meta-ingest + sync already cover everything,
  including YouTube).
- No newsletter skill modifications.
- No dedicated blog-ideas email (deferred).
- No workflow-engine or fan-out architecture.
- Writing the posts themselves — this surfaces and shapes ideas; Les writes.
- **No natural-language-question routing.** The skill serves the daily scheduled run and
  the explicit `/blog-ideas` command (same workflow); it deliberately does *not* try to
  catch casual "what should I write about?" phrasings or special-case question-vs-command
  behavior. Keeping that out avoids over-loading the skill and removes any risk of the
  scheduled prompt being misclassified and silently skipped. (A routing experiment that
  touched `context_composer.py`'s preempt-hint scoring was reverted and filed separately.)

## Open questions

- **Output folder:** default `agent/pages/blog-ideas/` (no write gate). If Les prefers it
  under `blog/ideas/` next to his drafts (more discoverable in his Obsidian blog
  workflow), that needs a one-line entry in the `vault_write` write-allowlist. Decide at
  implementation.
- **Skill name:** `blog-ideas` vs `muse`.
- **Verify at build time:** that `journals/` entries surface as `source_type=user` in
  `vault_search` (vs. needing a folder filter) on the live deployment.
