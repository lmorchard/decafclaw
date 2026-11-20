"""Create a forked conversation from a parent (#1570).

Owns the web-specific parts of a fork: the conversation index entry, the
seeded first turn, and the notice left in the parent. The extraction and brief
rendering live in :mod:`decafclaw.fork` (general) and
:mod:`decafclaw.dissect` (failure template) so the agent tool, REST endpoint,
and chat commands share one implementation.
"""

from __future__ import annotations

import logging
from typing import Callable

from ..archive import append_message, read_archive
from ..dissect import build_dissection_brief
from ..fork import build_fork_brief, find_fork_focus, normalize_fork_template
from .conversations import ConversationIndex

log = logging.getLogger(__name__)

# The sidebar titles are compact; keep the source title recognizable without
# letting a long conversation title push the "Fork"/"Dissect" marker out of
# view.
MAX_TITLE_CHARS = 72


class ForkError(Exception):
    """A fork could not be started (nothing to copy, no manager, bad template)."""


def _source_title(source_conv_id: str, index: ConversationIndex) -> str:
    meta = index.get(source_conv_id)
    if meta and meta.title and meta.title != "New conversation":
        return meta.title
    return ""


def _child_title(source_title: str, tool_name: str, template: str) -> str:
    if template == "dissect":
        prefix = "Dissect"
        suffix = source_title or (f"{tool_name} failure" if tool_name else "conversation")
    else:
        prefix = "Fork"
        suffix = source_title or "conversation"
    title = f"{prefix}: {suffix}"
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
    """Transport context for a fork turn run by the web manager."""

    def context_setup(ctx) -> None:
        from ..media import LocalFileMediaHandler

        ctx.media_handler = LocalFileMediaHandler(config)
        ctx.channel_name = "web"
        ctx.thread_id = ""
        ctx.transport = "web"

    return context_setup


async def create_fork(
    config,
    source_conv_id: str,
    user_id: str,
    *,
    manager,
    template: str = "general",
    focus_tool_call_id: str = "",
    instruction: str = "",
    model: str = "",
    context_setup: Callable | None = None,
) -> dict:
    """Fork ``source_conv_id`` into a seeded top-level conversation.

    ``template="dissect"`` uses the structured failure brief; when the focus
    has no error it falls back to a general fork (a conversational branch).
    The child starts its first turn immediately. A ``fork_notice`` record is
    appended to the parent and emitted live so an open parent tab renders the
    link without an agent turn in the parent.
    """
    if manager is None:
        raise ForkError("Chat service unavailable")
    try:
        requested_template = normalize_fork_template(template)
    except ValueError as exc:
        raise ForkError(str(exc)) from exc

    messages = read_archive(config, source_conv_id)
    focus = find_fork_focus(messages, focus_tool_call_id)
    if focus is None:
        raise ForkError("This conversation has no messages to fork")

    # A dissection request without a failure in the latest exchange is just a
    # conversational branch; do not label it or prime it as a failure.
    effective_template = "dissect" if requested_template == "dissect" and focus.has_error else "general"

    index = ConversationIndex(config)
    source_title = _source_title(source_conv_id, index)
    title = _child_title(source_title, focus.tool_name, effective_template)
    child = index.create(user_id, title=title, forked_from=source_conv_id)
    # The generated title names the fork; keep periodic auto-titling from
    # replacing it with a generic summary (#1072).
    index.rename(child.conv_id, title, manual=True)

    active_model = model or resolve_conversation_model(config, source_conv_id, manager)
    if active_model:
        append_message(config, child.conv_id, {"role": "model", "content": active_model})

    if effective_template == "dissect":
        brief = build_dissection_brief(
            messages,
            focus,
            source_conv_id=source_conv_id,
            source_title=source_title,
            instruction=instruction,
        )
    else:
        brief = build_fork_brief(
            messages,
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
        "template": effective_template,
        "tool": focus.tool_name if effective_template == "dissect" else "",
        "had_error": focus.has_error if effective_template == "dissect" else False,
    }
    if effective_template == "dissect":
        notice_text = f"Dissected {focus.tool_name or 'the conversation'} into “{title}”"
    else:
        notice_text = f"Forked into “{title}”"
    append_message(
        config,
        source_conv_id,
        {"role": "fork_notice", "content": notice_text, "data": notice_data},
    )
    try:
        await manager.emit(source_conv_id, {"type": "fork_notice", **notice_data})
    except Exception:
        # The archive record above is the durable path; a live emit failure
        # only costs an already-open tab an immediate card.
        log.exception("Failed to emit fork_notice for %s", source_conv_id)

    result = child.to_dict()
    result["title"] = title
    result["forked_from"] = source_conv_id
    result["template"] = effective_template
    return result
