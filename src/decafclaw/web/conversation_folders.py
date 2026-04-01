"""Conversation folder index — per-user folder structure for organizing conversations."""

import asyncio
import json
import logging
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)


def _validate_path(path: str) -> str | None:
    """Validate a folder path. Returns error message or None if valid."""
    if not path:
        return "Path cannot be empty"
    if ".." in path.split("/"):
        return "Path cannot contain '..'"
    if path.startswith("/"):
        return "Path cannot start with '/'"
    if path.startswith("_") or any(
        seg.startswith("_") for seg in path.split("/")
    ):
        return "Path segments starting with '_' are reserved"
    if any(seg == "" for seg in path.split("/")):
        return "Path cannot contain empty segments"
    return None


class ConversationFolderIndex:
    """Manages per-user conversation folder structure and assignments.

    Backed by a JSON file at data/{agent_id}/web/users/{username}/conversation_folders.json.

    JSON structure:
    {
        "folders": ["projects", "projects/bot-redesign", "research"],
        "assignments": {
            "web-les-abc123": "projects/bot-redesign"
        }
    }
    """

    def __init__(self, config, username: str):
        self._path = (
            config.agent_path / "web" / "users" / username
            / "conversation_folders.json"
        )
        self._lock = asyncio.Lock()

    def _load(self) -> dict:
        if not self._path.exists():
            return {"folders": [], "assignments": {}}
        try:
            data = json.loads(self._path.read_text())
            # Ensure expected shape
            if not isinstance(data.get("folders"), list):
                data["folders"] = []
            if not isinstance(data.get("assignments"), dict):
                data["assignments"] = {}
            return data
        except (json.JSONDecodeError, OSError) as e:
            log.warning(f"Could not read folder index: {e}")
            return {"folders": [], "assignments": {}}

    def _save(self, data: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(data, indent=2) + "\n"
        # Atomic write via temp file + rename
        fd, tmp = tempfile.mkstemp(
            dir=self._path.parent, suffix=".tmp"
        )
        try:
            with open(fd, "w") as f:
                f.write(content)
            Path(tmp).replace(self._path)
        except Exception:
            Path(tmp).unlink(missing_ok=True)
            raise

    async def list_folders(self, parent: str = "") -> list[str]:
        """Return immediate child folder names under parent."""
        async with self._lock:
            data = self._load()
        prefix = f"{parent}/" if parent else ""
        result = set()
        for folder in data["folders"]:
            if parent == "":
                # Top-level: folders with no "/" in them
                if "/" not in folder:
                    result.add(folder)
            elif folder.startswith(prefix):
                rest = folder[len(prefix):]
                if rest and "/" not in rest:
                    result.add(rest)
        return sorted(result)

    async def create_folder(self, path: str) -> tuple[bool, str]:
        """Create a folder (and parents). Returns (success, error_message)."""
        err = _validate_path(path)
        if err:
            return False, err
        async with self._lock:
            data = self._load()
            if path in data["folders"]:
                return False, "Folder already exists"
            # Auto-create parent folders
            parts = path.split("/")
            for i in range(1, len(parts) + 1):
                ancestor = "/".join(parts[:i])
                if ancestor not in data["folders"]:
                    data["folders"].append(ancestor)
            self._save(data)
        return True, ""

    async def delete_folder(self, path: str) -> tuple[bool, str]:
        """Delete an empty folder. Returns (success, error_message)."""
        async with self._lock:
            data = self._load()
            if path not in data["folders"]:
                return False, "Folder not found"
            # Check for conversations in this folder
            for folder in data["assignments"].values():
                if folder == path:
                    return False, "Folder contains conversations"
            # Check for child folders
            prefix = f"{path}/"
            for folder in data["folders"]:
                if folder.startswith(prefix):
                    return False, "Folder contains subfolders"
            data["folders"].remove(path)
            self._save(data)
        return True, ""

    async def rename_folder(
        self, old_path: str, new_path: str
    ) -> tuple[bool, str]:
        """Rename/move a folder. Merges if target exists. Returns (success, error_message)."""
        err = _validate_path(new_path)
        if err:
            return False, err
        async with self._lock:
            data = self._load()
            if old_path not in data["folders"]:
                return False, "Folder not found"
            old_prefix = f"{old_path}/"
            # Collect all folders to rename (old_path + children)
            to_rename = [f for f in data["folders"] if f == old_path or f.startswith(old_prefix)]
            # Remove old folders, add new ones
            for old_f in to_rename:
                data["folders"].remove(old_f)
                if old_f == old_path:
                    new_f = new_path
                else:
                    new_f = new_path + old_f[len(old_path):]
                if new_f not in data["folders"]:
                    data["folders"].append(new_f)
            # Update assignments
            for conv_id, folder in list(data["assignments"].items()):
                if folder == old_path:
                    data["assignments"][conv_id] = new_path
                elif folder.startswith(old_prefix):
                    data["assignments"][conv_id] = new_path + folder[len(old_path):]
            self._save(data)
        return True, ""

    async def folder_exists(self, path: str) -> bool:
        """Check if a folder exists."""
        err = _validate_path(path)
        if err:
            return False
        async with self._lock:
            data = self._load()
        return path in data["folders"]

    async def get_folder(self, conv_id: str) -> str:
        """Return folder path for a conversation, or '' for top-level."""
        async with self._lock:
            data = self._load()
        return data["assignments"].get(conv_id, "")

    async def set_folder(
        self, conv_id: str, folder: str
    ) -> tuple[bool, str]:
        """Assign a conversation to a folder. Returns (success, error_message)."""
        if folder == "":
            # Move to top-level: just remove assignment
            async with self._lock:
                data = self._load()
                data["assignments"].pop(conv_id, None)
                self._save(data)
            return True, ""
        err = _validate_path(folder)
        if err:
            return False, err
        async with self._lock:
            data = self._load()
            if folder not in data["folders"]:
                return False, "Folder does not exist"
            data["assignments"][conv_id] = folder
            self._save(data)
        return True, ""

    async def remove_assignment(self, conv_id: str) -> None:
        """Remove a conversation's folder assignment."""
        async with self._lock:
            data = self._load()
            if conv_id in data["assignments"]:
                del data["assignments"][conv_id]
                self._save(data)

    async def list_conversations_in_folder(
        self, folder: str = ""
    ) -> list[str]:
        """Return conv_ids assigned to this exact folder."""
        async with self._lock:
            data = self._load()
        if folder == "":
            # Top-level: caller must filter by checking assignments
            return []
        return [
            cid for cid, f in data["assignments"].items() if f == folder
        ]

    async def get_all_assignments(self) -> dict[str, str]:
        """Return all conv_id → folder assignments."""
        async with self._lock:
            data = self._load()
        return dict(data["assignments"])
