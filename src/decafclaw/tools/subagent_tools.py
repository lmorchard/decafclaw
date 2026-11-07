"""Subagent lifecycle tools — list / cancel (#1113).

Wraps the existing ``delegate_task`` machinery behind agent-facing
lifecycle tools so a parent agent can manage its own subagents from the
tool surface:

- ``subagent_list`` — enumerate active/finished subagents of this
  session with status and task description.
- ``subagent_cancel`` — abort an active subagent's turn.

Spawning is no longer a separate tool: ``delegate_task`` gained a
``background`` flag that folds in the former ``subagent_spawn``
fire-and-forget mode. ``delegate_task(background=true)`` returns a spawn
ack carrying a ``child_conv_id`` immediately (the child runs in the
background, pollable via ``subagent_list`` and abortable via
``subagent_cancel``); ``delegate_task(background=false)`` (the default)
awaits the child and returns its answer. The ``subagent_spawn`` tool is
retired — use the ``background`` flag on ``delegate_task`` instead.

The child agent loop itself stays gated by the vault-access policy in
``delegate.py``: no vault reads unless opted in, no vault writes unless
opted in via ``allow_vault_write``.
"""

import json
import logging
from typing import TYPE_CHECKING

from ..media import ToolResult

if TYPE_CHECKING:
    from decafclaw.context import Context

log = logging.getLogger(__name__)


def _parent_conv_id(ctx: "Context") -> str:
    return ctx.conv_id or ctx.channel_id


def _child_conv_ids(ctx: "Context") -> list[str]:
    """Return sorted child conversation ids spawned under the current
    conversation's session (``{parent}--child-{hex}``), per #1112's
    child-conv-id convention."""
    manager = ctx.manager
    parent_conv = _parent_conv_id(ctx)
    prefix = f"{parent_conv}--child-"
    states = manager._conversations if manager is not None else {}
    return sorted(conv_id for conv_id in states if conv_id.startswith(prefix) and conv_id != parent_conv)


def subagent_list(ctx: "Context") -> ToolResult:
    """List subagents spawned in the current session with status and task."""
    manager = ctx.manager
    subagents: list[dict] = []
    states = manager._conversations if manager is not None else {}
    for conv_id in _child_conv_ids(ctx):
        state = states.get(conv_id)
        if state is None:
            continue
        task = state.subagent_task or ""
        agent_task = state.agent_task
        if state.busy and agent_task and not agent_task.done():
            status = "running"
        elif state.inmemory_turn_data:
            # A turn is enqueued but not yet dispatched (queued behind a
            # busy turn or awaiting the drain) — not running, not done.
            status = "queued"
        else:
            status = "completed"
        subagents.append(
            {
                "child_conv_id": conv_id,
                "status": status,
                "task": task,
            }
        )
    if not subagents:
        return ToolResult(text="No subagents found in this session.", data={"subagents": []})
    return ToolResult(text=json.dumps(subagents), data={"subagents": subagents})


async def subagent_cancel(ctx: "Context", child_conv_id: str) -> ToolResult:
    """Abort an active subagent's turn.

    Cancels the in-flight child turn via the ConversationManager; the
    child conversation stays tracked (it just stops running). Only
    subagents of the caller's own conversation are cancellable — the
    target must be a tracked ``{caller}--child-{hex}`` conversation.
    """
    manager = ctx.manager
    if manager is None:
        return ToolResult(text="[error: subagent_cancel requires a ConversationManager]")
    if child_conv_id not in _child_conv_ids(ctx):
        return ToolResult(text=f"[error: {child_conv_id} is not a subagent of this conversation]")
    await manager.cancel_turn(child_conv_id)
    return ToolResult(text=f"[subagent cancelled: {child_conv_id}]")


SUBAGENT_TOOLS = {
    "subagent_list": subagent_list,
    "subagent_cancel": subagent_cancel,
}


SUBAGENT_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "priority": "normal",
        "writes": False,
        "function": {
            "name": "subagent_list",
            "description": (
                "List subagents spawned in the current session with their "
                "status (queued/running/completed) and task description. Use "
                "to poll background subagents launched via delegate_task "
                "(background=true), or to discover existing subagents before "
                "dispatching another."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "priority": "normal",
        "writes": True,
        "function": {
            "name": "subagent_cancel",
            "description": (
                "Abort an active subagent's turn. Provide the child_conv_id "
                "returned by a background delegate_task spawn (or visible in "
                "subagent_list) to cancel a running subagent. Only subagents "
                "of your own conversation can be cancelled."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "child_conv_id": {
                        "type": "string",
                        "description": (
                            "Conversation id of the subagent to cancel (format: {parent_conv_id}--child-{hex})."
                        ),
                    },
                },
                "required": ["child_conv_id"],
            },
        },
    },
]
