"""Recent journal core extension (#306, #1491).

Auto-surfaces recently written journal entries as a `WORKING_MEMORY`
contribution at interactive turn start, on a small fixed budget separate from
the dynamic semantic-retrieval pool so it doesn't compete with it. Recency is a
strong signal as a retrieval *mode* ("what was I just thinking about?") where
it is weak folded into a composite score.

Under ephemeral working memory (#1574), entries are deduplicated against active
conversation history (prompt-residency deduplication) rather than advancing a
persistent cursor: entries already resident in history (e.g. legacy
recent_journal rows) are suppressed, while entries evicted with prior ephemeral
blocks remain visible across turns. Emits a ``recent_journal`` role message.

Skipped for heartbeat / scheduled / child-agent turns and when disabled.
Fail-open: read or formatting failures log and yield no contribution.
"""

from __future__ import annotations

import logging
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


def _is_entry_in_history(entry: Any, history: list | None) -> bool:
    """Check if a journal entry is already resident in conversation history (#1574)."""
    if not history:
        return False
    entry_header = f"## {entry.timestamp.strftime('%Y-%m-%d %H:%M')}"
    for msg in history:
        if msg.get("role") == "recent_journal":
            content = str(msg.get("content") or "")
            if entry_header in content or entry.text in content:
                return True
    return False


class RecentJournalExtension(BaseExtension):
    """Inject unseen recent journal entries as a WORKING_MEMORY contribution."""

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
            return self._contribute(ctx, history)
        except Exception:
            log.warning("Recent journal injection failed", exc_info=True)
            return None

    def _contribute(self, ctx: Any, history: list | None = None):
        from decafclaw.context_types import ComposerMode, SourceEntry

        config = getattr(ctx, "config", None) or self.config
        mode = getattr(getattr(ctx, "composer", None), "mode", ComposerMode.INTERACTIVE)
        cfg = config.recent_journal
        if not cfg.enabled or mode.is_skip:
            return None

        entries = read_recent_journal_entries(
            config,
            now=_now(),
            max_hours=cfg.max_hours,
            max_entries=cfg.max_entries,
        )
        if not entries:
            return None

        # Prompt-residency deduplication (#1574): only deduplicate entries that
        # are actually resident in active conversation history (such as legacy
        # archived recent_journal rows). Ephemeral working-memory blocks evicted on
        # prior turns do not suppress subsequent turns.
        if history:
            entries = [e for e in entries if not _is_entry_in_history(e, history)]
            if not entries:
                return None

        truncated = 0
        text = format_recent_journal_for_context(entries, cfg.max_hours)
        while len(entries) > 1 and estimate_tokens(text) > cfg.max_tokens:
            entries = entries[1:]
            truncated += 1
            text = format_recent_journal_for_context(entries, cfg.max_hours)

        return ContextContribution(
            slot=ContextSlot.WORKING_MEMORY,
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
