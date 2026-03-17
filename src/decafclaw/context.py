"""Runtime context — carries config, event bus, and request-scoped state."""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import uuid4


class Context:
    """Forkable runtime context inspired by Go's context pattern."""

    def __init__(self, config, event_bus, context_id=None):
        self.config = config
        self.event_bus = event_bus
        self.context_id = context_id or uuid4().hex[:12]

        # Per-conversation state (set via fork() overrides)
        self.user_id: str = ""
        self.channel_id: str = ""
        self.channel_name: str = ""
        self.thread_id: str = ""
        self.conv_id: str = ""
        self.history: list | None = None
        self.messages: list | None = None
        self.cancelled: asyncio.Event | None = None
        self.media_handler: Any = None
        self.on_stream_chunk: Any = None
        self.extra_tools: dict = {}
        self.extra_tool_definitions: list = []
        self.activated_skills: set = set()
        self.total_prompt_tokens: int = 0
        self.total_completion_tokens: int = 0
        self.last_prompt_tokens: int = 0
        self.allowed_tools: set | None = None

    def fork(self, **overrides) -> "Context":
        """Create a child context with a new ID, sharing the event bus."""
        config = overrides.pop("config", self.config)
        child = Context(
            config=config,
            event_bus=self.event_bus,
        )
        for key, value in overrides.items():
            setattr(child, key, value)
        return child

    async def publish(self, event_type: str, **kwargs) -> None:
        """Convenience: publish an event with context_id auto-included."""
        event = {"type": event_type, "context_id": self.context_id, **kwargs}
        await self.event_bus.publish(event)
