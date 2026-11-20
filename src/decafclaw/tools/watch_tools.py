"""Tools for one-shot file watches that wake the agent on filesystem events (#1560)."""

import logging
import time
from typing import TYPE_CHECKING

from ..file_watches import WATCH_EVENTS, FileWatchManager, display_watch_path
from ..media import ToolResult, WidgetRequest
from ..secret_policy import is_refused
from ..web.workspace_paths import PathContainmentError, resolve_contained
from .schedule_tools import _is_disallowed_task_mode

if TYPE_CHECKING:
    from decafclaw.context import Context

log = logging.getLogger(__name__)


def _get_watch_manager(ctx: "Context") -> FileWatchManager:
    """Resolve the FileWatchManager from manager or construct an ephemeral one."""
    if ctx.manager is not None and hasattr(ctx.manager, "file_watches"):
        return ctx.manager.file_watches
    return FileWatchManager(ctx.config, manager=ctx.manager)


def _resolve_watch_path(ctx: "Context", path: str):
    """Resolve a workspace path for watching and refuse protected secret files.

    Returns ``(target, None)`` on success or ``(None, error_result)``.
    """
    if not path or not str(path).strip():
        return None, ToolResult(text="[error: path is required]", data={"error": "missing_path"})
    try:
        target = resolve_contained(ctx.config.workspace_path, str(path).strip(), label="the workspace")
    except PathContainmentError:
        target = None
    if target is None:
        return None, ToolResult(
            text="[error: path must stay inside the workspace]",
            data={"error": "path_outside_workspace", "path": path},
        )
    try:
        agent_rel = target.relative_to(ctx.config.agent_path.resolve())
    except ValueError:
        agent_rel = None
    if agent_rel is not None and is_refused(str(agent_rel), ctx.config):
        return None, ToolResult(
            text=f"[error: path '{path}' is a protected secret and cannot be watched]",
            data={"error": "path_refused", "path": path},
        )
    return target, None


async def tool_watch_file(
    ctx: "Context",
    path: str,
    event: str = "modify",
    timeout: int = 3600,
    reason: str = "",
) -> ToolResult:
    """Arm a one-shot watch that wakes the agent on the next filesystem event."""
    if getattr(ctx, "is_child", False) or _is_disallowed_task_mode(ctx) or getattr(ctx, "parent_is_unattended", False):
        return ToolResult(
            text="[error: watch_file is only available in interactive user conversations]",
            data={"error": "unattended_or_child_not_permitted"},
        )

    conv_id = ctx.conv_id
    if not conv_id:
        return ToolResult(
            text="[error: No active conversation ID found in context to watch a file]",
            data={"error": "missing_conv_id"},
        )

    target, error = _resolve_watch_path(ctx, path)
    if error is not None:
        return error
    assert target is not None

    if not isinstance(timeout, (int, float)) or int(timeout) < 1:
        return ToolResult(
            text=f"[error: timeout must be a positive integer >= 1 (got {timeout})]",
            data={"error": "invalid_timeout", "timeout": timeout},
        )

    event_str = str(event).strip().lower() if event else "modify"
    if event_str not in WATCH_EVENTS:
        return ToolResult(
            text=f"[error: event must be one of {', '.join(WATCH_EVENTS)} (got {event!r})]",
            data={"error": "invalid_event", "event": event},
        )

    mgr = _get_watch_manager(ctx)
    try:
        watch = await mgr.watch(
            conv_id=conv_id,
            path=str(target),
            event=event_str,
            timeout_seconds=int(timeout),
            reason=str(reason).strip() if reason else "",
            user_id=getattr(ctx, "user_id", "") or "",
            channel_id=getattr(ctx, "channel_id", "") or "",
            thread_id=getattr(ctx, "thread_id", "") or "",
            transport=getattr(ctx, "transport", "") or "",
        )
    except ValueError as exc:
        return ToolResult(text=f"[error: {exc}]", data={"error": str(exc)})
    except OSError as exc:
        log.warning("Persistence error arming file watch: %s", exc)
        return ToolResult(text=f"[error: Failed to persist file watch: {exc}]", data={"error": "persistence_failure"})
    except Exception as exc:
        log.warning("Unexpected error arming file watch: %s", exc)
        return ToolResult(text=f"[error: Failed to watch file: {exc}]", data={"error": str(exc)})

    display = display_watch_path(ctx.config, target)
    text = f"Watching {watch.event} on {display} (watch '{watch.watch_id}', expires at {watch.expires_at})"
    if watch.reason:
        text += f": {watch.reason}"
    return ToolResult(
        text=text,
        data={
            "watch_id": watch.watch_id,
            "path": display,
            "event": watch.event,
            "reason": watch.reason,
            "timeout_seconds": watch.timeout_seconds,
            "created_at": watch.created_at,
            "expires_at": watch.expires_at,
        },
    )


async def tool_watch_cancel(ctx: "Context", watch_id: str) -> ToolResult:
    """Disarm a pending file watch."""
    conv_id = ctx.conv_id
    if not conv_id:
        return ToolResult(
            text="[error: No active conversation ID found in context]",
            data={"error": "missing_conv_id"},
        )
    if not watch_id or not str(watch_id).strip():
        return ToolResult(
            text="[error: watch_id is required]",
            data={"error": "missing_watch_id"},
        )

    wid = str(watch_id).strip()
    mgr = _get_watch_manager(ctx)
    try:
        cancelled = await mgr.cancel(conv_id=conv_id, watch_id=wid)
    except OSError as exc:
        log.warning("Persistence error cancelling file watch %s: %s", wid, exc)
        return ToolResult(
            text=f"[error: Failed to update file watch sidecar: {exc}]",
            data={"error": "persistence_failure"},
        )

    if cancelled:
        return ToolResult(
            text=f"Cancelled file watch '{wid}'.",
            data={"watch_id": wid, "cancelled": True},
        )
    return ToolResult(
        text=f"[error: File watch '{wid}' not found]",
        data={"watch_id": wid, "cancelled": False},
    )


def tool_watch_list(ctx: "Context") -> ToolResult:
    """List pending file watches for the current conversation."""
    conv_id = ctx.conv_id
    if not conv_id:
        return ToolResult(
            text="[error: No active conversation ID found in context]",
            data={"error": "missing_conv_id"},
        )

    mgr = _get_watch_manager(ctx)
    watches = mgr.list_watches(conv_id=conv_id)
    if not watches:
        return ToolResult(
            text="No pending file watches for this conversation.",
            data={"watches": []},
        )

    now_ts = time.time()
    rows = []
    lines = [f"Found {len(watches)} pending file watch(es):"]
    for w in watches:
        display = display_watch_path(ctx.config, w.path)
        remaining = max(0, int(w.expires_at_ts - now_ts))
        lines.append(f"- {w.watch_id}: {w.event} {display}, expires in {remaining}s — {w.reason or '(no reason)'}")
        rows.append(
            {
                "watch_id": w.watch_id,
                "path": display,
                "event": w.event,
                "expires_at": w.expires_at,
                "remaining": remaining,
                "reason": w.reason,
            }
        )

    widget = WidgetRequest(
        widget_type="data_table",
        data={
            "caption": f"watch_list: {len(watches)} pending file watch(es)",
            "columns": [
                {"key": "watch_id", "label": "Watch ID"},
                {"key": "path", "label": "Path"},
                {"key": "event", "label": "Event"},
                {"key": "remaining", "label": "Remaining (s)", "type": "duration"},
                {"key": "reason", "label": "Reason"},
            ],
            "rows": rows,
        },
        target="inline",
    )
    return ToolResult(
        text="\n".join(lines),
        data={"watches": rows},
        widget=widget,
    )


WATCH_TOOLS = {
    "watch_file": tool_watch_file,
    "watch_cancel": tool_watch_cancel,
    "watch_list": tool_watch_list,
}

WATCH_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "priority": "normal",
        "writes": True,
        "function": {
            "name": "watch_file",
            "description": (
                "Arm a one-shot watch on a workspace file and be woken by a new turn when it changes. "
                "Use this when you are waiting for an external event that touches a known file — a canvas "
                "app writing its content.json after a user interaction, a notebook save, or a build sentinel — "
                "instead of polling with wait() or guessing a delay with schedule_turn. "
                "The watch disarms after the first matching event or when the timeout expires."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Workspace-relative path to watch (e.g. 'television/board/content.json')",
                    },
                    "event": {
                        "type": "string",
                        "enum": list(WATCH_EVENTS),
                        "description": "Filesystem event to wake on: modify (default), create, delete, or any",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Maximum seconds to wait before the watch expires (default 3600)",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Why you are watching (included in the wake prompt, e.g. 'User may approve the request board')",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "priority": "normal",
        "writes": True,
        "function": {
            "name": "watch_cancel",
            "description": "Disarm a pending file watch by its watch_id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "watch_id": {
                        "type": "string",
                        "description": "The watch ID to cancel (from watch_file or watch_list)",
                    },
                },
                "required": ["watch_id"],
            },
        },
    },
    {
        "type": "function",
        "priority": "normal",
        "writes": False,
        "function": {
            "name": "watch_list",
            "description": "List all pending file watches for the current conversation.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
]
