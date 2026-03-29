"""Tool search — deferred tool loading via keyword or exact name selection."""

import json
import logging

from ..media import ToolResult
from .tool_registry import add_fetched_tools, get_description

log = logging.getLogger(__name__)


def tool_search(ctx, query: str, max_results: int = 10) -> ToolResult:
    """Search for and load tool definitions from the deferred pool."""
    pool = ctx.tools.deferred_pool
    if not pool:
        return ToolResult(text="No deferred tools available.")

    if query.startswith("select:"):
        # Exact selection by name
        names = {n.strip() for n in query[7:].split(",") if n.strip()}
        matched = [
            td for td in pool
            if td.get("function", {}).get("name", "") in names
        ]
        found_names = {td["function"]["name"] for td in matched}
        missing = names - found_names
    else:
        # Keyword search: case-insensitive substring on name + description
        keyword = query.lower()
        matched = []
        for td in pool:
            name = td.get("function", {}).get("name", "")
            desc = get_description(td)
            if keyword in name.lower() or keyword in desc.lower():
                matched.append(td)
        matched = matched[:max_results]
        missing = set()

    if not matched:
        return ToolResult(
            text=f"No tools found matching '{query}'. "
            "Check the deferred tools list in the system prompt."
        )

    # Register matched tools as fetched
    matched_names = {td["function"]["name"] for td in matched}
    add_fetched_tools(ctx, matched_names)

    # Format result
    parts = []
    for td in matched:
        parts.append(json.dumps(td, indent=2))

    result_text = (
        f"{len(matched)} tool(s) loaded. "
        "These tools are now available to call.\n\n"
        + "\n\n".join(parts)
    )

    if missing:
        result_text += f"\n\nNot found: {', '.join(sorted(missing))}"

    return ToolResult(text=result_text)


SEARCH_TOOLS = {
    "tool_search": tool_search,
}

SEARCH_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "tool_search",
            "description": (
                "Search for and load tool definitions. Use 'select:name1,name2' "
                "for exact tools by name, or a keyword to search tool names and "
                "descriptions. Returns full tool schemas, making matched tools "
                "callable. Check the deferred tools list in the system prompt "
                "to see what's available."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "Keyword search term, or 'select:name1,name2' "
                            "for exact selection"
                        ),
                    },
                    "max_results": {
                        "type": "integer",
                        "description": (
                            "Max tools to return for keyword search (default 10)"
                        ),
                    },
                },
                "required": ["query"],
            },
        },
    },
]
