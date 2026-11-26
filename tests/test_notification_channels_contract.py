"""Shared notification-adapter contract (#1913).

`meets_priority` is one shared function, so its threshold cases live here once.
The disabled-adapter cases keep each channel's own delivery assertion but share
the test name and fixture.
"""

import asyncio
from dataclasses import dataclass
from typing import Callable
from unittest.mock import AsyncMock, patch

import pytest

from decafclaw import notifications as notifs
from decafclaw.notification_channels import meets_priority
from decafclaw.notification_channels import ntfy as ntfy_mod
from decafclaw.notification_channels.mattermost_dm import make_mattermost_dm_adapter
from decafclaw.notification_channels.ntfy import make_ntfy_adapter
from decafclaw.notification_channels.vault_page import make_vault_page_adapter


def _rec(**overrides):
    return notifs.NotificationRecord(
        id=overrides.get("id", "abc"),
        timestamp=overrides.get("timestamp", "2026-04-23T12:00:00Z"),
        category=overrides.get("category", "heartbeat"),
        title=overrides.get("title", "Heartbeat"),
        priority=overrides.get("priority", "high"),
        body=overrides.get("body", ""),
        link=overrides.get("link"),
        conv_id=overrides.get("conv_id"),
    )


def _event():
    return {"type": "notification_created", "record": _rec().to_dict()}


class TestMeetsPriority:
    def test_exact_match(self):
        assert meets_priority("high", "high") is True

    def test_above_threshold(self):
        assert meets_priority("high", "normal") is True

    def test_below_threshold(self):
        assert meets_priority("low", "high") is False
        assert meets_priority("normal", "high") is False

    def test_unknown_priority_treated_as_normal(self):
        assert meets_priority("weird", "normal") is True
        assert meets_priority("weird", "high") is False

    def test_default_low_captures_everything(self):
        assert meets_priority("low", "low")
        assert meets_priority("normal", "low")
        assert meets_priority("high", "low")


async def _mattermost_disabled(config):
    cfg = config.notifications.channels.mattermost_dm
    cfg.enabled = False
    cfg.recipient_username = "les"
    cfg.min_priority = "high"
    client = AsyncMock()
    await make_mattermost_dm_adapter(config, client)(_event())
    await asyncio.sleep(0)
    client.post_direct_message.assert_not_called()


async def _ntfy_disabled(config):
    cfg = config.notifications.channels.ntfy
    cfg.enabled = False
    cfg.server_url = "https://ntfy.example"
    cfg.topic = "agent-alerts"
    cfg.min_priority = "high"
    with patch.object(ntfy_mod, "_deliver", new=AsyncMock()) as deliver:
        await make_ntfy_adapter(config)(_event())
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        deliver.assert_not_called()


async def _vault_page_disabled(config):
    cfg = config.notifications.channels.vault_page
    cfg.enabled = False
    cfg.folder = "agent/pages/notifications"
    cfg.min_priority = "low"
    await make_vault_page_adapter(config)(_event())
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert not (config.vault_root / "agent").exists()


@dataclass(frozen=True)
class SkipCase:
    name: str
    run: Callable


SKIP_CASES = [
    SkipCase("mattermost", _mattermost_disabled),
    SkipCase("ntfy", _ntfy_disabled),
    SkipCase("vault_page", _vault_page_disabled),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("case", SKIP_CASES, ids=[case.name for case in SKIP_CASES])
async def test_skips_when_disabled(case, config):
    await case.run(config)
