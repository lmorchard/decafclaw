"""Tools for scheduling asynchronous wake turns in long-running workflows (#1103)."""

import logging
import time
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from ..media import ToolResult, WidgetRequest
from ..scheduled_wakes import ScheduledWakeManager

if TYPE_CHECKING:
    from decafclaw.context import Context

log = logging.getLogger(__name__)


def _get_wake_manager(ctx: "Context") -> ScheduledWakeManager:
    """Resolve the ScheduledWakeManager from manager or construct an ephemeral one."""
    if ctx.manager is not None and hasattr(ctx.manager, "scheduled_wakes"):
        return ctx.manager.scheduled_wakes
    return ScheduledWakeManager(ctx.config, manager=ctx.manager)


async def tool_schedule_turn(
    ctx: "Context",
    delay_seconds: int,
    reason: str,
    metadata: dict[str, Any] | None = None,
) -> ToolResult:
    """Schedule an autonomous future wake turn in this conversation after a delay."""
    if getattr(ctx, "is_unattended", False) or getattr(ctx, "is_child", False):
        return ToolResult(
            text="[error: schedule_turn is only available in interactive user conversations]",
            data={"error": "unattended_or_child_not_permitted"},
        )

    conv_id = ctx.conv_id
    if not conv_id:
        return ToolResult(
            text="[error: No active conversation ID found in context to schedule a turn]",
            data={"error": "missing_conv_id"},
        )

    if not isinstance(delay_seconds, (int, float)) or int(delay_seconds) < 1:
        return ToolResult(
            text=f"[error: delay_seconds must be a positive integer >= 1 (got {delay_seconds})]",
            data={"error": "invalid_delay", "delay_seconds": delay_seconds},
        )

    reason_str = str(reason).strip() if reason else ""
    if not reason_str:
        return ToolResult(
            text="[error: reason is required when scheduling a future wake turn]",
            data={"error": "missing_reason"},
        )

    mgr = _get_wake_manager(ctx)
    try:
        wake = await mgr.schedule(
            conv_id=conv_id,
            delay_seconds=int(delay_seconds),
            reason=reason_str,
            metadata=metadata,
            user_id=getattr(ctx, "user_id", "") or "",
            channel_id=getattr(ctx, "channel_id", "") or "",
            thread_id=getattr(ctx, "thread_id", "") or "",
            transport=getattr(ctx, "transport", "") or "",
        )
        return ToolResult(
            text=(
                f"Scheduled wake turn '{wake.schedule_id}' in {wake.delay_seconds} seconds "
                f"(at {wake.fire_at}): {wake.reason}"
            ),
            data=asdict(wake),
        )
    except ValueError as exc:
        return ToolResult(text=f"[error: {exc}]", data={"error": str(exc)})
    except OSError as exc:
        log.warning("Persistence error scheduling turn: %s", exc)
        return ToolResult(text=f"[error: Failed to persist schedule: {exc}]", data={"error": "persistence_failure"})
    except Exception as exc:
        log.warning("Unexpected error scheduling turn: %s", exc)
        return ToolResult(text=f"[error: Failed to schedule turn: {exc}]", data={"error": str(exc)})


async def tool_schedule_cancel(ctx: "Context", schedule_id: str) -> ToolResult:
    """Cancel a pending scheduled wake turn."""
    conv_id = ctx.conv_id
    if not conv_id:
        return ToolResult(
            text="[error: No active conversation ID found in context]",
            data={"error": "missing_conv_id"},
        )

    if not schedule_id or not str(schedule_id).strip():
        return ToolResult(
            text="[error: schedule_id is required]",
            data={"error": "missing_schedule_id"},
        )

    sched_id = str(schedule_id).strip()
    mgr = _get_wake_manager(ctx)
    try:
        cancelled = await mgr.cancel(conv_id=conv_id, schedule_id=sched_id)
    except OSError as exc:
        log.warning("Persistence error cancelling schedule %s: %s", sched_id, exc)
        return ToolResult(
            text=f"[error: Failed to update schedule sidecar: {exc}]", data={"error": "persistence_failure"}
        )

    if cancelled:
        return ToolResult(
            text=f"Cancelled scheduled wake '{sched_id}'.",
            data={"schedule_id": sched_id, "cancelled": True},
        )
    return ToolResult(
        text=f"[error: Scheduled wake '{sched_id}' not found]",
        data={"schedule_id": sched_id, "cancelled": False},
    )


def tool_schedule_list(ctx: "Context") -> ToolResult:
    """List pending scheduled wake turns for the current conversation."""
    conv_id = ctx.conv_id
    if not conv_id:
        return ToolResult(
            text="[error: No active conversation ID found in context]",
            data={"error": "missing_conv_id"},
        )

    mgr = _get_wake_manager(ctx)
    wakes = mgr.list_schedules(conv_id=conv_id)
    if not wakes:
        return ToolResult(
            text="No pending scheduled wake turns for this conversation.",
            data={"schedules": []},
        )

    now_ts = time.time()
    lines = [f"Found {len(wakes)} pending scheduled wake turn(s):"]
    for w in wakes:
        remaining = max(0, int(w.fire_at_ts - now_ts))
        lines.append(f"- {w.schedule_id}: fires in {remaining}s (at {w.fire_at}) — {w.reason}")

    widget = WidgetRequest(
        widget_type="data_table",
        data={
            "caption": f"schedule_list: {len(wakes)} pending wake turn(s)",
            "columns": [
                {"key": "schedule_id", "label": "Schedule ID"},
                {"key": "fire_at", "label": "Fire At"},
                {"key": "remaining", "label": "Remaining"},
                {"key": "reason", "label": "Reason"},
            ],
            "rows": [
                {
                    "schedule_id": w.schedule_id,
                    "fire_at": w.fire_at,
                    "remaining": f"{max(0, int(w.fire_at_ts - now_ts))}s",
                    "reason": w.reason,
                }
                for w in wakes
            ],
        },
        target="inline",
    )
    return ToolResult(
        text="\n".join(lines),
        data={"schedules": [asdict(w) for w in wakes]},
        widget=widget,
    )


SCHEDULE_TOOLS = {
    "schedule_turn": tool_schedule_turn,
    "schedule_cancel": tool_schedule_cancel,
    "schedule_list": tool_schedule_list,
}

SCHEDULE_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "priority": "normal",
        "function": {
            "name": "schedule_turn",
            "description": (
                "Schedule an autonomous future wake turn in the current conversation after a specified delay. "
                "Use this for long-running operations (builds, deployments, test suites, remote jobs) "
                "where you want to yield the current turn cleanly without blocking on wait() or "
                "holding the connection open. When the timer expires, an autonomous turn will wake up "
                "with the given reason in context. You can reply with BACKGROUND_WAKE_OK during that wake "
                "to remain silent if no user notification is required."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "delay_seconds": {
                        "type": "integer",
                        "description": "Number of seconds in the future to wake up (minimum 1)",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Reason for the wake-up (e.g. 'Check status of build job-42')",
                    },
                    "metadata": {
                        "type": "object",
                        "description": "Optional arbitrary metadata dict stored with the schedule and passed into the wake turn",
                    },
                },
                "required": ["delay_seconds", "reason"],
            },
        },
    },
    {
        "type": "function",
        "priority": "normal",
        "function": {
            "name": "schedule_cancel",
            "description": "Cancel a pending scheduled wake turn by its schedule_id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "schedule_id": {
                        "type": "string",
                        "description": "The schedule ID to cancel (from schedule_turn or schedule_list)",
                    },
                },
                "required": ["schedule_id"],
            },
        },
    },
    {
        "type": "function",
        "priority": "normal",
        "function": {
            "name": "schedule_list",
            "description": "List all pending scheduled wake turns for the current conversation.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
]
