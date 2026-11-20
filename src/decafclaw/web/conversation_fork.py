"""Fork a conversation from a message anchor (#1581).

Time-travel branching: copy the source conversation's archive up to the
chosen message into a fresh, independent conversation. The fork is a plain
conversation — no lineage graph, no split view — so it can later compact,
rename, or archive like any other.

The slice always ends on a complete turn boundary. Clicking branch on a tool
result or on an assistant message with tool calls would otherwise produce a
history whose last assistant turn has unanswered ``tool_calls``, which some
providers reject; the boundary walk drops that partial turn and ends at the
nearest user message or plain assistant message.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from ..archive import read_archive, write_archive
from ..attachments import uploads_dir
from ..conversation_paths import remap_legacy_rel_path
from .conversations import ConversationIndex

if TYPE_CHECKING:
    from ..config import Config
    from .conversations import ConversationMeta


class ForkError(ValueError):
    """User-facing fork refusal (bad anchor, empty slice, unknown message)."""


def _plain_assistant(message: dict) -> bool:
    return message.get("role") == "assistant" and not message.get("tool_calls")


def resolve_anchor(messages: list[dict], *, message_index: int | None, message_timestamp: str) -> int:
    """Resolve the requested anchor to an index in ``messages``.

    Exactly one of ``message_index`` (0-based, inclusive) and
    ``message_timestamp`` (an archive timestamp) identifies the anchor. The
    web UI sends the timestamp because it renders merged display rows that do
    not map 1:1 onto raw archive lines.
    """
    if not messages:
        raise ForkError("conversation has no messages to branch from")
    if message_index is not None:
        if isinstance(message_index, bool) or not isinstance(message_index, int):
            raise ForkError("message_index must be an integer")
        if message_index < 0 or message_index >= len(messages):
            raise ForkError("message_index is out of range")
        return message_index
    if message_timestamp:
        for idx in range(len(messages) - 1, -1, -1):
            if messages[idx].get("timestamp", "") == message_timestamp:
                return idx
        raise ForkError("message not found in conversation archive")
    raise ForkError("message_index or message_timestamp is required")


def turn_boundary(messages: list[dict], anchor: int) -> int:
    """Walk back from ``anchor`` to the last message that ends a safe turn."""
    for idx in range(anchor, -1, -1):
        message = messages[idx]
        if message.get("role") == "user" or _plain_assistant(message):
            return idx
    raise ForkError("no complete turn at or before that message")


def count_turns(messages: list[dict]) -> int:
    """Number of user turns in a message slice (display use only)."""
    return sum(1 for message in messages if message.get("role") == "user")


def latest_model(messages: list[dict]) -> str:
    """Last ``role: "model"`` record in a slice, mirroring history reload."""
    for message in reversed(messages):
        if message.get("role") == "model":
            name = message.get("content", "")
            if name:
                return str(name)
    return ""


def _source_attachment(config: "Config", rel_path: str) -> "Path | None":
    """Resolve an archived attachment path to a readable file, or None."""
    direct = config.workspace_path / rel_path
    if direct.is_file():
        return direct
    # Attachments saved before `make migrate-sessions` (#1112) still store
    # conversations/{id}/... paths.
    remapped = remap_legacy_rel_path(config, rel_path)
    if remapped is not None:
        legacy = config.workspace_path / remapped
        if legacy.is_file():
            return legacy
    return None


def copy_attachments(config: "Config", source_conv_id: str, dest_conv_id: str, messages: list[dict]) -> None:
    """Copy attachment files into the fork and rewrite their archive paths.

    A fork is an independent conversation; leaving paths pointing into the
    source would break its images and downloads as soon as the source is
    deleted. Best-effort per file: an attachment that cannot be resolved keeps
    its original path instead of failing the fork.
    """
    if not any(message.get("attachments") for message in messages):
        return
    dest_dir = uploads_dir(config, dest_conv_id)
    copied: dict[str, str] = {}
    used_names: set[str] = set()

    def _dest_for(source: "Path") -> "Path":
        if not dest_dir.exists():
            dest_dir.mkdir(parents=True, exist_ok=True)
        candidate = dest_dir / source.name
        stem, suffix = candidate.stem, candidate.suffix
        counter = 1
        while candidate.name in used_names or candidate.exists():
            counter += 1
            candidate = dest_dir / f"{stem}-{counter}{suffix}"
        used_names.add(candidate.name)
        return candidate

    for message in messages:
        attachments = message.get("attachments")
        if not attachments:
            continue
        rewritten = []
        for attachment in attachments:
            rel_path = attachment.get("path", "")
            if not rel_path:
                rewritten.append(attachment)
                continue
            if rel_path not in copied:
                source = _source_attachment(config, rel_path)
                if source is None:
                    copied[rel_path] = rel_path
                else:
                    dest = _dest_for(source)
                    shutil.copy2(source, dest)
                    copied[rel_path] = str(dest.relative_to(config.workspace_path))
            new_path = copied[rel_path]
            rewritten.append(attachment if new_path == rel_path else {**attachment, "path": new_path})
        message["attachments"] = rewritten


def fork_conversation(
    config: "Config",
    index: ConversationIndex,
    user_id: str,
    source_conv_id: str,
    *,
    message_index: int | None = None,
    message_timestamp: str = "",
) -> tuple["ConversationMeta", str]:
    """Copy ``source_conv_id``'s history up to an anchor into a new conversation.

    Returns the new conversation's metadata and the active model inherited
    from the slice (empty when the source never recorded one).
    """
    messages = read_archive(config, source_conv_id)
    anchor = resolve_anchor(messages, message_index=message_index, message_timestamp=message_timestamp)
    cut = turn_boundary(messages, anchor)
    sliced = messages[: cut + 1]

    source = index.get(source_conv_id)
    source_title = source.title if source else source_conv_id
    title = f"Fork of {source_title} (turn {count_turns(sliced)})"
    conv = index.create(user_id, title=title)
    try:
        # A fork title carries its provenance; background auto-titling must
        # not overwrite it later (#1072).
        conv = index.rename(conv.conv_id, title, manual=True) or conv
        copy_attachments(config, source_conv_id, conv.conv_id, sliced)
        write_archive(config, conv.conv_id, sliced)
    except BaseException:
        index.delete(conv.conv_id)
        raise
    return conv, latest_model(sliced)
