# Conversation sidecar directory layout Spec

**Goal:** Move every per-conversation sidecar from the flat `conversations/{conv_id}.SUFFIX` convention into a single `conversations/{conv_id}/{file}` directory, so "everything belonging to a conversation" lives in one place — one listing, one `rm -rf` to delete, one answer for future sidecars.

**Source:** [#576](https://github.com/lmorchard/decafclaw/issues/576) (no dev-session marker — hand-written; refined here). Research: [research.md](research.md).

## Current state

Sidecars live under `{workspace}/conversations/` in two conventions (research.md §1):

- **Flat (9 types):** archive `{id}.jsonl` (archive.py:16-18), compacted `{id}.compacted.jsonl` (archive.py:32-33), notes `{id}.notes.md` (notes.py:35-47), decisions `{id}.decisions.json` (compaction_decisions.py:81-98), context diagnostics `{id}.context.json` (context_composer.py:109-118), canvas `{id}.canvas.json` (canvas.py:41-50), skills `{id}.skills.json` (persistence.py:10-11), skill_data `{id}.skill_data.json` (persistence.py:32-33), vault grants `{id}.vault_grants.json` (skills/vault/_grants.py:24-35).
- **Directory (PR #573):** workflow journal `{id}/workflow.json` (workflow/paths.py:16-27), uploads `{id}/uploads/*` (attachments.py:12-14).

Each module builds its own path; there is no shared `conversations_dir` helper (research.md §5). Sanitization (strip `/`,`\`,`..`, sandbox via `.is_relative_to`, `_invalid` sentinel) is duplicated across helpers and `workflow/paths.py:11-13 _safe_conv_id` (research.md §2/§3); `attachments.py` and `persistence.py` skip it entirely.

Two glob sites discover archives by `*.jsonl` (research.md §4): `startup_scan` (conversation_manager.py:1813) and `conversation_search` (conversation_tools.py:23).

## Desired end state

- A new module `conversation_paths.py` exposes:
  - `conversation_dir(config, conv_id, *, create=False) -> Path` → `{workspace}/conversations/{safe_id}/`, the single sanitization + sandbox chokepoint.
  - `sidecar_path(config, conv_id, filename, *, create=False) -> Path` → `{dir}/{filename}`.
- Every reader/writer of a per-conv sidecar resolves its path through these helpers. In-dir filenames drop the `{conv_id}.` prefix (the dir already namespaces):

  | Old flat | New |
  |---|---|
  | `{id}.jsonl` | `{id}/archive.jsonl` |
  | `{id}.compacted.jsonl` | `{id}/compacted.jsonl` |
  | `{id}.notes.md` | `{id}/notes.md` |
  | `{id}.decisions.json` | `{id}/decisions.json` |
  | `{id}.context.json` | `{id}/context.json` |
  | `{id}.canvas.json` | `{id}/canvas.json` |
  | `{id}.skills.json` | `{id}/skills.json` |
  | `{id}.skill_data.json` | `{id}/skill_data.json` |
  | `{id}.vault_grants.json` | `{id}/vault_grants.json` |
  | `{id}/workflow.json` | unchanged |
  | `{id}/uploads/*` | unchanged |

- `workflow/paths.py` delegates `_safe_conv_id` + dir resolution to `conversation_paths.py` (no behavior change for `workflow.json`).
- **Reads and writes operate on whichever form already exists, preferring the new dir layout.** If a conversation still has a flat legacy file and no new-layout file, code keeps reading *and writing* that flat file until the one-shot script migrates it. This keeps each sidecar coherent (no split archive). No code path *moves* a file — only the script does. After the script runs (or for brand-new conversations) everything is the dir layout.
- A single `iter_conversation_archives(config)` helper yields `(conv_id, archive_path)` across both layouts (dir `{id}/archive.jsonl` wins over flat `{id}.jsonl`; skips compacted). All four discovery sites use it: `startup_scan` (conversation_manager.py:1813), `conversation_search` (tools/conversation_tools.py:23), `list_system_conversations` (web/conversations.py:75), and newsletter `schedule-*` scan (skills/newsletter/tools.py:174, filtering on `conv_id` prefix).
- Conversation **delete** removes the whole `{id}/` dir (`rm -rf` — covers workflow.json, uploads, and all new sidecars) *and* unlinks any flat legacy files for all 9 suffixes during the window. Replaces the hardcoded 5-suffix list at http_server.py:668 (which currently leaks canvas/skills/skill_data/vault_grants).
- A `scripts/migrate_sidecars_to_dirs.py` one-shot, idempotent, `--dry-run`-capable migration moves existing flat sidecars into per-conv dirs (modeled on `scripts/migrate_to_vault.py`). Wired as `make migrate-sidecars` / `make migrate-sidecars-dry`.
- Docs: `docs/data-layout.md` updated; CLAUDE.md notes mentioning `{conv_id}.notes.md` / `.decisions.json` paths corrected; `docs/context-composer.md` diagnostics-sidecar path updated.

## Design decisions

- **Decision:** Migrate all 9 flat sidecars, not just the 4 named in the issue.
  - **Why:** The goal is one convention. Leaving 5 flat keeps the exact friction the issue exists to remove and guarantees a second migration later.
  - **Rejected:** Issue's literal 4 — smaller diff, but ships a still-mixed layout.
- **Decision:** New `conversation_paths.py` module as the single helper home; `workflow/paths.py` delegates.
  - **Why:** archive/notes/etc. importing path logic from `workflow/` reads wrong. A neutral module is the natural home and lets us consolidate the duplicated `_safe_conv_id` sandboxing into one place.
  - **Rejected:** Extend `workflow/paths.py` — name implies workflow-only.
- **Decision:** Hybrid migration — one-shot script + "operate on whichever form exists, prefer new" fallback in the helpers; no code path moves files.
  - **Why:** Matches the issue's lean and the `migrate_to_vault.py` precedent. The fallback protects conversations not yet migrated and keeps each sidecar coherent (a flat archive keeps being appended-to in place rather than split across two files); the script does the actual moving in one deliberate, reviewable step. Migrate-on-access would make reads/writes mutate file *locations* (surprising under concurrency).
  - **Rejected:** "Writes always new, reads fall back" — splits an existing flat archive the moment a new message is appended. One-shot only (a pre-migration access finds nothing). Lazy-move-on-access (never converges cleanly, reads/writes relocate files).
- **Decision:** In-dir filenames drop the `{conv_id}.` prefix; archive becomes `archive.jsonl`.
  - **Why:** The directory already namespaces by conv_id; repeating it is redundant. `archive.jsonl` is clearer than a bare `{id}.jsonl`-style name inside the dir.
  - **Rejected:** Keep `{conv_id}.jsonl` inside the dir — redundant, and makes the glob/derive logic awkward.

## Patterns to follow

- Sanitization + sandbox: `workflow/paths.py:11-23` (`_safe_conv_id`, `workflow_dir`) and `notes.py:35-47` (strip + `.is_relative_to` + `_invalid` sentinel). Consolidate into `conversation_dir`.
- Migration script shape: `scripts/migrate_to_vault.py` (`--dry-run`, `shutil.move`, empty-dir cleanup, `load_config()`); Makefile targets `migrate-vault` / `migrate-vault-dry`.
- Glob sites to update: conversation_manager.py:1813-1815 (skip `.compacted.jsonl`), conversation_tools.py:23.
- Path-sandbox test pattern: test_notes.py:43-57.

## What we're NOT doing

- **NOT running the real migration against live data this session.** The worktree's `.env` points at the main clone's shared `data/` dir, so `make migrate-sidecars` would move Les's actual conversation sidecars. The script ships + is unit-tested against `tmp_path`; running it for real (with a backup) is Les's deliberate call, like a deploy step.
- **NOT removing the flat read-fallback in this PR.** It stays for one deprecation cycle; a follow-up issue removes it once migration has run everywhere.
- **NOT migrate-on-read/write file moves.** Only the explicit script moves files.
- **NOT changing the `workflow.json` or `uploads/` layout** — already directory-based.
- **NOT redesigning conv_id minting** or adding new sidecar types.
- **NOT touching `conversation_folders` metadata** (per-user JSON index, separate from sidecars).

## Open questions

- **Compacted-archive glob exclusion in the new layout.** Flat code skips `.compacted.jsonl` by stem check; the new glob keys on `archive.jsonl` so `compacted.jsonl` is naturally excluded. *Default:* `iter_conversation_archives` keys on `archive.jsonl` for the dir layout (no separate exclusion); keeps the stem-based `.compacted` exclusion only on the flat-fallback branch. No blocker.
- **`conv_id` collision between a flat file and a new dir.** A conv mid-migration could have both `{id}.jsonl` and `{id}/archive.jsonl`. *Default:* dir wins — `iter_conversation_archives` yields the conv once (dir form), and `sidecar_path` prefers the new path when it exists. The migration script skips moving a flat file when the dir target already exists (idempotent). No blocker.
- **Two `.exists()` stats per path resolution** (hot paths: notes every turn, archive append every message). *Default:* acceptable — stat is microseconds; the extra check disappears when the legacy form is gone (post-migration the flat file simply isn't there) and entirely after the deprecation removal. No blocker.
