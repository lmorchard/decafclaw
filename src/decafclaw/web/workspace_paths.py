"""Path containment plus permission and kind helpers for workspace paths.

The permission flags live here because the web UI applies a stricter model
than the agent's workspace tools: certain files are hidden from direct
editing in the Files tab even when the agent can write them (e.g.
conversation archives, embedding databases, secret-looking files).

``resolve_contained`` is the single root-containment rule: callers pass the
root they are sandboxed to and get one labeled error when a path escapes.

Patterns here are intentionally conservative; the frontend uses the flags
to gray out / hide entries, and the PUT/DELETE handlers refuse to mutate
anything flagged ``secret`` or ``readonly``.
"""

from __future__ import annotations

import os
from fnmatch import fnmatch
from pathlib import Path

# SECRET_PATTERNS — match against the basename (case-insensitive).
SECRET_PATTERNS = ("*.env", "*credentials*", "*.key")
# READONLY_PATTERNS — match against the full relative path (posix-slashed, case-insensitive).
READONLY_PATTERNS = (
    "conversations/*.jsonl",
    "*.db",
    "*.db-wal",
    "*.db-shm",
    ".last_run",
    ".schedule_last_run/*",
)

TEXT_EXTENSIONS = frozenset(
    {
        ".md",
        ".py",
        ".json",
        ".yaml",
        ".yml",
        ".sh",
        ".js",
        ".ts",
        ".css",
        ".html",
        ".txt",
        ".toml",
        ".ini",
        ".cfg",
        ".conf",
        ".log",
        ".csv",
        ".sql",
    }
)
IMAGE_EXTENSIONS = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".svg",
        ".webp",
        ".bmp",
        ".ico",
    }
)


class PathContainmentError(ValueError):
    """A path resolved outside the root it is meant to stay within."""


def resolve_contained(
    root: Path, path: str | Path, *, label: str, allow_roots: tuple[Path, ...] | list[Path] = ()
) -> Path:
    """Resolve ``path`` under ``root``, rejecting escapes.

    The root and the candidate are fully resolved, so ``..`` traversal and
    symlinks cannot leave the root; a sibling directory whose name shares
    the root's prefix does not pass either. ``label`` names the root in the
    error message (for example ``"the workspace"``).

    ``allow_roots`` admits a symlink whose *lexical* path stays inside the
    root but whose resolved target is inside one of those roots. Workspace
    tools use it so a symlinked ``workspace/skills`` can point at the
    agent/contrib skill directories. An arbitrary symlink outside the root,
    and any ``..`` traversal, is still rejected.

    Raises :class:`PathContainmentError` when the candidate escapes.
    """
    resolved_root = Path(root).resolve()
    raw = resolved_root / path
    candidate = raw.resolve()
    if candidate.is_relative_to(resolved_root):
        return candidate
    # The resolved target escaped. Only a path whose lexical position is
    # still inside the root may follow a symlink into an allowed root;
    # ``..`` segments are normalized without resolving symlinks here.
    lexical = Path(os.path.abspath(raw))
    if not lexical.is_relative_to(resolved_root):
        raise PathContainmentError(f"path '{path}' is outside {label}")
    for allowed in allow_roots:
        try:
            allowed_resolved = Path(allowed).resolve()
        except OSError:
            continue
        if candidate.is_relative_to(allowed_resolved):
            return candidate
    raise PathContainmentError(f"path '{path}' is outside {label}")


def workspace_allowed_roots(config) -> tuple[Path, ...]:
    """External roots a workspace symlink may target (#1810).

    The agent-level skills directory, configured ``extra_skill_paths``, the
    bundled skills, and the repository's ``contrib/skills``. Operators point
    ``workspace/skills`` at these; arbitrary filesystem targets stay blocked.
    """
    roots: list[Path] = []
    agent_path = getattr(config, "agent_path", None)
    if agent_path is not None:
        roots.append(Path(agent_path) / "skills")
    for raw in getattr(config, "extra_skill_paths", ()) or ():
        roots.append(Path(os.path.expandvars(str(raw))).expanduser())
    package_root = Path(__file__).resolve().parents[1]
    roots.append(package_root / "skills")
    roots.append(package_root.parents[1] / "contrib" / "skills")
    return tuple(roots)


def is_secret(rel_path: str) -> bool:
    """Return True if the basename matches a known secret pattern."""
    name = Path(rel_path).name.lower()
    return any(fnmatch(name, p.lower()) for p in SECRET_PATTERNS)


def is_readonly(rel_path: str) -> bool:
    """Return True if the path matches a readonly pattern (full relative path)."""
    rel_lower = rel_path.lower().replace("\\", "/")
    return any(fnmatch(rel_lower, p.lower()) for p in READONLY_PATTERNS)


def detect_kind(path: Path) -> str:
    """Classify a file as 'text', 'image', or 'binary'.

    Extension check wins over content sniff; for unknown extensions we
    peek at the first 8 KiB and look for NUL bytes / UTF-8 decode errors.
    """
    ext = path.suffix.lower()
    if ext in TEXT_EXTENSIONS:
        return "text"
    if ext in IMAGE_EXTENSIONS:
        return "image"
    try:
        with path.open("rb") as fh:
            head = fh.read(8192)
    except OSError:
        return "binary"
    if b"\x00" in head:
        return "binary"
    try:
        head.decode("utf-8")
    except UnicodeDecodeError:
        return "binary"
    return "text"
