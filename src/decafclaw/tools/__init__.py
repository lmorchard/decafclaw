"""Tool registry — combines core and built-in tools. Skill tools loaded on demand."""

import asyncio

from .core import CORE_TOOLS, CORE_TOOL_DEFINITIONS
from .memory_tools import MEMORY_TOOLS, MEMORY_TOOL_DEFINITIONS
from .todo_tools import TODO_TOOLS, TODO_TOOL_DEFINITIONS
from .conversation_tools import CONVERSATION_TOOLS, CONVERSATION_TOOL_DEFINITIONS
from .workspace_tools import WORKSPACE_TOOLS, WORKSPACE_TOOL_DEFINITIONS
from .shell_tools import SHELL_TOOLS, SHELL_TOOL_DEFINITIONS
from .skill_tools import SKILL_TOOLS, SKILL_TOOL_DEFINITIONS
from .mcp_tools import MCP_TOOLS, MCP_TOOL_DEFINITIONS

# Combined registry (tabstack via skill, MCP tools via registry)
TOOLS = {**CORE_TOOLS, **MEMORY_TOOLS, **TODO_TOOLS,
         **CONVERSATION_TOOLS, **WORKSPACE_TOOLS, **SHELL_TOOLS,
         **SKILL_TOOLS, **MCP_TOOLS}
TOOL_DEFINITIONS = (CORE_TOOL_DEFINITIONS
                    + MEMORY_TOOL_DEFINITIONS + TODO_TOOL_DEFINITIONS
                    + CONVERSATION_TOOL_DEFINITIONS + WORKSPACE_TOOL_DEFINITIONS
                    + SHELL_TOOL_DEFINITIONS + SKILL_TOOL_DEFINITIONS
                    + MCP_TOOL_DEFINITIONS)


async def execute_tool(ctx, name: str, arguments: dict) -> str:
    """Execute a tool by name and return the result as a string.

    Checks ctx.extra_tools (from activated skills) first, then the global registry.
    """
    # Check allowed tools list (used by eval runner)
    allowed = getattr(ctx, "allowed_tools", None)
    if allowed is not None and name not in allowed:
        return f"[error: tool '{name}' is not available in this context]"

    # Route MCP tools to the MCP registry (different call signature — no ctx)
    if name.startswith("mcp__"):
        from ..mcp_client import get_registry
        registry = get_registry()
        if registry:
            mcp_tools = registry.get_tools()
            fn = mcp_tools.get(name)
            if fn:
                try:
                    return await fn(arguments)
                except Exception as e:
                    return f"[error executing {name}: {e}]"
        return f"[error: MCP tool '{name}' not available]"

    # Check skill-provided tools first, then global registry
    extra_tools = getattr(ctx, "extra_tools", {})
    fn = extra_tools.get(name) or TOOLS.get(name)
    if fn is None:
        return f"[error: unknown tool: {name}]"
    try:
        if asyncio.iscoroutinefunction(fn):
            return await fn(ctx, **arguments)
        else:
            return await asyncio.to_thread(fn, ctx, **arguments)
    except Exception as e:
        return f"[error executing {name}: {e}]"
