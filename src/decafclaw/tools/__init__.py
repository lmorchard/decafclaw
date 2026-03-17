"""Tool registry — combines core and built-in tools. Skill tools loaded on demand."""

import asyncio

from ..media import ToolResult
from .conversation_tools import CONVERSATION_TOOL_DEFINITIONS, CONVERSATION_TOOLS
from .core import CORE_TOOL_DEFINITIONS, CORE_TOOLS
from .heartbeat_tools import HEARTBEAT_TOOL_DEFINITIONS, HEARTBEAT_TOOLS
from .mcp_tools import MCP_TOOL_DEFINITIONS, MCP_TOOLS
from .memory_tools import MEMORY_TOOL_DEFINITIONS, MEMORY_TOOLS
from .shell_tools import SHELL_TOOL_DEFINITIONS, SHELL_TOOLS
from .skill_tools import SKILL_TOOL_DEFINITIONS, SKILL_TOOLS
from .todo_tools import TODO_TOOL_DEFINITIONS, TODO_TOOLS
from .workspace_tools import WORKSPACE_TOOL_DEFINITIONS, WORKSPACE_TOOLS

# Combined registry (tabstack via skill, MCP tools via registry)
TOOLS = {**CORE_TOOLS, **MEMORY_TOOLS, **TODO_TOOLS,
         **CONVERSATION_TOOLS, **WORKSPACE_TOOLS, **SHELL_TOOLS,
         **SKILL_TOOLS, **MCP_TOOLS, **HEARTBEAT_TOOLS}
TOOL_DEFINITIONS = (CORE_TOOL_DEFINITIONS
                    + MEMORY_TOOL_DEFINITIONS + TODO_TOOL_DEFINITIONS
                    + CONVERSATION_TOOL_DEFINITIONS + WORKSPACE_TOOL_DEFINITIONS
                    + SHELL_TOOL_DEFINITIONS + SKILL_TOOL_DEFINITIONS
                    + MCP_TOOL_DEFINITIONS + HEARTBEAT_TOOL_DEFINITIONS)


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
    allowed = getattr(ctx, "allowed_tools", None)
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
                    result = await fn(arguments)
                    return _to_tool_result(result)
                except Exception as e:
                    return ToolResult(text=f"[error executing {name}: {e}]")
        return ToolResult(text=f"[error: MCP tool '{name}' not available]")

    # Check skill-provided tools first, then global registry
    extra_tools = getattr(ctx, "extra_tools", {})
    fn = extra_tools.get(name) or TOOLS.get(name)
    if fn is None:
        return ToolResult(text=f"[error: unknown tool: {name}]")
    cancel_event = getattr(ctx, "cancelled", None)
    try:
        if asyncio.iscoroutinefunction(fn):
            tool_task = asyncio.create_task(fn(ctx, **arguments))
        else:
            tool_task = asyncio.create_task(asyncio.to_thread(fn, ctx, **arguments))

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
                return ToolResult(text="[tool interrupted: agent turn cancelled]")
            result = tool_task.result()
        else:
            result = await tool_task

        return _to_tool_result(result)
    except Exception as e:
        return ToolResult(text=f"[error executing {name}: {e}]")
