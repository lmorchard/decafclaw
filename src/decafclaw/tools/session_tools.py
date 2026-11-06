"""Session-scoped messaging tools (#1114).

``session_send`` is the agent-facing producer for the session-messaging
primitive (``session_messaging.deliver_session_message``): a conversation
in a session — the parent or a delegated child — posts an asynchronous
message to the session root or a child of that root. Delivery is a WAKE
turn: it triggers a real handling turn in the target without
interrupting any in-flight turn.

Sends are session-scoped: the target must be the sender's session root
(``parent_conv_id_of`` of the sender) or a child of that root
(``is_child_conv_id``). Cross-session sends are refused.
"""

import logging
from typing import TYPE_CHECKING

from ..media import ToolResult
from ..session_messaging import deliver_session_message, is_child_conv_id, parent_conv_id_of

if TYPE_CHECKING:
    from decafclaw.context import Context

log = logging.getLogger(__name__)


def _resolve_conv_id(ctx: "Context") -> str:
    """Match the notes_tools fallback chain so the sender id is stable."""
    return ctx.conv_id or ctx.channel_id or ""


async def tool_session_send(ctx: "Context", target_conv_id: str, text: str) -> ToolResult:
    """Post an asynchronous session message to the session root or a child of it."""
    manager = ctx.manager
    if manager is None:
        return ToolResult(text="[error: session_send requires a ConversationManager; no manager on ctx]")
    sender_conv_id = _resolve_conv_id(ctx)
    if not sender_conv_id:
        return ToolResult(text="[error: session_send requires a conversation id; none on ctx]")
    if not target_conv_id or not text or not text.strip():
        return ToolResult(text="[error: session_send needs a non-empty target_conv_id and text]")

    # Session-scope guard: the target must be the sender's session root or
    # a child of that root. Anything else is a cross-session send —
    # refused (#1114: messaging stays inside one parent+children session).
    session_root = parent_conv_id_of(sender_conv_id) or sender_conv_id
    if target_conv_id == sender_conv_id:
        return ToolResult(
            text=f"[error: session_send cannot target the sending conversation itself ({target_conv_id})]"
        )
    if target_conv_id != session_root and not is_child_conv_id(target_conv_id, session_root):
        return ToolResult(
            text=(
                f"[error: refused — target conversation {target_conv_id} is outside this "
                f"session (sender {sender_conv_id}; session root {session_root}). "
                "session_send can only reach the session root or children of it.]"
            )
        )

    # The delivered turn runs from a bare prompt with no history, so the
    # handling agent needs the sender's identity to route a reply.
    prompt = (
        f"[Session message from conversation `{sender_conv_id}`]\n\n"
        f"{text}\n\n"
        "(This turn was triggered by an asynchronous session message, not a "
        "user message. Reply via `session_send` to the sending conversation "
        "if one is needed, or answer BACKGROUND_WAKE_OK to end silently.)"
    )
    try:
        future = await deliver_session_message(
            manager,
            target_conv_id,
            sender_conv_id,
            prompt,
            user_id=ctx.user_id,
        )
    except Exception as exc:
        log.warning("session_send from %s to %s failed: %s", sender_conv_id, target_conv_id, exc)
        return ToolResult(text=f"[error: delivering session message failed: {exc}]")

    # Both flags are attached by the manager's enqueue_turn; see
    # scheduled_wakes.py for the established read idiom.
    if getattr(future, "rate_limited", None) is True:
        return ToolResult(
            text=(
                f"[error: session message to {target_conv_id} was dropped by the wake rate "
                "limiter — too many wakes in the current window; retry later.]"
            )
        )
    queued = getattr(future, "queued", False) is True
    detail = " (will run after the conversation's current turn)" if queued else ""
    return ToolResult(
        text=f"Session message queued for delivery to {target_conv_id}{detail}.",
        data={
            "target_conv_id": target_conv_id,
            "source_conv_id": sender_conv_id,
            "queued": queued,
        },
    )


SESSION_TOOLS = {
    "session_send": tool_session_send,
}


SESSION_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "priority": "normal",
        "function": {
            "name": "session_send",
            "description": (
                "Send an asynchronous message to another conversation in "
                "this session — the session root (parent) conversation or "
                "a child of it. Delivered as a wake turn: it queues behind "
                "any in-flight turn in the target conversation and never "
                "interrupts it. This does NOT return the target's reply — "
                "a reply, if any, arrives later as a session message back "
                "to you. Use this for fire-and-forget progress reports or "
                "non-blocking requests to peer conversations while you "
                "keep working; use `delegate_task` instead when you need "
                "a sub-agent's result back synchronously."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "target_conv_id": {
                        "type": "string",
                        "description": (
                            "Conversation id of the recipient: the session "
                            "root, or a child of it (``{root}--child-...``). "
                            "Cross-session targets are refused."
                        ),
                    },
                    "text": {
                        "type": "string",
                        "description": "Message body delivered to the target conversation.",
                    },
                },
                "required": ["target_conv_id", "text"],
            },
        },
    },
]
