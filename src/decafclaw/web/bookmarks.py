"""Per-agent bookmark index — pinned file and vault locations for the web UI.

Bookmarks live at ``data/{agent_id}/bookmarks.json`` so they sync across
desktop, tablet, and mobile sessions (issue #1086). Shape::

    {
      "files": [{"path": "content/blog/drafts", "label": "Blog Drafts", "kind": "dir"}],
      "vault": [{"path": "journals/2026-10-20.md", "label": "Today's Journal", "kind": "page"}],
    }
"""

import asyncio
import json
import logging
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)

TARGETS = ("files", "vault")
KINDS_BY_TARGET = {
    "files": ("file", "dir"),
    "vault": ("page", "folder"),
}


def validate_bookmark_path(path: str) -> str | None:
    """Validate a bookmark path. Returns an error message or None if valid.

    Same rules as the workspace folder parameter: non-empty, relative, no
    empty or ``..`` segments.
    """
    if not path:
        return "Path cannot be empty"
    if path.startswith("/"):
        return "Path cannot start with '/'"
    segments = path.split("/")
    if any(not seg for seg in segments):
        return "Path cannot contain empty segments"
    if ".." in segments:
        return "Path cannot contain '..'"
    return None


def default_label(path: str) -> str:
    """Label a bookmark from its last path segment when the client sends none."""
    return path.rsplit("/", 1)[-1]


class BookmarkStore:
    """Manages the per-agent bookmark file.

    Reads and writes the JSON index under an ``asyncio.Lock`` (same pattern
    as ``ConversationFolderIndex``). Writes are atomic: temp file + rename,
    so a crash mid-write never leaves a truncated ``bookmarks.json``.
    """

    def __init__(self, config):
        self._path = config.agent_path / "bookmarks.json"
        self._lock = asyncio.Lock()

    @property
    def path(self) -> Path:
        return self._path

    def _empty(self) -> dict:
        return {"files": [], "vault": []}

    def _load(self) -> dict:
        if not self._path.exists():
            return self._empty()
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return self._empty()
            result = self._empty()
            for target in TARGETS:
                entries = data.get(target)
                if not isinstance(entries, list):
                    continue
                for entry in entries:
                    if not isinstance(entry, dict):
                        continue
                    path = entry.get("path")
                    kind = entry.get("kind")
                    if not isinstance(path, str) or not isinstance(kind, str):
                        continue
                    label = entry.get("label")
                    result[target].append(
                        {
                            "path": path,
                            "label": label if isinstance(label, str) and label else default_label(path),
                            "kind": kind,
                        }
                    )
            return result
        except (json.JSONDecodeError, OSError) as e:
            log.warning("Could not read bookmark index at %s: %s", self._path, e)
            return self._empty()

    def _save(self, data: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(data, indent=2) + "\n"
        fd, tmp = tempfile.mkstemp(dir=self._path.parent, suffix=".tmp")
        try:
            with open(fd, "w", encoding="utf-8") as f:
                f.write(content)
            Path(tmp).replace(self._path)
        except Exception:
            Path(tmp).unlink(missing_ok=True)
            raise

    def _find(self, data: dict, target: str, path: str) -> int | None:
        for i, entry in enumerate(data[target]):
            if entry["path"] == path:
                return i
        return None

    async def list(self) -> dict:
        """Return the current bookmark payload."""
        async with self._lock:
            return self._load()

    async def add(self, target: str, path: str, kind: str, label: str | None) -> dict:
        """Add (or update) a bookmark. Returns the updated payload.

        Upsert semantics: re-adding an existing ``(target, path)`` updates
        its label/kind instead of duplicating the row.
        """
        async with self._lock:
            data = self._load()
            entry = {"path": path, "label": label or default_label(path), "kind": kind}
            idx = self._find(data, target, path)
            if idx is None:
                data[target].append(entry)
            else:
                data[target][idx] = entry
            self._save(data)
            return data

    async def remove(self, target: str, path: str) -> dict | None:
        """Remove a bookmark. Returns the updated payload, or None when the
        bookmark is not present."""
        async with self._lock:
            data = self._load()
            idx = self._find(data, target, path)
            if idx is None:
                return None
            data[target].pop(idx)
            self._save(data)
            return data
