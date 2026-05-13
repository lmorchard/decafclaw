# Session notes — kindle-skill-375

Started: 2026-05-13 11:37
Issue: https://github.com/lmorchard/decafclaw/issues/375
Branch: kindle-skill-375
Worktree: .claude/worktrees/kindle-skill-375/
Baseline tests: 2431 passed (16.31s)

Spec seeded from issue body (no `<!-- dev-session:spec -->` marker, so treating as sketch to refine in brainstorm).

## Brainstorm conclusions (2026-05-13 ~12:00)

Documentarian research → `research.md` (key findings: newsletter is closest prior art; vault has no user-preserved-region convention; #374 unimplemented so we go first on the cookie convention).

Four design decisions resolved via Q&A:

1. **Scope:** All three phases in one PR (on-demand + scheduled + archive).
2. **HTTP client:** `curl_cffi` primary; Playwright as fallback if Amazon escalates fingerprinting.
3. **Cookies:** Netscape `cookies.txt` at `data/{agent_id}/secrets/kindle.cookies.txt` (admin path, not workspace). Convention #374 can adopt later.
4. **Page ownership:** Per-book pages fully agent-owned (mechanical overwrite + archived section in same file). Synthesis is user-owned on a separate page. Sidesteps the missing vault primitive for user-preserved regions.
5. **Observability:** `vault_journal_append` per run, tagged `[ingested, kindle]`.

Spec passes readiness checklist (no placeholders; open questions all have default answers; "what we're NOT doing" has 11 concrete non-goals; 140 lines).

## Plan (2026-05-13 ~12:20)

Plan at `plan.md` — 9 vertical-slice phases (~980 lines for ~1000-1500 LOC expected, proportional). One commit per phase:

1. **Scaffolding + `curl-cffi` dep** — skill registers with empty TOOLS; baseline test green
2. **Auth substrate** — cookie loading + curl_cffi session helper; unit-tested in isolation
3. **HTML parsers** — books-list + highlights parsers via BeautifulSoup, fixture-tested
4. **Page upsert** — pure-Python `_upsert_book_page` with archive section, fixture-tested
5. **HTTP fetch tools** — `kindle_list_books` + `kindle_fetch_highlights` (network boundary; Playwright-swap point)
6. **`kindle_sync_book`** — single-book end-to-end (spec Phase 1)
7. **`kindle_sync_all` + journal entry** — multi-book + observability (spec Phase 2)
8. **SKILL.md body + schedule frontmatter** — on-demand `!kindle-sync` + daily cron (spec Phase 3 cron; archive logic done in Phase 4)
9. **Docs** — `docs/kindle.md`, `docs/index.md`, `CLAUDE.md` bundled-skill list

Self-review fixes applied inline:
- Removed undefined `_render_book_page` reference (rendering happens inside `_upsert_book_page`).
- Added `summary` / `keywords` / `importance` frontmatter defaults in Phase 4 (spec example showed them but plan didn't set them).
- Added `timeout=None` to `kindle_list_books`, `kindle_sync_book`, `kindle_sync_all` TOOL_DEFINITIONS (per spec open-question default).
- Made Playwright fallback explicit as the swap point at Phase 5's fetch helpers.
- Removed punt language ("may simplify by ...") in Phase 4 archive parsing — committed to textual round-trip.
- Consolidated `make lint` + `make typecheck` → `make check` (per Makefile convention).

Plan passes self-review checklist: spec coverage complete, no placeholders, type consistency intact.

## Execute (subagent-driven, 2026-05-13 ~13:00–16:00)

12 commits on branch `kindle-skill-375`:

| Commit | Phase |
|---|---|
| `1e44709` | Phase 1: scaffolding + `curl-cffi` (cherry-picked recovery — implementer subagent landed it on `main` initially) |
| `2d37a09` | Phase 2: auth substrate |
| `2cd3f3b` | Phase 3: HTML parsers (real fixture from `/Users/lorchard/Downloads/amazon-kindle-notebook.html`) |
| `3124d17` | Phase 4: page upsert logic |
| `6e17ecb` | Phase 4 cleanup: drop dead `_parse_existing_archived` + fix empty-metadata summary |
| `400807e` | Phase 5: HTTP fetch tools |
| `613b152` | Phase 6: `kindle_sync_book` |
| `69e5e43` | Phase 7: `kindle_sync_all` + journal |
| `c042036` | Phase 8: SKILL.md body + schedule |
| `8f08ed2` | Phase 8 fix: push `enabled` gate into `kindle_sync_all` (was unreachable from SKILL.md) |
| `4dfe5f0` | Phase 9: docs (`docs/kindle.md` + index + CLAUDE.md skill list) |
| `a5deea7` | Phase 9 fix: archived_count double-counting + drop unused `title` param |

**Final state:** `make check` clean; `make test` = 2473 passed (started from 2431 baseline, +42 kindle tests).

### Lessons / surprises from execute

- **Subagent CWD doesn't propagate from parent.** Phase 1 implementer initially committed on `main` because it ran `git commit` from the main clone instead of the worktree. Recovery via cherry-pick + reset. **Mitigation:** every subsequent implementer prompt opened with explicit `cd <worktree>` + verify `pwd` + `git rev-parse --abbrev-ref HEAD` instructions. No recurrences.
- **Real Amazon fixture made Phase 3 much more accurate.** Les provided `~/Downloads/amazon-kindle-notebook.html` (444 KB). Implementer redacted to ~6.5 KB while preserving real ASINs, annotation IDs, and DOM structure. Selector adaptations vs the plan sketch: location lives in a hidden `<input>` not a span; author prefix is "By: " (colon); annotation-row filter via `#annotationHighlightHeader` presence; `span#highlight` (not bare `#highlight`).
- **Bug caught at branch-level review that wasn't caught per-phase:** `kindle_sync_book`'s `existing_ids` regex scanned the WHOLE page, so on a 3rd+ sync previously-archived entries leaked back into the "archived this run" count. Cross-phase review surfaces things per-phase reviews don't.
- **Design bug caught by code-quality review at Phase 8:** SKILL.md instructed the LLM to "read the skill config and check `enabled`" but no tool exposed config to the LLM. Fixed by pushing the gate into `kindle_sync_all` itself (mirror of `newsletter_publish`'s `ctx.task_mode` short-circuit).

### Deferred to live-smoke batch

The following manual verifications were deferred from per-phase checks. Run them with real Amazon cookies before opening the PR:

- [ ] Place real `cookies.txt` at `data/{agent_id}/secrets/kindle.cookies.txt`.
- [ ] `/kindle-sync` in web UI → returns book list + sync summary with correct counts.
- [ ] `/kindle-sync <real-asin>` → syncs single book; vault page appears at `agent/pages/kindle/<asin>-<slug>.md`.
- [ ] Frontmatter fields match `docs/kindle.md` schema; `## Highlights` populated.
- [ ] Re-run `/kindle-sync <same-asin>` → idempotent (`new_count=0`, `archived_count=0`).
- [ ] Set `skills.kindle.enabled=false`; trigger scheduled run manually → silent skip message.
- [ ] Set `skills.kindle.enabled=true`; trigger scheduled run → journal entry appears tagged `[ingested, kindle]`.
- [ ] Verify cookies age warning when file is artificially old (`touch -d "301 days ago"`).
- [ ] Open one rendered page in Obsidian; confirm strikethrough on archived highlights renders correctly and `<!-- annotation-id -->` markers don't visually break.

### Squash plan for PR

12 commits, but the 3 follow-up fix commits are worth squashing for review clarity:
- Squash `6e17ecb` into `3124d17` → single Phase 4 commit.
- Squash `8f08ed2` into `c042036` → single Phase 8 commit.
- Squash `a5deea7` into `4dfe5f0` → single Phase 9 commit (alternative: leave separate as a final-review fix commit; preference up to Les).

Resulting clean PR history: 9 commits, one per phase.

## PR + smoke (2026-05-13 ~16:00–17:30)

### PR #490

Opened https://github.com/lmorchard/decafclaw/pull/490. Project board: #375 → "In review".

### Copilot review (first pass)

Returned 5 substantive comments after ~3 minutes. All real, all addressed:

1. `newly_archived` set iteration is non-deterministic → sort before iterating.
2. `archived_count` reported even when `archive_deleted=False` → zero it out.
3. `kindle_sync_all` makes 2N+1 requests (each `sync_book` re-calls `list_books`) → add keyword-only `book: BookSummary | None` to `sync_book`; `sync_all` passes the known summary. N+1 total.
4. `docs/kindle.md` "recent activity" claim doesn't match impl (no filter) + `B0XXXXXX` example is 8 chars, not 10.
5. `docs/kindle.md` shows removed `title` param on `sync_book`.

### Live smoke against real Amazon

All steps passed:

| Step | Result |
|---|---|
| Cookies parse | 26 cookies, `at-main` + `sess-at-main` present |
| `kindle_list_books` | 24 books returned |
| `kindle_fetch_highlights B078VWDNKT` | 65 highlights (matches Amazon UI) |
| `kindle_sync_book B078VWDNKT` | 343-line vault page, frontmatter clean |
| Idempotent re-sync | 0 new / 65 re-checked / 0 archived |
| Scheduled+disabled gate | Short-circuits, no network |

Critical-path validated: `curl_cffi` Chrome 131 impersonation works; we don't need the Playwright fallback.

### Contrib relocation (post-Copilot-fixes)

Les requested moving the skill to `contrib/skills/kindle/` since it's not core functionality (parallels `linkding-ingest`, `mastodon-ingest`). Full refactor:

- Moved skill + fixtures + smoke + tests into `contrib/skills/kindle/`.
- Tests use `importlib.util.spec_from_file_location` to load `tools.py` (production skill loader pattern).
- Extended `decafclaw.schedules.discover_schedules` to scan `extra_skill_paths` — previously only bundled + admin could self-schedule. CLAUDE.md updated.
- Initially tried Option C (`make test-contrib` only); pivoted to Option B (contrib tests in default `make test`) to avoid bit-rot.
- New target: `make test-contrib` for focused contrib-only runs.

### Lessons

- **Main advanced mid-session.** `feat(tui) #489` merged while we were refactoring. Caught at squash time when the soft-reset diff showed `tui/` files as deleted. Rebased onto fresh `origin/main` before re-squashing. Reinforces the [[feedback_rebase_before_squash]] memory.
- **Subagent missed `git add` after `git mv`.** The refactor subagent moved `test_tools.py` via `git mv` (preserves content) then edited it for importlib loading but didn't `git add` before committing. Commit `5b04974` had the file at the new path but with old content. Tests passed locally (pytest reads working tree, not commit) so the gap wasn't visible until I checked `git status`. Fixup commit before squash recovered.
- **Branch-level code review catches things per-phase reviews miss.** Two real bugs surfaced only at the end-of-branch review pass (archived_count overcount, enabled-gate unreachable from SKILL.md). Worth keeping that step in the workflow.
