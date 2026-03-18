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
        self.skill_data: dict = {}  # generic per-conversation state for skills
        self.total_prompt_tokens: int = 0
        self.total_completion_tokens: int = 0
        self.last_prompt_tokens: int = 0
        self.allowed_tools: set | None = None
        self.current_tool_call_id: str = ""
        self.event_context_id: str = ""  # publish events under this ID instead of context_id

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

    def fork_for_tool_call(self, tool_call_id: str) -> "Context":
        """Create a lightweight context fork for a concurrent tool call.

        Shares the same context_id and event_bus so events route correctly,
        but has its own current_tool_call_id so concurrent tools don't race.
        """
        child = Context(
            config=self.config,
            event_bus=self.event_bus,
            context_id=self.context_id,
        )
        child.current_tool_call_id = tool_call_id
        child.event_context_id = self.event_context_id
        child.cancelled = self.cancelled
        child.extra_tools = self.extra_tools
        child.extra_tool_definitions = self.extra_tool_definitions
        child.activated_skills = self.activated_skills
        child.skill_data = self.skill_data
        child.allowed_tools = self.allowed_tools
        child.conv_id = self.conv_id
        child.channel_id = self.channel_id
        child.channel_name = self.channel_name
        child.thread_id = self.thread_id
        child.user_id = self.user_id
        child.media_handler = self.media_handler
        return child

    async def publish(self, event_type: str, **kwargs) -> None:
        """Convenience: publish an event with context_id auto-included.

        Automatically includes tool_call_id from current_tool_call_id
        when set, unless explicitly provided in kwargs.
        """
        ctx_id = self.event_context_id or self.context_id
        event = {"type": event_type, "context_id": ctx_id, **kwargs}
        if self.current_tool_call_id and "tool_call_id" not in kwargs:
            event["tool_call_id"] = self.current_tool_call_id
        await self.event_bus.publish(event)
