"""Runtime context — carries config, event bus, and request-scoped state."""

from uuid import uuid4


class Context:
    """Forkable runtime context inspired by Go's context pattern."""

    def __init__(self, config, event_bus, context_id=None):
        self.config = config
        self.event_bus = event_bus
        self.context_id = context_id or uuid4().hex[:12]

    def fork(self, **overrides):
        """Create a child context with a new ID, sharing the event bus."""
        config = overrides.pop("config", self.config)
        child = Context(
            config=config,
            event_bus=self.event_bus,
        )
        for key, value in overrides.items():
            setattr(child, key, value)
        return child

    async def publish(self, event_type: str, **kwargs):
        """Convenience: publish an event with context_id auto-included."""
        event = {"type": event_type, "context_id": self.context_id, **kwargs}
        await self.event_bus.publish(event)
