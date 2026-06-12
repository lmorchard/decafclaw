# Conversation sidecar directory layout — Implementation Plan

**Goal:** Move every per-conversation sidecar from flat `conversations/{conv_id}.SUFFIX` into `conversations/{conv_id}/{file}`, behind one path helper, with a one-shot migration script + in-place legacy fallback.

**Approach:** New `conversation_paths.py` is the single sanitization + path chokepoint (`conversation_dir`, `sidecar_path`, `iter_conversation_archives`, `delete_conversation_files`). Every reader/writer routes through it. `sidecar_path` prefers the new dir path but keeps operating on an existing flat legacy file until the script moves it (so no archive split). `workflow/paths.py` delegates. A `scripts/migrate_sidecars_to_dirs.py` moves existing data; `make migrate-sidecars`.

**Tech stack:** Python 3, stdlib `pathlib`/`shutil`, pytest (`tmp_path`).

**Sidecar map (legacy suffix → in-dir filename):**

| legacy | new filename |
|---|---|
| `.jsonl` | `archive.jsonl` |
| `.compacted.jsonl` | `compacted.jsonl` |
| `.notes.md` | `notes.md` |
| `.decisions.json` | `decisions.json` |
| `.context.json` | `context.json` |
| `.canvas.json` | `canvas.json` |
| `.skills.json` | `skills.json` |
| `.skill_data.json` | `skill_data.json` |
| `.vault_grants.json` | `vault_grants.json` |

---

## Phase 1: `conversation_paths.py` foundation

New module with all path logic + tests. No callers yet (infrastructure scaffolding — TDD applies to the helpers' behavior; no opt-out).

**Files:**
- Create: `src/decafclaw/conversation_paths.py`
- Test: `tests/test_conversation_paths.py`

**Key changes:**
- `_safe_conv_id(conv_id: str) -> str` — strips `/`, `\`, `..`; `_invalid` sentinel for empty/`.`.
- `conversations_root(config) -> Path` — resolved `{workspace}/conversations`.
- `conversation_dir(config, conv_id, *, create=False) -> Path` — `{root}/{safe}`, sandboxed via `.is_relative_to`, optional mkdir.
- `sidecar_path(config, conv_id, filename, legacy_suffix) -> Path` — prefer new; fall back to existing flat legacy.
- `iter_conversation_archives(config) -> Iterator[tuple[str, Path]]` — both layouts, dir wins, skips compacted.
- `delete_conversation_files(config, conv_id) -> None` — `rmtree({id}/)` + unlink flat legacy suffixes.
- `SIDECAR_FILENAMES` / `SIDECAR_LEGACY_SUFFIXES` — the 9 mappings (for delete + the migration script).

```python
"""Per-conversation sidecar file paths.

Convention (#576): every per-conversation sidecar lives in a directory
named for the conversation id — conversations/{conv_id}/{file} — e.g.
archive.jsonl, notes.md, decisions.json. Supersedes the legacy flat
conversations/{conv_id}.SUFFIX layout. During the deprecation window
code keeps reading AND writing an existing flat legacy file in place
(no split data); only scripts/migrate_sidecars_to_dirs.py moves files.
"""
from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

# legacy flat suffix → in-directory filename. Order: most-specific first
# so `.compacted.jsonl` is matched before `.jsonl` by the migration script.
SIDECAR_FILENAMES: tuple[tuple[str, str], ...] = (
    (".compacted.jsonl", "compacted.jsonl"),
    (".jsonl", "archive.jsonl"),
    (".notes.md", "notes.md"),
    (".decisions.json", "decisions.json"),
    (".context.json", "context.json"),
    (".canvas.json", "canvas.json"),
    (".skills.json", "skills.json"),
    (".skill_data.json", "skill_data.json"),
    (".vault_grants.json", "vault_grants.json"),
)
SIDECAR_LEGACY_SUFFIXES: tuple[str, ...] = tuple(s for s, _ in SIDECAR_FILENAMES)


def _safe_conv_id(conv_id: str) -> str:
    safe = conv_id.replace("/", "").replace("\\", "").replace("..", "")
    return safe if safe not in ("", ".") else "_invalid"


def conversations_root(config) -> Path:
    return (config.workspace_path / "conversations").resolve()


def conversation_dir(config, conv_id: str, *, create: bool = False) -> Path:
    base = conversations_root(config)
    d = (base / _safe_conv_id(conv_id)).resolve()
    if not d.is_relative_to(base):
        d = base / "_invalid"
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d


def _legacy_flat_path(config, conv_id: str, legacy_suffix: str) -> Path:
    base = conversations_root(config)
    p = (base / f"{_safe_conv_id(conv_id)}{legacy_suffix}").resolve()
    if not p.is_relative_to(base):
        return base / f"_invalid{legacy_suffix}"
    return p


def sidecar_path(config, conv_id: str, filename: str, legacy_suffix: str) -> Path:
    """Resolve a per-conversation sidecar path. Prefers the directory
    layout; while a flat legacy file still exists and the new path does
    not, returns the legacy path so reads and writes stay on the existing
    file until the migration script moves it. Callers mkdir the parent
    before writing (the conversation dir is created lazily that way)."""
    new = conversation_dir(config, conv_id) / filename
    if not new.exists():
        legacy = _legacy_flat_path(config, conv_id, legacy_suffix)
        if legacy.exists():
            return legacy
    return new


def iter_conversation_archives(config) -> Iterator[tuple[str, Path]]:
    """Yield (conv_id, archive_path) across both layouts. Directory layout
    ({id}/archive.jsonl) wins over flat ({id}.jsonl); a conv in both yields
    once. Compacted sidecars are never yielded."""
    base = conversations_root(config)
    if not base.exists():
        return
    seen: set[str] = set()
    children = sorted(base.iterdir())
    for child in children:
        if child.is_dir():
            archive = child / "archive.jsonl"
            if archive.exists():
                seen.add(child.name)
                yield child.name, archive
    for child in children:
        if (child.is_file() and child.name.endswith(".jsonl")
                and not child.name.endswith(".compacted.jsonl")):
            conv_id = child.name[: -len(".jsonl")]
            if conv_id not in seen:
                yield conv_id, child


def delete_conversation_files(config, conv_id: str) -> None:
    """Remove all on-disk state for a conversation: the {id}/ directory
    (archive, sidecars, workflow.json, uploads) and any remaining flat
    legacy sidecars."""
    d = conversation_dir(config, conv_id)
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)
    base = conversations_root(config)
    safe = _safe_conv_id(conv_id)
    for suffix in SIDECAR_LEGACY_SUFFIXES:
        p = (base / f"{safe}{suffix}").resolve()
        if p.is_relative_to(base) and p.exists():
            p.unlink()
```

**Tests** (`tests/test_conversation_paths.py`) — use a tiny `config` stub with `workspace_path` pointing at `tmp_path`:
- `conversation_dir` returns `{root}/{id}`; sandbox: `../../etc` → under root; empty → `_invalid`.
- `sidecar_path` returns new path when neither file exists; returns legacy when only flat exists; returns new when only new exists; returns new when **both** exist (dir wins).
- `iter_conversation_archives`: yields dir-layout convs, flat convs, dedups a conv present in both (yields dir form once), skips `*.compacted.jsonl`, skips a dir without `archive.jsonl`, empty when root missing.
- `delete_conversation_files`: removes the dir (incl. nested `uploads/` + `workflow.json`) and flat legacy files; no-op when nothing present.

**Verification — automated:**
- [x] `uv run pytest tests/test_conversation_paths.py -v` passes (20 passed)
- [x] `make lint` passes
- [x] `make check` passes

**Verification — manual:**
- [x] None (no behavior wired yet).

_Code-quality review fix applied: `delete_conversation_files` now logs `OSError` instead of `rmtree(ignore_errors=True)`; `removesuffix` for clarity._

---

## Phase 2: Archive + discovery + delete through the helper

Route the core sidecar (archive/compacted) and all four discovery sites + delete through `conversation_paths`. End-to-end: a conversation's archive lives in `{id}/archive.jsonl`, is discovered/searched, and deleted as a unit; a pre-existing flat archive keeps being appended in place.

**Files:**
- Modify: `src/decafclaw/archive.py` — `archive_path`, `_compacted_path` delegate to `sidecar_path`.
- Modify: `src/decafclaw/conversation_manager.py` — `startup_scan` uses `iter_conversation_archives`.
- Modify: `src/decafclaw/tools/conversation_tools.py` — `tool_conversation_search` uses `iter_conversation_archives`.
- Modify: `src/decafclaw/web/conversations.py` — `list_system_conversations` uses `iter_conversation_archives`.
- Modify: `src/decafclaw/skills/newsletter/tools.py` — `_collect_scheduled_activity` uses `iter_conversation_archives`, filters on `conv_id`.
- Modify: `src/decafclaw/http_server.py` — `delete_conversation` calls `delete_conversation_files`.
- Test: `tests/test_archive.py` (extend), conversation-search test (nearest), `tests/test_web_conversations.py` (extend).

**Key changes:**
```python
# archive.py
from .conversation_paths import sidecar_path

def archive_path(config, conv_id: str) -> Path:
    return sidecar_path(config, conv_id, "archive.jsonl", ".jsonl")

def _compacted_path(config, conv_id: str) -> Path:
    return sidecar_path(config, conv_id, "compacted.jsonl", ".compacted.jsonl")
```
```python
# conversation_manager.py startup_scan — replace the glob loop body:
from .conversation_paths import iter_conversation_archives
...
for conv_id, archive_file in iter_conversation_archives(self.config):
    try:
        pending = self._scan_archive_for_pending(archive_file, stale_cutoff)
        ...
```
(`conversations_dir.exists()` early-return stays; drop the `.stem.endswith(".compacted")` skip — the iterator excludes compacted.)
```python
# tools/conversation_tools.py — replace glob:
from ..conversation_paths import iter_conversation_archives
...
archives = sorted(iter_conversation_archives(ctx.config), key=lambda t: t[0], reverse=True)
for conv_id, filepath in archives:
    with filepath.open(...) as f: ...
```
```python
# web/conversations.py list_system_conversations — replace glob:
from ..conversation_paths import iter_conversation_archives
...
for conv_id, path in iter_conversation_archives(config):
    # existing web-/--child- filters on conv_id, mtime from path.stat().st_mtime
```
```python
# skills/newsletter/tools.py _collect_scheduled_activity:
from decafclaw.conversation_paths import iter_conversation_archives
...
for conv_id, path in iter_conversation_archives(ctx.config):
    if not conv_id.startswith("schedule-"):
        continue
    parsed = _parse_conv_id(conv_id)   # was _parse_conv_id(path.stem)
    ...
```
```python
# http_server.py delete_conversation — replace the suffix loop + delete_conversation_uploads:
from .conversation_paths import delete_conversation_files
...
delete_conversation_files(config, conv_id)
index.delete(conv_id)
```

**Tests:**
- Archive round-trip writes to `{id}/archive.jsonl` (new conv); reading it back works (`tests/test_archive.py`).
- **No-split invariant:** pre-create a flat `{id}.jsonl` with one message, `append_message`, assert the flat file grew and **no** `{id}/archive.jsonl` was created.
- Compacted round-trip in dir layout.
- `conversation_search` finds a match in a dir-layout archive and in a flat archive.
- `list_system_conversations` returns a dir-layout conv (and still applies `web-`/`--child-` filters).
- Newsletter `_collect_scheduled_activity` picks up a `schedule-*` conv in dir layout (extend existing newsletter test if present; else add a focused one).
- Delete removes a dir-layout conv (with uploads) and a half-migrated conv (dir + leftover flat notes).

**Verification — automated:**
- [x] `uv run pytest tests/test_archive.py tests/test_web_conversations.py -v` passes
- [x] `make test` passes (2849 passed — startup_scan / search / newsletter covered)
- [x] `make check` passes

**Verification — manual:**
- [x] `make config` still resolves (smoke that nothing import-broke).

_Review notes (deprecation-window cleanup, non-blocking): `startup_scan`'s `conversations_dir.exists()` guard is now redundant with the iterator; `delete_conversation_uploads` is production-orphaned (uploads always lived at `{id}/uploads/`, covered by the `{id}/` rmtree)._

---

## Phase 3: Remaining 7 sidecars through the helper

Route notes, decisions, context-diagnostics, canvas, skills, skill_data, vault_grants through `sidecar_path`. Each keeps its existing `path.parent.mkdir(...)` (creates the conv dir lazily).

**Files:**
- Modify: `src/decafclaw/notes.py` — `notes_path`.
- Modify: `src/decafclaw/compaction_decisions.py` — `_slice_path`.
- Modify: `src/decafclaw/context_composer.py` — `_context_sidecar_path`.
- Modify: `src/decafclaw/canvas.py` — `_canvas_sidecar_path`.
- Modify: `src/decafclaw/persistence.py` — `_skills_path`, `_skill_data_path`.
- Modify: `src/decafclaw/skills/vault/_grants.py` — `_grants_sidecar_path`.
- Test: extend `tests/test_notes.py`, `tests/test_compaction_decisions.py`, `tests/test_context_composer.py`, plus canvas/persistence/grants tests where they exist.

**Key changes** (each helper body collapses to one line):
```python
# notes.py
from .conversation_paths import sidecar_path
def notes_path(config, conv_id: str) -> Path:
    return sidecar_path(config, conv_id, "notes.md", ".notes.md")

# compaction_decisions.py
def _slice_path(config, conv_id: str) -> Path:
    return sidecar_path(config, conv_id, "decisions.json", ".decisions.json")

# context_composer.py
def _context_sidecar_path(config, conv_id: str) -> Path:
    return sidecar_path(config, conv_id, "context.json", ".context.json")

# canvas.py
def _canvas_sidecar_path(config, conv_id: str) -> Path:
    return sidecar_path(config, conv_id, "canvas.json", ".canvas.json")

# persistence.py
def _skills_path(config, conv_id: str) -> Path:
    return sidecar_path(config, conv_id, "skills.json", ".skills.json")
def _skill_data_path(config, conv_id: str) -> Path:
    return sidecar_path(config, conv_id, "skill_data.json", ".skill_data.json")

# skills/vault/_grants.py
from decafclaw.conversation_paths import sidecar_path  # absolute (skill loader)
def _grants_sidecar_path(config, conv_id: str) -> Path:
    return sidecar_path(config, conv_id, "vault_grants.json", ".vault_grants.json")
```
Existing sandbox tests (e.g. test_notes.py:43-57 asserting `_invalid.notes.md`) must be **rewritten** to the new contract — sandbox now lives in `conversation_paths` and the per-module helper returns dir-layout paths. Update those tests to assert dir-layout output (e.g. traversal → under root / `_invalid/notes.md`), not the old flat sentinel. (CLAUDE.md: rewrite tests to the new path; no deprecated compat shims.)

**Tests:**
- Per module: write → reads back from `{id}/{file}`; read-fallback to a pre-existing flat file; the dir-wins case for at least one (notes).
- Rewrite the traversal/sandbox assertions to the new helper output.

**Verification — automated:**
- [x] `uv run pytest tests/test_notes.py tests/test_compaction_decisions.py tests/test_context_composer.py -v` passes
- [x] `make test` passes (2851 passed)
- [x] `make check` passes

**Verification — manual:**
- [x] None.

_Intended semantic unification: canvas/grants now strip (not reject) slash/`..` conv_ids via shared `_safe_conv_id` — still sandboxed. Stale module-header docstrings (`canvas.py:4`, `_grants.py:4`) deferred to Phase 6 sweep._

---

## Phase 4: `workflow/paths.py` delegates

Consolidate the duplicated `_safe_conv_id` + dir logic. `workflow.json` path is unchanged.

**Files:**
- Modify: `src/decafclaw/workflow/paths.py` — delegate to `conversation_paths`.
- Test: existing `tests/test_workflow_paths.py` guards (no behavior change).

**Key changes:**
```python
from decafclaw.conversation_paths import conversation_dir

def workflow_dir(config, conv_id: str, *, create: bool = False) -> Path:
    return conversation_dir(config, conv_id, create=create)

def workflow_path(config, conv_id: str) -> Path:
    return workflow_dir(config, conv_id) / "workflow.json"
```
Drop the local `_safe_conv_id`. First check `tests/test_workflow_paths.py` for a direct import of `_safe_conv_id`; if present, rewrite the test to import from `conversation_paths` (no compat shim).

**Verification — automated:**
- [x] `uv run pytest tests/test_workflow_paths.py -v` passes (unchanged)
- [x] `make test` passes (2851 passed)
- [x] `make check` passes

**Verification — manual:**
- [x] None.

---

## Phase 5: Migration script + Makefile targets

One-shot, idempotent, `--dry-run` migration of existing flat sidecars into per-conv dirs. Modeled on `scripts/migrate_to_vault.py`.

**Files:**
- Create: `scripts/migrate_sidecars_to_dirs.py`
- Modify: `Makefile` — add `migrate-sidecars` / `migrate-sidecars-dry` next to `migrate-vault`.
- Test: `tests/test_migrate_sidecars.py`

**Key changes** — core as a config-free function for testability:
```python
from decafclaw.conversation_paths import SIDECAR_FILENAMES, _safe_conv_id

def migrate_sidecars(conversations_dir: Path, *, dry_run: bool) -> int:
    """Move flat {id}.SUFFIX files into {id}/{filename}. Idempotent:
    skips a file whose target already exists. Returns count moved."""
    if not conversations_dir.is_dir():
        return 0
    moved = 0
    for entry in sorted(conversations_dir.iterdir()):
        if not entry.is_file():
            continue
        match = next(((suf, fn) for suf, fn in SIDECAR_FILENAMES
                      if entry.name.endswith(suf)), None)
        if match is None:
            continue
        suffix, filename = match
        conv_id = entry.name[: -len(suffix)]
        target = conversations_dir / _safe_conv_id(conv_id) / filename
        if target.exists():
            print(f"  SKIP (exists): {target}")
            continue
        if dry_run:
            print(f"  WOULD MOVE: {entry} → {target}")
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(entry), str(target))
            print(f"  MOVED: {entry} → {target}")
        moved += 1
    return moved
```
`main()` parses `--dry-run`, `load_config()`, calls `migrate_sidecars(config.workspace_path / "conversations", ...)`. (`SIDECAR_FILENAMES` ordering puts `.compacted.jsonl` before `.jsonl` so a compacted file isn't mis-matched as the archive.)

```makefile
migrate-sidecars:
	uv run python scripts/migrate_sidecars_to_dirs.py

migrate-sidecars-dry:
	uv run python scripts/migrate_sidecars_to_dirs.py --dry-run
```

**Tests** (`tests/test_migrate_sidecars.py`, operate directly on a `tmp_path` dir):
- Flat `{id}.jsonl`, `.notes.md`, `.context.json` → moved to `{id}/archive.jsonl`, `notes.md`, `context.json`.
- `.compacted.jsonl` → `{id}/compacted.jsonl` (NOT `archive.jsonl`).
- **Idempotent:** second run moves 0; a pre-existing target is skipped (not overwritten / not double-moved).
- `--dry-run` moves nothing, returns the would-move count.
- Non-sidecar files and existing `{id}/` dirs (workflow.json, uploads) are left untouched.

**Verification — automated:**
- [x] `uv run pytest tests/test_migrate_sidecars.py -v` passes (8 passed)
- [x] core `migrate_sidecars` proven on throwaway dirs incl. compacted-ordering (NOT live data)
- [x] `make check` passes

**Verification — manual:**
- [x] Eyeball `migrate-sidecars-dry` output against a scratch fixture dir. **Do NOT run the non-dry target against the shared `data/` dir** (see spec non-goals).

_Review fixes applied: removed dead `log` binding; added an operator-safety WARNING line before a non-dry run._

---

## Phase 6: Docs

Doc-only (TDD opt-out — no behavior).

**Files:**
- Modify: `docs/data-layout.md` — describe `conversations/{id}/{archive.jsonl,notes.md,…,workflow.json,uploads/}`; note legacy flat layout + migration (`make migrate-sidecars`) + deprecation window.
- Modify: `docs/context-composer.md` — update diagnostics-sidecar path (`{id}/context.json`), notes-scratchpad path (`{id}/notes.md`), decision-slice path (`{id}/decisions.json`).
- Modify: `CLAUDE.md` (project) — the notes-scratchpad bullet and decision-slice path mention; add `conversation_paths.py` to the key-files "Data and persistence" list.
- Modify: docstrings mentioning flat paths: `config_types.py:52`, `notes.py:38`, `canvas.py:4`, `skills/vault/_grants.py:4`, `skills/vault/tools.py:515`.

**Verification — automated:**
- [x] `make check` passes (docstring edits don't break lint)
- [x] `grep -rn 'conversations/{conv_id}\.' docs/ src/ CLAUDE.md` returns only intentional legacy-layout references (data-layout.md:80 + conversation_paths.py:6 legacy-context; files-tab.md:33 `conversations/*.jsonl` still matches dir archives via fnmatch)

**Verification — manual:**
- [x] Read `docs/data-layout.md` — layout + migration steps accurate; newcomer-followable.

_Beyond the planned list, the docs sweep also updated stale flat-path refs in config.md, web-ui.md, preemptive-tool-search.md, vault.md, conversations.md, notes.md, newsletter.md, and fixed the eval seed path (`conversations/eval/archive.jsonl`) in eval/runner.py + eval-loop.md. Verified the `workspace_paths.py` READONLY_PATTERN `conversations/*.jsonl` still protects dir-layout archives (fnmatch `*` crosses `/`) — no regression._

---

## Notes

- **One commit per phase** (`Phase N: <name>`). Lint + test before each.
- **Live test after merge:** start the web UI against a scratch workspace, create a conversation, confirm sidecars land in `{id}/`, then run `make migrate-sidecars-dry` against a copy of real data to sanity-check the mapping before Les runs it for real.
