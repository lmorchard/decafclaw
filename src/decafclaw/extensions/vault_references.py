"""Explicit wiki-reference core extension (#301, #1491).

`@[[Page]]` references and the currently open wiki page are fixed-cost
injections: they are always included, archived with the turn, and deduplicated
against history. They mount in the `TAIL` slot directly before the user
message.

Skipped for heartbeat, scheduled, and child-agent turns. Fail-open: a read or
publish failure logs and yields no contribution.
"""

from __future__ import annotations

import logging
from typing import Any

from decafclaw.events import EventBus, TurnLifecycle
from decafclaw.memory_context import get_already_injected_pages, parse_wiki_references, read_wiki_page
from decafclaw.util import estimate_tokens

from . import BaseExtension, ContextContribution, ContextSlot, register_extension_type

log = logging.getLogger(__name__)


class VaultReferencesExtension(BaseExtension):
    """Inject explicit wiki references as a TAIL contribution."""

    def __init__(self, options: dict[str, Any], config: Any):
        super().__init__(name="vault_references")
        self.options = options or {}
        self.config = config

    def enable(self, bus: EventBus) -> list[str]:
        self.disable(bus)
        self.subscription_ids = [bus.subscribe(TurnLifecycle.PRE_COMPOSE, self.pre_compose_hook)]
        return self.subscription_ids

    async def pre_compose_hook(self, ctx: Any, user_message: str = "", history: list | None = None):
        try:
            return await self._contribute(ctx, user_message, history or [])
        except Exception:
            log.warning("Vault reference injection failed", exc_info=True)
            return None

    async def _contribute(self, ctx: Any, user_message: str, history: list):
        from decafclaw.context_types import ComposerMode, SourceEntry

        config = getattr(ctx, "config", None) or self.config
        mode = getattr(getattr(ctx, "composer", None), "mode", ComposerMode.INTERACTIVE)
        if mode.is_skip:
            return None

        vault_dir = config.vault_root
        if not vault_dir.exists():
            return None

        wiki_refs = parse_wiki_references(user_message, ctx.wiki_page)
        if not wiki_refs:
            return None

        already_injected = get_already_injected_pages(history)
        messages = []
        skipped = 0
        for ref in wiki_refs:
            if ref["page"] in already_injected:
                skipped += 1
                continue
            content = read_wiki_page(config, ref["page"])
            if content is None:
                text = f"[Wiki page '{ref['page']}' not found]"
            elif ref["source"] == "open_page":
                text = f"[Currently viewing wiki page: {ref['page']}]\n\n{content}"
            else:
                text = f"[Referenced wiki page: {ref['page']}]\n\n{content}"
            messages.append({"role": "vault_references", "content": text, "wiki_page": ref["page"]})

        if not messages and skipped == 0:
            return None

        for message in messages:
            await ctx.publish("vault_references", text=message["content"], page=message.get("wiki_page"))

        tokens = sum(estimate_tokens(m["content"]) for m in messages)
        return ContextContribution(
            slot=ContextSlot.TAIL,
            messages=messages,
            source_entry=SourceEntry(
                source="wiki",
                tokens_estimated=tokens,
                items_included=len(messages),
                items_truncated=skipped,
            ),
        )


register_extension_type("vault_references", VaultReferencesExtension, default_enabled=True)
