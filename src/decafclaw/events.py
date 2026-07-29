"""Event bus — simple in-process pub/sub."""

import asyncio
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING
from uuid import uuid4

if TYPE_CHECKING:
    from decafclaw.context import Context

log = logging.getLogger(__name__)


class EventBus:
    """Simple pub/sub event bus. Supports sync and async subscribers."""

    def __init__(self):
        self._subscribers: dict[str, Callable] = {}

    def subscribe(self, callback) -> str:
        """Register a callback. Returns a subscription ID."""
        sub_id = uuid4().hex
        self._subscribers[sub_id] = callback
        return sub_id

    def unsubscribe(self, subscription_id: str) -> None:
        """Remove a subscriber by ID."""
        self._subscribers.pop(subscription_id, None)

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
