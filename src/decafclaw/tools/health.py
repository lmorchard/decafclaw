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


def _format_relative_time(seconds: float) -> str:
    """Format a time delta as relative text like '5m ago' or 'in 25m'."""
    abs_secs = abs(seconds)
    if abs_secs < 60:
        label = f"{int(abs_secs)}s"
    elif abs_secs < 3600:
        label = f"{int(abs_secs // 60)}m"
    else:
        h = int(abs_secs // 3600)
        m = int((abs_secs % 3600) // 60)
        label = f"{h}h {m}m" if m else f"{h}h"

    if seconds < 0:
        return f"overdue by {label}"
    return f"in {label}"


def _heartbeat_section(config) -> list[str]:
    """Gather heartbeat timing status."""
    from ..heartbeat import parse_interval, read_last_heartbeat

    interval = parse_interval(config.heartbeat.interval)
    if interval is None:
        return ["### Heartbeat", "- **Status:** disabled"]

    lines = [
        "### Heartbeat",
        "- **Status:** enabled",
        f"- **Interval:** {config.heartbeat.interval}",
    ]

    last_run = read_last_heartbeat(config)
    if last_run > 0:
        from datetime import datetime, timezone

        dt = datetime.fromtimestamp(last_run, tz=timezone.utc).astimezone()
        elapsed = time.time() - last_run
        elapsed_str = _format_uptime(elapsed)
        lines.append(f"- **Last run:** {dt.strftime('%Y-%m-%d %H:%M:%S')} ({elapsed_str} ago)")

        remaining = interval - elapsed
        lines.append(f"- **Next due:** {_format_relative_time(remaining)}")
    else:
        lines.append("- **Last run:** never")

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

    sections.append("")

    # Heartbeat
    try:
        sections.extend(_heartbeat_section(ctx.config))
    except Exception as e:
        sections.append(f"### Heartbeat\n- [error: {e}]")

    return "\n".join(sections)
