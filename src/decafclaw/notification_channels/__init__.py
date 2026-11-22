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

from decafclaw.notification_escalation import EscalationScheduler, register_scheduler

log = logging.getLogger(__name__)


# Priority comparison shared by every channel adapter. Each channel filters
# the bus on its own ``min_priority`` threshold, so the ordering and glyph
# tables live here to keep the channel modules in sync.
PRIORITY_ORDER = {"low": 0, "normal": 1, "high": 2}
PRIORITY_GLYPH = {"low": "·", "normal": "🔔", "high": "⚠️"}


def meets_priority(record_priority: str, min_priority: str) -> bool:
    """True when ``record_priority`` is at or above ``min_priority``."""
    return PRIORITY_ORDER.get(record_priority, 1) >= PRIORITY_ORDER.get(min_priority, 1)


# Priorities that must never wait for acknowledgment. `high` covers what the
# crash guard in scheduled runs relies on: steadily demoting an unattended
# failure report into a deferred push would stop those runs from alarming.
IMMEDIATE_PRIORITIES = frozenset({"high", "urgent"})

# Categories that are always immediate regardless of priority: boundary
# refusals and denials are not "may I?" questions or routine summaries.
IMMEDIATE_CATEGORIES = frozenset({"security", "denial", "egress", "outside_workspace"})


def should_defer(channel_cfg: Any, record: Any) -> bool:
    """True when ``channel_cfg`` should arm a deferred delivery for ``record``.

    Deferral applies only to a ``delivery: "deferred"`` channel, an attended
    record (a human may be watching), a deferrable priority, and a category
    outside the must-not-be-delayed set (#1664).
    """
    if getattr(channel_cfg, "delivery", "immediate") != "deferred":
        return False
    if getattr(record, "priority", "normal") in IMMEDIATE_PRIORITIES:
        return False
    if getattr(record, "category", "") in IMMEDIATE_CATEGORIES:
        return False
    return bool(getattr(record, "attended", False))


def init_notification_channels(
    config,
    event_bus,
    *,
    mm_client: Any = None,
) -> EscalationScheduler | None:
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
    # One scheduler fans deferred deliveries and confirmation escalations.
    # It is started after the adapters below register their deliverers, so
    # ignored entries are dropped instead of replayed.
    scheduler = EscalationScheduler(config, event_bus)
    subscribed_any = False

    # Mattermost DM — needs both the channel config and a live MM client.
    mm_dm_cfg = config.notifications.channels.mattermost_dm
    if mm_dm_cfg.enabled and mm_dm_cfg.recipient_username and mm_client:
        from .mattermost_dm import make_mattermost_dm_adapter

        event_bus.subscribe(make_mattermost_dm_adapter(config, mm_client, scheduler=scheduler))
        subscribed_any = True
        log.info(
            "Notifications: Mattermost DM adapter subscribed (recipient=%s, min_priority=%s)",
            mm_dm_cfg.recipient_username,
            mm_dm_cfg.min_priority,
        )

    # Email — needs both the channel config and the core email config
    # (including a non-empty sender_address; otherwise every send queues
    # a doomed task and logs a warning per notification).
    email_ch_cfg = config.notifications.channels.email
    if (
        email_ch_cfg.enabled
        and email_ch_cfg.recipient_addresses
        and config.email.enabled
        and config.email.smtp_host
        and (config.email.sender_address or "").strip()
    ):
        from .email import make_email_adapter

        event_bus.subscribe(make_email_adapter(config, scheduler=scheduler))
        subscribed_any = True
        log.info(
            "Notifications: email adapter subscribed (recipients=%s, min_priority=%s)",
            email_ch_cfg.recipient_addresses,
            email_ch_cfg.min_priority,
        )

    # NTFY push — guard on `enabled` + a usable publish endpoint
    # (`server_url` http(s) and a non-empty topic). The adapter also
    # pushes confirmation requests with Approve/Deny action buttons
    # when the HTTP callback server is enabled.
    ntfy_cfg = config.notifications.channels.ntfy
    if ntfy_cfg.enabled and ntfy_cfg.topic.strip() and ntfy_cfg.server_url.strip().startswith(("http://", "https://")):
        from .ntfy import make_ntfy_adapter

        event_bus.subscribe(make_ntfy_adapter(config, scheduler=scheduler))
        subscribed_any = True
        log.info(
            "Notifications: ntfy adapter subscribed (server=%s, topic=%s, min_priority=%s, "
            "confirmations=%s, delivery=%s, escalate_after_sec=%s, confirmation_escalate_after_sec=%s)",
            ntfy_cfg.server_url,
            ntfy_cfg.topic,
            ntfy_cfg.min_priority,
            ntfy_cfg.notify_on_confirmation,
            ntfy_cfg.delivery,
            ntfy_cfg.escalate_after_sec,
            ntfy_cfg.confirmation_escalate_after_sec,
        )

    # Vault page — pure local file writes; no transport dep to check.
    # `enabled` is the single user-visible switch; folder validation
    # happens at use time in `_daily_page_path`, which warns once per
    # bad folder (empty / absolute / `..` / outside-vault).
    vp_cfg = config.notifications.channels.vault_page
    if vp_cfg.enabled:
        from .vault_page import make_vault_page_adapter

        event_bus.subscribe(make_vault_page_adapter(config))
        subscribed_any = True
        log.info(
            "Notifications: vault page adapter subscribed (folder=%s, min_priority=%s)",
            vp_cfg.folder,
            vp_cfg.min_priority,
        )

    if not subscribed_any:
        return None
    register_scheduler(scheduler)
    scheduler.start()
    return scheduler
