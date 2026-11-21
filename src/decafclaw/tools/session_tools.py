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

from ..events import emit_for_ctx
from ..media import ToolResult
from ..session_messaging import deliver_session_message, is_child_conv_id, parent_conv_id_of

if TYPE_CHECKING:
    from decafclaw.context import Context

log = logging.getLogger(__name__)


def _resolve_conv_id(ctx: "Context") -> str:
    """Match the notes_tools fallback chain so the sender id is stable."""
    return ctx.conv_id or ctx.channel_id or ""


async def _emit_switch_event(
    ctx: "Context",
    sender_conv_id: str,
    target_conv_id: str,
    title: str,
    reason: str,
) -> None:
    event_payload = {
        "type": "switch_conversation",
        "conv_id": sender_conv_id,
        "target_conv_id": target_conv_id,
        "title": title,
        "reason": reason,
    }
    emit = emit_for_ctx(ctx)
    if emit:
        try:
            await emit(sender_conv_id, event_payload)
        except Exception as exc:
            log.warning("Failed to emit switch_conversation event: %s", exc)
    elif ctx.event_bus:
        try:
            context_id = getattr(ctx, "context_id", "")
            await ctx.event_bus.publish({**event_payload, "context_id": context_id})
        except Exception as exc:
            log.warning("Failed to publish switch_conversation to event bus: %s", exc)


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


def _resolve_username(ctx: "Context") -> str:
    """Resolve the active human username for session lookups."""
    username = getattr(ctx, "username", None)
    if username:
        return str(username)
    if ctx.user_id:
        parts = ctx.user_id.split("-")
        if len(parts) >= 2 and parts[0] == "web":
            return parts[1]
        return ctx.user_id
    return "lmorchard"


async def tool_session_list(ctx: "Context", limit: int = 10, include_archived: bool = False) -> ToolResult:
    """List recent conversation sessions."""
    from ..web.conversations import ConversationIndex

    username = _resolve_username(ctx)
    index = ConversationIndex(ctx.config)
    convs = index.list_for_user(username, include_archived=include_archived)
    if not convs:
        return ToolResult(text="No conversation sessions found.", data={"sessions": []})

    limit = max(1, min(int(limit), 50))
    selected = convs[:limit]
    lines = [f"Found {len(selected)} recent session(s):"]
    results_data = []
    for c in selected:
        is_home = c.conv_id.endswith("-home")
        tags = []
        if is_home:
            tags.append("Home/Lobby")
        if c.archived:
            tags.append("Archived")
        tag = f" [{' / '.join(tags)}]" if tags else ""
        lines.append(f"- `{c.conv_id}`: **{c.title}**{tag} (updated {c.updated_at[:16].replace('T', ' ')})")
        results_data.append(
            {
                "conv_id": c.conv_id,
                "title": c.title,
                "updated_at": c.updated_at,
                "is_home": is_home,
                "archived": c.archived,
            }
        )
    return ToolResult(text="\n".join(lines), data={"sessions": results_data})


async def tool_session_search(ctx: "Context", query: str, limit: int = 5, include_archived: bool = False) -> ToolResult:
    """Search conversation sessions by title."""
    from ..preempt_search import tokenize
    from ..web.conversations import ConversationIndex

    if not query or not query.strip():
        return ToolResult(text="[error: session_search requires a non-empty query]")

    username = _resolve_username(ctx)
    index = ConversationIndex(ctx.config)
    convs = index.list_for_user(username, include_archived=include_archived)
    if not convs:
        return ToolResult(text=f"No conversation sessions found matching '{query}'", data={"matches": []})

    query_lower = query.lower().strip()
    query_tokens = set(tokenize(query_lower))

    scored = []
    for c in convs:
        title_lower = c.title.lower()
        score = 0
        if query_lower in title_lower:
            score += 10
        title_tokens = set(tokenize(title_lower))
        overlap = len(query_tokens & title_tokens)
        score += overlap * 3
        if score > 0:
            scored.append((score, c))

    scored.sort(key=lambda x: (x[0], x[1].updated_at), reverse=True)
    limit = max(1, min(int(limit), 20))
    top = scored[:limit]

    if not top:
        return ToolResult(text=f"No sessions found matching '{query}'", data={"matches": []})

    lines = [f"Found {len(top)} session(s) matching '{query}':"]
    results_data = []
    for _score, c in top:
        is_home = c.conv_id.endswith("-home")
        tags = []
        if is_home:
            tags.append("Home/Lobby")
        if c.archived:
            tags.append("Archived")
        tag = f" [{' / '.join(tags)}]" if tags else ""
        lines.append(f"- `{c.conv_id}`: **{c.title}**{tag} (updated {c.updated_at[:16].replace('T', ' ')})")
        results_data.append(
            {
                "conv_id": c.conv_id,
                "title": c.title,
                "updated_at": c.updated_at,
                "is_home": is_home,
                "archived": c.archived,
            }
        )
    return ToolResult(text="\n".join(lines), data={"matches": results_data})


async def tool_session_switch(ctx: "Context", conv_id: str, reason: str = "") -> ToolResult:
    """Instruct the client (web UI, voice companion, mobile app) to switch to another conversation."""
    if not conv_id or not conv_id.strip():
        return ToolResult(text="[error: session_switch requires a non-empty conv_id]")

    from ..web.conversations import ConversationIndex

    username = _resolve_username(ctx)
    index = ConversationIndex(ctx.config)

    target_conv_id = conv_id.strip()
    if target_conv_id.lower() in ("home", "lobby", "default"):
        home_conv = index.get_or_create_home(username)
        target_conv_id = home_conv.conv_id
        target_title = home_conv.title
    else:
        meta = index.get(target_conv_id)
        if meta is None:
            return ToolResult(text=f"[error: conversation '{target_conv_id}' not found in index]")
        target_title = meta.title

    sender_conv_id = _resolve_conv_id(ctx)
    if sender_conv_id:
        await _emit_switch_event(ctx, sender_conv_id, target_conv_id, target_title, reason)

    msg = f"Switching client to session '{target_title}' (`{target_conv_id}`)."
    if reason:
        msg += f" Reason: {reason}"
    return ToolResult(
        text=msg,
        data={
            "target_conv_id": target_conv_id,
            "title": target_title,
            "reason": reason,
        },
    )


async def tool_session_home(ctx: "Context", reason: str = "") -> ToolResult:
    """Switch the client to the canonical Home/Lobby conversation."""
    return await tool_session_switch(ctx, "home", reason=reason or "Returning to home lobby")


async def tool_session_create(ctx: "Context", title: str = "", initial_topic: str = "") -> ToolResult:
    """Create a new conversation session and immediately switch the client into it."""
    from ..web.conversations import ConversationIndex

    username = _resolve_username(ctx)
    index = ConversationIndex(ctx.config)
    conv_title = title.strip() or initial_topic.strip() or "New session"
    new_conv = index.create(username, title=conv_title)

    sender_conv_id = _resolve_conv_id(ctx)
    if sender_conv_id:
        await _emit_switch_event(
            ctx,
            sender_conv_id,
            new_conv.conv_id,
            new_conv.title,
            f"Created new session: {conv_title}",
        )

    return ToolResult(
        text=f"Created and switched client to new session '{new_conv.title}' (`{new_conv.conv_id}`).",
        data={
            "target_conv_id": new_conv.conv_id,
            "title": new_conv.title,
        },
    )


SESSION_TOOLS = {
    "session_send": tool_session_send,
    "session_list": tool_session_list,
    "session_search": tool_session_search,
    "session_switch": tool_session_switch,
    "session_home": tool_session_home,
    "session_create": tool_session_create,
}


SESSION_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "priority": "normal",
        "writes": True,
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
    {
        "type": "function",
        "priority": "normal",
        "writes": False,
        "function": {
            "name": "session_list",
            "description": (
                "List recent conversation sessions for the active user, showing "
                "conversation IDs, titles, and last updated timestamps. Use this "
                "to see available sessions or find a session to switch into."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of sessions to return (default 10, max 50).",
                    },
                    "include_archived": {
                        "type": "boolean",
                        "description": "Whether to include archived sessions (default false).",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "priority": "normal",
        "writes": False,
        "function": {
            "name": "session_search",
            "description": (
                "Search past conversation sessions by topic, keyword, or title. "
                "Returns matching conversation IDs and titles ranked by relevance. "
                "Use this when the user asks to return to, resume, or check an "
                "earlier conversation or project."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Keywords or search phrase matching conversation titles.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of matching sessions to return (default 5).",
                    },
                    "include_archived": {
                        "type": "boolean",
                        "description": "Whether to include archived sessions (default false).",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "priority": "normal",
        "writes": True,
        "function": {
            "name": "session_switch",
            "description": (
                "Instruct the connected client (voice companion, web UI, mobile app) "
                "to redirect its active conversation view to another conversation. "
                "Pass 'home' or 'lobby' to return to the canonical home conversation."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "conv_id": {
                        "type": "string",
                        "description": "Target conversation ID to switch to, or 'home'/'lobby'.",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Optional human-readable explanation of why the switch was made.",
                    },
                },
                "required": ["conv_id"],
            },
        },
    },
    {
        "type": "function",
        "priority": "normal",
        "writes": True,
        "function": {
            "name": "session_home",
            "description": (
                "Instruct the connected client (voice companion, web UI, mobile app) "
                "to return to the canonical global Home / Lobby conversation."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "Optional reason for returning home.",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "priority": "normal",
        "writes": True,
        "function": {
            "name": "session_create",
            "description": (
                "Create a dedicated new conversation session and immediately "
                "switch the client into it. Use when starting a new project, "
                "topic, or when the user asks for a fresh thread."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Title for the new conversation session.",
                    },
                    "initial_topic": {
                        "type": "string",
                        "description": "Optional topic or description for the session.",
                    },
                },
            },
        },
    },
]
