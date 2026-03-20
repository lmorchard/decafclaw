"""Health/diagnostic status tool — reports agent operational state."""

import logging
import resource
import sys
import time

log = logging.getLogger(__name__)

# Captured at import time for uptime calculation
_start_time = time.monotonic()


def _format_uptime(seconds: float) -> str:
    """Format seconds into human-readable uptime like '2h 14m 32s'."""
    parts = []
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    parts.append(f"{secs}s")
    return " ".join(parts)


def _process_section() -> list[str]:
    """Gather process uptime and memory usage."""
    uptime = time.monotonic() - _start_time
    uptime_str = _format_uptime(uptime)

    # RSS memory — macOS reports bytes, Linux reports KB
    ru = resource.getrusage(resource.RUSAGE_SELF)
    if sys.platform == "darwin":
        rss_mb = ru.ru_maxrss / (1024 * 1024)
    else:
        rss_mb = ru.ru_maxrss / 1024

    return [
        "### Process",
        f"- **Uptime:** {uptime_str}",
        f"- **Memory (RSS):** {rss_mb:.1f} MB",
    ]


def _mcp_section() -> list[str]:
    """Gather MCP server connection status."""
    from ..mcp_client import get_registry

    registry = get_registry()
    if not registry or not registry.servers:
        return ["### MCP Servers", "No MCP servers configured."]

    lines = [
        "### MCP Servers",
        "| Server | Status | Tools | Retries |",
        "|--------|--------|-------|---------|",
    ]
    for name, state in registry.servers.items():
        tool_count = len(state.tools)
        lines.append(f"| {name} | {state.status} | {tool_count} | {state.retry_count} |")
    return lines


async def tool_health_status(ctx) -> str:
    """Show agent health and diagnostic status."""
    log.info("[tool:health_status]")

    sections = ["## Agent Health", ""]

    # Process
    try:
        sections.extend(_process_section())
    except Exception as e:
        sections.append(f"### Process\n- [error: {e}]")

    sections.append("")

    # MCP Servers
    try:
        sections.extend(_mcp_section())
    except Exception as e:
        sections.append(f"### MCP Servers\n- [error: {e}]")

    return "\n".join(sections)
