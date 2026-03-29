"""Runtime context — carries config, event bus, and request-scoped state."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
from typing import Any
from uuid import uuid4


@dataclass
class TokenUsage:
    """Per-turn token counters."""
    total_prompt: int = 0
    total_completion: int = 0
    last_prompt: int = 0


@dataclass
class ToolState:
    """Tool-related state for the current conversation."""
    extra: dict = field(default_factory=dict)
    extra_definitions: list = field(default_factory=list)
    deferred_pool: list = field(default_factory=list)
    allowed: set | None = None
    preapproved: set = field(default_factory=set)
    preapproved_shell_patterns: list[str] = field(default_factory=list)
    current_call_id: str = ""


@dataclass
class SkillState:
    """Skill activation state for the current conversation."""
    activated: set = field(default_factory=set)
    data: dict = field(default_factory=dict)


class Context:
    """Forkable runtime context inspired by Go's context pattern."""

    def __init__(self, config, event_bus, context_id=None):
        self.config = config
        self.event_bus = event_bus
        self.context_id = context_id or uuid4().hex[:12]

        # Per-conversation identity (kept flat — most accessed fields)
        self.user_id: str = ""
        self.channel_id: str = ""
        self.channel_name: str = ""
        self.thread_id: str = ""
        self.conv_id: str = ""

        # Grouped state
        self.tokens = TokenUsage()
        self.tools = ToolState()
        self.skills = SkillState()

        # Per-conversation state (set via fork() overrides)
        self.history: list | None = None
        self.messages: list | None = None
        self.cancelled: asyncio.Event | None = None
        self.media_handler: Any = None
        self.on_stream_chunk: Any = None
        self.event_context_id: str = ""  # publish events under this ID instead of context_id
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
            ctx.tools.allowed = allowed_tools
        if preapproved_tools is not None:
            ctx.tools.preapproved = preapproved_tools
        if preapproved_shell_patterns is not None:
            ctx.tools.preapproved_shell_patterns = preapproved_shell_patterns
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

        Shallow-copies all flat fields from the parent. Sub-objects:
        - tokens: fresh instance (per-call counters, not accumulated)
        - tools: new ToolState instance via dataclasses.replace, sharing
          inner containers but with current_call_id overridden
        - skills: shared reference (concurrent reads, no mutation)
        """
        child = Context(
            config=self.config,
            event_bus=self.event_bus,
            context_id=self.context_id,
        )
        # Copy flat fields from parent
        child.user_id = self.user_id
        child.channel_id = self.channel_id
        child.channel_name = self.channel_name
        child.thread_id = self.thread_id
        child.conv_id = self.conv_id
        child.history = self.history
        child.messages = self.messages
        child.cancelled = self.cancelled
        child.media_handler = self.media_handler
        child.on_stream_chunk = self.on_stream_chunk
        child.event_context_id = self.event_context_id
        child._current_iteration = self._current_iteration
        child.is_child = self.is_child
        child.skip_reflection = self.skip_reflection
        child.skip_memory_context = self.skip_memory_context
        child.effort = self.effort
        # Share tools + skills (concurrent tool calls read but don't mutate)
        child.tools = self.tools
        child.skills = self.skills
        # Fresh token counters — don't accumulate child usage into parent
        child.tokens = TokenUsage()
        # Override the tool call ID (the purpose of this fork)
        child.tools = replace(self.tools, current_call_id=tool_call_id)
        return child

    async def publish(self, event_type: str, **kwargs) -> None:
        """Convenience: publish an event with context_id auto-included.

        Automatically includes tool_call_id from tools.current_call_id
        when set, unless explicitly provided in kwargs.
        """
        ctx_id = self.event_context_id or self.context_id
        event = {"type": event_type, "context_id": ctx_id, **kwargs}
        if self.tools.current_call_id and "tool_call_id" not in kwargs:
            event["tool_call_id"] = self.tools.current_call_id
        await self.event_bus.publish(event)
