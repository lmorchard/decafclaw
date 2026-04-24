"""Notification channel adapters.

Each channel (Mattermost DM, email, etc.) is an ``EventBus`` subscriber
wired up at startup via :func:`init_notification_channels`. Adapters
filter the ``notification_created`` event stream against their own
config section, format the payload for their delivery medium, and hand
off to an ``asyncio.create_task`` so ``notify()`` doesn't block on
channel I/O.

See ``docs/notifications.md`` for the full design.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


def init_notification_channels(
    config,
    event_bus,
    *,
    mm_client: Any = None,
) -> None:
    """Subscribe every enabled notification channel adapter to the bus.

    Called once from ``runner.py`` at startup. Each channel guards its
    own prerequisites: if its config is incomplete or a required
    transport dep isn't running, the adapter simply isn't subscribed —
    no errors raised, no log spam beyond the debug path.

    This consolidates channel wiring so adding a new channel is a single
    file-scoped change (its module + its config dataclass + an extra
    ``if`` block here) instead of touching ``runner.py``. Closes #317.

    Args:
        config: Runtime Config with ``notifications.channels.*`` populated.
        event_bus: The shared EventBus that ``notify()`` publishes to.
        mm_client: Live ``MattermostClient`` for the DM channel.
            Pass ``None`` when Mattermost isn't configured.
    """
    # Mattermost DM — needs both the channel config and a live MM client.
    mm_dm_cfg = config.notifications.channels.mattermost_dm
    if mm_dm_cfg.enabled and mm_dm_cfg.recipient_username and mm_client:
        from .mattermost_dm import make_mattermost_dm_adapter
        event_bus.subscribe(make_mattermost_dm_adapter(config, mm_client))
        log.info(
            "Notifications: Mattermost DM adapter subscribed "
            "(recipient=%s, min_priority=%s)",
            mm_dm_cfg.recipient_username, mm_dm_cfg.min_priority,
        )

    # Email — needs both the channel config and the core email config
    # (including a non-empty sender_address; otherwise every send queues
    # a doomed task and logs a warning per notification).
    email_ch_cfg = config.notifications.channels.email
    if (email_ch_cfg.enabled
            and email_ch_cfg.recipient_addresses
            and config.email.enabled
            and config.email.smtp_host
            and (config.email.sender_address or "").strip()):
        from .email import make_email_adapter
        event_bus.subscribe(make_email_adapter(config))
        log.info(
            "Notifications: email adapter subscribed "
            "(recipients=%s, min_priority=%s)",
            email_ch_cfg.recipient_addresses, email_ch_cfg.min_priority,
        )

    # Vault page — pure local file writes; no transport dep to check.
    # `enabled` is the single user-visible switch; folder validation
    # happens at use time in `_daily_page_path`, which warns once per
    # bad folder (empty / absolute / `..` / outside-vault).
    vp_cfg = config.notifications.channels.vault_page
    if vp_cfg.enabled:
        from .vault_page import make_vault_page_adapter
        event_bus.subscribe(make_vault_page_adapter(config))
        log.info(
            "Notifications: vault page adapter subscribed "
            "(folder=%s, min_priority=%s)",
            vp_cfg.folder, vp_cfg.min_priority,
        )
