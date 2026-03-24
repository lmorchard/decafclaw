"""Health/diagnostic status tool — reports agent operational state.

Provides both structured data (dicts for JSON API) and markdown formatting
(for the agent tool). The HTTP health endpoint uses the data functions directly.
"""

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


def _format_relative_time(seconds: float) -> str:
    """Format a time delta as 'in 25m' (future) or 'overdue by 5m' (past)."""
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


# -- Structured data gatherers (for JSON API) ---------------------------------


def get_process_data() -> dict:
    """Return process health data as a dict."""
    uptime = time.monotonic() - _start_time

    ru = resource.getrusage(resource.RUSAGE_SELF)
    if sys.platform == "darwin":
        rss_mb = ru.ru_maxrss / (1024 * 1024)
    else:
        rss_mb = ru.ru_maxrss / 1024

    return {
        "uptime_seconds": round(uptime, 1),
        "memory_rss_mb": round(rss_mb, 1),
    }


def get_mcp_data() -> dict:
    """Return MCP server status as a dict."""
    from ..mcp_client import get_registry

    registry = get_registry()
    if not registry or not registry.servers:
        return {"connected": 0, "failed": 0, "servers": {}}

    connected = 0
    failed = 0
    servers = {}
    for name, state in registry.servers.items():
        status = state.status
        if status == "connected":
            connected += 1
        elif status in ("failed", "error"):
            failed += 1
        servers[name] = {
            "status": status,
            "tools": len(state.tools),
            "retries": state.retry_count,
        }

    return {"connected": connected, "failed": failed, "servers": servers}


def get_heartbeat_data(config) -> dict:
    """Return heartbeat timing data as a dict."""
    from ..heartbeat import parse_interval, read_last_heartbeat

    interval = parse_interval(config.heartbeat.interval)
    if interval is None:
        return {"enabled": False}

    data: dict = {
        "enabled": True,
        "interval": config.heartbeat.interval,
    }

    last_run = read_last_heartbeat(config)
    if last_run > 0:
        from datetime import datetime, timezone

        dt = datetime.fromtimestamp(last_run, tz=timezone.utc)
        data["last_run"] = dt.isoformat()
        elapsed = time.time() - last_run
        remaining = interval - elapsed
        next_due = datetime.fromtimestamp(time.time() + remaining, tz=timezone.utc)
        data["next_run"] = next_due.isoformat()
    else:
        data["last_run"] = None
        data["next_run"] = None

    return data


def get_embeddings_data(config) -> dict:
    """Return embedding index stats as a dict."""
    import sqlite3

    db_path = config.workspace_path / "embeddings.db"
    if not db_path.exists():
        return {"total": 0, "memory": 0, "conversation": 0}

    try:
        conn = sqlite3.connect(str(db_path))
        try:
            row = conn.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN source_type = 'memory' THEN 1 ELSE 0 END),
                    SUM(CASE WHEN source_type = 'conversation' THEN 1 ELSE 0 END),
                    SUM(CASE WHEN source_type = 'wiki' THEN 1 ELSE 0 END)
                FROM memory_embeddings
                """
            ).fetchone()
            total, memory, conversation, wiki = row
            memory = memory or 0
            conversation = conversation or 0
            wiki = wiki or 0
        finally:
            conn.close()
    except sqlite3.Error as exc:
        log.exception("Failed to read embeddings database at %s", db_path)
        return {"total": 0, "memory": 0, "conversation": 0, "wiki": 0, "error": str(exc)}

    return {"total": total, "memory": memory, "conversation": conversation, "wiki": wiki}


def get_schedule_data(config) -> dict:
    """Return scheduled task stats as a dict."""
    from ..schedules import discover_schedules, read_last_run

    tasks = discover_schedules(config)
    admin = sum(1 for t in tasks if t.source == "admin")
    workspace = sum(1 for t in tasks if t.source == "workspace")
    enabled = sum(1 for t in tasks if t.enabled)

    from datetime import datetime, timezone

    from croniter import croniter

    task_list = []
    for t in tasks:
        last_run = read_last_run(config, t.name)
        entry: dict = {
            "name": t.name,
            "schedule": t.schedule,
            "source": t.source,
            "enabled": t.enabled,
        }
        if last_run > 0:
            entry["last_run"] = datetime.fromtimestamp(
                last_run, tz=timezone.utc).isoformat()
        else:
            entry["last_run"] = None

        # Next run: based on last_run or now if never run
        try:
            base = datetime.fromtimestamp(last_run if last_run > 0 else time.time())
            cron = croniter(t.schedule, base)
            next_fire = cron.get_next(datetime)
            entry["next_run"] = next_fire.astimezone(timezone.utc).isoformat()
        except Exception:
            entry["next_run"] = None

        task_list.append(entry)

    return {
        "total": len(tasks),
        "admin": admin,
        "workspace": workspace,
        "enabled": enabled,
        "tasks": task_list,
    }


def get_health_data(config) -> dict:
    """Gather all health data as a JSON-serializable dict.

    Used by the HTTP /health endpoint. Does not require a per-conversation
    context, so tool stats are omitted.
    """
    data: dict = {"status": "ok"}
    errors: list[str] = []

    try:
        data["process"] = get_process_data()
    except Exception as exc:
        log.exception("Failed to gather process health data")
        data["process"] = None
        errors.append(f"process: {exc}")

    try:
        data["mcp_servers"] = get_mcp_data()
    except Exception as exc:
        log.exception("Failed to gather MCP health data")
        data["mcp_servers"] = None
        errors.append(f"mcp_servers: {exc}")

    try:
        data["heartbeat"] = get_heartbeat_data(config)
    except Exception as exc:
        log.exception("Failed to gather heartbeat health data")
        data["heartbeat"] = None
        errors.append(f"heartbeat: {exc}")

    try:
        data["embeddings"] = get_embeddings_data(config)
    except Exception as exc:
        log.exception("Failed to gather embeddings health data")
        data["embeddings"] = None
        errors.append(f"embeddings: {exc}")

    try:
        data["schedules"] = get_schedule_data(config)
    except Exception as exc:
        log.exception("Failed to gather schedule health data")
        data["schedules"] = None
        errors.append(f"schedules: {exc}")

    if errors:
        data["status"] = "degraded"
        data["errors"] = errors

    return data


# -- Markdown formatters (for agent tool) --------------------------------------


def _process_section() -> list[str]:
    """Gather process uptime and memory usage."""
    data = get_process_data()
    uptime_str = _format_uptime(data["uptime_seconds"])
    return [
        "### Process",
        f"- **Uptime:** {uptime_str}",
        f"- **Memory (RSS):** {data['memory_rss_mb']:.1f} MB",
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


def _tools_section(ctx) -> list[str]:
    """Gather tool deferral stats."""
    from . import TOOL_DEFINITIONS
    from .tool_registry import classify_tools, estimate_tool_tokens

    # Collect all tool definitions the agent currently has
    all_defs = TOOL_DEFINITIONS + ctx.extra_tool_definitions
    # Add MCP tool definitions if available
    from ..mcp_client import get_registry

    registry = get_registry()
    if registry:
        for state in registry.servers.values():
            all_defs = all_defs + state.tool_definitions

    fetched = ctx.skill_data.get("fetched_tools", set())
    active, deferred = classify_tools(all_defs, ctx.config, fetched)
    total_tokens = estimate_tool_tokens(all_defs)
    budget = ctx.config.tool_context_budget

    return [
        "### Tools",
        f"- **Active:** {len(active)} | **Deferred:** {len(deferred)}",
        f"- **Token usage:** ~{total_tokens:,} / {budget:,} budget",
    ]


def _embeddings_section(config) -> list[str]:
    """Gather embedding index stats."""
    data = get_embeddings_data(config)
    if "error" in data:
        return ["### Embeddings", f"- [error reading database: {data['error']}]"]
    if data["total"] == 0:
        db_path = config.workspace_path / "embeddings.db"
        if not db_path.exists():
            return ["### Embeddings", "No embedding database found."]

    return [
        "### Embeddings",
        f"- **Total entries:** {data['total']}",
        f"- **Memory:** {data['memory']} | **Conversation:** {data['conversation']} | **Wiki:** {data['wiki']}",
    ]


def _schedule_section(config) -> list[str]:
    """Gather scheduled task stats."""
    data = get_schedule_data(config)
    if data["total"] == 0:
        return ["### Scheduled Tasks", "No scheduled tasks found."]

    lines = [
        "### Scheduled Tasks",
        f"- **Total:** {data['total']} ({data['enabled']} enabled)",
        f"- **Admin:** {data['admin']} | **Workspace:** {data['workspace']}",
    ]
    for t in data["tasks"]:
        status = "enabled" if t["enabled"] else "disabled"
        last = t["last_run"] or "never"
        next_run = t.get("next_run") or "unknown"
        lines.append(f"- `{t['name']}` ({t['schedule']}) — {status}, last: {last}, next: {next_run}")
    return lines


async def tool_health_status(ctx) -> str:
    """Show agent health and diagnostic status."""
    log.info("[tool:health_status]")

    sections = ["## Agent Health", ""]

    # Process
    try:
        sections.extend(_process_section())
        # Add effort/model info (per-conversation state)
        from ..config import resolve_effort
        effort = getattr(ctx, "effort", "default")
        resolved = resolve_effort(ctx.config, effort)
        sections.append(f"- **Model:** {resolved.model} (effort: {effort})")
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

    sections.append("")

    # Tools
    try:
        sections.extend(_tools_section(ctx))
    except Exception as e:
        sections.append(f"### Tools\n- [error: {e}]")

    sections.append("")

    # Embeddings
    try:
        sections.extend(_embeddings_section(ctx.config))
    except Exception as e:
        sections.append(f"### Embeddings\n- [error: {e}]")

    sections.append("")

    # Schedules
    try:
        sections.extend(_schedule_section(ctx.config))
    except Exception as e:
        sections.append(f"### Scheduled Tasks\n- [error: {e}]")

    return "\n".join(sections)


HEALTH_TOOLS = {
    "health_status": tool_health_status,
}

HEALTH_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "health_status",
            "description": (
                "Show agent health and diagnostic status. "
                "Reports process uptime, memory usage, MCP server connections, "
                "heartbeat timing, tool deferral stats, and embedding index size. "
                "Use when asked about agent status, health, diagnostics, or uptime."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
]
