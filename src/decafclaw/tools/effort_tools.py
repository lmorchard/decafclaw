"""Effort level tools — switch model complexity for the conversation."""

import logging

from ..config import EFFORT_LEVELS
from ..media import ToolResult

log = logging.getLogger(__name__)


async def tool_set_effort(ctx, level: str) -> str | ToolResult:
    """Change the effort level for this conversation."""
    log.info(f"[tool:set_effort] level={level}")

    if level not in EFFORT_LEVELS:
        return ToolResult(
            text=f"[error: unknown effort level '{level}'. "
                 f"Valid: {', '.join(sorted(EFFORT_LEVELS))}]"
        )

    ctx.effort = level

    # Record effort change in conversation archive
    from ..archive import append_message

    conv_id = ctx.conv_id or ctx.channel_id
    if conv_id:
        append_message(ctx.config, conv_id, {
            "role": "effort", "content": level,
        })

    # Resolve to show the user which model they're getting
    from ..config import resolve_effort

    resolved = resolve_effort(ctx.config, level)

    return (
        f"Effort level set to **{level}** (model: {resolved.model}). "
        f"This applies for the rest of this conversation."
    )


EFFORT_TOOLS = {
    "set_effort": tool_set_effort,
}

EFFORT_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "set_effort",
            "description": (
                "Change the model effort level for this conversation. "
                "Levels: 'fast' (cheap, compliant), 'default' (normal), "
                "'strong' (complex reasoning). The change is sticky for "
                "the rest of this conversation. Use when a task needs "
                "more or less capable reasoning."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "level": {
                        "type": "string",
                        "enum": sorted(EFFORT_LEVELS),
                        "description": "The effort level to switch to",
                    },
                },
                "required": ["level"],
            },
        },
    },
]
