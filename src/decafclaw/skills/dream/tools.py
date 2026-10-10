"""Dream bundled skill — tools for reviewing recent memory consolidation runs."""

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from decafclaw.media import ToolResult
from decafclaw.scheduled_activity import collect_scheduled_activity

if TYPE_CHECKING:
    from decafclaw.context import Context

log = logging.getLogger(__name__)

_config = None
_skill_config = None


@dataclass
class SkillConfig:
    window_days: int = field(
        default=7,
        metadata={"env_alias": "DREAM_WINDOW_DAYS"},
    )


def init(config, skill_config: SkillConfig) -> None:
    """Initialize the dream skill. Called by the skill loader on activation."""
    global _config, _skill_config
    _config = config
    _skill_config = skill_config


async def dream_recent_runs(ctx: "Context", days: int | None = None) -> ToolResult:
    """List recent dream skill runs within the given window (default 7 days).

    Returns recent dream runs with started_at, final_message, and vault_pages_touched
    so dream can see what topics and pages were touched recently and avoid rehashing them.
    """
    default_days = _skill_config.window_days if _skill_config is not None else 7
    if days is None:
        resolved_days = default_days
    else:
        try:
            resolved_days = int(days)
        except (ValueError, TypeError):
            resolved_days = default_days
    if resolved_days <= 0:
        resolved_days = default_days

    runs = collect_scheduled_activity(
        ctx.config,
        hours=resolved_days * 24,
        skill_name="dream",
    )

    if not runs:
        summary = f"No dream runs found in the last {resolved_days} day(s)."
    else:
        lines = [f"Found {len(runs)} dream run(s) in the last {resolved_days} day(s):"]
        for r in runs:
            pages = ", ".join(r.get("vault_pages_touched", [])) or "none"
            lines.append(f"- [{r.get('started_at')}] ({r.get('conv_id')}): pages touched: [{pages}]")
            final = (r.get("final_message") or "").strip()
            if final:
                first_line = final.splitlines()[0][:120]
                lines.append(f"  Summary: {first_line}")
        summary = "\n".join(lines)

    return ToolResult(
        text=summary,
        data={"runs": runs, "days": resolved_days},
    )


TOOLS = {
    "dream_recent_runs": dream_recent_runs,
}

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "priority": "normal",
        "function": {
            "name": "dream_recent_runs",
            "description": (
                "List recent dream memory consolidation runs from the last N days (default 7). "
                "Returns each run's start time, narrative summary of consolidated insights, and "
                "vault pages touched. Use this in Phase 1 (Orient) to see what topics and pages "
                "have already been consolidated recently, and avoid re-processing or rewriting "
                "them unless new journal entries provide fresh source material."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "days": {
                        "type": "integer",
                        "description": "Optional: number of days of history to inspect (default 7)",
                    },
                },
                "required": [],
            },
        },
    },
]
