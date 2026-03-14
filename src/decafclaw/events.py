"""Event bus — simple in-process pub/sub."""

import asyncio
import logging
from uuid import uuid4

log = logging.getLogger(__name__)


class EventBus:
    """Simple pub/sub event bus. Supports sync and async subscribers."""

    def __init__(self):
        self._subscribers: dict[str, callable] = {}

    def subscribe(self, callback) -> str:
        """Register a callback. Returns a subscription ID."""
        sub_id = uuid4().hex
        self._subscribers[sub_id] = callback
        return sub_id

    def unsubscribe(self, subscription_id: str):
        """Remove a subscriber by ID."""
        self._subscribers.pop(subscription_id, None)

    async def publish(self, event: dict):
        """Publish an event to all subscribers. Never propagates exceptions."""
        for sub_id, callback in list(self._subscribers.items()):
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(event)
                else:
                    callback(event)
            except Exception:
                log.exception(f"Subscriber {sub_id} raised an exception")
