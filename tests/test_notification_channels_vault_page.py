"""Tests for the vault page notification channel adapter."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from decafclaw import notifications as notifs
from decafclaw.events import EventBus
from decafclaw.notification_channels import vault_page as vp_mod
from decafclaw.notification_channels.vault_page import (
    _daily_page_path,
    _format_entry,
    _format_new_page_header,
    _meets_priority,
    _resolve_link,
    make_vault_page_adapter,
)

# -- Fixtures + helpers ------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_module_state():
    """Each test gets a fresh lock dict + fresh warn-once set."""
    vp_mod._locks.clear()
    vp_mod._warned_bad_folders.clear()
    yield
    vp_mod._locks.clear()
    vp_mod._warned_bad_folders.clear()


def _rec(**overrides) -> notifs.NotificationRecord:
    return notifs.NotificationRecord(
        id=overrides.get("id", "abc"),
        timestamp=overrides.get("timestamp", "2026-04-23T14:32:00Z"),
        category=overrides.get("category", "heartbeat"),
        title=overrides.get("title", "Heartbeat"),
        priority=overrides.get("priority", "normal"),
        body=overrides.get("body", ""),
        link=overrides.get("link"),
        conv_id=overrides.get("conv_id"),
    )


def _enable_channel(config, *, folder="agent/pages/notifications",
                    min_priority="low"):
    config.notifications.channels.vault_page.enabled = True
    config.notifications.channels.vault_page.folder = folder
    config.notifications.channels.vault_page.min_priority = min_priority
    # Vault root must exist for the adapter to write there.
    config.vault_root.mkdir(parents=True, exist_ok=True)
    return config


async def _flush_create_tasks():
    """The handler creates detached delivery tasks; give them a tick."""
    await asyncio.sleep(0)
    await asyncio.sleep(0)


# -- Priority filter ----------------------------------------------------------


class TestMeetsPriority:
    def test_exact_match(self):
        assert _meets_priority("high", "high")

    def test_above_threshold(self):
        assert _meets_priority("high", "normal")

    def test_below_threshold(self):
        assert not _meets_priority("low", "normal")

    def test_default_low_captures_everything(self):
        assert _meets_priority("low", "low")
        assert _meets_priority("normal", "low")
        assert _meets_priority("high", "low")


# -- Link resolution ---------------------------------------------------------


class TestResolveLink:
    def test_explicit_http_wins(self):
        rec = _rec(link="https://example.com/x", conv_id="c-1")
        assert _resolve_link(rec, "http://agent.local") \
            == "https://example.com/x"

    def test_conv_id_builds_base_url_link(self):
        rec = _rec(conv_id="c-1")
        assert _resolve_link(rec, "http://agent.local") \
            == "http://agent.local/#conv=c-1"

    def test_trailing_slash_stripped(self):
        assert _resolve_link(_rec(conv_id="c-1"), "http://x/") \
            == "http://x/#conv=c-1"

    def test_none_without_base_url(self):
        assert _resolve_link(_rec(conv_id="c-1"), "") is None

    def test_none_without_conv_or_link(self):
        assert _resolve_link(_rec(), "http://x") is None

    def test_conv_scheme_link_falls_back_to_base_url(self):
        """`conv://...` is web-UI internal; can't follow verbatim in an
        external audit-trail. Falls through to the base_url construction."""
        rec = _rec(link="conv://c-1", conv_id="c-1")
        assert _resolve_link(rec, "http://x") == "http://x/#conv=c-1"


# -- New-page header + entry format ------------------------------------------


class TestFormatNewPageHeader:
    def test_frontmatter_present(self):
        header = _format_new_page_header("2026-04-23")
        assert header.startswith("---\n")
        assert 'title: "Notifications 2026-04-23"' in header
        assert "tags: [notifications, system]" in header
        assert "# Notifications — 2026-04-23" in header


class TestFormatEntry:
    def test_heading_shape(self):
        entry = _format_entry(
            _rec(priority="high", title="Alert"), "",
        )
        assert entry.startswith(
            "## 14:32 UTC · ⚠️ [heartbeat] Alert\n"
        )

    def test_metadata_block(self):
        entry = _format_entry(
            _rec(priority="normal", conv_id="c-1", body="something"), "",
        )
        assert "- priority: normal\n" in entry
        assert "- conv_id: c-1\n" in entry
        assert "something" in entry

    def test_em_dashes_for_empty_fields(self):
        entry = _format_entry(_rec(body=""), "")
        assert "- conv_id: —\n" in entry
        assert "- link: —\n" in entry
        # Body rendered as em-dash too
        assert "\n—\n" in entry

    def test_link_rendered_when_resolvable(self):
        entry = _format_entry(
            _rec(conv_id="c-1"), "http://agent.local",
        )
        assert "- link: http://agent.local/#conv=c-1\n" in entry

    def test_priority_glyphs(self):
        assert _format_entry(_rec(priority="low"), "").startswith("## ")
        assert "· · " in _format_entry(_rec(priority="low"), "")
        assert "· 🔔 " in _format_entry(_rec(priority="normal"), "")
        assert "· ⚠️ " in _format_entry(_rec(priority="high"), "")

    def test_malformed_timestamp_falls_back(self):
        entry = _format_entry(_rec(timestamp="not-a-date"), "")
        assert "## ??:?? UTC" in entry


# -- Path sandboxing ---------------------------------------------------------


class TestDailyPagePath:
    def test_default_folder_resolves_under_vault(self, config):
        _enable_channel(config)
        path = _daily_page_path(config, "2026-04-23T14:32:00Z")
        assert path is not None
        assert path.name == "2026-04-23.md"
        assert path.parent == (
            config.vault_root / "agent" / "pages" / "notifications"
        ).resolve()

    def test_empty_folder_returns_none(self, config):
        _enable_channel(config, folder="")
        assert _daily_page_path(config, "2026-04-23T14:32:00Z") is None

    def test_absolute_folder_rejected(self, config, caplog):
        _enable_channel(config, folder="/etc/notifications")
        caplog.set_level("WARNING")
        assert _daily_page_path(config, "2026-04-23T14:32:00Z") is None
        assert any("rejected" in r.message.lower() for r in caplog.records)

    def test_dotdot_escape_rejected(self, config):
        _enable_channel(config, folder="../../../etc")
        assert _daily_page_path(config, "2026-04-23T14:32:00Z") is None

    def test_bad_folder_warning_fires_once(self, config, caplog):
        _enable_channel(config, folder="../escape")
        caplog.set_level("WARNING")
        _daily_page_path(config, "2026-04-23T14:32:00Z")
        _daily_page_path(config, "2026-04-23T14:33:00Z")
        _daily_page_path(config, "2026-04-23T14:34:00Z")
        # Single warning — throttled after first bad folder seen.
        rejection_warnings = [
            r for r in caplog.records if "rejected" in r.message.lower()
        ]
        assert len(rejection_warnings) == 1

    def test_malformed_timestamp_returns_none(self, config):
        _enable_channel(config)
        assert _daily_page_path(config, "not-iso") is None


# -- Handler integration ----------------------------------------------------


class TestAdapterHandler:
    @pytest.mark.asyncio
    async def test_happy_path_creates_page(self, config):
        _enable_channel(config)
        handler = make_vault_page_adapter(config)

        await handler({
            "type": "notification_created",
            "record": _rec(priority="high", title="Boot").to_dict(),
        })
        await _flush_create_tasks()

        path = (config.vault_root / "agent" / "pages" /
                "notifications" / "2026-04-23.md")
        assert path.exists()
        content = path.read_text()
        # Header frontmatter + H1
        assert 'title: "Notifications 2026-04-23"' in content
        # Entry
        assert "## 14:32 UTC · ⚠️ [heartbeat] Boot" in content

    @pytest.mark.asyncio
    async def test_appends_to_existing_page(self, config):
        _enable_channel(config)
        handler = make_vault_page_adapter(config)

        await handler({
            "type": "notification_created",
            "record": _rec(
                timestamp="2026-04-23T14:32:00Z", title="First",
            ).to_dict(),
        })
        await _flush_create_tasks()
        await handler({
            "type": "notification_created",
            "record": _rec(
                timestamp="2026-04-23T15:00:00Z", title="Second",
            ).to_dict(),
        })
        await _flush_create_tasks()

        content = (config.vault_root / "agent" / "pages" /
                   "notifications" / "2026-04-23.md").read_text()
        assert "First" in content and "Second" in content
        # Header written once
        assert content.count("# Notifications — 2026-04-23") == 1

    @pytest.mark.asyncio
    async def test_ignores_non_notification_events(self, config):
        _enable_channel(config)
        handler = make_vault_page_adapter(config)
        await handler({"type": "tool_start"})
        await _flush_create_tasks()
        assert not (config.vault_root / "agent").exists()

    @pytest.mark.asyncio
    async def test_skips_when_disabled(self, config):
        _enable_channel(config)
        config.notifications.channels.vault_page.enabled = False
        handler = make_vault_page_adapter(config)
        await handler({
            "type": "notification_created",
            "record": _rec().to_dict(),
        })
        await _flush_create_tasks()
        assert not (config.vault_root / "agent").exists()

    @pytest.mark.asyncio
    async def test_skips_when_folder_empty(self, config):
        _enable_channel(config, folder="")
        handler = make_vault_page_adapter(config)
        await handler({
            "type": "notification_created",
            "record": _rec().to_dict(),
        })
        await _flush_create_tasks()
        # Nothing written anywhere
        assert not any(config.vault_root.iterdir()) \
            if config.vault_root.exists() else True

    @pytest.mark.asyncio
    async def test_priority_filter(self, config):
        _enable_channel(config, min_priority="high")
        handler = make_vault_page_adapter(config)
        await handler({
            "type": "notification_created",
            "record": _rec(priority="normal").to_dict(),
        })
        await _flush_create_tasks()
        assert not (config.vault_root / "agent" / "pages" /
                    "notifications" / "2026-04-23.md").exists()

    @pytest.mark.asyncio
    async def test_sandbox_rejection_skips_silently(self, config):
        _enable_channel(config, folder="../outside")
        handler = make_vault_page_adapter(config)
        await handler({
            "type": "notification_created",
            "record": _rec().to_dict(),
        })
        await _flush_create_tasks()
        # No file anywhere under vault_root
        assert not any(config.vault_root.rglob("*.md")) \
            if config.vault_root.exists() else True

    @pytest.mark.asyncio
    async def test_concurrent_appends_serialize(self, config):
        """Fire 10 handlers for the same day concurrently — all 10
        entries should land in order, no corruption, header present
        exactly once."""
        _enable_channel(config)
        handler = make_vault_page_adapter(config)
        base_time = datetime(2026, 4, 23, 14, 32, 0, tzinfo=timezone.utc)

        async def fire(i: int):
            ts = base_time.replace(second=i).strftime("%Y-%m-%dT%H:%M:%SZ")
            await handler({
                "type": "notification_created",
                "record": _rec(
                    id=f"id-{i}", timestamp=ts, title=f"#{i}",
                ).to_dict(),
            })

        await asyncio.gather(*(fire(i) for i in range(10)))
        # Let every detached _deliver task finish
        await asyncio.sleep(0.05)

        path = (config.vault_root / "agent" / "pages" /
                "notifications" / "2026-04-23.md")
        content = path.read_text()
        assert content.count("# Notifications — 2026-04-23") == 1
        # One `## ` heading per entry
        assert sum(1 for line in content.splitlines()
                   if line.startswith("## ")) == 10

    @pytest.mark.asyncio
    async def test_write_errors_are_swallowed(self, config, caplog):
        _enable_channel(config)
        handler = make_vault_page_adapter(config)
        caplog.set_level("WARNING")

        # Patch Path.open to raise inside _deliver
        with patch.object(type(config.vault_root), "open",
                          side_effect=OSError("disk full"),
                          create=True), \
            patch("pathlib.Path.open",
                  side_effect=OSError("disk full")):
            await handler({
                "type": "notification_created",
                "record": _rec().to_dict(),
            })
            await _flush_create_tasks()

        assert any("Vault page delivery failed" in r.message
                   for r in caplog.records)

    @pytest.mark.asyncio
    async def test_end_to_end_via_eventbus(self, config):
        """notify() → EventBus publish → adapter → page written."""
        _enable_channel(config)
        bus = EventBus()
        bus.subscribe(make_vault_page_adapter(config))

        await notifs.notify(
            config, bus, category="test", title="Ping", priority="high",
        )
        await _flush_create_tasks()

        # Daily file uses whatever date notify() stamped
        files = list(
            (config.vault_root / "agent" / "pages" / "notifications"
             ).glob("*.md")
        )
        assert len(files) == 1
        assert "Ping" in files[0].read_text()
