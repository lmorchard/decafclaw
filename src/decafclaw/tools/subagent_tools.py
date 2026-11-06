"""Subagent lifecycle tools — spawn / list / cancel (#1113).

Wraps the existing ``delegate_task`` machinery behind agent-facing
tools so a parent agent can manage its own subagents from the tool
surface:

- ``subagent_spawn`` — launch a child turn in the current session, with
  optional scoped vault-write permission. Fire-and-forget by default
  (``async_run=True``); pass ``async_run=False`` to await the child.
- ``subagent_list`` — enumerate active/finished subagents of this
  session with status and task description.
- ``subagent_cancel`` — abort an active subagent's turn.

The child agent loop itself stays gated by the vault-access policy in
``delegate.py``: no vault reads unless opted in, no vault writes unless
opted in via ``allow_vault_write``.
"""

import json
import logging
import secrets
from typing import TYPE_CHECKING

from ..media import ToolResult

if TYPE_CHECKING:
    from decafclaw.context import Context

from .delegate import run_child_turn

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


async def subagent_spawn(
    ctx: "Context",
    task: str,
    model: str = "",
    allow_vault_write: bool = False,
    async_run: bool = True,
) -> ToolResult:
    """Spawn a subagent child agent conversation in the current session.

    The child runs as its own agent turn with the parent's tools and
    skills, minus the delegation/activation excludes and minus the
    vault/admin write gates unless explicitly allowed.

    By default (``async_run=True``) the spawn is asynchronous /
    fire-and-forget: the child turn is enqueued and the tool returns the
    spawn ack immediately — poll ``subagent_list`` for status and abort
    via ``subagent_cancel``. With ``async_run=False`` the tool awaits
    the child turn and returns its answer (delegate_task-style).

    Args:
        task: Task description string with enough context for the child
            agent to work independently.
        model: Named model config for the subagent. Empty = inherit
            parent's model.
        allow_vault_write: When True, the child may persist pages via
            ``vault_write`` / ``vault_journal_append``. Default False —
            all vault writes stay blocked for children.
        async_run: When True (default), run the child asynchronously and
            return the spawn ack at once. When False, wait for the child
            to finish and return its answer.

    Returns:
        ``ToolResult`` whose ``data["child_conv_id"]`` carries the spawned
        child's conversation id (``{parent}--child-{hex}``), whether the
        turn was fire-and-forget (``async_run=True``, spawn ack on
        ``text``) or awaited (``async_run=False``, child answer on
        ``text``).
    """
    if not task or not isinstance(task, str) or not task.strip():
        return ToolResult(text="[error: task description is required]")

    parent_conv = _parent_conv_id(ctx)
    child_conv_id = f"{parent_conv}--child-{secrets.token_hex(4)}"
    # Tool-facing polarity (#1113): async_run=True = run asynchronously
    # (fire-and-forget); async_run=False = await the child turn.
    text, data = await run_child_turn(
        ctx,
        task,
        model=model,
        allow_vault_write=allow_vault_write,
        child_conv_id_override=child_conv_id,
        await_child=not async_run,
    )

    result_data: dict = {"child_conv_id": child_conv_id}
    if isinstance(data, dict) and data:
        result_data.update(data)
    return ToolResult(text=text, data=result_data)


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
    "subagent_spawn": subagent_spawn,
    "subagent_list": subagent_list,
    "subagent_cancel": subagent_cancel,
}


SUBAGENT_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "priority": "critical",
        "timeout": None,
        "function": {
            "name": "subagent_spawn",
            "description": (
                "Spawn a subagent (child agent) conversation in the current "
                "session to run a focused subtask in the background. The "
                "child runs as an independent agent turn with access to the "
                "parent's tools and skills, minus delegation/activation and "
                "(by default) all vault access. Use this when the caller "
                "wants a subagent they can track or control — a background "
                "run with an id to poll via subagent_list and abort via "
                "subagent_cancel — or a child with scoped vault-write "
                "authoring (allow_vault_write). If you just need a subtask's "
                "result back in this turn, use delegate_task instead.\n\n"
                "By default the spawn is asynchronous: the tool returns a "
                "spawn ack immediately and the child runs in the background — "
                "poll subagent_list for status and use subagent_cancel to "
                "abort. Pass async_run=false to wait for the child to finish "
                "and get its answer directly.\n\n"
                "Result shapes on ToolResult.data:\n"
                "- async_run=true (default): the spawn ack lands on "
                "ToolResult.text and ToolResult.data carries "
                '`{"child_conv_id": ...}`; the child\'s eventual answer is '
                "not returned to this tool.\n"
                "- async_run=false: the awaited child answer lands on "
                "ToolResult.text, and ToolResult.data carries "
                '`{"child_conv_id": ...}`.'
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": (
                            "Task description string with enough context for the child agent to work independently."
                        ),
                    },
                    "model": {
                        "type": "string",
                        "description": "Named model config for the subagent. Omit to inherit parent's model.",
                    },
                    "allow_vault_write": {
                        "type": "boolean",
                        "description": (
                            "When true, the spawned subagent may write to the "
                            "vault via vault_write / vault_journal_append. Default "
                            "false; all other safety boundaries remain in place."
                        ),
                    },
                    "async_run": {
                        "type": "boolean",
                        "description": (
                            "When true (default), run the subagent asynchronously: "
                            "return the spawn ack immediately, then poll "
                            "subagent_list for status and use subagent_cancel to "
                            "abort. When false, block until the subagent finishes "
                            "and return its answer as this tool's result."
                        ),
                    },
                },
                "required": ["task"],
            },
        },
    },
    {
        "type": "function",
        "priority": "normal",
        "function": {
            "name": "subagent_list",
            "description": (
                "List subagents spawned in the current session with their "
                "status (queued/running/completed) and task description. Use "
                "to poll background subagents spawned with subagent_spawn, or "
                "to discover existing subagents before spawning another."
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
        "function": {
            "name": "subagent_cancel",
            "description": (
                "Abort an active subagent's turn. Provide the child_conv_id "
                "returned by subagent_spawn (or visible in subagent_list) to "
                "cancel a running subagent. Only subagents of your own "
                "conversation can be cancelled."
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
