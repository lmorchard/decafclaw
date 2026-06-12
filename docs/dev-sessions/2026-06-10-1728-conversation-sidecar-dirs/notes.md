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
