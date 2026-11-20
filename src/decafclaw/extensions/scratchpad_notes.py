"""Scratchpad notes core extension (#1487).

Auto-injects the per-conversation scratchpad (#299) as a ``WORKING_MEMORY``
context contribution. The extension is observed by the composer through the
``PRE_COMPOSE`` lifecycle hook, so notes reading, cursor progression, and
formatting no longer live in ``context_composer.py``.

Under ephemeral working memory (#1574), notes are deduplicated against active
conversation history (prompt-residency deduplication) rather than advancing a
persistent cursor: notes already resident in history (e.g. legacy
conversation_notes rows) are suppressed, while notes evicted with prior
ephemeral blocks remain visible across turns.

Yankable: disabling ``config.extensions.scratchpad_notes`` (or the legacy
``config.notes.enabled``) leaves the turn loop with no notes injections and no
runtime errors. Every failure path logs a warning and yields no contribution.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from decafclaw.events import EventBus, TurnLifecycle
from decafclaw.notes import format_notes_for_context, read_notes

from . import BaseExtension, ContextContribution, ContextSlot, provenance_excerpt, register_extension_type

log = logging.getLogger(__name__)


def _is_note_in_history(note: Any, history: list | None) -> bool:
    """Check if a note is already resident in conversation history (#1574)."""
    if not history:
        return False
    for msg in history:
        if msg.get("role") == "conversation_notes":
            content = str(msg.get("content") or "")
            if note.text in content:
                return True
    return False


def _notes_enabled(config: Any) -> bool:
    notes_cfg = getattr(config, "notes", None)
    return bool(getattr(notes_cfg, "enabled", True))


def _is_interactive(ctx: Any) -> bool:
    """Only interactive turns surface scratchpad notes (#299)."""
    mode = getattr(getattr(ctx, "composer", None), "mode", None)
    if mode is None:
        # Direct hook calls (and older callers) default to the interactive path.
        return True
    return not mode.is_skip


class ScratchpadNotesExtension(BaseExtension):
    """Inject unseen conversation notes as a WORKING_MEMORY contribution."""

    def __init__(self, options: dict[str, Any], config: Any):
        super().__init__(name="scratchpad_notes")
        self.options = options or {}
        self.config = config

    def enable(self, bus: EventBus) -> list[str]:
        self.disable(bus)
        self.subscription_ids = [bus.subscribe(TurnLifecycle.PRE_COMPOSE, self.pre_compose_hook)]
        return self.subscription_ids

    async def pre_compose_hook(
        self,
        ctx: Any,
        _user_message: str = "",
        history: list | None = None,
    ) -> ContextContribution | None:
        """Return unseen notes for the working-memory bundle; fail open."""
        try:
            return await self._contribution(ctx, history)
        except Exception as exc:  # fail-open collector
            log.warning("Scratchpad notes injection failed: %s", exc)
            return None

    async def _contribution(self, ctx: Any, history: list | None = None) -> ContextContribution | None:
        config = getattr(ctx, "config", None) or self.config
        if not _notes_enabled(config):
            return None
        if not _is_interactive(ctx):
            return None

        notes_cfg = getattr(config, "notes", None)
        limit = self.options.get("context_max_entries", getattr(notes_cfg, "context_max_entries", None))
        max_chars = self.options.get("context_max_chars", getattr(notes_cfg, "context_max_chars", None))
        conv_id = getattr(ctx, "conv_id", "") or getattr(ctx, "channel_id", "") or "default"
        items = await asyncio.to_thread(read_notes, config, conv_id, limit=limit, max_chars=max_chars)
        if not items:
            return None

        from decafclaw.context_types import SourceEntry

        # Prompt-residency deduplication (#1574): only deduplicate notes that
        # are actually resident in active conversation history (such as legacy
        # archived conversation_notes rows). Ephemeral working-memory blocks evicted on
        # prior turns do not suppress subsequent turns.
        if history:
            items = [item for item in items if not _is_note_in_history(item, history)]
            if not items:
                return None

        text = format_notes_for_context(items)
        if not text:
            return None
        from decafclaw.util import estimate_tokens

        return ContextContribution(
            slot=ContextSlot.WORKING_MEMORY,
            messages=[{"role": "conversation_notes", "content": text}],
            source_entry=SourceEntry(
                source="notes",
                tokens_estimated=estimate_tokens(text),
                items_included=len(items),
                items_truncated=0,
            ),
            provenance={
                "scratchpad": [{"timestamp": note.timestamp, "text": provenance_excerpt(note.text)} for note in items]
            },
        )


register_extension_type(
    "scratchpad_notes",
    ScratchpadNotesExtension,
    default_enabled=True,
)
