"""Durable deferred delivery for notification channels (#1664).

An interrupting channel (Mattermost DM, email, ntfy push) should not fire
while the operator is plausibly already looking at the event. Instead of
delivering at publish time, a channel configured ``delivery: "deferred"``
arms an escalation: a persisted fire-at time plus the payload the channel
needs to deliver later.

Two things cancel an armed escalation before it fires:

- ``notification_read`` on the global event bus, for inbox records.
- An explicit :func:`cancel_escalations` call keyed by confirmation id,
  made by ``ConversationManager`` when a confirmation is answered,
  cancelled, or times out.

The pending-escalation file lives at
``data/{agent_id}/pending_escalations.jsonl`` — outside the
agent-writable workspace. A forged entry would make the agent buzz the
operator, the same reason ``approval_state/`` and ``scheduled_wakes/``
live there.

Delivery survives a restart: :meth:`EscalationScheduler.start` reloads
the file, re-arms future entries, and fires overdue ones immediately.
An entry is removed from disk *before* its delivery runs, so a crash
mid-delivery cannot duplicate the push (it can lose it, which matches
the inbox-is-authoritative rule).
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import os
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# Deliverer signature: receives the persisted payload dict.
Deliverer = Callable[[dict], Awaitable[None]]

PENDING_FILE_NAME = "pending_escalations.jsonl"


def _parse_iso(ts: str) -> datetime:
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    return datetime.fromisoformat(ts)


@dataclasses.dataclass
class PendingEscalation:
    """One armed-but-unfired delivery."""

    channel: str
    key: str
    fire_at: str  # ISO-8601 UTC
    payload: dict = dataclasses.field(default_factory=dict)

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, raw: dict) -> "PendingEscalation | None":
        try:
            channel = str(raw["channel"])
            key = str(raw["key"])
            fire_at = str(raw["fire_at"])
            payload = raw.get("payload") or {}
            if not isinstance(payload, dict):
                return None
            return cls(channel=channel, key=key, fire_at=fire_at, payload=payload)
        except (KeyError, TypeError):
            return None


def pending_path(config) -> Path:
    """Admin-dir path of the pending-escalation log."""
    return config.agent_path / PENDING_FILE_NAME


def _same_key(a: PendingEscalation, key: str, channel: str | None) -> bool:
    return a.key == key and (channel is None or a.channel == channel)


class EscalationScheduler:
    """Arm, persist, cancel, and fire deferred channel deliveries."""

    def __init__(self, config, event_bus: Any = None, *, now: Callable[[], datetime] | None = None) -> None:
        self.config = config
        self.event_bus = event_bus
        self._now = now or (lambda: datetime.now(tz=timezone.utc))
        self._deliverers: dict[str, Deliverer] = {}
        self._entries: dict[tuple[str, str], PendingEscalation] = {}
        self._tasks: dict[tuple[str, str], asyncio.Task] = {}
        self._subscription: str | None = None
        self._started = False

    # -- Registration ---------------------------------------------------------

    def register(self, channel: str, deliver: Deliverer) -> None:
        """Register the delivery callback for ``channel``.

        A restarted process re-registers from its channel adapters before
        :meth:`start` fires overdue entries.
        """
        self._deliverers[channel] = deliver

    # -- Arm / cancel ---------------------------------------------------------

    def arm(self, channel: str, key: str, delay_sec: float, payload: dict | None = None) -> PendingEscalation:
        """Schedule delivery of ``payload`` on ``channel`` in ``delay_sec``.

        Re-arming the same ``(channel, key)`` replaces the earlier entry.
        """
        delay = max(0.0, float(delay_sec))
        fire_at = (self._now() + timedelta(seconds=delay)).astimezone(timezone.utc)
        entry = PendingEscalation(
            channel=channel,
            key=key,
            # Keep sub-second precision: truncating to whole seconds made a
            # sub-second delay compute as already-due and fire immediately.
            fire_at=fire_at.isoformat(),
            payload=payload or {},
        )
        self._entries[(channel, key)] = entry
        self._persist()
        self._schedule(entry)
        return entry

    def cancel(self, key: str, channel: str | None = None) -> int:
        """Drop entries matching ``key`` (and ``channel`` when given).

        Returns the number of entries removed. Idempotent.
        """
        matches = [ident for ident, entry in self._entries.items() if _same_key(entry, key, channel)]
        for ident in matches:
            self._entries.pop(ident, None)
            task = self._tasks.pop(ident, None)
            if task is not None:
                task.cancel()
        if matches:
            self._persist()
        return len(matches)

    def cancel_many(self, keys: list[str], channel: str | None = None) -> int:
        return sum(self.cancel(key, channel) for key in keys)

    def pending(self) -> list[PendingEscalation]:
        """Currently armed entries, earliest fire first (for tests and diagnostics)."""
        return sorted(self._entries.values(), key=lambda e: e.fire_at)

    # -- Lifecycle ------------------------------------------------------------

    def start(self) -> None:
        """Subscribe for read cancellations and re-arm persisted entries."""
        if self._started:
            return
        self._started = True
        if self.event_bus is not None:
            self._subscription = self.event_bus.subscribe(self._on_event)
        for entry in self._load():
            self._entries[(entry.channel, entry.key)] = entry
            self._schedule(entry)

    async def stop(self) -> None:
        """Stop timers. Persisted entries stay so a restart re-arms them."""
        self._started = False
        if self._subscription is not None and self.event_bus is not None:
            self.event_bus.unsubscribe(self._subscription)
            self._subscription = None
        tasks = list(self._tasks.values())
        self._tasks.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    # -- Internals ------------------------------------------------------------

    def _on_event(self, event: dict) -> None:
        if event.get("type") != "notification_read":
            return
        ids = event.get("ids") or []
        if isinstance(ids, list):
            self.cancel_many([str(i) for i in ids])

    def _schedule(self, entry: PendingEscalation) -> None:
        ident = (entry.channel, entry.key)
        old = self._tasks.pop(ident, None)
        if old is not None:
            old.cancel()
        try:
            delay = (_parse_iso(entry.fire_at) - self._now()).total_seconds()
        except (ValueError, TypeError):
            log.warning("Dropping pending escalation with unparseable fire_at: %s", entry.fire_at)
            self._entries.pop(ident, None)
            self._persist()
            return
        self._tasks[ident] = asyncio.create_task(self._sleep_and_fire(entry, max(0.0, delay)))

    async def _sleep_and_fire(self, entry: PendingEscalation, delay: float) -> None:
        try:
            if delay > 0:
                await asyncio.sleep(delay)
            await self._fire(entry)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Pending escalation failed (channel=%s key=%s)", entry.channel, entry.key)

    async def _fire(self, entry: PendingEscalation) -> None:
        ident = (entry.channel, entry.key)
        # Remove before delivering so a crash cannot replay the push.
        if ident in self._entries:
            self._entries.pop(ident, None)
            self._persist()
        self._tasks.pop(ident, None)
        deliver = self._deliverers.get(entry.channel)
        if deliver is None:
            log.warning(
                "No deliverer registered for deferred channel %r (key=%s); dropping escalation",
                entry.channel,
                entry.key,
            )
            return
        await deliver(entry.payload)

    def _load(self) -> list[PendingEscalation]:
        path = pending_path(self.config)
        if not path.exists():
            return []
        entries: list[PendingEscalation] = []
        try:
            with path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = PendingEscalation.from_dict(json.loads(line))
                    except json.JSONDecodeError as exc:
                        log.warning("Malformed pending-escalation line in %s: %s", path, exc)
                        continue
                    if entry is not None:
                        entries.append(entry)
        except OSError as exc:
            log.warning("Failed to read %s: %s", path, exc)
            return []
        return entries

    def _persist(self) -> None:
        path = pending_path(self.config)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".tmp")
            with tmp.open("w", encoding="utf-8") as f:
                for entry in self._entries.values():
                    f.write(json.dumps(entry.to_dict(), separators=(",", ":")) + "\n")
            os.replace(tmp, path)
        except OSError as exc:
            # Fail-open for the in-process timer: the push still happens, but
            # the entry is lost on restart. Log loudly because durability is
            # the whole point of the file.
            log.error("Failed to persist pending escalations to %s: %s", path, exc)


# -- Process-wide registry ----------------------------------------------------

_schedulers: list[EscalationScheduler] = []


def register_scheduler(scheduler: EscalationScheduler) -> None:
    if scheduler not in _schedulers:
        _schedulers.append(scheduler)


def cancel_escalations(key: str) -> int:
    """Cancel any armed escalation with ``key`` across schedulers.

    Fail-open: used by confirmation resolution paths that must not raise.
    """
    removed = 0
    for scheduler in list(_schedulers):
        try:
            removed += scheduler.cancel(key)
        except Exception:
            log.exception("Failed to cancel escalation for key %s", key)
    return removed


def reset_schedulers() -> None:
    """Test helper: forget registered schedulers without stopping them."""
    _schedulers.clear()
