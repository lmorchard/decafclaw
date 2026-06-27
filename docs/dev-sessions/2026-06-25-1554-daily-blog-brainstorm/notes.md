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

## Feedback from a weeknotes-composer session (cross-skill consumer)

Another agent flagged issues while wanting to consume the living page. Applied:
- **Contradiction fixed:** the intro said "read and write only within `agent/`",
  contradicting Phase 2's reads of `journals/` + `blog/` (and likely contributing
  to the journal-miss — a literal model avoiding non-`agent/` reads). Reworded to
  **write** within `agent/`, **read** broadly.
- **Incremental reads:** Phase 2 re-read all of Mon–Thu on every run (5× by
  Friday). Now: first run of the week reads everything since `monday`; later runs
  only `vault_read` entries modified after the page's `updated:` watermark and
  trust the page for earlier material. Flat daily cost.
- **Downstream contract:** Headliners now ordered strongest-first (headliner #1 =
  the week's lead); page structure (Headliners/Seeds/Recurring + the headliner
  field set) declared a stable contract other skills parse. The weeknotes composer
  intends to use headliners as the post's lead/spine, recurring as continuity
  callbacks, seeds as Miscellanea candidates.

**Decided (Les): keep seeds ~1 month as a slow-burn theme tracker** (Phase 5).
The motivating case: themes that develop across notes/bookmarks over a week or
two before they're an obvious trend — a single week rarely makes them clear. So
a standing `agent/pages/blog-ideas/parking-lot.md` (under `agent/`, writable by
the cron run — `blog/drafts` is NOT, write gate) accumulates un-ripened seeds
with `seen`/`reinforced` dates. Each run: reinforce parked themes with new
evidence, graduate ripe ones to Headliners, add new seeds, prune anything that
hasn't gained traction in ~1 month (≈4 weeks). Prompt-only, approximate
date-eyeballing (a seed lingering 4 vs 5 weeks is harmless). Complements
`Recurring` (which catches themes already repeating on weekly pages).

## New core tool: `vault_recent` (the gather fix; skill surfaced a core gap)

Building the gather phase exposed a real gap in the **core** vault toolset: there
was `vault_search` (needs a content query) and `vault_list` (folder enumeration,
no date filter) but **nothing for "what changed recently."** `newsletter` had
even grown its own private `_collect_vault_changes` for exactly this. So instead
of a blog-ideas-local helper, we added a general **`vault_recent(days=7,
folder="", source_type="")`** to the always-loaded vault skill (newest-first,
recency-based, optional folder/source_type scoping) with a unit test and a
tool_choice eval disambiguating it from search/list. blog-ideas Phase 2 now calls
`vault_recent(days=days_so_far)` to read *everything* changed this week across all
ingest + distilled folders AND the Obsidian journal (`source_type=user`), then
`vault_read`s the relevant entries. The scan logic is a shared, symlink-safe
`collect_recent_pages(config, cutoff_ts, folder, source_type)` helper; both
`vault_recent` and `newsletter._collect_vault_changes` use it (DRY — newsletter's
private rglob is gone). `vault_recent` errors on non-positive `days` rather than
silently coercing. (Copilot review on #611 prompted the symlink-safety + days
fixes; the newsletter refactor was done in the same PR at Les's request.)

## Bugfix: gather phase used the wrong tool (`vault_search` → `vault_recent`)

Second deploy smoke: the skill ran but produced shallow, generic ideas and never
touched the Obsidian daily journal. Root cause was the Phase 2 gather:
- `vault_search` **requires a content query** and (on the deployment) runs in
  substring mode — so it returns only pages *containing* the query text, not
  "everything recent." The model searched `"blog ideas"`/`"entry"`, found almost
  nothing, then errored on an empty query.
- The journal step said `journals/…` but the model searched `folder=agent/journal`
  (the agent's journal → newsletter pages), missing the human's daily notes.
- Result: ideas synthesized from page *titles* quoted in newsletter recaps, not
  real content.

Fix (prompt-only): rewrote Phase 2 to **enumerate with `vault_list`** per folder
(incl. `journals` explicitly = my Obsidian daily notes, NOT `agent/journal`) and
`vault_read` entries modified on/after `monday`; `vault_search` is reserved for
the published-archive dedup query only, with an explicit warning that it can't
enumerate. (`vault_list` is the right tool — the model already used it correctly
for `blog/drafts` in the smoke.) Helper-tool gather kept as a fallback if a
re-smoke still struggles.

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
