"""Tool registry — combines core and tabstack tools."""

from .core import CORE_TOOLS, CORE_TOOL_DEFINITIONS
from .tabstack_tools import (
    TABSTACK_TOOLS,
    TABSTACK_TOOL_DEFINITIONS,
    init_tabstack,
)

# Combined registry
TOOLS = {**CORE_TOOLS, **TABSTACK_TOOLS}
TOOL_DEFINITIONS = CORE_TOOL_DEFINITIONS + TABSTACK_TOOL_DEFINITIONS


def execute_tool(name: str, arguments: dict) -> str:
    """Execute a tool by name and return the result as a string."""
    fn = TOOLS.get(name)
    if fn is None:
        return f"[error: unknown tool: {name}]"
    try:
        return fn(**arguments)
    except Exception as e:
        return f"[error executing {name}: {e}]"
