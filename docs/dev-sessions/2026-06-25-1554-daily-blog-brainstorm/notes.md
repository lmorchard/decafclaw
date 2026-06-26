# Notes — Daily blog-idea brainstorm skill

**Date:** 2026-06-25
**Branch:** `feat-blog-ideas-skill`
**Outcome:** Feature complete on the branch; final whole-branch review = ready to merge. One post-merge manual gate remains (deployment smoke).

## What was built

A new bundled, scheduled skill `blog-ideas` (dir `src/decafclaw/skills/blog_ideas/`):

- `tools.py` — one deterministic helper, `blog_ideas_week(offset_weeks=0)`, returning the ISO-week key, that week's Monday, days-so-far, and the page path. Pure `compute_week(now, offset_weeks)` underneath, exhaustively unit-tested (mid-week, Monday, Sunday, prior-week offset, ISO year boundary 2021-01-01 → 2020-W53).
- `SKILL.md` — daily-run prompt: orient (compute week via the helper, read current + ~2 prior pages) → gather at snippet level (7 ingest folders + journal via `source_type=user` + `blog/drafts` + `blog/daily`/`weeknotes`) → synthesize (journal & ingests as equal peers) → rewrite the living tiered page in place → finish with a digest summary (or `HEARTBEAT_OK`).
- `SCHEDULE.md` — daily 06:00 UTC, `model: strong`, `required-skills: [blog-ideas, vault]`, ships enabled.
- `evals/blog-ideas.yaml` — one behavior case: explicit run computes the week and writes `agent/pages/blog-ideas/{week}.md`.
- Docs: `blog_ideas` added to the CLAUDE.md bundled-skills list and a one-line entry in `docs/schedules.md`.

Output lands at `agent/pages/blog-ideas/{ISO-week}.md` (under `agent/` to avoid the out-of-agent write gate). Delivery rides the **existing** newsletter's scheduled-activity aggregation — no newsletter changes; the run's narrative summary is the newsletter's raw material and the nudge to read the page.

## Key decisions

- **Almost prompt-only, plus one helper.** Mirrors `dream`/`garden`, but `current_time` has no ISO-week number and the living-page model needs a stable week key (wrong key → duplicate page instead of refine). ISO-week math (and year boundaries / "N weeks ago") belongs in code. Spec amended to record this deviation.
- **Single-turn `dream`-style synthesis**, not fan-out or the workflow engine. The value is cross-source connection, which wants one reasoning context; fan-out would fragment it; the living page is already the durable state.
- **Descoped to the scheduled path** (+ explicit `/blog-ideas`). Mid-implementation we tried to make natural-language questions ("what should I write about?") route to the skill. Les's call: the skill was trying to serve too many purposes — drop NL routing entirely. This also removed a real risk: a QUESTION/COMMAND split in the prompt could have caused the *scheduled* run to be misclassified as a question and silently no-op (the silent-scheduled-task-failure class CLAUDE.md warns about).
- **Newsletter: contribute, don't modify.** Confirmed in code that the newsletter already aggregates all scheduled-task activity (`final_message` + `vault_pages_touched`).

## Deviations / churn (squashed at merge)

- A routing experiment briefly added a `context_composer.py` preempt-hint score-gap filter (cross-cutting, all skills) + an aggressive SKILL.md description + a "Routing note." All reverted when NL routing was descoped. The composer idea is filed separately as **issue #604** to be judged on its own merits.
- The discovery test was narrowed (Task 4) from `build_skill_tool_owners` (which eagerly imports every skill's `tools.py`, including the subprocess-spawning `claude_code`/`background`) to `discover_skills` + the skill's own `TOOL_DEFINITIONS`. **Kept as an independent scoping improvement** (a per-skill test shouldn't import every other skill). It was initially believed to fix a `PytestUnraisableExceptionWarning`, but follow-up measurement disproved that: the warning appears intermittently on the suite **with our test file excluded too** (3/3 runs) — it's a pre-existing, flaky GC artifact (a leaked asyncio subprocess transport finalized late, attributed to whatever test is running at GC time), unrelated to this feature. Filed as **issue #605**, not fixed here.

## Bugfix: `/blog-ideas` command had no vault access (`context: fork` → `inline`)

First deployment smoke of `/blog-ideas` failed: *"vault_read tool is not available in this context."* Root cause (systematic-debugging): the SKILL.md had `context: fork`, which routes the `/command` through `run_child_turn` (delegate.py). Per the #396 child-agent vault policy, **child agents get no vault read access by default and vault writes are categorically blocked** — but this skill must read and write the weekly page. The `dream`/`garden` skills use `context: fork` too, but only ever run via the *scheduled* path (which ignores `context`); `/dream` as a command would hit the same wall. The working reference is `newsletter`: a user-invocable, vault-writing skill that uses **`context: inline`** (runs in the user's conversation with the full tool set). Fix: `context: fork` → `context: inline`, plus a regression test asserting it. The scheduled path is unaffected by `context`.

> General gotcha: a `user-invocable` skill that needs the vault (especially writes) must use `context: inline`, not `fork`.

## Relocation to contrib (post-review)

After the PR was opened, the skill was moved from a core bundled skill
(`src/decafclaw/skills/blog_ideas/`) to **`contrib/skills/blog-ideas/`** — it's personal
and vault-layout-specific, not core infrastructure. Adaptations:

- Dir renamed `blog_ideas` → `blog-ideas` (hyphen, contrib convention); no `__init__.py`
  (matches `writing-clearly` — a hyphen dir can't be a package).
- Unit test moved to a colocated `contrib/skills/blog-ideas/test_blog_ideas.py` using the
  `importlib.spec_from_file_location` pattern (the production loader's pattern). The
  `discover_skills`-by-name assertion was dropped (contrib isn't auto-discovered);
  replaced with a tool-registration check off the loaded module. `make test` already runs
  `contrib/skills/`.
- `evals/blog-ideas.yaml` removed — a contrib skill isn't on the default eval skill path,
  so the eval couldn't resolve it; contrib convention is colocated tests + manual smoke.
- Docs: removed from the CLAUDE.md bundled list and the `docs/schedules.md` bundled-schedule
  count; added a `### blog-ideas` section to `contrib/skills/README.md`.

**Activation now requires explicit opt-in** (the point of contrib): the deployment's
`extra_skill_paths` must include `$CONTRIB/skills/blog-ideas`, and the daily schedule is
enabled via an overlay at `data/{agent_id}/schedules/blog-ideas.md` (the skill's
`SCHEDULE.md` ships force-disabled). `/blog-ideas` works on demand regardless.

## Verification

- `make check` clean (ruff + pyright). `make test`: 2930 passed. (A flaky pre-existing `PytestUnraisableExceptionWarning` may appear intermittently — issue #605 — independent of this branch; not introduced here.)
- ISO-week helper: 5 unit tests. Behavior eval: 1/1 pass (explicit-run page production).
- Final whole-branch review (opus): ready to merge; no Critical/Important.

## Follow-ups / open items

- **Post-merge manual gate (do with Les):** trigger `/blog-ideas` once against the live deployment vault (real week of ingests + journal + drafts), read the produced page, judge idea quality (grounded vs. bland restatement), tune SKILL.md wording if needed. *Then* let the 06:00 cron run.
- **Verify at smoke time:** that `journals/` entries surface as `source_type=user` in `vault_search` on the deployment; if not, add a `folder=journals` filter to the Phase-2 journal step.
- **Issue #604** — context_composer preempt-hint noise reduction (spun out).
- **Issue #605** — pre-existing flaky `PytestUnraisableExceptionWarning` (leaked asyncio subprocess transport, likely in `claude_code`/`background`/`mcp_client` tests). Confirmed independent of this branch. Spun out.
- **Deferred design options (YAGNI):** a dedicated blog-ideas email; output under `blog/ideas/` (needs a write-allowlist entry); skill rename (`muse`).

## Accepted Minors (final review triage — left as-is)

- `blog_ideas_week` uses naive `datetime.now()` (matches `current_time`; 06:00 cron is nowhere near a midnight/weekday rollover).
- `monday` returned as a string (internal display contract; YAGNI).
- Eval `response_contains` checks the path prefix, not the week suffix (asserting the suffix would make the eval date-fragile; week-key correctness is in the unit tests).
- Discovery test's tool-name assert has no failure message (1-element set from a same-file constant; unambiguous on failure).
