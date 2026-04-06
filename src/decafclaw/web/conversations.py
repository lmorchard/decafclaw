"""Conversation index — lightweight metadata for web UI conversations."""

import json
import logging
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

log = logging.getLogger(__name__)


# -- System conversation discovery ---------------------------------------------

# Patterns for inferring conversation type and title from conv_id
_SYSTEM_PATTERNS: list[tuple[re.Pattern, str, str]] = [
    # schedule-{name}-{YYYYMMDD-HHMMSS}
    (re.compile(r"^schedule-(.+)-(\d{8}-\d{6})$"), "schedule",
     "Schedule: {name} [{ts}]"),
    # heartbeat-{YYYYMMDD-HHMMSS}-{index}
    (re.compile(r"^heartbeat-(\d{8}-\d{6})-(\d+)$"), "heartbeat",
     "Heartbeat [{ts}] #{idx}"),
    # web-{user}--child-{hex} (delegated subtask)
    (re.compile(r"^(.+)--child-([0-9a-f]+)$"), "delegated",
     "Subtask {child_id}"),
]


def _format_ts(ts_raw: str) -> str:
    """Format YYYYMMDD-HHMMSS as YYYY-MM-DD HH:MM."""
    d, t = ts_raw[:8], ts_raw[9:]  # "20260324", "125204"
    return f"{d[:4]}-{d[4:6]}-{d[6:8]} {t[:2]}:{t[2:4]}"


def _classify_conv_id(conv_id: str) -> tuple[str, str]:
    """Classify a conv_id and generate a display title.

    Returns (conv_type, title). Returns ("unknown", conv_id) for
    unrecognized patterns.
    """
    for pattern, conv_type, title_fmt in _SYSTEM_PATTERNS:
        m = pattern.match(conv_id)
        if not m:
            continue
        groups = m.groups()
        if conv_type == "schedule":
            name, ts_raw = groups
            ts = _format_ts(ts_raw)
            return conv_type, title_fmt.format(name=name, ts=ts)
        if conv_type == "heartbeat":
            ts_raw, idx = groups
            ts = _format_ts(ts_raw)
            return conv_type, title_fmt.format(ts=ts, idx=idx)
        if conv_type == "delegated":
            _, child_id = groups
            return conv_type, title_fmt.format(child_id=child_id[:8])
    return "unknown", conv_id


def list_system_conversations(config, username: str = "",
                              limit: int = 100) -> list[dict]:
    """Discover system conversations from the archive directory.

    Returns dicts with conv_id, title, conv_type, updated_at, sorted
    newest-first by file mtime. Excludes web-originated conversations
    (those are managed by ConversationIndex). Delegated children are
    filtered to only show those belonging to the given username.
    """
    conv_dir = config.workspace_path / "conversations"
    if not conv_dir.exists():
        return []

    results = []
    for path in conv_dir.glob("*.jsonl"):
        # Skip compacted sidecars
        if path.name.endswith(".compacted.jsonl"):
            continue
        conv_id = path.stem
        # Skip web-originated conversations (managed by ConversationIndex)
        if conv_id.startswith("web-") and "--child-" not in conv_id:
            continue
        # Filter delegated children to current user only
        if "--child-" in conv_id and username:
            if not conv_id.startswith(f"web-{username}-"):
                continue
        conv_type, title = _classify_conv_id(conv_id)
        mtime = path.stat().st_mtime
        results.append({
            "conv_id": conv_id,
            "title": title,
            "conv_type": conv_type,
            "updated_at": datetime.fromtimestamp(mtime).isoformat(),
        })

    results.sort(key=lambda c: c["updated_at"], reverse=True)
    return results[:limit]


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
        # Note: _load/_save are sync, so read-modify-write is atomic
        # within the asyncio event loop (no yield points between them).
        # If these ever become async, add an asyncio.Lock.

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
        now = datetime.now(timezone.utc).isoformat()
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
                d["updated_at"] = datetime.now(timezone.utc).isoformat()
                self._save(data)
                return ConversationMeta(**d)
        return None

    def archive(self, conv_id: str) -> bool:
        """Archive a conversation (hide from list, keep data on disk)."""
        data = self._load()
        for d in data:
            if d.get("conv_id") == conv_id:
                d["archived"] = True
                d["updated_at"] = datetime.now(timezone.utc).isoformat()
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
                d["updated_at"] = datetime.now(timezone.utc).isoformat()
                self._save(data)
                log.info(f"Unarchived web conversation {conv_id}")
                return True
        return False

    def touch(self, conv_id: str) -> None:
        """Update the updated_at timestamp."""
        data = self._load()
        for d in data:
            if d.get("conv_id") == conv_id:
                d["updated_at"] = datetime.now(timezone.utc).isoformat()
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
