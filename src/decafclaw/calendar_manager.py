"""Durable one-shot calendar events and their wake turns (#1896).

A calendar event stores an absolute fire time and a target conversation. At
the fire time the manager enqueues a ``TurnKind.WAKE`` turn with the target
conversation's history, then marks the event fired. Events that became due
while the server was offline fire on the startup scan.

The store lives in :mod:`decafclaw.calendar_events`. This module owns timers,
dedicated-session upserts, notification fallback, and lifecycle.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import replace
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from .calendar_events import (
    CalendarEvent,
    calendar_session_conv_id,
    load_all_calendar_events,
    load_calendar_events,
    now_iso,
    parse_fire_at,
    save_calendar_events,
)
from .calendar_policy import activate_required_skills, apply_event_policy
from .calendar_recurrence import next_occurrence
from .conversation_paths import conversation_dir

if TYPE_CHECKING:
    from .conversation_manager import ConversationManager

log = logging.getLogger(__name__)


def ensure_dedicated_session(config, user_id: str, session_key: str, session_title: str) -> str:
    """Upsert the web conversation for a dedicated calendar session.

    Returns the deterministic conversation id. An existing conversation keeps
    its title and is unarchived. A missing conversation is created.
    """
    from .web.conversations import ConversationIndex

    conv_id = calendar_session_conv_id(user_id, session_key)
    index = ConversationIndex(config)
    index.get_or_create(conv_id, user_id, session_title)
    return conv_id


def _build_prompt(event: CalendarEvent, config: Any = None) -> str:
    """Build the wake prompt for a calendar event."""
    from .calendar_policy import resolve_event_skill_dir
    from .skills import substitute_skill_placeholders

    reason = event.reason or ""
    if reason:
        skill_dir = resolve_event_skill_dir(config, event) if config else ""
        reason = substitute_skill_placeholders(reason, skill_dir=skill_dir)

    lines = [f"Calendar event: {event.title}."]
    if reason:
        lines.append(f"Reason: {reason}.")
    lines.append(f"Due: {event.fire_at}.")
    if event.session_mode == "dedicated" and event.session_title:
        lines.append(f"Target session: {event.session_title}.")
    if time.time() > event.fire_at_ts + 60:
        lines.append("This event became due while the server was offline. It is firing late.")
    lines.append("This is a calendar reminder. Respond to the user with the reminder or the action they requested.")
    return " ".join(lines)


class CalendarManager:
    """Coordinates calendar timers, sidecars, and target sessions."""

    def __init__(self, config, manager: "ConversationManager | None" = None) -> None:
        self.config = config
        self.manager = manager
        self._tasks: dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()
        self._shutting_down = False

    # -- store and timer operations ------------------------------------------

    def _with_target(self, event: CalendarEvent) -> CalendarEvent:
        """Resolve the target conversation for an event."""
        if event.session_mode == "current":
            return replace(event, target_conv_id=event.conv_id, session_key="", session_title="")
        if event.session_mode == "dedicated":
            if not event.user_id or not event.session_key:
                raise ValueError("dedicated events require user_id and session_key")
            target = ensure_dedicated_session(
                self.config,
                event.user_id,
                event.session_key,
                event.session_title,
            )
            return replace(event, target_conv_id=target)
        raise ValueError(f"unknown session_mode: {event.session_mode!r}")

    async def add(self, event: CalendarEvent) -> CalendarEvent:
        """Persist a new event and arm its timer."""
        if self._shutting_down:
            raise RuntimeError("CalendarManager is shutting down")
        event = self._with_target(event)
        async with self._lock:
            events = load_calendar_events(self.config, event.conv_id)
            events.append(event)
            save_calendar_events(self.config, event.conv_id, events)
            self._arm_locked(event)
        log.info(
            "Calendar event %s for conv %s at %s: %s",
            event.event_id,
            event.conv_id[:8],
            event.fire_at,
            event.title,
        )
        return event

    async def update(self, conv_id: str, event_id: str, **changes) -> CalendarEvent | None:
        """Update a pending event and re-arm its timer."""
        async with self._lock:
            events = load_calendar_events(self.config, conv_id)
            for index, event in enumerate(events):
                if event.event_id != event_id:
                    continue
                if event.status != "pending":
                    raise ValueError("only pending events can be updated")
                updated = replace(event, **changes, updated_at=now_iso())
                updated = self._with_target(updated)
                events[index] = updated
                save_calendar_events(self.config, conv_id, events)
                self._arm_locked(updated)
                return updated
        return None

    async def cancel(self, conv_id: str, event_id: str) -> bool:
        """Cancel a pending event and remove its timer."""
        async with self._lock:
            events = load_calendar_events(self.config, conv_id)
            for index, event in enumerate(events):
                if event.event_id != event_id:
                    continue
                if event.status != "pending":
                    return False
                events[index] = replace(event, status="cancelled", updated_at=now_iso())
                save_calendar_events(self.config, conv_id, events)
                self._cancel_task_locked(event_id)
                return True
        return False

    async def snooze(
        self,
        conv_id: str,
        event_id: str,
        *,
        duration_seconds: int | None = None,
        until: str | None = None,
    ) -> CalendarEvent | None:
        """Move a pending or fired event forward."""
        if (duration_seconds is None) == (until is None):
            raise ValueError("provide exactly one of duration_seconds or until")
        async with self._lock:
            events = load_calendar_events(self.config, conv_id)
            for index, event in enumerate(events):
                if event.event_id != event_id:
                    continue
                if event.status in ("cancelled", "orphaned", "completed"):
                    raise ValueError(f"cannot snooze a {event.status} event")
                if duration_seconds is not None:
                    if not isinstance(duration_seconds, (int, float)) or int(duration_seconds) < 1:
                        raise ValueError("duration_seconds must be a positive integer")
                    base_ts = event.fire_at_ts if event.status == "pending" else time.time()
                    fire_ts = max(time.time(), base_ts) + int(duration_seconds)
                else:
                    assert until is not None
                    _, fire_ts = parse_fire_at(until)
                    if fire_ts <= time.time():
                        raise ValueError("until must be in the future")
                if event.status == "pending" and fire_ts <= event.fire_at_ts:
                    raise ValueError("snooze time must be after the current fire time")
                fire_dt = datetime.fromtimestamp(fire_ts, tz=timezone.utc)
                updated = replace(
                    event,
                    status="pending",
                    fire_at=fire_dt.isoformat(),
                    fire_at_ts=fire_ts,
                    snooze_until=fire_dt.isoformat(),
                    updated_at=now_iso(),
                )
                updated = self._with_target(updated)
                events[index] = updated
                save_calendar_events(self.config, conv_id, events)
                self._arm_locked(updated)
                return updated
        return None

    def get_event(self, conv_id: str, event_id: str) -> CalendarEvent | None:
        """Return one event from an origin conversation, or None."""
        for event in load_calendar_events(self.config, conv_id):
            if event.event_id == event_id:
                return event
        return None

    def list_events(
        self,
        conv_id: str,
        *,
        status: str = "pending",
        from_ts: float | None = None,
        to_ts: float | None = None,
    ) -> list[CalendarEvent]:
        """List events for an origin conversation, sorted by fire time."""
        events = load_calendar_events(self.config, conv_id)
        if status and status != "all":
            events = [event for event in events if event.status == status]
        if from_ts is not None:
            events = [event for event in events if event.fire_at_ts >= from_ts]
        if to_ts is not None:
            events = [event for event in events if event.fire_at_ts <= to_ts]
        return sorted(events, key=lambda event: event.fire_at_ts)

    # -- lifecycle -----------------------------------------------------------

    async def startup_scan(self) -> int:
        """Resume pending events after a restart. Due events fire immediately."""
        events = load_all_calendar_events(self.config)
        recovered = 0
        async with self._lock:
            if self._shutting_down:
                return 0
            for event in events:
                if event.status != "pending" or event.event_id in self._tasks:
                    continue
                self._arm_locked(event)
                recovered += 1
        if recovered:
            log.info("Startup scan: resumed %d calendar event(s)", recovered)
        return recovered

    async def cancel_all_for_conversation(self, conv_id: str) -> None:
        """Cancel pending events whose origin is a deleted conversation."""
        async with self._lock:
            events = load_calendar_events(self.config, conv_id)
            changed = False
            for index, event in enumerate(events):
                if event.status != "pending":
                    continue
                events[index] = replace(event, status="cancelled", updated_at=now_iso())
                self._cancel_task_locked(event.event_id)
                changed = True
            if changed:
                save_calendar_events(self.config, conv_id, events)

    async def shutdown(self) -> None:
        """Cancel in-flight timers without changing sidecar state."""
        self._shutting_down = True
        async with self._lock:
            tasks = list(self._tasks.values())
            for task in tasks:
                if not task.done():
                    task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            self._tasks.clear()
        log.info("Calendar manager shutdown complete")

    # -- firing --------------------------------------------------------------

    def _arm_locked(self, event: CalendarEvent) -> None:
        """Arm a timer for a pending event. Caller holds the lock."""
        if self._shutting_down or event.status != "pending":
            return
        self._cancel_task_locked(event.event_id)
        self._tasks[event.event_id] = asyncio.create_task(self._run_timer(event))

    def _cancel_task_locked(self, event_id: str) -> None:
        task = self._tasks.pop(event_id, None)
        if task is not None and not task.done():
            task.cancel()

    def _discard_current_task_locked(self, event_id: str) -> None:
        """Drop a finished timer without cancelling the task running this call."""
        if self._tasks.get(event_id) is asyncio.current_task():
            self._tasks.pop(event_id, None)

    async def _run_timer(self, event: CalendarEvent) -> None:
        remaining = max(0.0, event.fire_at_ts - time.time())
        try:
            if remaining > 0:
                await asyncio.sleep(remaining)
            await self._fire(event)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("Error running calendar timer for %s: %s", event.event_id, exc)

    async def _fire(self, event: CalendarEvent) -> None:
        """Resolve the target, enqueue a wake turn, and mark the event fired."""
        if self._shutting_down:
            return
        async with self._lock:
            current = self._find_event(event.conv_id, event.event_id)
            if current is None or current.status != "pending" or current.fire_at_ts != event.fire_at_ts:
                self._discard_current_task_locked(event.event_id)
                return

        try:
            target_conv_id = self._resolve_target(current)
        except Exception as exc:
            log.warning("Calendar event %s target resolution failed: %s", current.event_id, exc)
            target_conv_id = None

        if target_conv_id is None:
            if await self._mark_status(current, "orphaned"):
                await self._notify(
                    current,
                    target_conv_id=None,
                    body="The target conversation no longer exists, so this event could not fire.",
                )
            return

        if self.manager is None:
            log.warning("No ConversationManager attached; calendar event %s not fired", current.event_id)
            self._discard_current_task_locked(current.event_id)
            return

        if not await self._enqueue_wake(current, target_conv_id):
            self._discard_current_task_locked(current.event_id)
            return

        if current.recurrence:
            if await self._reschedule_or_complete(current):
                await self._notify_if_unattended(current, target_conv_id)
        elif await self._mark_status(current, "fired", last_fired_at=now_iso()):
            await self._notify_if_unattended(current, target_conv_id)

    async def _enqueue_wake(self, event: CalendarEvent, target_conv_id: str) -> bool:
        """Enqueue the wake turn for an event. Returns False if it could not start.

        Extracted so a scheduled fire and a manual run (`fire_now`) put the same
        turn on the queue: identical prompt, identical policy, identical skill
        activation. A rehearsal that took a different path would not be
        rehearsing anything.

        Deliberately does NOT touch status or recurrence — the caller owns that.
        `_fire` advances the series; `fire_now` must not.
        """
        from .conversation_manager import TurnKind
        from .scheduled_wakes import compose_wake_context_setup

        manager = self.manager
        if manager is None:
            # Both callers check this, but the check must be visible here for the
            # narrowing below; a silent None return keeps the contract simple.
            log.warning("No ConversationManager attached; calendar event %s not started", event.event_id)
            return False

        base_setup = compose_wake_context_setup(
            manager,
            target_conv_id,
            channel_id=event.channel_id,
            thread_id=event.thread_id,
            transport=event.transport,
        )

        async def context_setup(ctx) -> None:
            """Inherited wake setup, then this event's own policy (#1971).

            Order matters: the inherited setup replays the conversation's
            ``last_context_setup``, which restores the previous turn's tool
            policy. Applying the event's policy first would let that stale
            policy win.

            Async because skill activation is async; ``_start_turn`` awaits a
            coroutine ``context_setup``.
            """
            base_setup(ctx)
            apply_event_policy(ctx, event)
            await activate_required_skills(ctx, event)

        try:
            future = await manager.enqueue_turn(
                conv_id=target_conv_id,
                kind=TurnKind.WAKE,
                prompt=_build_prompt(event, self.config),
                metadata={
                    "calendar_event_id": event.event_id,
                    "suppressible": False,
                    "channel_id": event.channel_id,
                    "thread_id": event.thread_id,
                    "transport": event.transport,
                },
                user_id=event.user_id,
                context_setup=context_setup,
                bypass_wake_rate_limit=True,
            )
        except Exception as exc:
            log.warning("Failed to enqueue calendar event %s: %s", event.event_id, exc)
            return False

        if future.rate_limited is True:
            log.warning(
                "Calendar event %s was rate-limited despite bypass; leaving it pending",
                event.event_id,
            )
            return False

        return True

    async def fire_now(self, conv_id: str, event_id: str) -> CalendarEvent | None:
        """Run a pending event's wake turn now, WITHOUT advancing its schedule.

        This is a rehearsal, not a fire. It matters that it is not a fire:
        ``_fire`` -> ``_reschedule_or_complete`` advances a recurring series, so
        testing a weekly event on a Tuesday would silently skip that week. Every
        side effect that makes a fire "count" is withheld here:

        * ``status`` is untouched, so a one-shot event stays pending and does not
          become ``fired``/``completed``.
        * ``fire_at`` and the recurrence anchor are untouched, so the armed timer
          still points at the real occurrence.
        * ``_reschedule_or_complete`` is never called.

        ``last_fired_at`` IS updated — it records that a run happened, which is
        true, and it is the only field whose meaning survives a rehearsal.

        Returns the updated event, or None when it is unknown or not pending.
        """
        async with self._lock:
            current = self._find_event(conv_id, event_id)
            if current is None or current.status != "pending":
                return None

            try:
                target_conv_id = self._resolve_target(current)
            except Exception as exc:  # noqa: BLE001
                log.warning("Calendar event %s target resolution failed: %s", event_id, exc)
                target_conv_id = None
            if target_conv_id is None:
                return None
            if self.manager is None:
                log.warning("No ConversationManager attached; cannot run calendar event %s", event_id)
                return None

            if not await self._enqueue_wake(current, target_conv_id):
                return None

            updated = replace(current, last_fired_at=now_iso(), updated_at=now_iso())
            events = load_calendar_events(self.config, conv_id)
            for index, event in enumerate(events):
                if event.event_id == event_id:
                    events[index] = updated
                    break
            save_calendar_events(self.config, conv_id, events)
            return updated

    async def _reschedule_or_complete(self, event: CalendarEvent) -> bool:
        """Advance a recurring event to its next occurrence, or mark it completed.

        The advance point is the moment this occurrence actually fired
        (``fire_at_ts``), not ``occurrence_at``: ``occurrence_at`` is the series
        anchor, and a snooze deliberately leaves it untouched. Advancing from the
        anchor after a snooze that overshoots a whole interval would recompute an
        occurrence at or before the instant that just fired, firing it twice
        (#1950). Using the later of the two keeps the anchor's wall-clock time
        for every following occurrence while never replaying a snoozed one.

        An occurrence more than a minute late advances from now, so a restart
        fires once instead of replaying every missed occurrence.
        """
        occurrence_ts = datetime.fromisoformat(event.occurrence_at or event.fire_at).timestamp()
        fired_ts = event.fire_at_ts or occurrence_ts
        now = time.time()
        if now - fired_ts > 60:
            base_ts = now
        else:
            base_ts = max(occurrence_ts, fired_ts)
        try:
            following = next_occurrence(event, base_ts)
        except ValueError as exc:
            log.warning("Calendar event %s recurrence failed: %s", event.event_id, exc)
            following = None

        async with self._lock:
            events = load_calendar_events(self.config, event.conv_id)
            for index, current in enumerate(events):
                if current.event_id != event.event_id or current.fire_at_ts != event.fire_at_ts:
                    continue
                if following is None:
                    events[index] = replace(
                        current,
                        status="completed",
                        last_fired_at=now_iso(),
                        updated_at=now_iso(),
                    )
                    save_calendar_events(self.config, event.conv_id, events)
                    self._discard_current_task_locked(event.event_id)
                    return True
                fire_iso, fire_ts = following
                updated = replace(
                    current,
                    status="pending",
                    fire_at=fire_iso,
                    fire_at_ts=fire_ts,
                    occurrence_at=fire_iso,
                    snooze_until="",
                    last_fired_at=now_iso(),
                    updated_at=now_iso(),
                )
                events[index] = updated
                save_calendar_events(self.config, event.conv_id, events)
                self._discard_current_task_locked(event.event_id)
                self._arm_locked(updated)
                return True
        return False

    def _resolve_target(self, event: CalendarEvent) -> str | None:
        if event.session_mode == "current":
            if not conversation_dir(self.config, event.conv_id).exists():
                return None
            return event.conv_id
        if event.session_mode == "dedicated":
            if not event.user_id or not event.session_key:
                return None
            return ensure_dedicated_session(
                self.config,
                event.user_id,
                event.session_key,
                event.session_title,
            )
        return None

    def _find_event(self, conv_id: str, event_id: str) -> CalendarEvent | None:
        for event in load_calendar_events(self.config, conv_id):
            if event.event_id == event_id:
                return event
        return None

    async def _mark_status(
        self,
        event: CalendarEvent,
        status: str,
        *,
        last_fired_at: str | None = None,
    ) -> bool:
        marked = False
        async with self._lock:
            events = load_calendar_events(self.config, event.conv_id)
            for index, current in enumerate(events):
                if current.event_id != event.event_id:
                    continue
                if current.fire_at_ts != event.fire_at_ts:
                    break
                changes = {"status": status, "updated_at": now_iso()}
                if last_fired_at is not None:
                    changes["last_fired_at"] = last_fired_at
                events[index] = replace(current, **changes)
                save_calendar_events(self.config, event.conv_id, events)
                marked = True
                break
            self._discard_current_task_locked(event.event_id)
        return marked

    # -- notification --------------------------------------------------------

    async def _notify_if_unattended(self, event: CalendarEvent, target_conv_id: str) -> None:
        state = self.manager.get_state(target_conv_id) if self.manager is not None else None
        if state is not None and state.subscribers:
            return
        await self._notify(
            event,
            target_conv_id=target_conv_id,
            body=event.reason or event.title,
        )

    async def _notify(self, event: CalendarEvent, *, target_conv_id: str | None, body: str) -> None:
        try:
            from .notifications import notify

            # Inbox-only: the existing Mattermost wake_delivery path handles
            # the channel message. Passing no event bus avoids a duplicate
            # notification-channel delivery for the same event.
            await notify(
                self.config,
                None,
                category="calendar",
                title=event.title,
                body=body,
                link=f"conv://{target_conv_id}" if target_conv_id else None,
                conv_id=target_conv_id or event.conv_id,
            )
        except Exception as exc:
            log.warning("Failed to write calendar notification for %s: %s", event.event_id, exc)
