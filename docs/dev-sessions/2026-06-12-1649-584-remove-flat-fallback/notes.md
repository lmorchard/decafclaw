# Notes: remove conversation-sidecar flat fallback (#584)

Follow-up to #576/#578. Branch `584-remove-flat-fallback`.

## Outcome

Removed the deprecation-window flat-layout fallback now that the dir layout is the only
layout and migration has run everywhere. Net −158 lines. 2 phases, subagent-driven
(Phase 1 spec + quality reviewed; Phase 2 docs done inline). 2905 tests pass, `make check` clean.

## Precondition handled first (the important part)

Removing the fallback makes any remaining flat sidecar invisible. Before touching code I
checked the **local** `data/` (shared by worktrees via `.env`) — it was NOT migrated (6465
flat files, only 18 dirs), even though the deployed agent was. Backed up
(`data/decafclaw/conversations-backup-pre-sidecar-migration.tar.gz`, 21M), ran
`make migrate-sidecars` locally (6465 moved → 0 flat remain), verified idempotent re-run = 0
and archives read back via the dir layout. Only then proceeded.

Lesson: "migration ran" on one instance ≠ ran everywhere. The worktree's shared `data/`
is a distinct instance from the deployed agent — verify each.

## What changed

- `sidecar_path(config, conv_id, filename)` — dropped `legacy_suffix` + fallback; now just
  `conversation_dir(...) / filename`. Removed `_legacy_flat_path`.
- `iter_conversation_archives` — dir-only (dropped the flat-`*.jsonl` branch + `seen` dedup);
  still excludes compacted (keys on `archive.jsonl`) + fails open on `OSError`.
- `delete_conversation_files` — dir rmtree; **kept** a best-effort delete-only flat-sidecar
  cleanup (Copilot review caught that dropping it would leave readable history on disk if an
  instance was only partially migrated — a privacy footgun, and a regression of #576's
  comprehensive-delete). Cleanup-only; never makes a flat file authoritative.
- `SIDECAR_LEGACY_SUFFIXES` deleted (unused after the delete-loop removal).
- 9 call sites dropped the `legacy_suffix` arg.
- Removed the fallback / both-layout / no-split-archive tests; rewrote others to dir-only.
- Docs: data-layout.md fallback section → one-time-migration framing; CLAUDE.md key-file note.

## Kept (intentionally)

`scripts/migrate_sidecars_to_dirs.py`, its tests, `make migrate-sidecars(-dry)`, and
`SIDECAR_FILENAMES` — an instance upgrading from the pre-#576 flat layout still needs the
one-shot migration; there's no longer a runtime fallback to lean on.

## Closes

#584. Sibling follow-ups still open: #585 (orphaned `delete_conversation_uploads`),
#586 (redundant startup_scan guard), #587 (`uploads_dir` sanitization).
