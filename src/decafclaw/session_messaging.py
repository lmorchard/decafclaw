"""Session-scoped asynchronous inter-conversation message passing (#1114).

A "session" is a parent conversation plus its delegated children (the
#1112 layout: a standalone conversation is its own single-conversation
session; delegated children live under the parent's session as
``{parent}--child-{hex}``).

This module owns the asynchronous, inter-conversation messaging inside a
session:

- child-conversation id conventions (``is_child_conv_id`` /
  ``parent_conv_id_of``);
- the delivery primitive (``deliver_session_message``) that enqueues a
  peer message into a target conversation as an asynchronous turn.

Delivery semantics: the target conversation's turn is serialized by the
conversation manager's busy check (one turn per conversation at a time),
so a delivered message either runs immediately or queues behind an
in-flight turn — it never interrupts active processing (#1114 success
condition 3).
"""

import asyncio
import logging
from typing import TYPE_CHECKING

from .conversation_manager import TurnKind

if TYPE_CHECKING:
    from decafclaw.context import Context

log = logging.getLogger(__name__)


def is_child_conv_id(conv_id: str, parent_conv_id: str) -> bool:
    """True when ``conv_id`` is a delegated child of ``parent_conv_id``.

    Child ids follow the ``{parent}--child-{hex}`` convention (#1112).
    Standalone conversations are not children of themselves.
    """
    return bool(parent_conv_id) and conv_id.startswith(f"{parent_conv_id}--child-")


def parent_conv_id_of(conv_id: str) -> str | None:
    """Return the parent conversation id for a child conv id, else ``None``.

    A non-child conversation has no parent. The marker search is anchored
    past position 0 so a parent whose own id merely contains the marker
    prefix shape is not misread.
    """
    marker = "--child-"
    idx = conv_id.find(marker)
    if idx <= 0:
        return None
    return conv_id[:idx]


async def deliver_session_message(
    manager,
    target_conv_id: str,
    source_conv_id: str,
    text: str,
    *,
    context_setup=None,
    user_id: str = "",
) -> "asyncio.Future":
    """Deliver an asynchronous peer message into the target conversation.

    The message is enqueued as a turn in ``target_conv_id`` and resolved
    when that conversation's turn completes. Delivery is asynchronous from
    the caller's perspective: the call returns the manager's future without
    blocking on the target's in-flight work. If the target conversation is
    mid-turn, the manager's busy check queues the delivered turn instead of
    interrupting it.

    Args:
        manager: ConversationManager owning the target conversation.
        target_conv_id: Conversation receiving the peer message.
        source_conv_id: Originating conversation (parent or peer) — recorded
            on the delivered turn's metadata for audit/debug.
        text: Message body to inject as the turn prompt.
        context_setup: Optional context setup closure for the delivered turn.
        user_id: User id to stamp on the delivered turn.

    Returns:
        The asyncio future the delivered turn resolves on. Errors and
        timeouts surface through the target's turn machinery as usual.
    """
    future = await manager.enqueue_turn(
        target_conv_id,
        kind=TurnKind.USER,
        prompt=text,
        history=[],
        context_setup=context_setup,
        user_id=user_id,
        metadata={"source_conv_id": source_conv_id, "session_message": True},
    )
    return future
