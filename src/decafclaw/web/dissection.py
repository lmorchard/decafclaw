"""Create a dissection conversation from a parent (#1570).

Owns the web-specific parts of a fork: the conversation index entry, the
seeded first turn, and the notice left in the parent. The extraction and brief
rendering live in :mod:`decafclaw.dissect` so the agent tool, REST endpoint,
and ``/dissect`` command share one implementation.
"""

from __future__ import annotations

import logging
from typing import Callable

from ..archive import append_message, read_archive
from ..dissect import build_dissection_brief, find_dissection_focus
from .conversations import ConversationIndex

log = logging.getLogger(__name__)

# The sidebar titles are compact; keep the source title recognizable without
# letting a long conversation title push the "Dissect" marker out of view.
MAX_TITLE_CHARS = 72


class DissectionError(Exception):
    """A dissection could not be started (nothing to copy, no manager, ...)."""


def _source_title(source_conv_id: str, index: ConversationIndex) -> str:
    meta = index.get(source_conv_id)
    if meta and meta.title and meta.title != "New conversation":
        return meta.title
    return ""


def _child_title(source_title: str, tool_name: str) -> str:
    if source_title:
        suffix = source_title
    elif tool_name:
        suffix = f"{tool_name} failure"
    else:
        suffix = "conversation"
    title = f"Dissect: {suffix}"
    if len(title) > MAX_TITLE_CHARS:
        title = title[: MAX_TITLE_CHARS - 1].rstrip() + "…"
    return title


def resolve_conversation_model(config, conv_id: str, manager=None) -> str:
    """Best-effort active model for a conversation.

    Archive records are the durable source (the same reverse scan the web
    history loader uses); live manager state covers a model changed in this
    process before the next turn. Empty means "use the default".
    """
    for msg in reversed(read_archive(config, conv_id)):
        if msg.get("role") == "model":
            name = str(msg.get("content") or "").strip()
            if name:
                return name
            break
    if manager is not None:
        state = manager.get_state(conv_id)
        if state is not None:
            name = str(getattr(state.persisted, "active_model", "") or "").strip()
            if name:
                return name
    return ""


def _web_context_setup(config) -> Callable:
    """Transport context for a dissection turn run by the web manager."""

    def context_setup(ctx) -> None:
        from ..media import LocalFileMediaHandler

        ctx.media_handler = LocalFileMediaHandler(config)
        ctx.channel_name = "web"
        ctx.thread_id = ""
        ctx.transport = "web"

    return context_setup


async def create_dissection(
    config,
    source_conv_id: str,
    user_id: str,
    *,
    manager,
    focus_tool_call_id: str = "",
    instruction: str = "",
    model: str = "",
    context_setup: Callable | None = None,
) -> dict:
    """Fork ``source_conv_id`` into a seeded dissection conversation.

    The child starts its first turn immediately with the failure brief. A
    ``dissect_notice`` record is appended to the parent and emitted live so an
    open parent tab renders the link without an agent turn in the parent.
    """
    if manager is None:
        raise DissectionError("Chat service unavailable")
    messages = read_archive(config, source_conv_id)
    focus = find_dissection_focus(messages, focus_tool_call_id)
    if focus is None:
        raise DissectionError("This conversation has no messages to dissect")

    index = ConversationIndex(config)
    source_title = _source_title(source_conv_id, index)
    title = _child_title(source_title, focus.tool_name)
    child = index.create(user_id, title=title, forked_from=source_conv_id)
    # The generated title names the fork; keep periodic auto-titling from
    # replacing it with a generic summary (#1072).
    index.rename(child.conv_id, title, manual=True)

    active_model = model or resolve_conversation_model(config, source_conv_id, manager)
    if active_model:
        append_message(config, child.conv_id, {"role": "model", "content": active_model})

    brief = build_dissection_brief(
        messages,
        focus,
        source_conv_id=source_conv_id,
        source_title=source_title,
        instruction=instruction,
    )
    # archive_text is empty on purpose: the full brief must survive reload as
    # the child's first user message, not just live in the current prompt.
    await manager.send_message(
        child.conv_id,
        brief,
        user_id=user_id,
        context_setup=context_setup or _web_context_setup(config),
    )
    index.touch(child.conv_id)

    notice_data = {
        "fork_conv_id": child.conv_id,
        "fork_title": title,
        "source_conv_id": source_conv_id,
        "tool": focus.tool_name,
        "had_error": focus.has_error,
    }
    notice_text = f"Dissected {focus.tool_name or 'the conversation'} into “{title}”"
    append_message(
        config,
        source_conv_id,
        {"role": "dissect_notice", "content": notice_text, "data": notice_data},
    )
    try:
        await manager.emit(source_conv_id, {"type": "dissect_notice", **notice_data})
    except Exception:
        # The archive record above is the durable path; a live emit failure
        # only costs an already-open tab an immediate card.
        log.exception("Failed to emit dissect_notice for %s", source_conv_id)

    result = child.to_dict()
    result["title"] = title
    result["forked_from"] = source_conv_id
    return result
