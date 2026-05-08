"""Tests for the vault EventBus publisher helper."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock

import pytest

from decafclaw.skills.vault._events import (
    KIND_CREATE,
    publish_vault_changed,
)


class TestPublishVaultChanged:
    @pytest.fixture
    def vault_dir(self, config):
        d = config.vault_root
        d.mkdir(parents=True, exist_ok=True)
        return d

    @pytest.mark.asyncio
    async def test_publishes_dict_event_with_relative_path(self, config, vault_dir):
        bus = AsyncMock()
        await publish_vault_changed(
            bus, config, kind=KIND_CREATE,
            path=vault_dir / "creative" / "foo.md",
        )
        bus.publish.assert_called_once()
        ((event,), _) = bus.publish.call_args
        assert event == {
            "type": "vault_changed",
            "kind": "create",
            "path": "creative/foo.md",
        }

    @pytest.mark.asyncio
    async def test_normalizes_relative_path_unchanged(self, config, vault_dir):
        bus = AsyncMock()
        await publish_vault_changed(
            bus, config, kind=KIND_CREATE, path="creative/foo.md",
        )
        ((event,), _) = bus.publish.call_args
        assert event["path"] == "creative/foo.md"

    @pytest.mark.asyncio
    async def test_none_path_becomes_empty_string(self, config, vault_dir):
        bus = AsyncMock()
        await publish_vault_changed(bus, config, kind=KIND_CREATE, path=None)
        ((event,), _) = bus.publish.call_args
        assert event["path"] == ""

    @pytest.mark.asyncio
    async def test_path_outside_vault_root_falls_back_to_empty(
        self, config, vault_dir, tmp_path,
    ):
        bus = AsyncMock()
        outside = tmp_path / "outside.md"
        outside.write_text("x")  # ensure resolve() works
        await publish_vault_changed(bus, config, kind=KIND_CREATE, path=outside)
        ((event,), _) = bus.publish.call_args
        # path can't be made relative to vault_root, so it falls back to ""
        assert event["path"] == ""

    @pytest.mark.asyncio
    async def test_publish_failure_is_swallowed(self, config, vault_dir, caplog):
        bus = AsyncMock()
        bus.publish.side_effect = RuntimeError("bus down")
        with caplog.at_level(
            logging.DEBUG, logger="decafclaw.skills.vault._events"
        ):
            await publish_vault_changed(
                bus, config, kind=KIND_CREATE, path="foo.md",
            )
        # Did not raise. Debug log captured.
        assert any("publish failed" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_event_bus_none_no_op(self, config):
        # Just must not raise. No way to assert "no call" without a bus to
        # inspect.
        await publish_vault_changed(
            None, config, kind=KIND_CREATE, path="foo.md",
        )
