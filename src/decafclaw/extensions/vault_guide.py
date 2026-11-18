"""Vault guide core extension (#1491).

Injects the vault's authored guide (default ``AGENTS.md``) as an always-loaded
``system`` section in the `HEAD` slot. Read fresh every turn from
``<vault_root>/<path>`` so the vault file stays the single source of truth.

Authoritative `system` role by design (#592): unlike retrieved vault content —
which is remapped to `user` for prompt-injection posture — this is the user's
own protocol document and the goal is reliable enforcement. HEAD contributions
are not archived; the same prefix replays each turn (#1117).

Fail-open: missing file, no vault, or a read error yields no contribution.
Skipped for heartbeat / scheduled / child-agent turns and when
``vault_guide.enabled`` is false.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from decafclaw.events import EventBus, TurnLifecycle
from decafclaw.prompts import wrap_xml
from decafclaw.util import estimate_tokens

from . import BaseExtension, ContextContribution, ContextSlot, register_extension_type

log = logging.getLogger(__name__)


class VaultGuideExtension(BaseExtension):
    """Inject the authored vault guide as a HEAD system message."""

    def __init__(self, options: dict[str, Any], config: Any):
        super().__init__(name="vault_guide")
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
            log.warning("Vault guide injection failed", exc_info=True)
            return None

    def _contribute(self, ctx: Any):
        from decafclaw.context_types import ComposerMode, SourceEntry

        config = getattr(ctx, "config", None) or self.config
        mode = getattr(getattr(ctx, "composer", None), "mode", ComposerMode.INTERACTIVE)
        if not config.vault_guide.enabled or mode is not ComposerMode.INTERACTIVE:
            return None

        rel_path = config.vault_guide.path
        if ".." in rel_path or rel_path.startswith("/") or Path(rel_path).is_absolute():
            log.debug("vault guide path %r is not vault-relative; skipping", rel_path)
            return None

        try:
            vault_root = config.vault_root.resolve()
            guide_path = (vault_root / rel_path).resolve()
            if not guide_path.is_relative_to(vault_root):
                log.debug("vault guide path %r escapes vault root; skipping", rel_path)
                return None
            if not guide_path.is_file():
                return None
            content = guide_path.read_text(encoding="utf-8").strip()
        except (OSError, ValueError) as exc:
            log.debug("vault guide read failed: %s", exc)
            return None

        if not content:
            return None

        max_tokens = config.vault_guide.max_tokens
        if max_tokens <= 0:
            return None

        truncated = 0
        if estimate_tokens(content) > max_tokens:
            marker = "\n\n[vault guide truncated]"
            char_budget = max(0, max_tokens * 4 - len(marker))
            full_len = len(content)
            content = content[:char_budget].rstrip() + marker
            truncated = 1
            log.warning(
                "vault guide %s exceeds max_tokens=%d; truncated from %d chars",
                config.vault_guide.path,
                max_tokens,
                full_len,
            )

        text = wrap_xml("vault_guide", content)
        return ContextContribution(
            slot=ContextSlot.HEAD,
            messages=[{"role": "system", "content": text}],
            source_entry=SourceEntry(
                source="vault_guide",
                tokens_estimated=estimate_tokens(text),
                items_included=1,
                items_truncated=truncated,
                details={"path": str(config.vault_guide.path)},
            ),
        )


register_extension_type("vault_guide", VaultGuideExtension, default_enabled=True)
