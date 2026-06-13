# Remove flat fallback — Implementation Plan

**Goal:** Subtract the deprecation-window flat-layout fallback from the conversation-sidecar path helpers and their call sites; keep the migration script.

**Approach:** Simplify `conversation_paths.py` to dir-only, drop the `legacy_suffix` arg at 9 call sites, rewrite tests to the single-layout contract, update docs.

---

## Phase 1: Code + tests (dir-only path helpers)

**Files:**
- Modify: `src/decafclaw/conversation_paths.py`
  - `sidecar_path(config, conv_id, filename)` → `return conversation_dir(config, conv_id) / filename`. Drop `legacy_suffix` param; remove `_legacy_flat_path`.
  - `iter_conversation_archives`: keep the `try/except OSError` fail-open + missing-root guard; iterate dirs only, yield `(child.name, child/"archive.jsonl")` when it exists. Remove the flat-`*.jsonl` loop and the `seen` set.
  - `delete_conversation_files`: keep the dir `rmtree` (with its `OSError` log); remove the `SIDECAR_LEGACY_SUFFIXES` unlink loop.
  - If `SIDECAR_LEGACY_SUFFIXES` is now unreferenced (grep), remove the constant. Keep `SIDECAR_FILENAMES` (migration script).
  - Update the module docstring: drop the "reads/writes an existing flat legacy file in place" paragraph; state the dir layout is canonical and upgrades run the migration script.
- Modify (drop the `legacy_suffix` arg, 9 sites):
  - `archive.py` `archive_path` → `sidecar_path(config, conv_id, "archive.jsonl")`; `_compacted_path` → `"compacted.jsonl"`.
  - `notes.py` `notes_path` → `"notes.md"`.
  - `compaction_decisions.py` `_slice_path` → `"decisions.json"`.
  - `context_composer.py` `_context_sidecar_path` → `"context.json"`.
  - `canvas.py` `_canvas_sidecar_path` → `"canvas.json"`.
  - `persistence.py` `_skills_path` → `"skills.json"`, `_skill_data_path` → `"skill_data.json"`.
  - `skills/vault/_grants.py` `_grants_sidecar_path` → `"vault_grants.json"`.
- Test: `tests/test_conversation_paths.py` — **remove** the fallback/both-layout tests (`test_sidecar_path_legacy_when_only_flat_exists`, `test_sidecar_path_new_wins_when_both_exist`, `test_iter_archives_yields_flat_layout`, `test_iter_archives_dir_wins_when_both_layouts`, `test_delete_removes_flat_legacy_files`, `test_delete_removes_both_dir_and_legacy`). **Keep/adjust:** `test_sidecar_path_new_when_neither_exists` (now the only sidecar_path behavior — rename to reflect it always returns the dir path), the dir-layout iter cases, `test_iter_archives_skips_compacted`, `test_iter_archives_skips_dir_without_archive`, `test_iter_archives_empty_when_root_missing`, `test_iter_archives_fails_open_on_oserror`, the dir-delete test.
- Test: `tests/test_archive.py` — **remove** `test_append_does_not_split_existing_flat_archive` (the in-place-flat-append behavior is intentionally gone). Keep the new-layout round-trip + compacted round-trip.
- Test: per-module fallback tests — **remove** `test_notes.py::test_legacy_flat_file_wins_until_migrated` (and any sibling "legacy flat" per-module tests). Keep the dir-layout path + round-trip assertions.

**Key snippet:**
```python
def sidecar_path(config, conv_id: str, filename: str) -> Path:
    """Resolve a per-conversation sidecar at conversations/{conv_id}/{filename}."""
    return conversation_dir(config, conv_id) / filename

def iter_conversation_archives(config) -> Iterator[tuple[str, Path]]:
    base = conversations_root(config)
    if not base.exists():
        return
    try:
        children = sorted(base.iterdir())
    except OSError as exc:
        log.warning("Failed to list conversations dir %s: %s", base, exc)
        return
    for child in children:
        if child.is_dir():
            archive = child / "archive.jsonl"
            if archive.exists():
                yield child.name, archive
```

**Verification — automated:**
- [x] `grep -rn "legacy_suffix\|_legacy_flat_path" src/ tests/` returns nothing
- [x] `uv run pytest tests/test_conversation_paths.py tests/test_archive.py tests/test_notes.py -v` passes
- [x] `make test` passes (2905 passed)
- [x] `make check` passes

**Verification — manual:**
- [x] None.

_Review fixes: `SIDECAR_LEGACY_SUFFIXES` deleted (unused); `notes.py` docstring de-staled. Code-quality flagged 4 stale doc refs (data-layout.md sidecar_path signature + SIDECAR_LEGACY_SUFFIXES + fallback section; CLAUDE.md key-file line) — all in Phase 2 scope._

---

## Phase 2: Docs

Doc-only (no behavior).

**Files:**
- Modify: `docs/data-layout.md` — remove the "Legacy flat layout and the deprecation-window fallback" subsection; reword so the dir layout is the only layout and instances upgrading from the old flat layout run `make migrate-sidecars` (script retained). Keep the Migration subsection.
- Modify: `CLAUDE.md` — the `conversation_paths.py` key-files line: drop "in-place legacy flat-file fallback"; keep "path chokepoint: `conversations/{conv_id}/{file}` dir layout, archive iteration + delete helpers (migration: `make migrate-sidecars`)".
- Sweep: `grep -rn "fallback\|legacy flat\|deprecation" docs/data-layout.md docs/context-composer.md CLAUDE.md` — fix any remaining prose implying the code still reads flat files.

**Verification — automated:**
- [x] `make check` passes
- [x] `grep -rn "legacy flat\|flat legacy\|deprecation-window fallback" docs/ CLAUDE.md` returns only migration-context references (not "the code reads flat files")

**Verification — manual:**
- [x] Read the updated `data-layout.md` section — accurate (single layout + migration path).

---

## Notes
- One commit per phase. Lint + test before each.
- The migration script, its tests, Makefile targets, and `SIDECAR_FILENAMES` stay.
