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


async def run_section_turn(
    config, event_bus, section: dict, timestamp: str, index: int,
) -> dict:
    """Run a single heartbeat section as an agent turn.

    Returns {"title": str, "response": str, "is_ok": bool, "context_id": str | None}.
    Shared by run_heartbeat_cycle and heartbeat_tools._run_heartbeat_to_channel.
    """
    from .agent import run_agent_turn  # deferred: circular dep
    from .context import Context

    title = section["title"]
    log.info(f"Heartbeat section {index + 1}: {title}")

    try:
        source = section.get("source", "workspace")
        ctx = Context(config=config, event_bus=event_bus)
        ctx.user_id = f"heartbeat-{source}"
        ctx.channel_id = "heartbeat"
        ctx.channel_name = "heartbeat"
        ctx.thread_id = ""
        ctx.conv_id = f"heartbeat-{timestamp}-{index}"

        prompt = build_section_prompt(section)
        result = await run_agent_turn(ctx, prompt, history=[])
        response = result.text or "(no response)"
        ok = is_heartbeat_ok(response)
        log.info(f"Heartbeat section '{title}': {'OK' if ok else 'ALERT'}")

        return {
            "title": title,
            "response": response,
            "is_ok": ok,
            "context_id": ctx.context_id,
        }
    except Exception as e:
        log.error(f"Heartbeat section '{title}' failed: {e}", exc_info=True)
        return {
            "title": title,
            "response": f"[error: heartbeat section failed: {e}]",
            "is_ok": False,
            "context_id": None,
        }


async def run_heartbeat_cycle(config, event_bus) -> list[dict]:
    """Execute one heartbeat cycle — read sections and run each as an agent turn.

    Returns list of {"title": str, "response": str, "is_ok": bool} dicts.
    """
    sections = load_heartbeat_sections(config)
    if not sections:
        log.debug("Heartbeat: no sections found, skipping")
        return []

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    results = []

    for i, section in enumerate(sections):
        result = await run_section_turn(config, event_bus, section, timestamp, i)
        results.append(result)

    return results


# -- Heartbeat timer -----------------------------------------------------------


def _heartbeat_timestamp_path(config):
    """Path to the file that persists the last heartbeat time."""
    return config.workspace_path / ".heartbeat_last_run"


def read_last_heartbeat(config) -> float:
    """Read the last heartbeat timestamp from disk. Returns 0 if not found."""
    path = _heartbeat_timestamp_path(config)
    try:
        if path.exists():
            return float(path.read_text().strip())
    except (ValueError, OSError):
        pass
    return 0


def _write_last_heartbeat(config):
    """Write the current time as the last heartbeat timestamp."""
    path = _heartbeat_timestamp_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(time.time()))


# Poll interval for checking if heartbeat is due (seconds)
_POLL_INTERVAL = 60


async def run_heartbeat_timer(config, event_bus, shutdown_event,
                              on_cycle=None, on_results=None):
    """Run the heartbeat timer loop.

    Persists the last heartbeat time to disk so the timer survives
    restarts. Polls every 60s to check if the interval has elapsed.

    Args:
        config: Agent config with heartbeat_interval
        event_bus: Event bus for agent turns
        shutdown_event: asyncio.Event to signal shutdown
        on_cycle: optional async callback() that handles the full cycle
                  (running + reporting). If provided, on_results is ignored.
        on_results: optional async callback(results) called after the default cycle.
    """
    interval = parse_interval(config.heartbeat.interval)
    if interval is None:
        log.info("Heartbeat disabled (no interval configured)")
        return

    last_run = read_last_heartbeat(config)
    if last_run > 0:
        elapsed = time.time() - last_run
        remaining = max(0, interval - elapsed)
        log.info(f"Heartbeat timer starting: interval={config.heartbeat.interval} ({interval}s), "
                 f"last run {elapsed:.0f}s ago, next in {remaining:.0f}s")
    else:
        log.info(f"Heartbeat timer starting: interval={config.heartbeat.interval} ({interval}s), "
                 f"no previous run recorded")

    cycle_running = False

    while not shutdown_event.is_set():
        # Poll every _POLL_INTERVAL seconds (or less if shutdown)
        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=_POLL_INTERVAL)
            break  # shutdown signaled
        except asyncio.TimeoutError:
            pass  # normal — check if heartbeat is due

        if shutdown_event.is_set():
            break

        # Check if enough time has passed since last heartbeat
        last_run = read_last_heartbeat(config)
        if last_run > 0 and (time.time() - last_run) < interval:
            continue  # not yet due

        # Overlap protection
        if cycle_running:
            log.warning("Heartbeat cycle still running, skipping this tick")
            continue

        cycle_running = True
        try:
            log.info("Heartbeat cycle starting")
            _write_last_heartbeat(config)

            if on_cycle:
                await on_cycle()
            else:
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
