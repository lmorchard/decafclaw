"""Recent journal core extension (#306, #1491).

Auto-surfaces recently written journal entries as a `DELTA` contribution at
interactive turn start, on a small fixed budget separate from the dynamic
semantic-retrieval pool so it doesn't compete with it. Recency is a strong
signal as a retrieval *mode* ("what was I just thinking about?") where it is
weak folded into a composite score.

Only entries newer than the newest one already surfaced this conversation
(``ComposerState.injection_cursors``) are injected, so each entry appears at
most once. Emits a ``recent_journal`` role message remapped to ``user``.

Skipped for heartbeat / scheduled / child-agent turns and when disabled.
Fail-open: read or formatting failures log and yield no contribution.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import datetime
from typing import Any

from decafclaw.events import EventBus, TurnLifecycle
from decafclaw.skills.vault.tools import format_recent_journal_for_context, read_recent_journal_entries
from decafclaw.util import estimate_tokens

from . import BaseExtension, ContextContribution, ContextSlot, provenance_excerpt, register_extension_type

log = logging.getLogger(__name__)


def _now() -> datetime:
    """Current naive local time — a seam so tests can patch the clock."""
    return datetime.now()


class RecentJournalExtension(BaseExtension):
    """Inject unseen recent journal entries as a DELTA contribution."""

    def __init__(self, options: dict[str, Any], config: Any):
        super().__init__(name="recent_journal")
        self.options = options or {}
        self.config = config

    def enable(self, bus: EventBus) -> list[str]:
        self.disable(bus)
        self.subscription_ids = [bus.subscribe(TurnLifecycle.PRE_COMPOSE, self.pre_compose_hook)]
        return self.subscription_ids

    async def pre_compose_hook(self, ctx: Any, user_message: str = "", history: list | None = None):
        try:
            return self._contribute(ctx)
        except Exception:
            log.warning("Recent journal injection failed", exc_info=True)
            return None

    def _contribute(self, ctx: Any):
        from decafclaw.context_types import ComposerMode, SourceEntry, select_unseen_items

        config = getattr(ctx, "config", None) or self.config
        mode = getattr(getattr(ctx, "composer", None), "mode", ComposerMode.INTERACTIVE)
        cfg = config.recent_journal
        if not cfg.enabled or mode is not ComposerMode.INTERACTIVE:
            return None

        entries = read_recent_journal_entries(
            config,
            now=_now(),
            max_hours=cfg.max_hours,
            max_entries=cfg.max_entries,
        )
        if not entries:
            return None

        cursors = ctx.composer.injection_cursors
        entries, journal_ts, journal_boundary = select_unseen_items(
            entries, cursors.journal_ts, cursors.journal_boundary
        )
        if not entries:
            return None
        ctx.composer.injection_cursors = replace(cursors, journal_ts=journal_ts, journal_boundary=journal_boundary)

        truncated = 0
        text = format_recent_journal_for_context(entries, cfg.max_hours)
        while len(entries) > 1 and estimate_tokens(text) > cfg.max_tokens:
            entries = entries[1:]
            truncated += 1
            text = format_recent_journal_for_context(entries, cfg.max_hours)

        return ContextContribution(
            slot=ContextSlot.DELTA,
            messages=[{"role": "recent_journal", "content": text}],
            source_entry=SourceEntry(
                source="recent_journal",
                tokens_estimated=estimate_tokens(text),
                items_included=len(entries),
                items_truncated=truncated,
            ),
            provenance={
                "journals": [
                    {
                        "timestamp": entry.timestamp.strftime("%Y-%m-%d %H:%M"),
                        "text": provenance_excerpt(entry.text),
                    }
                    for entry in entries
                ]
            },
        )


register_extension_type("recent_journal", RecentJournalExtension, default_enabled=True)
