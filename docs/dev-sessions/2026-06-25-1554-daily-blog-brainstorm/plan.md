# Daily blog-idea brainstorm skill — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a bundled, scheduled `blog-ideas` skill that reviews the week-so-far across ingested content, the daily journal, and the blog archive, and maintains a living tiered "blog ideas this week" vault page surfaced through the existing newsletter.

**Architecture:** An almost-prompt-only bundled skill (`SKILL.md` + `SCHEDULE.md`) in the `dream`/`garden` mold, plus one small `tools.py` carrying a single deterministic ISO-week helper (`blog_ideas_week`) so page identity never depends on LLM date math. A daily scheduled run gathers vault material as search snippets, reads the current weekly page, synthesizes ideas in one context, and rewrites the page in place. Delivery rides the newsletter's existing scheduled-activity aggregation — no newsletter changes.

**Tech Stack:** Python 3 (stdlib `datetime.isocalendar`), decafclaw skill framework (`TOOLS` / `TOOL_DEFINITIONS` / `init` / `SkillConfig` contract), pytest, the YAML eval harness (`evals/*.yaml`).

**Preflight (once, before Task 1):** the worktree has no venv yet. Run `uv sync` in the worktree root, then `make test` for a clean baseline. Commands below assume `uv run …` resolves to that venv. (`make test` → `uv run pytest tests/ contrib/skills/`; `make lint` → `uv run ruff check src/ tests/`.)

## Global Constraints

- **Skills use absolute imports** — `from decafclaw.X import ...` (the loader imports `tools.py` via `spec_from_file_location` with no package context; relative imports fail at runtime).
- **Tools receive `ctx` as the first parameter**, always, even if unused.
- **Tool returns are `ToolResult`** (`from decafclaw.media import ToolResult`); errors as `ToolResult(text="[error: ...]")`.
- **Agent writes stay under `agent/`** — `vault_write` outside `agent/` hits an interactive-confirmation gate a scheduled run can't satisfy. Output page lives at `agent/pages/blog-ideas/{week}.md`.
- **Skill directory is `blog_ideas/` (underscore, importable); skill `name` is `blog-ideas` (hyphen).** `name` comes from SKILL.md frontmatter (`skills/__init__.py:87`), independent of the directory name.
- **Ships enabled.** Bundled `SCHEDULE.md` is honored as-is; the deployment is Les's, he can disable via the admin overlay.
- **Model/effort `strong`** — heavy synthesis, matching `dream`/`garden`.
- **Evals bound with `max_tool_calls` and `max_tool_errors`**; prefer positive `expect_tool` over `expect_no_tool` (self-reflection can retry).
- **No fixed `asyncio.sleep` in tests**; the helper is a pure function tested with injected `datetime` values (no scheduler involved).

---

## File Structure

- **Create `src/decafclaw/skills/blog_ideas/__init__.py`** — empty package marker (mirrors `newsletter/__init__.py`), makes `tools.py` importable for unit tests.
- **Create `src/decafclaw/skills/blog_ideas/tools.py`** — the single deterministic helper: pure `compute_week(now, offset_weeks)` + the `blog_ideas_week(ctx, offset_weeks=0)` tool wrapper, with `TOOLS` / `TOOL_DEFINITIONS` registration. One responsibility: ISO-week → page identity.
- **Create `src/decafclaw/skills/blog_ideas/SKILL.md`** — the prompt: frontmatter + the daily-run phases (orient → gather → synthesize → write → finish).
- **Create `src/decafclaw/skills/blog_ideas/SCHEDULE.md`** — daily 06:00 UTC cron, `model: strong`, `required-skills: [blog-ideas, vault]`.
- **Create `tests/test_blog_ideas_skill.py`** — unit tests for `compute_week` (mid-week, Monday, Sunday, prior-week offset, ISO year boundary) + a discovery test (skill loads under name `blog-ideas`, owns `blog_ideas_week`).
- **Create `evals/blog-ideas.yaml`** — behavior evals: natural-language routing → `activate_skill`; explicit run → `blog_ideas_week` + `vault_write` page production.
- **Modify `CLAUDE.md`** — add `blog-ideas` to the bundled-skills list (Key files → Skills).
- **Modify `docs/schedules.md`** — add `blog-ideas` to the bundled scheduled-skills list if one is enumerated there.

---

## Task 1: ISO-week helper (`blog_ideas_week`) + unit tests

**Files:**
- Create: `src/decafclaw/skills/blog_ideas/__init__.py`
- Create: `src/decafclaw/skills/blog_ideas/tools.py`
- Test: `tests/test_blog_ideas_skill.py`

**Interfaces:**
- Produces:
  - `compute_week(now: datetime, offset_weeks: int = 0) -> dict` with keys `week` (str, e.g. `"2026-W26"`), `monday` (str `"YYYY-MM-DD"`), `days_so_far` (int 1–7, ISO weekday of `now`), `page_path` (str, e.g. `"agent/pages/blog-ideas/2026-W26.md"`).
  - `blog_ideas_week(ctx, offset_weeks: int = 0) -> ToolResult` — tool wrapper; `.data` is the `compute_week` dict.
  - Module-level `PAGE_FOLDER = "agent/pages/blog-ideas"`, `TOOLS`, `TOOL_DEFINITIONS`.

- [ ] **Step 1: Create the empty package marker**

Create `src/decafclaw/skills/blog_ideas/__init__.py` with a single line:

```python
"""blog-ideas bundled skill."""
```

- [ ] **Step 2: Write the failing unit tests**

Create `tests/test_blog_ideas_skill.py`:

```python
from datetime import datetime

from decafclaw.skills.blog_ideas.tools import compute_week


def test_midweek_thursday():
    # 2026-06-25 is a Thursday in ISO week 2026-W26 (Monday 2026-06-22).
    info = compute_week(datetime(2026, 6, 25, 15, 0, 0))
    assert info["week"] == "2026-W26"
    assert info["monday"] == "2026-06-22"
    assert info["days_so_far"] == 4
    assert info["page_path"] == "agent/pages/blog-ideas/2026-W26.md"


def test_monday_is_day_one():
    info = compute_week(datetime(2026, 6, 22, 6, 0, 0))
    assert info["week"] == "2026-W26"
    assert info["monday"] == "2026-06-22"
    assert info["days_so_far"] == 1


def test_sunday_is_day_seven():
    info = compute_week(datetime(2026, 6, 28, 23, 0, 0))
    assert info["week"] == "2026-W26"
    assert info["monday"] == "2026-06-22"
    assert info["days_so_far"] == 7


def test_prior_week_offset():
    # offset_weeks=-1 from a Thursday → previous ISO week, its Monday and path.
    info = compute_week(datetime(2026, 6, 25, 15, 0, 0), offset_weeks=-1)
    assert info["week"] == "2026-W25"
    assert info["monday"] == "2026-06-15"
    assert info["page_path"] == "agent/pages/blog-ideas/2026-W25.md"
    # days_so_far still reflects "now" (Thursday), independent of offset.
    assert info["days_so_far"] == 4


def test_iso_year_boundary():
    # 2021-01-01 is a Friday belonging to ISO week 2020-W53 (Monday 2020-12-28).
    # The key must use the ISO year (2020), not the calendar year (2021).
    info = compute_week(datetime(2021, 1, 1, 9, 0, 0))
    assert info["week"] == "2020-W53"
    assert info["monday"] == "2020-12-28"
    assert info["days_so_far"] == 5
    assert info["page_path"] == "agent/pages/blog-ideas/2020-W53.md"
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd /Users/lorchard/devel/decafclaw/.claude/worktrees/feat-blog-ideas-skill && uv run pytest tests/test_blog_ideas_skill.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'decafclaw.skills.blog_ideas.tools'`.

- [ ] **Step 4: Implement the helper**

Create `src/decafclaw/skills/blog_ideas/tools.py`:

```python
"""blog-ideas bundled skill — deterministic ISO-week helper for the living weekly page.

`current_time` returns a plain timestamp with no ISO-week number, and the
living-page model needs a correct, stable week key (a wrong key duplicates the
page instead of refining it). ISO-week math — especially around year boundaries
and "N weeks ago" — is exactly the deterministic mechanic that belongs in code,
so the skill exposes one helper and the LLM never computes weeks itself.
"""

import logging
from datetime import datetime, timedelta

from decafclaw.media import ToolResult

log = logging.getLogger(__name__)

PAGE_FOLDER = "agent/pages/blog-ideas"


def compute_week(now: datetime, offset_weeks: int = 0) -> dict:
    """ISO-week identity for the blog-ideas page, offset by whole weeks.

    Returns a dict with:
      - ``week``:        ISO week key, e.g. ``"2026-W26"``. Uses the ISO year,
                         which differs from the calendar year near boundaries.
      - ``monday``:      the offset week's Monday, ``"YYYY-MM-DD"``.
      - ``days_so_far``: ISO weekday of ``now`` (1=Mon .. 7=Sun). Always reflects
                         ``now`` — it measures progress into the current week and
                         is meaningless for non-zero ``offset_weeks`` (used only
                         to size the gather window when offset is 0).
      - ``page_path``:   vault path of the offset week's page.
    """
    shifted = now + timedelta(weeks=offset_weeks)
    iso = shifted.isocalendar()  # (year, week, weekday)
    week_key = f"{iso.year}-W{iso.week:02d}"
    monday = shifted - timedelta(days=iso.weekday - 1)
    return {
        "week": week_key,
        "monday": monday.strftime("%Y-%m-%d"),
        "days_so_far": now.isocalendar().weekday,
        "page_path": f"{PAGE_FOLDER}/{week_key}.md",
    }


def blog_ideas_week(ctx, offset_weeks: int = 0) -> ToolResult:
    """Return the ISO-week identity + page path for the living weekly page."""
    info = compute_week(datetime.now(), offset_weeks)
    return ToolResult(
        text=(
            f"{info['week']} (week of {info['monday']}, "
            f"day {info['days_so_far']}/7) -> {info['page_path']}"
        ),
        data=info,
    )


TOOLS = {"blog_ideas_week": blog_ideas_week}

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "blog_ideas_week",
            "description": (
                "Return the ISO-week identity for the living weekly blog-ideas "
                "page: the week key (e.g. '2026-W26'), that week's Monday, how "
                "many days into the week it is now (1=Mon..7=Sun), and the vault "
                "page_path. Pass offset_weeks=-1 for last week, -2 for two weeks "
                "ago, etc. ALWAYS call this to get the page path — NEVER "
                "hand-compute ISO week numbers."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "offset_weeks": {
                        "type": "integer",
                        "description": (
                            "Whole-week offset from the current week. 0 = this "
                            "week (default), -1 = last week, -2 = two weeks ago."
                        ),
                    },
                },
            },
        },
    },
]
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_blog_ideas_skill.py -v`
Expected: PASS (5 tests).

- [ ] **Step 6: Commit**

```bash
git add src/decafclaw/skills/blog_ideas/__init__.py src/decafclaw/skills/blog_ideas/tools.py tests/test_blog_ideas_skill.py
git commit -m "feat(blog-ideas): deterministic ISO-week helper for the weekly page

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Skill definition (`SKILL.md` + `SCHEDULE.md`) + discovery test

**Files:**
- Create: `src/decafclaw/skills/blog_ideas/SKILL.md`
- Create: `src/decafclaw/skills/blog_ideas/SCHEDULE.md`
- Modify: `tests/test_blog_ideas_skill.py` (append discovery test)

**Interfaces:**
- Consumes: `compute_week` / `blog_ideas_week` / `TOOL_DEFINITIONS` from Task 1.
- Produces: a discoverable bundled skill named `blog-ideas` that owns the `blog_ideas_week` tool and carries a parseable `SCHEDULE.md`.

- [ ] **Step 1: Write the failing discovery test**

Append to `tests/test_blog_ideas_skill.py`:

```python
def test_skill_discovered_and_owns_tool():
    from decafclaw.config import load_config
    from decafclaw.skills import build_skill_tool_owners, discover_skills

    config = load_config()
    skills = discover_skills(config)
    by_name = {s.name for s in skills}
    assert "blog-ideas" in by_name, f"blog-ideas not discovered; got {sorted(by_name)}"

    owners = build_skill_tool_owners(skills)
    assert owners.get("blog_ideas_week") == "blog-ideas"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_blog_ideas_skill.py::test_skill_discovered_and_owns_tool -v`
Expected: FAIL — `blog-ideas not discovered` (no SKILL.md yet, so the directory isn't a discoverable skill).

- [ ] **Step 3: Write `SKILL.md`**

Create `src/decafclaw/skills/blog_ideas/SKILL.md`:

```markdown
---
name: blog-ideas
description: Review the week's ingested content, journal notes, and blog archive, then maintain a living weekly page of blog-post ideas. Use when asked what to write/blog about, or to brainstorm post ideas from recent activity.
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

`days_so_far` is how many days of "this week" exist; use it as the `days` window.

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
```

- [ ] **Step 4: Write `SCHEDULE.md`**

Create `src/decafclaw/skills/blog_ideas/SCHEDULE.md`:

```markdown
---
schedule: "0 6 * * *"
model: strong
required-skills:
  - blog-ideas
  - vault
---

Time for the daily blog-idea brainstorm. Follow the blog-ideas skill instructions to completion.
```

- [ ] **Step 5: Run the discovery test to verify it passes**

Run: `uv run pytest tests/test_blog_ideas_skill.py::test_skill_discovered_and_owns_tool -v`
Expected: PASS.

- [ ] **Step 6: Verify the whole unit-test file and lint pass**

Run: `uv run pytest tests/test_blog_ideas_skill.py -v && make lint`
Expected: all PASS, no lint errors.

- [ ] **Step 7: Commit**

```bash
git add src/decafclaw/skills/blog_ideas/SKILL.md src/decafclaw/skills/blog_ideas/SCHEDULE.md tests/test_blog_ideas_skill.py
git commit -m "feat(blog-ideas): SKILL.md prompt + daily SCHEDULE.md

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Behavior eval

**Files:**
- Create: `evals/blog-ideas.yaml`

**Interfaces:**
- Consumes: the discoverable `blog-ideas` skill (Task 2) and its `blog_ideas_week` tool (Task 1).

- [ ] **Step 1: Write the eval cases**

Create `evals/blog-ideas.yaml`:

```yaml
# Evals for the blog-ideas skill.
# Case 1: natural-language ask routes to the skill (catalog disambiguation).
# Case 2: an explicit run computes the week deterministically and writes the
#         living weekly page. Output content is non-deterministic, so the
#         assertions are structural (right tools, page path) — quality is judged
#         by the manual deployment smoke, not here.

- name: "blog-ideas: 'what should I write about' activates the skill"
  input: "What should I write about on my blog this week? Brainstorm some ideas from what I've been reading and jotting down lately."
  expect:
    expect_tool: activate_skill
    response_not_contains:
      - "[error"
    max_tool_calls: 4
    max_tool_errors: 0

- name: "blog-ideas: explicit run computes the week and writes the weekly page"
  setup:
    skills: [blog-ideas]
    memories:
      - tags: [journal, blog-candidate]
        content: "Spent the morning wrestling llamafile into doing local topic clustering — the embedding step was the tricky part. *(blog candidate)*"
      - tags: [bookmark, ai]
        content: "Bookmarked Anil Dash 'What do coders do after AI?' — resonates with the craft-vs-result tension."
  input: "Do the weekly blog-ideas brainstorm now and update this week's page."
  expect:
    expect_tool: blog_ideas_week
    response_contains:
      - "agent/pages/blog-ideas/"
    response_not_contains:
      - "[error"
      - "I'm sorry"
    max_tool_calls: 18
    max_tool_errors: 0
```

- [ ] **Step 2: Run the eval**

Run: `uv run python -m decafclaw.eval evals/blog-ideas.yaml` (the eval runner takes a yaml path or a directory; `make eval` runs the whole `evals/` dir).
Expected: both cases PASS. If case 2's `max_tool_calls` is too tight for the gather phase, raise it (note the change in the dev-session notes) rather than weakening the tool/page assertions.

- [ ] **Step 3: Commit**

```bash
git add evals/blog-ideas.yaml
git commit -m "test(blog-ideas): routing + page-production behavior evals

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Docs, full verification, and deployment smoke

**Files:**
- Modify: `CLAUDE.md` (bundled-skills list)
- Modify: `docs/schedules.md` (if it enumerates bundled scheduled skills)

**Interfaces:**
- Consumes: everything from Tasks 1–3.

- [ ] **Step 1: Add `blog-ideas` to the CLAUDE.md bundled-skills list**

In `CLAUDE.md`, under "Key files → Skills (bundled)", add `blog-ideas` to the
skill list line:

```
### Skills (bundled)
`skills/{vault,tabstack,dream,garden,project,claude_code,health,postmortem,ingest,background,mcp,newsletter,blog_ideas}/`. ...
```

(Use the directory name `blog_ideas` to match the others, which are directory names.)

- [ ] **Step 2: Note the scheduled skill in docs/schedules.md**

Open `docs/schedules.md`. If it lists bundled scheduled skills (alongside
`dream`/`garden`/`newsletter`), add a one-line entry:

```
- **blog-ideas** — daily 06:00 UTC; reviews the week-so-far across ingests, journal, and blog archive and refines a living weekly blog-ideas page (`agent/pages/blog-ideas/{ISO-week}.md`). Surfaced via the newsletter's scheduled-activity aggregation.
```

If no such list exists, skip this step (don't invent a new section).

- [ ] **Step 3: Run the full check + test suite**

Run: `make check && make test`
Expected: all PASS, zero warnings (project has zero-tolerance for warning/traceback noise).

- [ ] **Step 4: Check test durations for hidden slow tests**

Run: `uv run pytest tests/test_blog_ideas_skill.py --durations=10`
Expected: the helper tests are sub-millisecond; the discovery test calls `discover_skills` (imports tools.py) but should not approach the top-25 slow list. If it does, that signals an accidental real scheduler/LLM call — investigate before proceeding.

- [ ] **Step 5: Commit the docs**

```bash
git add CLAUDE.md docs/schedules.md
git commit -m "docs(blog-ideas): list the bundled scheduled skill

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 6: Manual deployment smoke (judgment gate — do with Les)**

This is where output *quality* is judged; no unit test covers it.

1. Confirm on the deployment that journal entries surface as `source_type=user`
   in `vault_search` (the spec's build-time open question). If they don't, adjust
   the Phase-2 journal step in `SKILL.md` to use a `folder=journals` filter
   instead, and re-commit.
2. Trigger the skill once interactively (`/blog-ideas`) against the real vault
   (a live week of ingests + journal + drafts).
3. Read the generated `agent/pages/blog-ideas/{week}.md`. Judge: are the ideas
   grounded and genuinely useful, or bland topic-restatements? Tune the SKILL.md
   synthesis wording if needed (tool descriptions / prompt wording are the
   control surface) and re-run.
4. Only after the manual output looks good, let the 06:00 cron run it (it ships
   enabled).

---

## Self-Review

**Spec coverage:**
- Inputs (ingests / journal / drafts / published) → Task 2 SKILL.md Phase 2. ✓ (Kindle included.)
- Daily cadence, week-so-far window → SKILL.md Phase 2 (`days=days_so_far`) + SCHEDULE.md `0 6 * * *`. ✓
- Balanced journal+ingest synthesis → SKILL.md Phase 3. ✓
- Draft resurrection, dedup, continuity, voice → SKILL.md Phase 2 steps 3–4. ✓
- Living weekly page, tiered, in-place rewrite, recurring themes → SKILL.md Phase 4 + Task 1 page path. ✓
- Output under `agent/pages/blog-ideas/` (no write gate) → Task 1 `PAGE_FOLDER`. ✓
- Cross-week memory (look back ~2–3 weeks, signal not re-pitch) → SKILL.md Phase 1 step 3 + Phase 4 Recurring, `blog_ideas_week(offset_weeks=…)`. ✓
- Newsletter delivery without newsletter changes → SKILL.md "Finishing up" rich summary + `HEARTBEAT_OK`; no newsletter file touched. ✓
- Deterministic week helper + unit tests → Task 1. ✓
- Behavior eval → Task 3. ✓
- Ships enabled, model strong → SCHEDULE.md. ✓
- Build-time open questions (journal `source_type`, output folder) → Task 4 Step 6. ✓

**Placeholder scan:** No TBD/TODO; every code step shows complete code; eval-entrypoint command flagged to confirm against `make` targets (a real ambiguity, not a placeholder) with a concrete fallback.

**Type consistency:** `compute_week` keys (`week`, `monday`, `days_so_far`, `page_path`) are used identically in tests (Task 1), the tool wrapper (Task 1), and referenced by name in SKILL.md (Task 2). Tool name `blog_ideas_week` consistent across `TOOLS`, `TOOL_DEFINITIONS`, discovery test, and eval. Skill name `blog-ideas` vs directory `blog_ideas` consistent throughout (frontmatter name vs dir, per Global Constraints).
