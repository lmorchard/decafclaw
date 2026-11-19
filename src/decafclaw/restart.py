"""Centralized restart coordinator with on-idle turn draining (#1533).

Provides a unified facility for requesting server restarts across config change
detection, self-updates, slash commands, and agent admin tools. Coordinates
draining active conversation turns before signaling shutdown so running agent
tasks are never killed mid-execution.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from .restart_wake import RestartWake, delete_restart_wake, save_restart_wake

log = logging.getLogger(__name__)

DEFAULT_MAX_WAIT_SECONDS = 120.0
DEFAULT_POLL_INTERVAL_SECONDS = 0.5


class RestartCoordinator:
    """Coordinates graceful process restarts with on-idle turn draining."""

    def __init__(
        self,
        shutdown_event: asyncio.Event,
        manager: Any | None = None,
        event_bus: Any | None = None,
        config: Any | None = None,
    ) -> None:
        self.shutdown_event = shutdown_event
        self.manager = manager
        self.event_bus = event_bus
        self.config = config
        self.restart_pending = False
        self.restart_reason = ""
        self.restart_requested_at: float | None = None
        self._drain_task: asyncio.Task | None = None
        self._wake_conv_ids: set[str] = set()
        self._lock = asyncio.Lock()

    def _config(self) -> Any | None:
        """Config for wake-record persistence; falls back to the bound manager."""
        return self.config if self.config is not None else getattr(self.manager, "config", None)

    def set_manager(self, manager: Any) -> None:
        """Bind the conversation manager once instantiated."""
        self.manager = manager

    def is_restart_pending(self) -> bool:
        """Return True if a restart has been requested and is draining or pending."""
        return self.restart_pending

    def get_busy_conversations(self) -> list[str]:
        """Return IDs/names of conversations with an active turn."""
        if not self.manager:
            return []
        busy = []
        conversations = getattr(self.manager, "_conversations", {})
        for cid, state in conversations.items():
            is_busy = getattr(state, "busy", False)
            agent_task = getattr(state, "agent_task", None)
            has_task = agent_task is not None and not agent_task.done()
            if is_busy or has_task:
                name = cid[:8]
                index = getattr(self.manager, "_index", None)
                if index and hasattr(index, "get"):
                    conv = index.get(cid)
                    if conv and getattr(conv, "title", None):
                        name = f"'{conv.title}' ({cid[:8]})"
                busy.append(name)
        return busy

    async def request_restart(
        self,
        *,
        reason: str,
        on_idle: bool = True,
        max_wait_seconds: float = DEFAULT_MAX_WAIT_SECONDS,
        poll_interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
        wake: RestartWake | None = None,
    ) -> bool:
        """Request a server restart.

        If ``on_idle=False``, signals the shutdown event immediately.
        If ``on_idle=True``, schedules on-idle draining: waits for active turns
        across all conversations to settle before signaling shutdown.

        ``wake`` records a single-shot post-restart wake for the calling
        conversation (#1554). It is persisted before any restart state changes,
        so a write failure leaves the restart unrequested. Cancelling the
        restart removes it.
        """
        async with self._lock:
            if wake is not None:
                config = self._config()
                if config is None:
                    log.warning(
                        "Cannot persist post-restart wake for conv %s: no config available",
                        wake.conv_id[:8],
                    )
                else:
                    save_restart_wake(config, wake)
                    self._wake_conv_ids.add(wake.conv_id)

            if not on_idle:
                log.info("Immediate restart requested: %s", reason)
                self.restart_pending = True
                self.restart_reason = reason
                if self.event_bus:
                    await self.event_bus.publish(
                        {
                            "type": "restart_scheduled",
                            "reason": reason,
                            "immediate": True,
                        }
                    )
                self.shutdown_event.set()
                return True

            if self.restart_pending:
                log.info("Restart already pending (%s); new request: %s", self.restart_reason, reason)
                return True

            self.restart_pending = True
            self.restart_reason = reason
            self.restart_requested_at = time.time()
            log.info("Restart scheduled on idle: %s", reason)

            if self.event_bus:
                busy = self.get_busy_conversations()
                await self.event_bus.publish(
                    {
                        "type": "restart_scheduled",
                        "reason": reason,
                        "immediate": False,
                        "busy_conversations": busy,
                    }
                )

            self._drain_task = asyncio.create_task(self._drain_and_restart(max_wait_seconds, poll_interval))
            return True

    async def _drain_and_restart(self, max_wait_seconds: float, poll_interval: float) -> None:
        """Poll active conversations until idle, then signal shutdown."""
        start = time.time()
        while not self.shutdown_event.is_set():
            busy = self.get_busy_conversations()
            if not busy:
                log.info("All conversations idle; triggering restart for: %s", self.restart_reason)
                self.shutdown_event.set()
                return

            elapsed = time.time() - start
            if elapsed >= max_wait_seconds:
                log.warning(
                    "Restart on-idle drain timed out after %.1fs with active turns (%s); forcing restart",
                    elapsed,
                    ", ".join(busy),
                )
                self.shutdown_event.set()
                return

            try:
                await asyncio.sleep(poll_interval)
            except asyncio.CancelledError:
                return

    async def cancel_restart(self) -> bool:
        """Cancel a pending on-idle restart request."""
        async with self._lock:
            if not self.restart_pending:
                return False
            log.info("Cancelling pending restart: %s", self.restart_reason)
            self.restart_pending = False
            self.restart_reason = ""
            self.restart_requested_at = None
            config = self._config()
            if config is not None:
                for conv_id in self._wake_conv_ids:
                    delete_restart_wake(config, conv_id)
            self._wake_conv_ids.clear()
            if self._drain_task and not self._drain_task.done():
                self._drain_task.cancel()
                self._drain_task = None
            if self.event_bus:
                await self.event_bus.publish(
                    {
                        "type": "restart_cancelled",
                    }
                )
            return True
