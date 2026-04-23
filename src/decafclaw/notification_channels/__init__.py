"""Notification channel adapters.

Each channel (Mattermost DM, email, etc.) is an ``EventBus`` subscriber
wired up at startup from ``runner.py``. Adapters filter the
``notification_created`` event stream against their own config section,
format the payload for their delivery medium, and hand off to an
``asyncio.create_task`` so ``notify()`` doesn't block on channel I/O.

See ``docs/notifications.md`` for the full design.
"""
