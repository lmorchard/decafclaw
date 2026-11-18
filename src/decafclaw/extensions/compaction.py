"""Compaction core extension (#1490).

Owns the compaction *policy* for the turn loop: whether the context budget is
over threshold and, if so, running ``compaction.compact_history`` (which
dispatches ``PRE_COMPACTION`` to sibling extensions, summarizes, and rewrites
the transcript and file ledger).

Core still drives *when* the check runs — after each LLM call with actual
prompt usage, and after a provider context-length error — because compaction
rewrites live history and performs an LLM call of its own. That work cannot
live inside a bounded ``PRE_COMPOSE`` collector, so the turn loop looks up the
enabled instance through ``ctx.extension_manager`` and calls
``maybe_compact`` / ``compact`` directly.

Disabled (``extensions.compaction.enabled: false``), core stops performing
proactive compaction and reactive overflow recovery; the bounded LLM recovery
policy still handles the resulting provider error. Options override the
legacy``config.compaction`` policy fields this extension owns:

- ``max_tokens`` — fixed threshold fallback.
- ``threshold_ratio`` — fraction of the resolved model context window.

Summarization settings (``model``, ``preserve_turns``, decisions slice, memory
sweep) stay on ``config.compaction`` because ``compact_history`` reads them.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any

from decafclaw import compaction
from decafclaw.events import EventBus

from . import BaseExtension, register_extension_type

log = logging.getLogger(__name__)


class CompactionExtension(BaseExtension):
    """Compaction policy service consulted by the turn loop."""

    def __init__(self, options: dict[str, Any], config: Any):
        super().__init__(name="compaction")
        self.options = options or {}
        self.config = config
        # Parse policy knobs once; a malformed option is reported and ignored
        # rather than turning every turn's budget check into an exception.
        self._max_tokens = self._parse_option("max_tokens", int)
        self._threshold_ratio = self._parse_option("threshold_ratio", float)

    def _parse_option(self, name: str, coerce: Any) -> Any:
        if name not in self.options:
            return None
        try:
            return coerce(self.options[name])
        except (TypeError, ValueError):
            log.warning("Invalid compaction option %s=%r; ignoring", name, self.options[name])
            return None

    def enable(self, bus: EventBus) -> list[str]:
        """No bus hooks: core invokes this service directly (see module doc)."""
        self.disable(bus)
        return self.subscription_ids

    def _policy_config(self, config: Any) -> Any:
        """Apply extension options over the legacy ``compaction`` section."""
        overrides: dict[str, Any] = {}
        if self._max_tokens is not None:
            overrides["max_tokens"] = self._max_tokens
        if self._threshold_ratio is not None:
            overrides["threshold_ratio"] = self._threshold_ratio
        if not overrides:
            return config
        return replace(config, compaction=replace(config.compaction, **overrides))

    def threshold(self, ctx: Any, config: Any, *, lowered: int | None = None) -> int:
        """Resolve the effective auto-compaction token threshold."""
        from decafclaw.context_composer import effective_compaction_threshold

        return effective_compaction_threshold(self._policy_config(config), ctx, lowered=lowered)

    async def compact(self, ctx: Any, history: list) -> bool:
        """Run compaction; fail-open so a summarizer error cannot crash a turn."""
        try:
            return await compaction.compact_history(ctx, history)
        except Exception:
            log.error("Compaction failed", exc_info=True)
            return False

    async def maybe_compact(
        self,
        ctx: Any,
        history: list,
        prompt_tokens: int,
        *,
        lowered: int | None = None,
    ) -> bool:
        """Compact when actual prompt usage exceeds the effective threshold."""
        config = getattr(ctx, "config", None) or self.config
        threshold = self.threshold(ctx, config, lowered=lowered)
        log.info("Compaction check: prompt_tokens=%s, threshold=%s", prompt_tokens, threshold)
        if prompt_tokens and prompt_tokens > threshold:
            log.info("Token budget exceeded (%s > %s), triggering compaction", prompt_tokens, threshold)
            return await self.compact(ctx, history)
        return False

    @staticmethod
    def overflow_threshold(prompt_tokens: int, estimated_tokens: int) -> int:
        """Turn-local threshold after a provider context-length error."""
        return max(1000, int((prompt_tokens or estimated_tokens or 10000) * 0.8))


def find_compaction_extension(ctx: Any) -> CompactionExtension | None:
    """Return the enabled CompactionExtension for this context, if any."""
    manager = getattr(ctx, "extension_manager", None)
    if manager is None:
        return None
    try:
        for name in manager.list_extensions():
            if not manager.is_enabled(name):
                continue
            extension = manager.get(name)
            if isinstance(extension, CompactionExtension):
                return extension
    except Exception:  # fail-open lookup
        log.warning("Compaction extension lookup failed", exc_info=True)
    return None


register_extension_type("compaction", CompactionExtension, default_enabled=True)
