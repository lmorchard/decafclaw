"""Event bus — simple in-process pub/sub and lifecycle hooks."""

import asyncio
import logging
from collections.abc import Callable
from enum import Enum, auto
from typing import TYPE_CHECKING
from uuid import uuid4

if TYPE_CHECKING:
    from decafclaw.context import Context

log = logging.getLogger(__name__)


class TurnLifecycle(Enum):
    """Lifecycle phases for context interception and extension hooks."""

    PRE_COMPOSE = auto()  # async collector
    BEFORE_LLM_CALL = auto()  # sync interceptor
    ON_TURN_SETTLE = auto()  # async observer
    PRE_COMPACTION = auto()  # async collector / observer
    # Async collector run on a candidate final response, before delivery. The
    # first hook returning a CandidateVerdict settles it; see
    # decafclaw.candidate_response.
    CANDIDATE_RESPONSE = auto()


class EventBus:
    """Simple pub/sub event bus. Supports sync and async subscribers and lifecycle hooks."""

    def __init__(self):
        self._subscribers: dict[str, Callable] = {}
        self._lifecycle_subscribers: dict[TurnLifecycle, dict[str, Callable]] = {}
        self._sub_id_to_phase: dict[str, TurnLifecycle] = {}

    def subscribe(self, callback_or_phase, callback=None, *, phase: TurnLifecycle | None = None) -> str:
        """Register a callback. Returns a subscription ID.

        Can be called as:
          - bus.subscribe(callback) -> global event subscriber
          - bus.subscribe(TurnLifecycle.PRE_COMPOSE, callback)
          - bus.subscribe(callback, phase=TurnLifecycle.PRE_COMPOSE)
        """
        sub_id = uuid4().hex
        if isinstance(callback_or_phase, TurnLifecycle):
            target_phase = callback_or_phase
            target_callback = callback
        elif phase is not None:
            target_phase = phase
            target_callback = callback_or_phase
        else:
            target_phase = None
            target_callback = callback_or_phase

        if target_phase is not None:
            if target_phase not in self._lifecycle_subscribers:
                self._lifecycle_subscribers[target_phase] = {}
            if target_callback is not None:
                self._lifecycle_subscribers[target_phase][sub_id] = target_callback
            self._sub_id_to_phase[sub_id] = target_phase
        elif target_callback is not None:
            self._subscribers[sub_id] = target_callback
        return sub_id

    def subscribe_lifecycle(self, phase: TurnLifecycle, callback: Callable) -> str:
        """Register a callback for a specific lifecycle phase."""
        return self.subscribe(phase, callback)

    def unsubscribe(self, subscription_id: str) -> None:
        """Remove a subscriber by ID."""
        self._subscribers.pop(subscription_id, None)
        phase = self._sub_id_to_phase.pop(subscription_id, None)
        if phase and phase in self._lifecycle_subscribers:
            self._lifecycle_subscribers[phase].pop(subscription_id, None)

    def get_lifecycle_hooks(self, phase: TurnLifecycle) -> list[Callable]:
        """Return list of active hooks registered for a lifecycle phase."""
        return list(self._lifecycle_subscribers.get(phase, {}).values())

    async def publish(self, event: dict) -> None:
        """Publish an event to all subscribers. Never propagates exceptions."""
        for sub_id, callback in list(self._subscribers.items()):
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(event)
                else:
                    callback(event)
            except Exception:
                log.exception(f"Subscriber {sub_id} raised an exception")


def emit_for_ctx(ctx: "Context"):
    """The manager's emit callable for this ctx, or None when there's no manager.

    Shared by every producer that mirrors state into a UI surface — the canvas
    tools, the sticky slot, the checklist loop, and the project skill — so the
    fail-open "no manager, no emit" case is written once instead of four times.

    ``getattr`` rather than ``ctx.manager``, kept verbatim from the four local
    copies this replaced (#657). A real ``Context`` always has the attribute
    (``Context.__init__`` sets ``self.manager = None``), so the default only
    matters for ctx-*like* objects — which today means test doubles such as the
    ``SimpleNamespace`` in ``tests/test_project_tools.py``. Tolerating those is
    the point: narrowing this to ``ctx.manager`` would be a behaviour change
    that no existing test could catch, so it stays as-is.
    """
    manager = getattr(ctx, "manager", None)
    if manager is None:
        return None
    return manager.emit
