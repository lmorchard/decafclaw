"""Per-conversation sidecar file paths.

Convention (#576): every per-conversation sidecar lives in a directory
named for the conversation id — conversations/{conv_id}/{file} — e.g.
archive.jsonl, notes.md, decisions.json. This is the only layout.

Instances upgrading from the old flat conversations/{conv_id}.SUFFIX
layout run scripts/migrate_sidecars_to_dirs.py (`make migrate-sidecars`)
once to move existing files into per-conversation directories.
"""
from __future__ import annotations

import logging
import shutil
from collections.abc import Iterator
from pathlib import Path

log = logging.getLogger(__name__)

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


def sidecar_path(config, conv_id: str, filename: str) -> Path:
    """Resolve a per-conversation sidecar at conversations/{conv_id}/{filename}."""
    return conversation_dir(config, conv_id) / filename


def iter_conversation_archives(config) -> Iterator[tuple[str, Path]]:
    """Yield (conv_id, archive_path) for every conversation directory."""
    base = conversations_root(config)
    if not base.exists():
        return
    try:
        children = sorted(base.iterdir())
    except OSError as exc:
        # Fail open: this feeds startup recovery, search, and UI listing —
        # a transient FS error shouldn't break those flows.
        log.warning("Failed to list conversations dir %s: %s", base, exc)
        return
    for child in children:
        if child.is_dir():
            archive = child / "archive.jsonl"
            if archive.exists():
                yield child.name, archive


def delete_conversation_files(config, conv_id: str) -> None:
    """Remove all on-disk state for a conversation: the {id}/ directory
    (archive, sidecars, workflow.json, uploads).

    Also best-effort removes any leftover pre-#576 flat sidecars
    (conversations/{id}.SUFFIX). The runtime no longer *reads* the flat
    layout, but a delete must still be thorough on a partially-migrated or
    migration-skipped instance — otherwise a "deleted" conversation could
    leave readable history on disk. This is cleanup-only; it never makes a
    flat file authoritative."""
    d = conversation_dir(config, conv_id)
    if d.exists():
        try:
            shutil.rmtree(d)
        except OSError as exc:
            log.warning("Failed to remove conversation dir %s: %s", d, exc)
    base = conversations_root(config)
    safe = _safe_conv_id(conv_id)
    for legacy_suffix, _ in SIDECAR_FILENAMES:
        p = (base / f"{safe}{legacy_suffix}").resolve()
        if p.is_relative_to(base) and p.exists():
            try:
                p.unlink()
            except OSError as exc:
                log.warning("Failed to remove legacy sidecar %s: %s", p, exc)
