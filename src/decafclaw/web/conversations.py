"""Conversation index — lightweight metadata for web UI conversations."""

import json
import logging
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from ..conversation_paths import iter_conversation_archives
from ..llm.history import without_provider_data
from ..session_messaging import is_child_conv_id, parent_conv_id_of

log = logging.getLogger(__name__)


# -- System conversation discovery ---------------------------------------------

# Patterns for inferring conversation type and title from conv_id
_SYSTEM_PATTERNS: list[tuple[re.Pattern, str, str]] = [
    # schedule-{name}-{YYYYMMDD-HHMMSS}
    (re.compile(r"^schedule-(.+)-(\d{8}-\d{6})$"), "schedule", "Schedule: {name} [{ts}]"),
    # heartbeat-{YYYYMMDD-HHMMSS}-{index}
    (re.compile(r"^heartbeat-(\d{8}-\d{6})-(\d+)$"), "heartbeat", "Heartbeat [{ts}] #{idx}"),
    # web-{user}--child-{hex} (delegated subtask)
    (re.compile(r"^(.+)--child-([0-9a-f]+)$"), "delegated", "Subtask {child_id}"),
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


def is_web_conv_for_user(conv_id: str, username: str) -> bool:
    """Return True if ``conv_id`` is a web conversation belonging to ``username``.

    Web conv_ids have the shape ``web-{username}-{suffix}`` (optionally with
    ``--child-{hex}`` for delegated tasks). The username is compared in full
    after stripping the leading ``web-`` and the trailing generated suffix,
    preventing prefix collisions between usernames (e.g. ``alice`` and
    ``alice-team``, #1132).
    """
    if not username or not conv_id.startswith("web-"):
        return False
    base = conv_id.split("--child-", 1)[0]
    user_part, sep, suffix = base[4:].rpartition("-")
    return bool(sep and suffix) and user_part == username


# Child-id helpers (parent_conv_id_of / is_child_conv_id) live in
# session_messaging — the single source of truth for the
# {parent}--child-{hex} convention (#1112/#1114/#1115); imported above and
# re-exported here for the web layer's callers.


def discover_child_convs(config, index: "ConversationIndex", username: str) -> list["ConversationMeta"]:
    """Discover delegated subagent conversations from the archive layer.

    Manager-spawned children (``{parent}--child-{hex}``, see ``delegate.py``)
    never receive index entries — the index only records
    ``ConversationIndex.create()`` mints — so the session listing discovers
    them from on-disk archives instead (#1115, #1223 review). A child is
    attributed to the owner of its root conversation in the index; children
    of unknown or archived roots are skipped (no user to inherit, or the
    session is hidden together with its root). Titles follow
    ``_classify_conv_id``'s delegated pattern; timestamps come from the
    archive file.
    """
    root_ids = {c.conv_id for c in index.list_for_user(username)}
    discovered: list[ConversationMeta] = []
    for conv_id, path in iter_conversation_archives(config):
        parent_id = parent_conv_id_of(conv_id)
        if parent_id is None or parent_id not in root_ids or conv_id in root_ids:
            continue
        st = path.stat()
        mtime = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat()
        birth = getattr(st, "st_birthtime", None)
        created = datetime.fromtimestamp(birth, tz=timezone.utc).isoformat() if birth else mtime
        _, title = _classify_conv_id(conv_id)
        discovered.append(
            ConversationMeta(
                conv_id=conv_id,
                user_id=username,
                title=title,
                created_at=created,
                updated_at=mtime,
            )
        )
    return discovered


def _activity_status(manager, conv_id: str) -> str:
    """Conversation activity status for the UI ('idle' | 'busy' | 'waiting' | ...)."""
    return manager.get_activity_status(conv_id) if manager else "idle"


def _session_item(conv: "ConversationMeta", kind: str, manager) -> dict:
    """One conversation inside a session group, with its UI status."""
    return {
        "conv_id": conv.conv_id,
        "title": conv.title,
        "kind": kind,
        "created_at": conv.created_at,
        "updated_at": conv.updated_at,
        "status": _activity_status(manager, conv.conv_id),
    }


def group_conversations_into_sessions(
    convs: list["ConversationMeta"],
    manager,
) -> list[dict]:
    """Group a user's conversations into sessions for the sidebar hierarchy.

    A session is a root conversation plus its delegated subagent children
    (``{root}--child-{hex}``). Standalone conversations are single-conversation
    sessions. Returns sessions sorted newest-first by updated_at, each with its
    ``conversations`` list ordered parent-first / children-descending.
    """
    groups: dict[str, list] = {}
    order: list[str] = []
    for conv in convs:
        root = parent_conv_id_of(conv.conv_id) or conv.conv_id
        if root not in groups:
            groups[root] = []
            order.append(root)
        groups[root].append(conv)

    sessions: list[dict] = []
    for root in order:
        members = groups[root]
        root_conv = next((c for c in members if c.conv_id == root), None)
        conversations: list[dict] = []
        children = [c for c in members if c.conv_id != root]
        if root_conv is not None:
            conversations.append(_session_item(root_conv, "parent", manager))
        for child in sorted(children, key=lambda c: c.updated_at, reverse=True):
            conversations.append(_session_item(child, "subagent", manager))
        anchor = root_conv if root_conv is not None else members[0]
        sessions.append(
            {
                "session_id": root,
                "title": anchor.title,
                "created_at": anchor.created_at,
                "updated_at": anchor.updated_at,
                "status": _activity_status(manager, root),
                "conversations": conversations,
            }
        )
    sessions.sort(key=lambda s: s["updated_at"], reverse=True)
    return sessions


def list_system_conversations(config, username: str = "", limit: int = 100) -> list[dict]:
    """Discover system conversations from the archive directory.

    Returns dicts with conv_id, title, conv_type, updated_at, sorted
    newest-first by file mtime. Excludes web-originated conversations
    (those are managed by ConversationIndex). Delegated children are
    filtered to only show those belonging to the given username.
    """
    results = []
    for conv_id, path in iter_conversation_archives(config):
        # Skip web-originated conversations (managed by ConversationIndex)
        if conv_id.startswith("web-") and "--child-" not in conv_id:
            continue
        # Filter delegated children to current user only
        if "--child-" in conv_id and username:
            if not is_web_conv_for_user(conv_id, username):
                continue
        conv_type, title = _classify_conv_id(conv_id)
        mtime = path.stat().st_mtime
        results.append(
            {
                "conv_id": conv_id,
                "title": title,
                "conv_type": conv_type,
                "updated_at": datetime.fromtimestamp(mtime).isoformat(),
            }
        )

    results.sort(key=lambda c: c["updated_at"], reverse=True)
    return results[:limit]


def can_read_conversation(config, index: "ConversationIndex", conv_id: str, username: str) -> bool:
    """Whether ``username`` may read the conversation ``conv_id``.

    Web conversations (those tracked in ``web_conversations.json``) are
    gated by ``user_id``; another user's web conversation is rejected
    even if its archive happens to exist on disk. System conversations
    (schedule, heartbeat, delegated children) live only as archive
    files — they're readable by any authenticated user as long as the
    archive exists, mirroring the on-disk discovery model used by
    ``list_system_conversations``.
    """
    from ..archive import archive_path

    conv = index.get(conv_id)
    if conv and conv.user_id == username:
        return True
    # A `web-` prefix without `--child-` is a top-level web conversation
    # owned by some other user — never fall through to the on-disk check.
    if conv_id.startswith("web-") and "--child-" not in conv_id:
        return False
    return archive_path(config, conv_id).exists()


@dataclass
class ConversationMeta:
    """Metadata for a web UI conversation."""

    conv_id: str
    user_id: str
    title: str
    created_at: str  # ISO timestamp
    updated_at: str  # ISO timestamp
    archived: bool = False
    custom_title: bool = False  # user renamed manually; auto-titling must not overwrite (#1072)

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
            if d.get("user_id") == user_id and (include_archived or not d.get("archived", False))
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

    def rename(
        self,
        conv_id: str,
        title: str,
        *,
        manual: bool = False,
        clear_custom: bool = False,
    ) -> ConversationMeta | None:
        """Rename a conversation. Returns updated metadata or None.

        ``manual=True`` (user rename via REST or websocket command) marks the
        title as user-customized (``custom_title``) and applies it
        unconditionally. ``manual=False`` (automated periodic retitle, #1072)
        is refused for conversations the user already renamed — the index
        keeps the user's title and returns None.

        ``clear_custom=True`` (#1156) overrides an existing custom title and
        clears the ``custom_title`` flag so automated periodic retitling can
        continue refining it.
        """
        data = self._load()
        for d in data:
            if d.get("conv_id") == conv_id:
                if not manual and not clear_custom and d.get("custom_title"):
                    return None
                d["title"] = title
                d["updated_at"] = datetime.now(timezone.utc).isoformat()
                if manual:
                    d["custom_title"] = True
                elif clear_custom:
                    d.pop("custom_title", None)
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

    def delete(self, conv_id: str) -> bool:
        """Remove a conversation from the index entirely."""
        data = self._load()
        new_data = [d for d in data if d.get("conv_id") != conv_id]
        if len(new_data) == len(data):
            return False
        self._save(new_data)
        log.info(f"Deleted web conversation {conv_id} from index")
        return True

    def touch(self, conv_id: str) -> None:
        """Update the updated_at timestamp."""
        data = self._load()
        for d in data:
            if d.get("conv_id") == conv_id:
                d["updated_at"] = datetime.now(timezone.utc).isoformat()
                self._save(data)
                return

    def load_history(self, conv_id: str, limit: int = 50, before: str = "") -> tuple[list[dict], bool]:
        """Load paginated message history from the archive.

        Returns (messages, has_more). Messages are sorted oldest-first.
        If `before` is a timestamp, only return messages before that point.
        """
        from ..archive import read_archive

        all_messages = read_archive(self.config, conv_id)

        # Filter by timestamp if provided
        if before:
            all_messages = [m for m in all_messages if m.get("timestamp", "") < before]

        # Take the last `limit` messages
        has_more = len(all_messages) > limit
        messages = all_messages[-limit:] if has_more else all_messages

        return without_provider_data(messages), has_more
