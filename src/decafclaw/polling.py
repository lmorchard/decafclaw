"""Shared polling loop and task preamble utilities."""

import asyncio
import logging

log = logging.getLogger(__name__)


async def run_polling_loop(
    interval: int | float,
    shutdown_event,
    on_tick,
    label: str = "poll",
):
    """Run a polling loop that calls on_tick every `interval` seconds.

    - Waits for shutdown_event between ticks (clean cancel).
    - Logs and continues on tick failure.
    """
    while not shutdown_event.is_set():
        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=interval)
            break  # shutdown signaled
        except asyncio.TimeoutError:
            pass  # normal — time to check

        if shutdown_event.is_set():
            break

        try:
            await on_tick()
        except Exception as e:
            log.error(f"{label}: tick failed: {e}", exc_info=True)


def build_task_preamble(task_type: str, task_name: str = "") -> str:
    """Produce the common instruction text for heartbeat / scheduled tasks.

    Both heartbeat and scheduled tasks use ``HEARTBEAT_OK`` as a quiet-cycle
    signal consumed by ``heartbeat.is_heartbeat_ok()``: heartbeat uses it to
    gate alert-vs-all-clear notification priority; scheduled tasks use it to
    pick between a tidy "HEARTBEAT_OK" log line and a response-preview
    log line.

    Per #362 we want scheduled archives to always end with real narrative
    (not bare tokens) for retrospective tools like ``!newsletter``. So the
    scheduled branch requires a narrative summary AND allows ``HEARTBEAT_OK``
    as a leading marker when the cycle was quiet. The marker goes first so
    ``is_heartbeat_ok()`` (which scans the first 300 chars) still detects
    quiet cycles reliably.

    Heartbeat keeps its original terser wording — ``is_heartbeat_ok()``
    fires off responses that are often just the marker, and heartbeat has
    no narrative-retrospective consumer to serve.

    Args:
        task_type: e.g. "heartbeat check" or "scheduled task".
        task_name: optional task name included in the preamble.
    """
    name_clause = f': "{task_name}"' if task_name else ""
    if "heartbeat" in task_type.lower():
        closing = (
            "If there is nothing to report, respond with HEARTBEAT_OK.\n"
        )
    else:
        closing = (
            "End your turn with a short narrative summary of what you did "
            "this cycle. If the cycle was quiet — nothing notable "
            "happened, no changes made — begin your summary with "
            "HEARTBEAT_OK on its own line, followed by a brief note "
            "saying why. Otherwise, just describe the actual activity.\n"
        )
    return (
        f"You are running a {task_type}{name_clause}.\n"
        "Execute the following task and report your findings.\n"
        f"{closing}"
        "Prefer workspace tools (workspace_read, workspace_write, "
        "workspace_list) over shell commands.\n\n"
    )
