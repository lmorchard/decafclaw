"""Shared polling loop and task preamble utilities."""

import asyncio
import logging

log = logging.getLogger(__name__)


async def run_polling_loop(
    interval: int,
    shutdown_event,
    on_tick,
    label: str = "poll",
):
    """Run a polling loop that calls on_tick every `interval` seconds.

    - Waits for shutdown_event between ticks (clean cancel).
    - Skips tick if previous is still running (overlap protection).
    - Logs and continues on tick failure.
    """
    tick_running = False

    while not shutdown_event.is_set():
        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=interval)
            break  # shutdown signaled
        except asyncio.TimeoutError:
            pass  # normal — time to check

        if shutdown_event.is_set():
            break

        if tick_running:
            log.warning(f"{label}: previous tick still running, skipping")
            continue

        tick_running = True
        try:
            await on_tick()
        except Exception as e:
            log.error(f"{label}: tick failed: {e}", exc_info=True)
        finally:
            tick_running = False


def build_task_preamble(task_type: str, task_name: str = "") -> str:
    """Produce the common instruction text for heartbeat / scheduled tasks.

    Args:
        task_type: e.g. "heartbeat check" or "scheduled task".
        task_name: optional task name included in the preamble.
    """
    name_clause = f': "{task_name}"' if task_name else ""
    return (
        f"You are running a {task_type}{name_clause}.\n"
        "Execute the following task and report your findings.\n"
        "If there is nothing to report, respond with HEARTBEAT_OK.\n"
        "Prefer workspace tools (workspace_read, workspace_write, "
        "workspace_list) over shell commands.\n\n"
    )
