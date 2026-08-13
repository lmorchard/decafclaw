from typing import TYPE_CHECKING

from decafclaw.friction import analyze_friction
from decafclaw.media import ToolResult

if TYPE_CHECKING:
    from decafclaw.context import Context

async def tool_friction_analyze(ctx: "Context") -> ToolResult:
    """Scan recent archives for repeated user corrections and return proposed AGENT.md additions."""
    themes = await analyze_friction(ctx)
    if not themes:
        return ToolResult(text="No recurring friction themes found.")

    lines = ["Found the following recurring friction themes:\n"]
    for t in themes:
        lines.append(f"- **Theme:** {t.theme} ({t.occurrences} occurrences)")
        lines.append(f"  **Proposed Addition:** {t.proposed_addition}")

    return ToolResult(text="\n".join(lines))
