"""Runtime context — carries config, event bus, and request-scoped state."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
from typing import Any
from uuid import uuid4

from .context_composer import ComposerState


@dataclass
class TokenUsage:
    """Per-turn token counters."""
    total_prompt: int = 0
    total_completion: int = 0
    last_prompt: int = 0


@dataclass
class ToolState:
    """Tool-related state for the current conversation."""
    extra: dict[str, Any] = field(default_factory=dict)
    extra_definitions: list[dict] = field(default_factory=list)
    deferred_pool: list[dict] = field(default_factory=list)
    allowed: set[str] | None = None
    preapproved: set[str] = field(default_factory=set)
    preapproved_shell_patterns: list[str] = field(default_factory=list)
    # Scheduled-task overlay: addresses + `@domain.com` suffix patterns
    # that bypass confirmation for the `send_email` tool. Merged with
    # `config.email.allowed_recipients` at check time. Empty for
    # interactive runs.
    preapproved_email_recipients: list[str] = field(default_factory=list)
    current_call_id: str = ""
    # Dynamic tool providers: skill_name → get_tools(ctx) callable.
    # Called each turn to refresh that skill's tools and definitions.
    dynamic_providers: dict[str, Any] = field(default_factory=dict)
    # Tracks which tool names each dynamic provider contributed last turn,
    # so stale entries can be removed when the provider returns fewer tools.
    dynamic_provider_names: dict[str, set[str]] = field(default_factory=dict)
    # Tool names promoted for this turn by pre-emptive keyword matching
    # against the current user message + prior assistant response.
    # Populated once at the start of a turn by ContextComposer; reused
    # across iterations so classify_tools() stays consistent mid-turn.
    # See docs/preemptive-tool-search.md.
    preempt_matches: set[str] = field(default_factory=set)


@dataclass
class SkillState:
    """Skill activation state for the current conversation."""
    activated: set[str] = field(default_factory=set)
    data: dict[str, Any] = field(default_factory=dict)


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
        self.composer = ComposerState()

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
        self.skip_vault_retrieval: bool = False
        self.skip_archive: bool = False
        self.wiki_page: str | None = None  # open wiki page from web UI
        self.active_model: str = ""  # named model config from config.model_configs
        self.task_mode: str = ""  # "heartbeat" | "scheduled" | "" (interactive)
        self.request_confirmation: Any = None  # set by ConversationManager
        self.manager: Any = None  # set by ConversationManager

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
        active_model: str = "",
        task_mode: str = "",
        skip_reflection: bool = True,
        skip_vault_retrieval: bool = True,
        skip_archive: bool = False,
        allowed_tools: set | None = None,
        preapproved_tools: set | None = None,
        preapproved_shell_patterns: list[str] | None = None,
        preapproved_email_recipients: list[str] | None = None,
    ) -> "Context":
        """Create a context for a background task (heartbeat, scheduled task, etc.).

        Provides sensible defaults for non-interactive work: reflection and
        memory context are skipped by default.

        ``task_mode`` should be ``"heartbeat"`` or ``"scheduled"`` so that
        ``run_agent_turn`` can select the matching ``ComposerMode``.
        """
        ctx = cls(config=config, event_bus=event_bus)
        ctx.user_id = user_id
        ctx.conv_id = conv_id
        ctx.channel_id = channel_id
        ctx.channel_name = channel_name
        ctx.active_model = active_model
        ctx.task_mode = task_mode
        ctx.skip_reflection = skip_reflection
        ctx.skip_vault_retrieval = skip_vault_retrieval
        ctx.skip_archive = skip_archive
        if allowed_tools is not None:
            ctx.tools.allowed = allowed_tools
        if preapproved_tools is not None:
            ctx.tools.preapproved = preapproved_tools
        if preapproved_shell_patterns is not None:
            ctx.tools.preapproved_shell_patterns = preapproved_shell_patterns
        if preapproved_email_recipients is not None:
            ctx.tools.preapproved_email_recipients = preapproved_email_recipients
        return ctx

    def fork(self, **overrides) -> "Context":
        """Create a child context with a new ID, sharing the event bus.

        Each fork gets a fresh ComposerState by default, since forks may
        represent unrelated conversations (e.g. Mattermost request contexts
        forked from the long-lived app_ctx).  Callers that want to share
        composer state (same conversation, same turn) should pass
        ``composer=parent.composer`` explicitly.
        """
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
        child.skip_vault_retrieval = self.skip_vault_retrieval
        child.wiki_page = self.wiki_page
        child.active_model = self.active_model
        child.request_confirmation = self.request_confirmation
        child.manager = self.manager
        # Share skills + composer (concurrent tool calls read but don't mutate)
        child.skills = self.skills
        child.composer = self.composer
        # Fresh token counters — don't accumulate child usage into parent
        child.tokens = TokenUsage()
        # Fork tools with the specific tool call ID (the purpose of this fork)
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

    async def notify(self, **kwargs) -> None:
        """Convenience wrapper: append a notification carrying this ctx's correlation.

        Auto-populates ``conv_id`` from ``self.conv_id`` unless explicitly
        provided, and passes the event bus so channel adapters can fan out.
        See :func:`decafclaw.notifications.notify` for the full API.
        Producers without a ctx should call that function directly with
        an explicit ``event_bus`` argument.
        """
        from .notifications import notify
        kwargs.setdefault("conv_id", self.conv_id or None)
        await notify(self.config, self.event_bus, **kwargs)
