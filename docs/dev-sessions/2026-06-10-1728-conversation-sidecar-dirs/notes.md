# Notes: conversation sidecar directory layout

Issue [#576](https://github.com/lmorchard/decafclaw/issues/576). Branch `conversation-sidecar-dirs`.

## Outcome

All per-conversation sidecars moved from flat `conversations/{conv_id}.SUFFIX` to a
per-conversation directory `conversations/{conv_id}/{file}`, behind a single path
helper, with a one-shot migration script and an in-place legacy fallback.

6 implementation phases + 1 review-fix commit, executed subagent-driven (fresh
implementer per phase, spec-compliance + code-quality review each, final
whole-branch review = READY TO MERGE). 2859 tests pass (+39), `make check` clean.

## Key facts / decisions

- **Scope grew from the issue's 4 to all 9 flat sidecars** (issue missed `compacted.jsonl`,
  `canvas.json`, `skills.json`, `skill_data.json`, `vault_grants.json`). Research found
  4 discovery/glob sites (not 2) and a delete site that hardcoded only 5 of 9 suffixes.
- **In-place fallback for reads AND writes**, not "writes always new" — the latter would
  split an existing flat archive the moment a new message appended. `sidecar_path` returns
  the legacy flat path iff it exists and the new path doesn't; only the migration script
  moves files.
- **`iter_conversation_archives`** unified the 4 discovery sites (startup_scan,
  conversation_search, list_system_conversations, newsletter); dir layout wins over flat,
  compacted excluded.
- **Delete now removes the whole `{id}/` dir + all 9 flat legacy suffixes** — fixes a
  pre-existing leak (old delete orphaned canvas/skills/skill_data/vault_grants).
- **`SIDECAR_FILENAMES` is ordered most-specific-first** so `.compacted.jsonl` matches
  before `.jsonl` in the migration script (else a compacted file would be mis-mapped to
  `archive.jsonl` with conv_id `{id}.compacted`).
- **Semantic unification:** canvas/grants previously *rejected* slash/`..` conv_ids to a
  `_invalid` sentinel; the shared `_safe_conv_id` *strips* them instead — still sandboxed
  (verified `.is_relative_to(root)` holds in all cases).

## Verified non-issues (caught during review)

- `READONLY_PATTERNS = ("conversations/*.jsonl", ...)` still protects dir-layout archives —
  `fnmatch`'s `*` crosses `/`, so it matches `conversations/{id}/archive.jsonl`. No regression.
- Uploads were always at `{id}/uploads/` (dir layout); the `{id}/` rmtree covers them, so
  dropping the separate `delete_conversation_uploads` call from the delete handler is safe.

## Deferred (follow-up issue worth filing)

- **Remove the flat read-fallback** after migration has run everywhere (one deprecation cycle).
- `startup_scan`'s `conversations_dir.exists()` guard is now redundant with the iterator (cosmetic).
- `delete_conversation_uploads` in `attachments.py` is now production-orphaned (only its own
  tests call it) — decide whether to remove or keep as a utility.
- `attachments.uploads_dir` builds its path without `_safe_conv_id` (pre-existing; unify with
  `conversation_dir` for belt-and-suspenders).

## NOT done by design

- **Did not run the real migration.** The worktree shares the live `data/` dir, so
  `make migrate-sidecars` would move real conversation sidecars. Script is unit-tested +
  dry-run-verified; running it for real (after a backup) is Les's call.

## Files

New: `src/decafclaw/conversation_paths.py`, `scripts/migrate_sidecars_to_dirs.py`,
`tests/test_conversation_paths.py`, `tests/test_conversation_search_tool.py`,
`tests/test_migrate_sidecars.py`. Plus 9 sidecar helpers, 4 discovery sites, delete handler,
`workflow/paths.py`, Makefile, and ~12 doc/docstring files.

## Retrospective

Merged as #578 (squash `11e0691`) on 2026-06-12. Copilot review surfaced 2 fail-open gaps
in the central helper (`iter_conversation_archives` iterdir, `delete_conversation_files`
unlink) — both real, both fixed before merge.

**What was built vs. planned:** Tracked the 6-phase plan exactly, no re-planning. Each phase
landed as one commit, two-stage reviewed; the plan's code snippets were accurate enough that
implementers mostly transcribed them.

**Scope drift:** The only growth was deliberate and decided up front (9 sidecars vs the
issue's 4, surfaced in research before brainstorm Q&A). No mid-execution scope creep. The
docs phase quietly expanded to ~12 files once the sweep grep ran — expected, not drift.

**Surprises:**
- The issue undercounted sidecars (4 named, 9 real) and discovery sites (2 implied, 4 real)
  + a delete site that already leaked 4 of 9. The pre-brainstorm documentarian research is
  what caught this — going straight to plan would have shipped a half-migration.
- `fnmatch`'s `*` crosses `/`, so `READONLY_PATTERNS = ("conversations/*.jsonl",)` still
  matches the dir-layout archive. The docs subagent flagged a "regression" assuming glob
  semantics; verifying the actual fnmatch behavior turned a false alarm into a confirmed
  non-issue. Lesson: verify path-matching semantics, don't assume glob vs fnmatch.
- I initially told subagents to leave the session-docs dir untracked; at PR time found 434
  dev-session files ARE committed in this repo. They belong in the PR.

**Workflow friction:**
- `SendMessage` to continue a prior subagent isn't available in this harness, so the
  "implementer fixes its own review findings" loop in subagent-driven-dev couldn't round-trip.
  For the 1-3 line review fixes (rmtree logging, removesuffix, Copilot's OSError guards) I
  applied them directly with full context + re-verified, rather than spawning a fresh
  implementer for a few lines. Reasonable trade; worth knowing the loop isn't available.
- The per-phase two-stage review was high-value on Phases 1-3 (caught the silent-rmtree and
  the strip-vs-reject semantic change) and lighter-weight on Phase 4 (trivial delegation) —
  collapsing 4's review to one combined pass was proportionate.

**Misses:** Research enumerated glob sites but I didn't initially clock `workspace_paths.py`
READONLY_PATTERNS as a path-coupled site (it's pattern-matching, not globbing). It turned out
fine (fnmatch crosses `/`), but a "what else is coupled to the flat path *shape*?" question in
research would have surfaced it deliberately instead of via the docs sweep.

**Memory candidates (acted on):** dev-session docs are committed in this repo;
`workspace_paths.py` READONLY/SECRET patterns are a path-shape-coupled site + the fnmatch
`*`-crosses-`/` gotcha.

**Deferred follow-ups:** see the "Deferred" section above — remove flat fallback post-migration,
orphaned `delete_conversation_uploads`, redundant startup_scan guard, unify `uploads_dir`.
