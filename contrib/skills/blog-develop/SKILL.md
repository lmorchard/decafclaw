---
name: blog-develop
description: On-demand workflow (/blog-develop <idea>) that develops any blog-post idea into a take-or-leave first draft — scout → interview → deep research → draft, with research and drafting run as child agents.
effort: strong
required-skills:
  - vault
  - tabstack
user-invocable: true
context: inline
allowed-tools: delegate_task, vault_read, vault_write, vault_list, vault_search, current_time, tabstack_research, tabstack_extract_markdown, web_fetch
---

# Develop a blog idea

Take **any** blog-post idea and develop it into a *take-or-leave first draft*:
**scout → interview → deep research → draft**. The idea can come from your
`blog-ideas` weekly page or from nowhere in particular — this skill does not
depend on it.

You (the orchestrator) run in this conversation and drive the phases. The heavy
phases (scout, deep research, draft) run as **child agents** via `delegate_task`,
so web-search noise and long source text stay in the children and never clog
this conversation. **Children CANNOT write to the vault** — they return
structured results and *you* write the final draft. (Best run in a fresh
conversation so the interview starts from a clean slate.)

You MUST run the scout, research, and draft work through `delegate_task`. Do
**NOT** do the web research or write the draft yourself in this conversation —
delegating the heavy work is what keeps this context clean and the workflow
reliable.

## The idea

The seed idea is `$ARGUMENTS`. If it is empty, ask the user **"What do you want
to write about?"**, end your turn, and wait. (They may paste a headliner from
their `blog-ideas` page, or anything else.) Once you have an idea, continue.

## Phase 1: Scout (child agent)

Get a quick lay-of-the-land so your interview questions are sharp. Call
`delegate_task` with `allow_vault_read=true` (so the scout can check whether the
user has already written about this) and this `return_schema`:

```json
{
  "summary": "2-3 sentences on the current lay of the land for this idea",
  "prior_art": [{"title": "...", "url": "...", "angle": "what it argues"}],
  "open_angles": ["under-explored angle or gap", "..."],
  "suggested_questions": ["a sharp interview question this surfaced", "..."],
  "already_written": "note if the user's own vault/blog already covers this, else null"
}
```

Scout task prompt (substitute `{idea}`):

```
You are a research SCOUT. Do a SHALLOW first pass on this blog-post idea so the
author can be interviewed well. Idea: "{idea}"

Use tabstack_research (and tabstack_extract_markdown only if one source is
clearly essential) for a quick survey — do NOT go deep. Also vault_search /
vault_read to see if the author has already written about this.

Return the structured JSON requested by return_schema: a short lay-of-the-land,
notable prior art, under-explored angles, a few sharp interview questions, and
whether the author's own vault already covers it. Keep prose to one line.
```

## Phase 2: Interview (you + the user)

Using the scout brief, interview the author to shape the piece. **Ask one question per turn**, end your turn, and wait for the answer before the next
question. Adapt each question to the prior answer. Ask **at most 5–6**; stop
early if the author says "go" / "draft it."

Cover, roughly in this order, skipping anything already obvious from the idea or
scout:
- **Angle / thesis** — what is the core claim or point of view?
- **Audience** — who is this for?
- **Prior art** — engage or differentiate from what the scout found?
- **Scope** — how deep/long; what to include vs. leave out?
- **Voice / tone** — anything specific for this piece?

Do not start the deep research until the interview is done.

When the interview is done, compose a tight `interview_summary` for yourself — a
few sentences digesting the angle, audience, scope, and voice the author chose
(a digest, **not** a transcript dump). You substitute this into the Phase 3 and
Phase 4 task prompts below.

## Phase 3: Deep research (child agent)

Now run the real research, steered by the idea + interview answers. Call
`delegate_task` with `allow_vault_read=true` and this `return_schema`:

```json
{
  "thesis_support": ["key claim or fact, with enough context to use it"],
  "sources": [{"title": "...", "url": "...", "takeaway": "why it matters here"}],
  "counterpoints": ["objection or counter-argument the draft should address"],
  "angles": ["framing / structure suggestion"],
  "notes": "anything else useful for drafting"
}
```

Deep-research task prompt (substitute `{idea}` and `{interview_summary}` — a
tight digest of the angle, audience, scope, and voice the author chose):

```
You are a DEEP RESEARCHER for a blog post. Idea: "{idea}"
Author's direction from the interview: {interview_summary}

Use tabstack_research for targeted searches and tabstack_extract_markdown /
web_fetch to read the most relevant sources. Gather what's needed to write a
credible, well-sourced post on the author's chosen angle. vault_read related
pages the author has written for continuity of voice/claims.

Return the structured JSON requested by return_schema: supporting claims/facts,
sources with URLs and takeaways, counterpoints to address, and framing
suggestions. Capture real URLs — do not invent them. Keep prose to one line.
```

## Phase 4: Draft (child agent)

First, locate the author's voice guide: `vault_search` for their voice/tone
page (try queries like "voice and tone", "writing voice", "style"). Note its
path — you'll hand it to the drafter.

Call `delegate_task` with `allow_vault_read=true` and this `return_schema`:

```json
{
  "title": "post title",
  "slug": "kebab-case-slug-from-the-title",
  "draft_markdown": "the full first-draft body in the author's voice",
  "sources_used": [{"title": "...", "url": "..."}]
}
```

When you build the draft prompt, paste the Phase 3 research-brief JSON verbatim
where `{research_brief_json}` appears — embed it as a literal block exactly as
the worker returned it; do not paraphrase, summarize, or hand-edit it.

Draft task prompt (substitute `{idea}`, `{interview_summary}`, `{voice_path}`,
and `{research_brief_json}` — the Phase 3 JSON):

```
You are a DRAFTER writing a blog post in the AUTHOR'S voice. Idea: "{idea}"
Author's direction: {interview_summary}

First read the author's voice guide so you match their style:
  vault_read("{voice_path}")
(If that path is empty or missing, infer voice from a recent post under blog/.)

Use this research brief — cite its sources, address its counterpoints:
{research_brief_json}

Write a complete FIRST DRAFT (it's a starting point, not a final post). Use the
author's voice, a clear structure, and real links from the brief. Do NOT invent
sources or quotes.

Return the structured JSON requested by return_schema: title, kebab-case slug,
the full draft_markdown, and the sources you used. Keep prose to one line.
```

## Phase 5: Write the draft (you)

Children can't write the vault, so you do it. `vault_write` to
`blog/drafts/<slug>.md` (use the drafter's `slug`; if missing, slugify the
title) with this structure:

```markdown
---
title: <title>
tags: [blog-draft]
status: draft
created: <today's date, YYYY-MM-DD — get it from current_time>
seed: <the original idea>
sources:
  - <url>
  - <url>
---

<draft_markdown>

## Research notes
<a compact digest of the Phase 3 research brief: key claims, sources with URLs,
counterpoints — so the sources stay with the draft for later development>
```

## Finishing up

End with a short summary: the title, a one-line hook, and a link to
`[[blog/drafts/<slug>]]`. State plainly that it's a *take-or-leave first draft*
and suggest one or two next steps for developing it.

## Rules

- **Children cannot write the vault.** Workers return structured results; you
  apply the single `vault_write`.
- **Delegate the heavy work.** Scout, deep research, and drafting MUST go
  through `delegate_task` — never research the web or write the draft inline in
  this turn.
- **One interview question per turn.** Ask, end the turn, wait. Cap ~5–6.
- **No publishing.** Output is a disposable draft in `blog/drafts/`. Do NOT
  commit, deploy, or touch the live blog. Do NOT modify the `blog-ideas` page.
- **Real sources only.** Never invent URLs or quotes. Convert relative dates to
  absolute (`YYYY-MM-DD`).
