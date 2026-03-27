"""Tool registry — combines core and built-in tools. Skill tools loaded on demand."""

import asyncio
import logging

from ..media import ToolResult
from .attachment_tools import ATTACHMENT_TOOL_DEFINITIONS, ATTACHMENT_TOOLS
from .conversation_tools import CONVERSATION_TOOL_DEFINITIONS, CONVERSATION_TOOLS
from .core import CORE_TOOL_DEFINITIONS, CORE_TOOLS
from .delegate import DELEGATE_TOOL_DEFINITIONS, DELEGATE_TOOLS
from .effort_tools import EFFORT_TOOL_DEFINITIONS, EFFORT_TOOLS
from .health import HEALTH_TOOL_DEFINITIONS, HEALTH_TOOLS
from .heartbeat_tools import HEARTBEAT_TOOL_DEFINITIONS, HEARTBEAT_TOOLS
from .mcp_tools import (
    MCP_DEFERRED_TOOL_DEFINITIONS,
    MCP_DEFERRED_TOOLS,
    MCP_TOOL_DEFINITIONS,
    MCP_TOOLS,
)
from .memory_tools import MEMORY_TOOL_DEFINITIONS, MEMORY_TOOLS
from .shell_tools import SHELL_TOOL_DEFINITIONS, SHELL_TOOLS
from .skill_tools import SKILL_TOOL_DEFINITIONS, SKILL_TOOLS
from .todo_tools import TODO_TOOL_DEFINITIONS, TODO_TOOLS
from .workspace_tools import WORKSPACE_TOOL_DEFINITIONS, WORKSPACE_TOOLS

log = logging.getLogger(__name__)

# Combined registry (tabstack via skill, MCP tools via registry)
TOOLS = {**CORE_TOOLS, **MEMORY_TOOLS, **TODO_TOOLS,
         **CONVERSATION_TOOLS, **WORKSPACE_TOOLS, **SHELL_TOOLS,
         **SKILL_TOOLS, **MCP_TOOLS, **MCP_DEFERRED_TOOLS,
         **HEARTBEAT_TOOLS, **HEALTH_TOOLS,
         **EFFORT_TOOLS, **DELEGATE_TOOLS, **ATTACHMENT_TOOLS}
TOOL_DEFINITIONS = (CORE_TOOL_DEFINITIONS
                    + MEMORY_TOOL_DEFINITIONS + TODO_TOOL_DEFINITIONS
                    + CONVERSATION_TOOL_DEFINITIONS + WORKSPACE_TOOL_DEFINITIONS
                    + SHELL_TOOL_DEFINITIONS + SKILL_TOOL_DEFINITIONS
                    + MCP_TOOL_DEFINITIONS + MCP_DEFERRED_TOOL_DEFINITIONS
                    + HEARTBEAT_TOOL_DEFINITIONS
                    + HEALTH_TOOL_DEFINITIONS + EFFORT_TOOL_DEFINITIONS
                    + DELEGATE_TOOL_DEFINITIONS + ATTACHMENT_TOOL_DEFINITIONS)


async def _run_with_cancel(coro, cancel_event):
    """Run a coroutine, cancelling it if cancel_event fires first.

    Returns (task, interrupted) where interrupted is a ToolResult if the
    turn was cancelled, or None if the coroutine completed normally.
    """
    tool_task = asyncio.create_task(coro)
    if cancel_event:
        cancel_task = asyncio.create_task(cancel_event.wait())
        done, _ = await asyncio.wait(
            [tool_task, cancel_task], return_when=asyncio.FIRST_COMPLETED
        )
        cancel_task.cancel()
        if tool_task not in done:
            tool_task.cancel()
            try:
                await tool_task
            except (asyncio.CancelledError, Exception):
                pass
            return tool_task, ToolResult(text="[tool interrupted: agent turn cancelled]")
    else:
        await tool_task
    return tool_task, None


def _to_tool_result(value) -> ToolResult:
    """Normalize a tool return value to ToolResult."""
    if isinstance(value, ToolResult):
        return value
    if isinstance(value, str):
        return ToolResult(text=value)
    if value is None:
        return ToolResult(text="")
    return ToolResult(text=str(value))


async def execute_tool(ctx, name: str, arguments: dict) -> ToolResult:
    """Execute a tool by name and return the result.

    Returns a ToolResult with text (for LLM history) and optional media
    (for file attachments). Checks ctx.extra_tools (from activated skills)
    first, then the global registry.
    """
    # Check allowed tools list (used by eval runner)
    allowed = ctx.allowed_tools
    if allowed is not None and name not in allowed:
        return ToolResult(text=f"[error: tool '{name}' is not available in this context]")

    # Route MCP tools to the MCP registry (different call signature — no ctx)
    if name.startswith("mcp__"):
        from ..mcp_client import get_registry
        registry = get_registry()
        if registry:
            mcp_tools = registry.get_tools()
            fn = mcp_tools.get(name)
            if fn:
                try:
                    cancel_event = ctx.cancelled
                    tool_task, interrupted = await _run_with_cancel(fn(arguments), cancel_event)
                    if interrupted:
                        return interrupted
                    return _to_tool_result(tool_task.result())
                except Exception as e:
                    return ToolResult(text=f"[error executing {name}: {e}]")
        return ToolResult(text=f"[error: MCP tool '{name}' not available]")

    # Check skill-provided tools first, then global registry, then search tools
    from .search_tools import SEARCH_TOOLS

    extra_tools = ctx.extra_tools
    fn = extra_tools.get(name) or TOOLS.get(name) or SEARCH_TOOLS.get(name)
    if fn is None:
        # Check if tool is in the deferred pool — auto-fetch if so
        deferred_pool = ctx.deferred_tool_pool
        deferred_names = {td.get("function", {}).get("name") for td in deferred_pool}
        if name in deferred_names:
            log.debug(f"Auto-fetching deferred tool: {name}")
            from .tool_registry import add_fetched_tools
            add_fetched_tools(ctx, {name})
            fn = extra_tools.get(name) or TOOLS.get(name)
        if fn is None:
            return ToolResult(text=f"[error: unknown tool: {name}]")
    cancel_event = ctx.cancelled
    try:
        coro = fn(ctx, **arguments) if asyncio.iscoroutinefunction(fn) else asyncio.to_thread(fn, ctx, **arguments)
        tool_task, interrupted = await _run_with_cancel(coro, cancel_event)
        if interrupted:
            return interrupted
        return _to_tool_result(tool_task.result())
    except TypeError as e:
        # Common: model guesses wrong parameter names (e.g. 'path' instead of 'file').
        # Include expected params in the error to help the model self-correct.
        import inspect
        try:
            sig = inspect.signature(fn)
            params = [p for p in sig.parameters if p != "ctx"]
            return ToolResult(text=f"[error executing {name}: {e}. Expected parameters: {', '.join(params)}]")
        except (ValueError, TypeError):
            return ToolResult(text=f"[error executing {name}: {e}]")
    except Exception as e:
        return ToolResult(text=f"[error executing {name}: {e}]")
