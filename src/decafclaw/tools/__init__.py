"""Tool registry — combines core and tabstack tools."""

import asyncio

from .core import CORE_TOOLS, CORE_TOOL_DEFINITIONS
from .memory_tools import MEMORY_TOOLS, MEMORY_TOOL_DEFINITIONS
from .tabstack_tools import (
    TABSTACK_TOOLS,
    TABSTACK_TOOL_DEFINITIONS,
    init_tabstack,
)
from .todo_tools import TODO_TOOLS, TODO_TOOL_DEFINITIONS
from .conversation_tools import CONVERSATION_TOOLS, CONVERSATION_TOOL_DEFINITIONS

# Combined registry
TOOLS = {**CORE_TOOLS, **TABSTACK_TOOLS, **MEMORY_TOOLS, **TODO_TOOLS, **CONVERSATION_TOOLS}
TOOL_DEFINITIONS = (CORE_TOOL_DEFINITIONS + TABSTACK_TOOL_DEFINITIONS
                    + MEMORY_TOOL_DEFINITIONS + TODO_TOOL_DEFINITIONS
                    + CONVERSATION_TOOL_DEFINITIONS)


async def execute_tool(ctx, name: str, arguments: dict) -> str:
    """Execute a tool by name and return the result as a string."""
    # Check allowed tools list (used by eval runner)
    allowed = getattr(ctx, "allowed_tools", None)
    if allowed is not None and name not in allowed:
        return f"[error: tool '{name}' is not available in this context]"

    fn = TOOLS.get(name)
    if fn is None:
        return f"[error: unknown tool: {name}]"
    try:
        if asyncio.iscoroutinefunction(fn):
            return await fn(ctx, **arguments)
        else:
            return await asyncio.to_thread(fn, ctx, **arguments)
    except Exception as e:
        return f"[error executing {name}: {e}]"
