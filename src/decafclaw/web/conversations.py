"""Conversation index — lightweight metadata for web UI conversations."""

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from uuid import uuid4

log = logging.getLogger(__name__)


@dataclass
class ConversationMeta:
    """Metadata for a web UI conversation."""
    conv_id: str
    user_id: str
    title: str
    created_at: str  # ISO timestamp
    updated_at: str  # ISO timestamp
    archived: bool = False

    def to_dict(self) -> dict:
        """Serialize to dict for API/websocket responses (excludes internal fields)."""
        return {
            "conv_id": self.conv_id,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class ConversationIndex:
    """Manages the conversation metadata index for the web UI.

    Backed by a JSON file. Conversations use the existing archive system
    (JSONL per conv_id) for message storage.
    """

    def __init__(self, config):
        self.config = config
        self.path = config.agent_path / "web_conversations.json"

    def _load(self) -> list[dict]:
        if not self.path.exists():
            return []
        try:
            return json.loads(self.path.read_text())
        except (json.JSONDecodeError, OSError) as e:
            log.warning(f"Could not read conversation index: {e}")
            return []

    def _save(self, data: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2) + "\n")

    def list_for_user(self, user_id: str, include_archived: bool = False) -> list[ConversationMeta]:
        """List conversations for a user, sorted by updated_at (newest first)."""
        data = self._load()
        convs = [
            ConversationMeta(**{k: v for k, v in d.items() if k in ConversationMeta.__dataclass_fields__})
            for d in data
            if d.get("user_id") == user_id
            and (include_archived or not d.get("archived", False))
        ]
        convs.sort(key=lambda c: c.updated_at, reverse=True)
        return convs

    def create(self, user_id: str, title: str = "") -> ConversationMeta:
        """Create a new conversation. Returns the metadata."""
        now = datetime.now().isoformat()
        conv = ConversationMeta(
            conv_id=f"web-{user_id}-{uuid4().hex[:8]}",
            user_id=user_id,
            title=title or "New conversation",
            created_at=now,
            updated_at=now,
        )
        data = self._load()
        data.append(asdict(conv))
        self._save(data)
        log.info(f"Created web conversation {conv.conv_id} for {user_id}")
        return conv

    def get(self, conv_id: str) -> ConversationMeta | None:
        """Get conversation metadata by ID."""
        data = self._load()
        for d in data:
            if d.get("conv_id") == conv_id:
                return ConversationMeta(**d)
        return None

    def rename(self, conv_id: str, title: str) -> ConversationMeta | None:
        """Rename a conversation. Returns updated metadata or None."""
        data = self._load()
        for d in data:
            if d.get("conv_id") == conv_id:
                d["title"] = title
                d["updated_at"] = datetime.now().isoformat()
                self._save(data)
                return ConversationMeta(**d)
        return None

    def archive(self, conv_id: str) -> bool:
        """Archive a conversation (hide from list, keep data on disk)."""
        data = self._load()
        for d in data:
            if d.get("conv_id") == conv_id:
                d["archived"] = True
                d["updated_at"] = datetime.now().isoformat()
                self._save(data)
                log.info(f"Archived web conversation {conv_id}")
                return True
        return False

    def unarchive(self, conv_id: str) -> bool:
        """Unarchive a conversation (restore to active list)."""
        data = self._load()
        for d in data:
            if d.get("conv_id") == conv_id:
                d["archived"] = False
                d["updated_at"] = datetime.now().isoformat()
                self._save(data)
                log.info(f"Unarchived web conversation {conv_id}")
                return True
        return False

    def touch(self, conv_id: str) -> None:
        """Update the updated_at timestamp."""
        data = self._load()
        for d in data:
            if d.get("conv_id") == conv_id:
                d["updated_at"] = datetime.now().isoformat()
                self._save(data)
                return

    def load_history(self, conv_id: str, limit: int = 50,
                     before: str = "") -> tuple[list[dict], bool]:
        """Load paginated message history from the archive.

        Returns (messages, has_more). Messages are sorted oldest-first.
        If `before` is a timestamp, only return messages before that point.
        """
        from ..archive import read_archive
        all_messages = read_archive(self.config, conv_id)

        # Filter by timestamp if provided
        if before:
            all_messages = [
                m for m in all_messages
                if m.get("timestamp", "") < before
            ]

        # Take the last `limit` messages
        has_more = len(all_messages) > limit
        messages = all_messages[-limit:] if has_more else all_messages

        return messages, has_more
