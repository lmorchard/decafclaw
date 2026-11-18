"""Scratchpad notes core extension (#1487).

Auto-injects the per-conversation scratchpad (#299) as a ``DELTA`` context
contribution. The extension is observed by the composer through the
``PRE_COMPOSE`` lifecycle hook, so notes reading, cursor progression, and
formatting no longer live in ``context_composer.py``.

Delta injection (#1111): only notes newer than the last surfaced note are
injected, tracked by ``ComposerState.injection_cursors``. The composer
snapshots that state before composing and restores it when composition fails,
so an aborted turn re-surfaces what was never delivered.

Yankable: disabling ``config.extensions.scratchpad_notes`` (or the legacy
``config.notes.enabled``) leaves the turn loop with no notes injections and no
runtime errors. Every failure path logs a warning and yields no contribution.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from typing import Any

from decafclaw.events import EventBus, TurnLifecycle
from decafclaw.notes import format_notes_for_context, read_notes

from . import BaseExtension, ContextContribution, ContextSlot, register_extension_type

log = logging.getLogger(__name__)


def _notes_enabled(config: Any) -> bool:
    notes_cfg = getattr(config, "notes", None)
    return bool(getattr(notes_cfg, "enabled", True))


def _is_interactive(ctx: Any) -> bool:
    """Only interactive turns surface scratchpad notes (#299)."""
    from decafclaw.context_composer import ComposerMode

    mode = getattr(getattr(ctx, "composer", None), "mode", None)
    if mode is None:
        # Direct hook calls (and older callers) default to the interactive path.
        return True
    return mode is ComposerMode.INTERACTIVE


class ScratchpadNotesExtension(BaseExtension):
    """Inject unseen conversation notes as a DELTA contribution."""

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
        _history: list | None = None,
    ) -> ContextContribution | None:
        """Return unseen notes for the DELTA slot; fail open."""
        try:
            return await self._contribution(ctx)
        except Exception as exc:  # fail-open collector
            log.warning("Scratchpad notes injection failed: %s", exc)
            return None

    async def _contribution(self, ctx: Any) -> ContextContribution | None:
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

        from decafclaw.context_composer import SourceEntry, select_unseen_items

        cursors = ctx.composer.injection_cursors
        new_items, notes_ts, notes_boundary = select_unseen_items(items, cursors.notes_ts, cursors.notes_boundary)
        if not new_items:
            return None
        ctx.composer.injection_cursors = replace(cursors, notes_ts=notes_ts, notes_boundary=notes_boundary)

        text = format_notes_for_context(new_items)
        if not text:
            return None
        from decafclaw.util import estimate_tokens

        return ContextContribution(
            slot=ContextSlot.DELTA,
            messages=[{"role": "conversation_notes", "content": text}],
            source_entry=SourceEntry(
                source="notes",
                tokens_estimated=estimate_tokens(text),
                items_included=len(new_items),
                items_truncated=0,
            ),
        )


register_extension_type(
    "scratchpad_notes",
    ScratchpadNotesExtension,
    default_enabled=True,
)
