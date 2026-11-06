"""Subagent lifecycle tools — spawn / list / cancel (#1113).

Wraps the existing ``delegate_task`` machinery behind agent-facing
tools so a parent agent can manage its own subagents from the tool
surface:

- ``subagent_spawn`` — launch a child turn in the current session, with
  optional scoped vault-write permission.
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
    states = getattr(manager, "_conversations", None) or {}
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

    Args:
        task: Task description string with enough context for the child
            agent to work independently.
        model: Named model config for the subagent. Empty = inherit
            parent's model.
        allow_vault_write: When True, the child may persist pages via
            ``vault_write`` / ``vault_journal_append``. Default False —
            all vault writes stay blocked for children.

    Returns:
        ``ToolResult`` whose ``data["child_conv_id"]`` carries the spawned
        child's conversation id (``{parent}--child-{hex}``), whether the
        turn was awaited (``async_run=True``, result lands on ``data`` as
        the child answer) or spawn-only (``async_run=False``, spawn ack).
    """
    if not task or not isinstance(task, str) or not task.strip():
        return ToolResult(text="[error: task description is required]")

    parent_conv = _parent_conv_id(ctx)
    child_conv_id = f"{parent_conv}--child-{secrets.token_hex(4)}"
    text, data = await run_child_turn(
        ctx,
        task,
        model=model,
        allow_vault_write=allow_vault_write,
        child_conv_id_override=child_conv_id,
        async_run=async_run,
    )

    if not async_run:
        # Spawn-only mode: the turn never started, so hand back the ack
        # plus the child id for lifecycle tracking instead of a result.
        return ToolResult(text=text, data={"child_conv_id": child_conv_id})

    result_data: dict = {"child_conv_id": child_conv_id}
    if isinstance(data, dict) and data:
        result_data.update(data)
    return ToolResult(text=text, data=result_data)


def subagent_list(ctx: "Context") -> ToolResult:
    """List subagents spawned in the current session with status and task."""
    manager = ctx.manager
    subagents: list[dict] = []
    states = getattr(manager, "_conversations", None) or {}
    for conv_id in _child_conv_ids(ctx):
        state = states.get(conv_id)
        if state is None:
            continue
        task = getattr(state, "subagent_task", "") or ""
        agent_task = getattr(state, "agent_task", None)
        status = "running" if (state.busy and agent_task and not agent_task.done()) else "completed"
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
    child conversation stays tracked (it just stops running).
    """
    manager = ctx.manager
    if manager is None:
        return ToolResult(text="[error: subagent_cancel requires a ConversationManager]")
    state = manager.get_state(child_conv_id)
    if state is None:
        return ToolResult(text=f"[error: no subagent conversation {child_conv_id}]")
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
                "session to run a focused subtask. The child runs as an "
                "independent agent turn with access to the parent's tools "
                "and skills, minus delegation/activation and (by default) all "
                "vault access. Use this whenever the user asks you to spin up, "
                "fork off, or hand off a task to a sub-agent / child agent.\n\n"
                "Result shapes on ToolResult.data:\n"
                "- async_run=true (default): the awaited child answer lands on "
                "ToolResult.text, and ToolResult.data carries "
                '`{"child_conv_id": ...}`.\n'
                "- async_run=false: the spawn ack lands on ToolResult.text and "
                'ToolResult.data carries `{"child_conv_id": ...}`.'
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
                "status (running/completed) and task description. Use to check "
                "on or discover existing subagents before spawning another."
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
                "cancel a running subagent."
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
