# Remove conversation-sidecar flat read/write fallback Spec

**Goal:** Now that the directory layout (`conversations/{conv_id}/{file}`) has shipped (#578) and existing data is migrated, remove the deprecation-window flat-layout fallback so the code has one path, not two.

**Source:** [#584](https://github.com/lmorchard/decafclaw/issues/584) (follow-up to #576/#578).

## Precondition (satisfied)

The fallback's only job was to keep pre-migration flat sidecars working. Removing it makes any remaining flat sidecar invisible. Verified before starting:
- Deployed agent: migrated by Les.
- Local `data/` (shared by worktrees via `.env`): migrated this session — 6465 files moved, 0 flat sidecars remain, idempotent re-run = 0, archives read back via the dir layout. Backup at `data/decafclaw/conversations-backup-pre-sidecar-migration.tar.gz`.

## Current state

`src/decafclaw/conversation_paths.py` carries the fallback:
- `sidecar_path(config, conv_id, filename, legacy_suffix)` returns an existing flat legacy file when the new path is absent (`_legacy_flat_path`).
- `iter_conversation_archives` yields dir-layout archives **and** flat `*.jsonl`, dedup via a `seen` set.
- `delete_conversation_files` rmtrees the dir **and** unlinks flat legacy files for all 9 `SIDECAR_LEGACY_SUFFIXES`.
- All 9 sidecar helpers pass a `legacy_suffix` arg.

## Desired end state

- `sidecar_path(config, conv_id, filename)` → `conversation_dir(config, conv_id) / filename`. No `legacy_suffix`, no `_legacy_flat_path`.
- `iter_conversation_archives` yields only dir-layout `{id}/archive.jsonl` (no flat branch, no `seen` dedup; compacted still excluded by keying on `archive.jsonl`; still fails open on `OSError`).
- `delete_conversation_files` rmtrees the `{id}/` dir. It also keeps a **best-effort, delete-only** flat-sidecar cleanup (iterating `SIDECAR_FILENAMES`) so a delete stays thorough on a partially-migrated / migration-skipped instance — this is cleanup, not a read/write fallback (added in response to Copilot review; preserves #576's comprehensive-delete intent).
- 9 call sites drop the `legacy_suffix` arg.
- Tests rewritten to the single-layout contract (no fallback/both-layout/no-split tests).
- Docs drop the deprecation-window fallback description.

## Design decisions

- **Keep the migration script + `make migrate-sidecars(-dry)` + `SIDECAR_FILENAMES`.** Removing the fallback doesn't remove the need to migrate an instance upgrading from an old version; the one-shot script is the supported path. Drop `SIDECAR_LEGACY_SUFFIXES` only if it's left with no users after the delete-loop removal.
  - **Rejected:** deleting the script too — would strand any not-yet-upgraded instance with no migration path.
- **Keep `sidecar_path` as a thin chokepoint** (`conversation_dir(...) / filename`) rather than inlining at 9 sites — clearer, one place to evolve.
- **Remove the no-split-archive invariant test.** It guarded "append to an existing flat archive in place." That behavior is intentionally gone; post-migration no flat archive exists. (Per CLAUDE.md: rewrite/remove tests to the new path, no compat shims.)

## Patterns to follow

- `conversation_paths.py` helpers as shipped in #578 (this is a subtraction).
- Test contract: `tests/test_conversation_paths.py` dir-layout cases stay; fallback cases go.

## What we're NOT doing

- NOT deleting `scripts/migrate_sidecars_to_dirs.py`, its tests, the Makefile targets, or `SIDECAR_FILENAMES`.
- NOT changing the dir layout, filenames, or `conversation_dir`/`_safe_conv_id` sanitization.
- NOT touching the 3 sibling follow-ups (#585 orphaned `delete_conversation_uploads`, #586 redundant startup_scan guard, #587 `uploads_dir` sanitization) — separate issues.
- NOT re-running or modifying the migration.

## Open questions

- **Is `SIDECAR_LEGACY_SUFFIXES` still referenced after the delete-loop removal?** *Default:* grep; if only the removed loop used it, delete the constant (keep `SIDECAR_FILENAMES`). No blocker.
