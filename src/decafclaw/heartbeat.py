"""Heartbeat — periodic agent wake-up that reads HEARTBEAT.md and performs tasks."""

import asyncio
import logging
import re
import time
from datetime import datetime

log = logging.getLogger(__name__)

# Interval parsing: 30m, 1h, 1h30m, or plain seconds
_INTERVAL_RE = re.compile(r"^(?:(\d+)h)?(?:(\d+)m)?$")


def parse_interval(value: str) -> int | None:
    """Parse a time interval string into seconds.

    Supports: "30m", "1h", "1h30m", "90" (plain seconds).
    Returns None if disabled ("", "0") or invalid.
    """
    value = value.strip()
    if not value or value == "0":
        return None

    # Try plain integer (seconds)
    if value.isdigit():
        n = int(value)
        return n if n > 0 else None

    match = _INTERVAL_RE.match(value)
    if not match:
        log.warning(f"Invalid heartbeat interval: {value!r}")
        return None

    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    total = hours * 3600 + minutes * 60
    return total if total > 0 else None


def load_heartbeat_sections(config) -> list[dict]:
    """Read and merge HEARTBEAT.md files, split into sections.

    Reads from agent_path (admin) and workspace_path (agent-writable),
    splits each into sections, and tags with source.

    Returns list of {"title": str, "body": str, "source": str} dicts.
    source is "admin" or "workspace".
    """
    sections = []

    # Admin-level HEARTBEAT.md
    admin_path = config.agent_path / "HEARTBEAT.md"
    if admin_path.exists():
        try:
            for s in _split_sections(admin_path.read_text()):
                s["source"] = "admin"
                sections.append(s)
        except OSError as e:
            log.warning(f"Cannot read {admin_path}: {e}")

    # Workspace-level HEARTBEAT.md
    workspace_path = config.workspace_path / "HEARTBEAT.md"
    if workspace_path.exists():
        try:
            for s in _split_sections(workspace_path.read_text()):
                s["source"] = "workspace"
                sections.append(s)
        except OSError as e:
            log.warning(f"Cannot read {workspace_path}: {e}")

    return sections


def _split_sections(text: str) -> list[dict]:
    """Split markdown text into sections on ## headers.

    Content before the first ## is treated as its own section with title "General".
    """
    sections = []
    current_title = None
    current_lines = []

    for line in text.splitlines():
        if line.startswith("## "):
            # Save previous section
            if current_title is not None or current_lines:
                body = "\n".join(current_lines).strip()
                if body:
                    sections.append({
                        "title": current_title or "General",
                        "body": body,
                    })
            current_title = line[3:].strip()
            current_lines = []
        else:
            current_lines.append(line)

    # Save final section
    if current_title is not None or current_lines:
        body = "\n".join(current_lines).strip()
        if body:
            sections.append({
                "title": current_title or "General",
                "body": body,
            })

    return sections


def is_heartbeat_ok(response: str | None) -> bool:
    """Check if a response indicates nothing to report.

    Returns True if HEARTBEAT_OK appears (case-insensitive) within
    the first 300 characters.
    """
    if not response:
        return False
    return "heartbeat_ok" in response[:300].lower()


def build_section_prompt(section: dict) -> str:
    """Build the prompt for a heartbeat section."""
    preamble = (
        "You are running a scheduled heartbeat check. "
        "Execute the following task and report your findings.\n"
        "If there is nothing to report, respond with HEARTBEAT_OK.\n"
        "Prefer workspace tools (workspace_read, workspace_write, workspace_list) over shell commands.\n\n"
    )

    if section["title"] == "General":
        return preamble + section["body"]
    return preamble + f"## {section['title']}\n\n{section['body']}"


# -- Heartbeat cycle runner ----------------------------------------------------


async def run_heartbeat_cycle(config, event_bus) -> list[dict]:
    """Execute one heartbeat cycle — read sections and run each as an agent turn.

    Returns list of {"title": str, "response": str, "is_ok": bool} dicts.
    """
    # Late imports to avoid circular dependency
    from .agent import run_agent_turn  # noqa: F811
    from .context import Context

    sections = load_heartbeat_sections(config)
    if not sections:
        log.debug("Heartbeat: no sections found, skipping")
        return []

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    results = []

    for i, section in enumerate(sections):
        title = section["title"]
        log.info(f"Heartbeat section {i + 1}/{len(sections)}: {title}")

        try:
            source = section.get("source", "workspace")
            ctx = Context(config=config, event_bus=event_bus)
            ctx.user_id = f"heartbeat-{source}"
            ctx.channel_id = "heartbeat"
            ctx.channel_name = "heartbeat"
            ctx.thread_id = ""
            ctx.conv_id = f"heartbeat-{timestamp}-{i}"

            prompt = build_section_prompt(section)
            result = await run_agent_turn(ctx, prompt, history=[])
            response = result.text if hasattr(result, "text") else (result or "")
            response = response or "(no response)"

            results.append({
                "title": title,
                "response": response,
                "is_ok": is_heartbeat_ok(response),
                "context_id": ctx.context_id,
            })
            log.info(f"Heartbeat section '{title}': {'OK' if results[-1]['is_ok'] else 'ALERT'}")

        except Exception as e:
            log.error(f"Heartbeat section '{title}' failed: {e}", exc_info=True)
            results.append({
                "title": title,
                "response": f"[error: heartbeat section failed: {e}]",
                "is_ok": False,
                "context_id": None,
            })

    return results


# -- Heartbeat timer -----------------------------------------------------------


async def run_heartbeat_timer(config, event_bus, shutdown_event,
                              on_cycle=None, on_results=None):
    """Run the heartbeat timer loop.

    Args:
        config: Agent config with heartbeat_interval
        event_bus: Event bus for agent turns
        shutdown_event: asyncio.Event to signal shutdown
        on_cycle: optional async callback() that handles the full cycle
                  (running + reporting). If provided, on_results is ignored.
        on_results: optional async callback(results) called after the default cycle.
    """
    interval = parse_interval(config.heartbeat_interval)
    if interval is None:
        log.info("Heartbeat disabled (no interval configured)")
        return

    log.info(f"Heartbeat timer starting: interval={config.heartbeat_interval} ({interval}s)")

    cycle_running = False

    while not shutdown_event.is_set():
        # Sleep for the interval, but wake on shutdown
        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=interval)
            # If we get here, shutdown was signaled
            break
        except asyncio.TimeoutError:
            # Normal — interval elapsed, time for a heartbeat
            pass

        if shutdown_event.is_set():
            break

        # Overlap protection
        if cycle_running:
            log.warning("Heartbeat cycle still running, skipping this tick")
            continue

        cycle_running = True
        try:
            log.info("Heartbeat cycle starting")

            if on_cycle:
                # Custom cycle handler (e.g., streaming Mattermost reporter)
                await on_cycle()
            else:
                # Default: run cycle, then report
                results = await run_heartbeat_cycle(config, event_bus)
                if on_results and results:
                    try:
                        await on_results(results)
                    except Exception as e:
                        log.error(f"Heartbeat reporting failed: {e}")

        except Exception as e:
            log.error(f"Heartbeat cycle failed: {e}")
        finally:
            cycle_running = False

    log.info("Heartbeat timer stopped")
