"""Permission + kind helpers for the workspace REST endpoints.

These helpers live in the web layer (separate from tools/workspace_tools.py)
because the web UI applies a stricter permission model than the agent's
workspace tools: certain files are hidden from direct editing in the Files
tab even when the agent can write them (e.g. conversation archives,
embedding databases, secret-looking files).

Patterns here are intentionally conservative; the frontend uses the flags
to gray out / hide entries, and the PUT/DELETE handlers refuse to mutate
anything flagged ``secret`` or ``readonly``.
"""

from __future__ import annotations

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

TEXT_EXTENSIONS = frozenset({
    ".md", ".py", ".json", ".yaml", ".yml", ".sh",
    ".js", ".ts", ".css", ".html", ".txt",
    ".toml", ".ini", ".cfg", ".conf", ".log", ".csv", ".sql",
})
IMAGE_EXTENSIONS = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".bmp", ".ico",
})


def resolve_safe(root: Path, rel: str) -> Path | None:
    """Resolve ``rel`` against ``root`` and guard against escapes.

    Returns the resolved absolute path if it lies inside ``root`` after
    symlink resolution; otherwise ``None``. An empty ``rel`` returns the
    resolved ``root`` itself (useful for listing the top level).
    """
    resolved_root = root.resolve()
    if not rel:
        return resolved_root
    candidate = (resolved_root / rel).resolve()
    try:
        candidate.relative_to(resolved_root)
    except ValueError:
        return None
    return candidate


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
