"""Per-conversation sidecar file paths.

Convention (#576): every per-conversation sidecar lives in a directory
named for the conversation id — conversations/{conv_id}/{file} — e.g.
archive.jsonl, notes.md, decisions.json. Supersedes the legacy flat
conversations/{conv_id}.SUFFIX layout. During the deprecation window
code keeps reading AND writing an existing flat legacy file in place
(no split data); only scripts/migrate_sidecars_to_dirs.py moves files.
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
                seen.add(child.name)
                yield child.name, archive
    for child in children:
        if (child.is_file() and child.name.endswith(".jsonl")
                and not child.name.endswith(".compacted.jsonl")):
            conv_id = child.name.removesuffix(".jsonl")
            if conv_id not in seen:
                yield conv_id, child


def delete_conversation_files(config, conv_id: str) -> None:
    """Remove all on-disk state for a conversation: the {id}/ directory
    (archive, sidecars, workflow.json, uploads) and any remaining flat
    legacy sidecars."""
    d = conversation_dir(config, conv_id)
    if d.exists():
        try:
            shutil.rmtree(d)
        except OSError as exc:
            log.warning("Failed to remove conversation dir %s: %s", d, exc)
    base = conversations_root(config)
    safe = _safe_conv_id(conv_id)
    for suffix in SIDECAR_LEGACY_SUFFIXES:
        p = (base / f"{safe}{suffix}").resolve()
        if p.is_relative_to(base) and p.exists():
            try:
                p.unlink()
            except OSError as exc:
                log.warning("Failed to remove legacy sidecar %s: %s", p, exc)
