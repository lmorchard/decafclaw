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
        self.deferred_tool_pool: list = []  # tool defs available via tool_search
        self.preapproved_tools: set = set()  # tools pre-approved by command invocation
        self.preapproved_shell_patterns: list[str] = []  # scoped shell approval globs
        self._current_iteration: int = 1
        self.is_child: bool = False
        self.skip_reflection: bool = False
        self.skip_memory_context: bool = False
        self.effort: str = "default"

    @classmethod
    def for_task(
        cls,
        config,
        event_bus,
        *,
        user_id: str,
        conv_id: str,
        channel_id: str = "",
        channel_name: str = "",
        effort: str = "default",
        skip_reflection: bool = True,
        skip_memory_context: bool = True,
        allowed_tools: set | None = None,
        preapproved_tools: set | None = None,
        preapproved_shell_patterns: list[str] | None = None,
    ) -> "Context":
        """Create a context for a background task (heartbeat, scheduled task, etc.).

        Provides sensible defaults for non-interactive work: reflection and
        memory context are skipped by default.
        """
        ctx = cls(config=config, event_bus=event_bus)
        ctx.user_id = user_id
        ctx.conv_id = conv_id
        ctx.channel_id = channel_id
        ctx.channel_name = channel_name
        ctx.effort = effort
        ctx.skip_reflection = skip_reflection
        ctx.skip_memory_context = skip_memory_context
        if allowed_tools is not None:
            ctx.allowed_tools = allowed_tools
        if preapproved_tools is not None:
            ctx.preapproved_tools = preapproved_tools
        if preapproved_shell_patterns is not None:
            ctx.preapproved_shell_patterns = preapproved_shell_patterns
        return ctx

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

        Shallow-copies all fields from the parent so new fields are
        automatically inherited. Only overrides what must differ:
        current_tool_call_id (the purpose of the fork) and token
        counters (fresh per-call, not accumulated into parent).
        """
        child = Context(
            config=self.config,
            event_bus=self.event_bus,
            context_id=self.context_id,
        )
        # Copy all fields from parent, then override specifics.
        # Note: mutable containers (dicts, sets, lists) are shared by reference.
        # This is intentional — concurrent tool calls read shared state but don't
        # mutate extra_tools, skill_data, etc. during execution.
        child.__dict__.update(self.__dict__)
        child.current_tool_call_id = tool_call_id
        # Fresh token counters — don't accumulate child usage into parent
        child.total_prompt_tokens = 0
        child.total_completion_tokens = 0
        child.last_prompt_tokens = 0
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
