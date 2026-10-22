"""First-class asynchronous turn scheduling (schedule_turn) for long-running workflows (#1103).

Provides durable, sidecar-backed timer scheduling for conversational wake turns (TurnKind.WAKE).
Survives server restarts and graceful shutdowns without losing scheduled wakes.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .conversation_paths import conversation_dir, scheduled_wakes_path

if TYPE_CHECKING:
    from .conversation_manager import ConversationManager

log = logging.getLogger(__name__)

# Max delay: 30 days in seconds
MAX_DELAY_SECONDS = 30 * 86400


@dataclass
class ScheduledWake:
    """A scheduled future wake turn."""

    schedule_id: str
    conv_id: str
    delay_seconds: int
    reason: str
    scheduled_at: str  # ISO-8601 UTC
    fire_at: str  # ISO-8601 UTC
    fire_at_ts: float  # Unix timestamp
    metadata: dict[str, Any] = field(default_factory=dict)
    user_id: str = ""
    channel_id: str = ""
    thread_id: str = ""
    transport: str = ""


def load_scheduled_wakes(config, conv_id: str) -> list[ScheduledWake]:
    """Load pending scheduled wakes from the conversation's sidecar file.

    Fail-open: missing, empty, or corrupt files return an empty list.
    """
    path = scheduled_wakes_path(config, conv_id)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        entries = data.get("schedules", [])
        return [ScheduledWake(**item) for item in entries if isinstance(item, dict)]
    except Exception as exc:
        log.warning("Failed to load scheduled wakes for conv %s: %s", conv_id, exc)
        return []


def save_scheduled_wakes(config, conv_id: str, schedules: list[ScheduledWake]) -> None:
    """Save scheduled wakes atomically to the conversation's sidecar file."""
    path = scheduled_wakes_path(config, conv_id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"schedules": [asdict(s) for s in schedules]}
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        log.warning("Failed to save scheduled wakes for conv %s: %s", conv_id, exc)
        raise


class ScheduledWakeManager:
    """Coordinates in-memory timers and persistent sidecars for scheduled wakes."""

    def __init__(self, config, manager: ConversationManager | None = None) -> None:
        self.config = config
        self.manager = manager
        self._tasks: dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()
        self._shutting_down = False

    async def schedule(
        self,
        conv_id: str,
        delay_seconds: int,
        reason: str,
        metadata: dict[str, Any] | None = None,
        user_id: str = "",
        channel_id: str = "",
        thread_id: str = "",
        transport: str = "",
    ) -> ScheduledWake:
        """Schedule a wake turn after delay_seconds."""
        if self._shutting_down:
            raise RuntimeError("ScheduledWakeManager is shutting down")
        if delay_seconds < 1:
            raise ValueError(f"delay_seconds must be >= 1 (got {delay_seconds})")
        if delay_seconds > MAX_DELAY_SECONDS:
            raise ValueError(f"delay_seconds exceeds maximum allowed ({MAX_DELAY_SECONDS}s)")
        if not reason or not reason.strip():
            raise ValueError("reason is required")

        now = datetime.now(timezone.utc)
        now_ts = now.timestamp()
        fire_ts = now_ts + delay_seconds
        fire_dt = datetime.fromtimestamp(fire_ts, tz=timezone.utc)

        schedule_id = f"wake_{uuid.uuid4().hex[:8]}"
        entry = ScheduledWake(
            schedule_id=schedule_id,
            conv_id=conv_id,
            delay_seconds=delay_seconds,
            reason=reason.strip(),
            scheduled_at=now.isoformat(),
            fire_at=fire_dt.isoformat(),
            fire_at_ts=fire_ts,
            metadata=metadata or {},
            user_id=user_id,
            channel_id=channel_id,
            thread_id=thread_id,
            transport=transport,
        )

        async with self._lock:
            schedules = load_scheduled_wakes(self.config, conv_id)
            schedules.append(entry)
            save_scheduled_wakes(self.config, conv_id, schedules)

            task = asyncio.create_task(self._run_timer(entry))
            self._tasks[schedule_id] = task

        log.info(
            "Scheduled wake %s for conv %s in %ds (at %s): %s",
            schedule_id,
            conv_id[:8],
            delay_seconds,
            entry.fire_at,
            entry.reason,
        )
        return entry

    async def cancel(self, conv_id: str, schedule_id: str) -> bool:
        """Cancel a pending scheduled wake. Returns True if found and cancelled."""
        async with self._lock:
            schedules = load_scheduled_wakes(self.config, conv_id)
            matching = [s for s in schedules if s.schedule_id == schedule_id]
            if not matching:
                return False

            schedules = [s for s in schedules if s.schedule_id != schedule_id]
            save_scheduled_wakes(self.config, conv_id, schedules)

            task = self._tasks.pop(schedule_id, None)
            if task and not task.done():
                task.cancel()

            log.info("Cancelled scheduled wake %s for conv %s", schedule_id, conv_id[:8])
            return True

    async def cancel_all_for_conversation(self, conv_id: str) -> None:
        """Cancel all pending in-memory timers for a conversation (e.g. on deletion)."""
        async with self._lock:
            schedules = load_scheduled_wakes(self.config, conv_id)
            for s in schedules:
                task = self._tasks.pop(s.schedule_id, None)
                if task and not task.done():
                    task.cancel()

    def list_schedules(self, conv_id: str) -> list[ScheduledWake]:
        """List all pending scheduled wakes for a conversation, sorted by fire time."""
        schedules = load_scheduled_wakes(self.config, conv_id)
        return sorted(schedules, key=lambda s: s.fire_at_ts)

    @staticmethod
    def _make_context_setup(channel_id: str, thread_id: str):
        if not channel_id and not thread_id:
            return None

        def setup(ctx):
            if channel_id:
                ctx.channel_id = channel_id
            if thread_id:
                ctx.thread_id = thread_id

        return setup

    async def _run_timer(self, entry: ScheduledWake) -> None:
        """Sleep until fire_at_ts, then dispatch the wake turn."""
        remaining = max(0.0, entry.fire_at_ts - time.time())
        try:
            if remaining > 0:
                await asyncio.sleep(remaining)
            await self._fire_wake(entry)
        except asyncio.CancelledError:
            log.debug("Wake timer cancelled for %s", entry.schedule_id)
            raise
        except Exception as exc:
            log.warning("Error running wake timer for %s: %s", entry.schedule_id, exc)

    async def _fire_wake(self, entry: ScheduledWake) -> None:
        """Enqueue TurnKind.WAKE on manager and clear sidecar only after acceptance."""
        if self._shutting_down:
            return

        async with self._lock:
            schedules = load_scheduled_wakes(self.config, entry.conv_id)
            if not any(s.schedule_id == entry.schedule_id for s in schedules):
                # Dropped if schedule was cancelled or conversation files deleted (#1119 review)
                self._tasks.pop(entry.schedule_id, None)
                log.debug(
                    "Scheduled wake %s not in sidecar for conv %s; dropping fire", entry.schedule_id, entry.conv_id[:8]
                )
                return

        if self.manager is None:
            log.debug("No manager attached to ScheduledWakeManager; wake %s dropped", entry.schedule_id)
            async with self._lock:
                self._tasks.pop(entry.schedule_id, None)
                schedules = load_scheduled_wakes(self.config, entry.conv_id)
                schedules = [s for s in schedules if s.schedule_id != entry.schedule_id]
                try:
                    save_scheduled_wakes(self.config, entry.conv_id, schedules)
                except OSError:
                    pass
            return

        from .conversation_manager import TurnKind

        prompt = (
            f"Scheduled wake triggered: {entry.reason}. Review history and current status, "
            "then respond to the user or reply with BACKGROUND_WAKE_OK to remain silent "
            "if nothing requires attention."
        )
        wake_meta = {
            **(entry.metadata or {}),
            "schedule_id": entry.schedule_id,
            "channel_id": entry.channel_id,
            "thread_id": entry.thread_id,
            "transport": entry.transport,
        }

        # Compose context setup: preserve existing/inherited setup (media handlers, etc.)
        state = self.manager.get_state(entry.conv_id)
        base_setup = state.last_context_setup if state else None

        def composed_setup(ctx):
            if base_setup:
                base_setup(ctx)
            if entry.channel_id:
                ctx.channel_id = entry.channel_id
            if entry.thread_id:
                ctx.thread_id = entry.thread_id
            if entry.transport:
                ctx.transport = entry.transport
            if entry.transport == "mattermost" and getattr(ctx, "media_handler", None) is None and entry.channel_id:
                mm_client = getattr(self.manager, "mm_client", None)
                if mm_client and getattr(mm_client, "_http", None):
                    from .media import MattermostMediaHandler

                    ctx.media_handler = MattermostMediaHandler(mm_client._http, channel_id=entry.channel_id)

        try:
            future = await self.manager.enqueue_turn(
                conv_id=entry.conv_id,
                kind=TurnKind.WAKE,
                prompt=prompt,
                metadata=wake_meta,
                user_id=entry.user_id,
                context_setup=composed_setup,
            )

            # Check if rate-limited: do NOT drop the wake! Re-arm and retry (#1119 review).
            if getattr(future, "rate_limited", None) is True:
                log.info("Wake %s rate-limited; re-arming in 10s", entry.schedule_id)
                entry.fire_at_ts = time.time() + 10.0
                async with self._lock:
                    if not self._shutting_down:
                        task = asyncio.create_task(self._run_timer(entry))
                        self._tasks[entry.schedule_id] = task
                return

            log.info("Dispatched wake turn for schedule %s (conv %s)", entry.schedule_id, entry.conv_id[:8])

            # Remove from sidecar and task registry only once enqueue succeeds
            async with self._lock:
                self._tasks.pop(entry.schedule_id, None)
                schedules = load_scheduled_wakes(self.config, entry.conv_id)
                schedules = [s for s in schedules if s.schedule_id != entry.schedule_id]
                save_scheduled_wakes(self.config, entry.conv_id, schedules)
        except Exception as exc:
            log.warning("Failed to enqueue wake turn for schedule %s: %s", entry.schedule_id, exc)
            raise

    async def startup_scan_conversation(self, conv_id: str) -> int:
        """Scan and resume pending scheduled wakes for a specific conversation."""
        schedules = load_scheduled_wakes(self.config, conv_id)
        if not schedules:
            return 0

        recovered = 0
        now_ts = time.time()
        for entry in schedules:
            async with self._lock:
                if entry.schedule_id in self._tasks:
                    continue
                task = asyncio.create_task(self._run_timer(entry))
                self._tasks[entry.schedule_id] = task
                recovered += 1

            delay = max(0.0, entry.fire_at_ts - now_ts)
            log.info(
                "Startup scan: resumed scheduled wake %s for conv %s (due in %.1fs)",
                entry.schedule_id,
                conv_id[:8],
                delay,
            )
        return recovered

    async def startup_scan(self) -> int:
        """Scan all conversation sidecars for pending scheduled wakes and resume them.

        Wakes whose fire time has already passed while offline are dispatched immediately.
        Returns the number of resumed timers.
        """
        convs_dir = (self.config.workspace_path / "conversations").resolve()
        if not convs_dir.exists():
            return 0

        recovered = 0
        try:
            children = sorted(convs_dir.iterdir())
        except OSError as exc:
            log.warning("Failed to list conversations dir for scheduled wakes scan: %s", exc)
            return 0

        for child in children:
            if not child.is_dir():
                continue
            recovered += await self.startup_scan_conversation(child.name)

        if recovered:
            log.info("Startup scan: resumed %d scheduled wake timer(s)", recovered)
        return recovered

    async def shutdown(self) -> None:
        """Cancel all in-flight timer tasks on shutdown without altering sidecar state."""
        self._shutting_down = True
        async with self._lock:
            tasks = list(self._tasks.values())
            for t in tasks:
                if not t.done():
                    t.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            self._tasks.clear()
        log.info("Scheduled wake manager shutdown complete")
