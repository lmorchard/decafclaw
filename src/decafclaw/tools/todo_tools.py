"""To-do list tools — per-conversation task tracking."""

import logging

from .. import todos

log = logging.getLogger(__name__)


def tool_todo_add(ctx, item: str) -> str:
    """Add a to-do item."""
    log.info(f"[tool:todo_add] {item}")
    conv_id = (ctx.conv_id or "default")
    return todos.todo_add(ctx.config, conv_id, item)


def tool_todo_complete(ctx, index: int) -> str:
    """Mark a to-do item as complete."""
    log.info(f"[tool:todo_complete] #{index}")
    conv_id = (ctx.conv_id or "default")
    return todos.todo_complete(ctx.config, conv_id, index)


def tool_todo_list(ctx) -> str:
    """List all to-do items."""
    log.info("[tool:todo_list]")
    conv_id = (ctx.conv_id or "default")
    return todos.todo_list(ctx.config, conv_id)


def tool_todo_clear(ctx) -> str:
    """Clear the to-do list."""
    log.info("[tool:todo_clear]")
    conv_id = (ctx.conv_id or "default")
    return todos.todo_clear(ctx.config, conv_id)


TODO_TOOLS = {
    "todo_add": tool_todo_add,
    "todo_complete": tool_todo_complete,
    "todo_list": tool_todo_list,
    "todo_clear": tool_todo_clear,
}

TODO_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "todo_add",
            "description": (
                "Add an item to the conversation's to-do list. Use this to plan "
                "multi-step work: break a task into steps, add them as to-do items, "
                "then work through them one by one. The list persists across restarts."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "item": {
                        "type": "string",
                        "description": "The to-do item text",
                    },
                },
                "required": ["item"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "todo_complete",
            "description": "Mark a to-do item as complete by its number (1-indexed).",
            "parameters": {
                "type": "object",
                "properties": {
                    "index": {
                        "type": "integer",
                        "description": "The item number to complete (1-indexed)",
                    },
                },
                "required": ["index"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "todo_list",
            "description": "Show the current to-do list for this conversation.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "todo_clear",
            "description": "Clear all items from the to-do list.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
]
