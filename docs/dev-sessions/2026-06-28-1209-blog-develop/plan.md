# blog-develop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an on-demand contrib skill `/blog-develop <idea>` that develops any blog-post idea into a take-or-leave first draft via scout → interview → deep research → draft, with research and drafting run as child agents.

**Architecture:** A pure prose **SKILL.md** orchestration skill (no `tools.py`) modeled on `contrib/skills/meta-ingest`. The forked command agent owns control flow: it dispatches `delegate_task` workers for the scout, deep-research, and draft phases (each returning structured JSON via `return_schema`), runs a human interview in between as ordinary multi-turn conversation, and — because **children cannot write the vault** — performs the final `vault_write` itself. Reliability rests on the human-driven interview plus bounded persona-workers, not on the LLM cranking a long-horizon state machine.

> **Correction (during implementation):** this plan was written for `context: fork`.
> Final review found `fork` runs the orchestrator through `run_child_turn`, which
> strips `delegate_task` + vault tools and is a one-shot child turn that cannot run
> the multi-turn interview. **The skill ships as `context: inline`** (full tool set,
> interview works, heavy work still isolated in `delegate_task` children). Wherever
> a task snippet below says `context: fork` / `assert meta["context"] == "fork"`,
> read it as `inline`. The implemented files are correct.

**Tech Stack:** DecafClaw skills system (`SKILL.md` frontmatter: `user-invocable`, `context: fork`, `required-skills`, `allowed-tools`), `delegate_task` (`return_schema`, `allow_vault_read`), tabstack web tools (`tabstack_research`, `tabstack_extract_markdown`), `web_fetch`, vault read/write tools. pytest for the structural/loader test; an evals YAML case for the structural guard.

---

## Background the implementer needs

- **Spec:** `docs/dev-sessions/2026-06-28-1209-blog-develop/spec.md`. Read it first.
- **Template to mirror:** `contrib/skills/meta-ingest/SKILL.md` — orchestrator preps, delegates workers with `allow_vault_read` + `return_schema` + per-worker task templates, then applies vault writes itself, then summarizes. Copy its *shape* and its prominent "**Children CANNOT write to the vault**" framing. We differ in two ways: (1) `delegate_task` singular/sequential, not `delegate_tasks` parallel; (2) a human interview phase sits between scout and deep-research.
- **`delegate_task` facts** (`src/decafclaw/tools/delegate.py`): `priority: critical` so it's always available; children inherit the parent's activated-skill tools (so tabstack reaches the worker); `allow_vault_read=true` opts a child into vault *read* tools; vault *write* tools are categorically blocked for children; `return_schema` makes the child emit a fenced JSON block parsed onto `ToolResult.data` (a hint, not enforced — parse failure falls back to prose).
- **`allowed-tools` constrains children too** (`delegate.py` ~line 233: child tools are intersected with `parent_ctx.tools.allowed`). So every tool a worker needs MUST appear in the command's `allowed-tools`.
- **Contrib skill conventions:** `contrib/skills/README.md`. Each skill is a directory with `SKILL.md` (+ optional `README.md`). Installed by adding `$CONTRIB/skills/<name>` to an agent's `extra_skill_paths`.
- **Voice docs are not at a known path** (spec open item). Resolve at runtime: the skill instructs the agent to `vault_search` for the user's voice/tone page and pass its path to the drafter with `allow_vault_read=true`. No hardcoded path.

## File structure

- Create: `contrib/skills/blog-develop/SKILL.md` — the entire workflow (orchestration prose + worker task templates + return schemas + output contract).
- Create: `contrib/skills/blog-develop/README.md` — one-paragraph what-it-is + install snippet (mirror a short contrib README).
- Create: `tests/test_blog_develop_skill.py` — structural/loader test guarding frontmatter + body contract strings against rot.
- Modify: `contrib/skills/README.md` — add `blog-develop` to the skill listing (if it enumerates skills).
- Create/Modify: an evals YAML case — structural guard that invoking the skill delegates the scout rather than researching inline.

---

## Task 1: Scaffold the skill directory + failing structural test

**Files:**
- Test: `tests/test_blog_develop_skill.py`
- Create: `contrib/skills/blog-develop/SKILL.md`

- [ ] **Step 1: Write the failing structural test**

```python
"""Structural guard for the blog-develop contrib skill.

The skill is prose (no tools.py), so this is the automated rot-guard:
it asserts the frontmatter contract and that the body still documents
the load-bearing rules (delegate the workers, children can't write the
vault, one-question-at-a-time interview, blog/drafts output). Prompt
quality itself is validated by live smoke testing, not here.
"""

from pathlib import Path

from decafclaw.skills import validate_skill_md

SKILL = (
    Path(__file__).resolve().parents[1]
    / "contrib" / "skills" / "blog-develop" / "SKILL.md"
)


def test_blog_develop_frontmatter_contract():
    result = validate_skill_md(SKILL)
    assert result.ok is True, result.first_failure
    meta = result.meta
    assert meta["name"] == "blog-develop"
    assert meta["user-invocable"] is True
    assert meta["context"] == "fork"
    assert "vault" in meta["required-skills"]
    assert "tabstack" in meta["required-skills"]
    # Every tool a worker needs must be in allowed-tools (it constrains
    # children too). delegate_task + research + vault read/write.
    allowed = meta["allowed-tools"]
    for tool in ("delegate_task", "tabstack_research", "vault_write", "vault_read"):
        assert tool in allowed, f"{tool} missing from allowed-tools"


def test_blog_develop_body_documents_contract():
    body = validate_skill_md(SKILL).body.lower()
    # The reliability-critical rules must stay in the prose.
    assert "delegate_task" in body
    assert "cannot write" in body            # children can't write the vault
    assert "blog/drafts" in body             # output location
    assert "one question" in body            # interview discipline
    for phase in ("scout", "interview", "research", "draft"):
        assert phase in body
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_blog_develop_skill.py -q`
Expected: FAIL — `validate_skill_md` errors / file not found (SKILL.md doesn't exist yet).

- [ ] **Step 3: Create a minimal valid SKILL.md so the frontmatter test passes**

Create `contrib/skills/blog-develop/SKILL.md` with the frontmatter and a stub body (full body comes in Task 2):

```markdown
---
name: blog-develop
description: On-demand workflow (/blog-develop <idea>) that develops any blog-post idea into a take-or-leave first draft — scout → interview → deep research → draft, with research and drafting run as child agents.
effort: strong
required-skills:
  - vault
  - tabstack
user-invocable: true
context: fork
allowed-tools: delegate_task, vault_read, vault_write, vault_list, vault_search, current_time, tabstack_research, tabstack_extract_markdown, web_fetch
---

# Develop a blog idea

Stub. Phases: scout, interview, research, draft. Workers run via
delegate_task and CANNOT write to the vault — you write the draft to
blog/drafts yourself. Interview asks one question per turn.
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_blog_develop_skill.py -q`
Expected: PASS (both tests — the stub body already contains the required strings).

- [ ] **Step 5: Commit**

```bash
git add tests/test_blog_develop_skill.py contrib/skills/blog-develop/SKILL.md
git commit -m "feat(blog-develop): scaffold skill + structural guard test"
```

---

## Task 2: Author the full SKILL.md orchestration

Replace the stub body (keep the frontmatter from Task 1) with the full workflow below. This is the deliverable; the test from Task 1 keeps guarding it.

- [ ] **Step 1: Write the full body**

Set `contrib/skills/blog-develop/SKILL.md` body (everything after the frontmatter `---`) to:

````markdown
# Develop a blog idea

Take **any** blog-post idea and develop it into a *take-or-leave first draft*:
**scout → interview → deep research → draft**. The idea can come from your
`blog-ideas` weekly page or from nowhere in particular — this skill does not
depend on it.

The heavy phases (scout, deep research, draft) run as **child agents** via
`delegate_task`, so web-search noise and long source text never clog this
context. **Children CANNOT write to the vault** — they return structured
results and *you* (this orchestrator) write the final draft.

You MUST run the scout, research, and draft work through `delegate_task`. Do
**NOT** do the web research or write the draft inline in this turn — keeping
this context clean is what makes the workflow reliable.

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

Using the scout brief, interview the author to shape the piece. **Ask ONE
question per turn**, end your turn, and wait for the answer before the next
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
````

- [ ] **Step 2: Run the structural test to confirm the full body still satisfies the contract**

Run: `uv run pytest tests/test_blog_develop_skill.py -q`
Expected: PASS.

- [ ] **Step 3: Lint**

Run: `make lint`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add contrib/skills/blog-develop/SKILL.md
git commit -m "feat(blog-develop): full scout/interview/research/draft orchestration"
```

---

## Task 3: README + contrib skill listing

**Files:**
- Create: `contrib/skills/blog-develop/README.md`
- Modify: `contrib/skills/README.md`

- [ ] **Step 1: Write the skill README**

Create `contrib/skills/blog-develop/README.md`:

```markdown
# blog-develop

On-demand workflow that develops **any** blog-post idea into a take-or-leave
first draft: **scout → interview → deep research → draft**. Sibling to
`blog-ideas`, but decoupled — the idea can come from your weekly ideas page or
from anywhere.

Run it with `/blog-develop <your idea>` (web UI) or `!blog-develop <your idea>`
(Mattermost). With no argument it asks what you want to write about. The scout,
research, and draft phases run as child agents; the finished draft lands in
`blog/drafts/<slug>.md` in your vault for you to take or leave.

## Install

Add to your agent's `extra_skill_paths` in `data/{agent_id}/config.json`:

```json
{
  "extra_skill_paths": [
    "$CONTRIB/skills/blog-develop"
  ]
}
```

Requires the `tabstack` skill configured (web research) and a vault. No extra
binaries or API keys beyond what `tabstack` needs.
```

- [ ] **Step 2: Add blog-develop to the contrib skill listing**

Inspect `contrib/skills/README.md` for a list/table of skills. If one exists, add a `blog-develop` row matching the existing format (name + one-line description). If there is no enumerated list, skip this step (the per-skill README is enough).

Run first to locate the listing: `grep -n "blog-ideas" contrib/skills/README.md`

- [ ] **Step 3: Commit**

```bash
git add contrib/skills/blog-develop/README.md contrib/skills/README.md
git commit -m "docs(blog-develop): skill README + contrib listing"
```

---

## Task 4: Structural eval — guard against narrate-instead-of-delegate

The #557 failure mode is the orchestrator *narrating* "I'll research this" and stalling instead of calling a tool. The high-value structural guard is: invoking the skill makes the **first substantive action a `delegate_task` (the scout)**, not an inline `tabstack_research` / `web_fetch`. (Per project rule: evals guard structure; prompt quality comes from live smoke testing.)

**Files:**
- Modify or create: an `evals/<theme>.yaml` case (pick the existing theme file that covers skills/commands; create `evals/blog_develop.yaml` if none fits).

- [ ] **Step 1: Inspect the evals format and pick a home**

Run: `ls evals/ && sed -n '1,60p' evals/$(ls evals | grep -m1 yaml)`
Read `docs/eval-loop.md` for the case schema (`expect_tool`, `expect_no_tool`, `max_tool_calls`, `max_tool_errors`). Confirm how a `user-invocable` `/blog-develop` command is invoked in a case (prompt text vs. command field).

- [ ] **Step 2: Add the case**

Add a case asserting the scout-first contract. Shape (adapt field names to the harness verified in Step 1):

```yaml
- name: blog_develop_scouts_via_delegate
  description: >
    /blog-develop must kick off the scout through delegate_task, not by
    researching inline (guards the narrate-instead-of-delegate stall).
  prompt: "/blog-develop the sovereignty ladder as a mental model"
  max_tool_calls: 4
  max_tool_errors: 0
  expect_tool: delegate_task
```

Avoid `expect_no_tool` (self-reflection retries can fire unexpected tools — see CLAUDE.md). The positive `expect_tool: delegate_task` under a tight `max_tool_calls` is the reliable assertion.

- [ ] **Step 3: Run the single case**

Run: `make eval-tools` (if the case lands in the tool_choice set) or the suite command from `docs/eval-loop.md` scoped to this case.
Expected: PASS — `delegate_task` invoked within the call budget. If it fails because the harness can't drive a `context: fork` command, downgrade this task to a documented manual smoke step (Task 5) and note the limitation in `notes.md` rather than shipping a broken eval.

- [ ] **Step 4: Commit**

```bash
git add evals/
git commit -m "test(blog-develop): eval guards scout-via-delegate contract"
```

---

## Task 5: Live smoke test (manual — run with Les)

Automated tests can't validate prompt quality or the multi-turn interview. Smoke-test in the real app (ask Les to run, or run in the web UI per CLAUDE.md — do not start a second bot instance).

- [ ] **Step 1: Install the skill** — add `$CONTRIB/skills/blog-develop` to the dev agent's `extra_skill_paths`; restart so the loader picks it up. Confirm it appears in the skill catalog.
- [ ] **Step 2: Run with an argument** — `/blog-develop <a real idea>`. Verify: (a) first action is a scout `delegate_task` (not inline research); (b) the interview asks one question at a time and waits; (c) deep research runs as a child; (d) a draft lands at `blog/drafts/<slug>.md` with the frontmatter contract + `## Research notes`; (e) the summary links the draft and calls it take-or-leave.
- [ ] **Step 3: Run with no argument** — `/blog-develop`. Verify it asks "What do you want to write about?" and waits.
- [ ] **Step 4: Watch for the stall mode** — if the orchestrator narrates instead of delegating, sharpen the imperative wording in the MUST rules (this is the prompt-tuning loop; structure is already guarded by Tasks 1 & 4). Record any tuning in `notes.md`.
- [ ] **Step 5: Capture results** in `docs/dev-sessions/2026-06-28-1209-blog-develop/notes.md` (final session summary).

---

## Deferred to v2 (not in this plan)

- **Reviewer agent** after the draft: a `delegate_task` phase running two quality lenses — a **"humanizer"** (scan/strip "AI tells") and an **"editor"** applying Strunk & White. Note: `contrib/skills/writing-clearly` already exists and is a likely fit for the editor lens — evaluate reusing it. This is the reliable reviewer-persona pattern; deliberately out of v1 scope.
- **Replace `tabstack_research` with an owned/local research workflow** in the scout + deep-research workers (reduce the hosted-service dependency; make the research loop inspectable/tunable). Needs a local search primitive — only `web_fetch` exists locally today. Worker `return_schema` contracts stay; only the tool changes. Natural home: the #255 replay engine.

---

## Self-review notes

- **Spec coverage:** placement/frontmatter (Task 1), scout→interview→deep-research→draft flow + worker contracts (Task 2), output contract incl. `## Research notes` single-file (Task 2 Phase 5), decoupling from blog-ideas + no-arg behavior (Task 2 "The idea"), #255 narrate-vs-delegate risk guard (Task 4), live smoke (Task 5), v2 reviewer deferral (Deferred section). Voice-doc open item resolved via runtime `vault_search` (Task 2 Phase 4).
- **Placeholder scan:** worker task prompts, return schemas, frontmatter, test code, and draft frontmatter are all concrete. The two genuinely environment-dependent lookups (evals harness invocation syntax; whether `contrib/skills/README.md` enumerates skills) are explicit inspect-first steps, not hidden TODOs.
- **Naming consistency:** `delegate_task` (singular) throughout; tool names match the codebase (`tabstack_research`, `tabstack_extract_markdown`, `web_fetch`, `vault_write`); slug/title/`draft_markdown` field names are consistent between the Phase 4 schema and the Phase 5 write.
