"""Tests for ScratchpadNotesExtension (#1487, #299 delta injection)."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import patch

import pytest

from decafclaw.config_types import ExtensionConfig
from decafclaw.context_composer import ComposerMode, ContextComposer
from decafclaw.events import EventBus, TurnLifecycle
from decafclaw.extensions import init_extensions
from decafclaw.extensions.scratchpad_notes import ScratchpadNotesExtension
from decafclaw.notes import append_note


def _extension(ctx) -> ScratchpadNotesExtension:
    return ScratchpadNotesExtension({}, ctx.config)


async def _hook(ctx, ext: ScratchpadNotesExtension | None = None):
    return await (ext or _extension(ctx)).pre_compose_hook(ctx, "hello", [])


class TestContribution:
    async def test_no_notes_returns_none(self, ctx):
        assert await _hook(ctx) is None

    async def test_delta_contribution_with_unseen_notes(self, ctx):
        append_note(ctx.config, ctx.conv_id, "user prefers concise replies")
        append_note(ctx.config, ctx.conv_id, "decided to use vertex provider", now="2026-01-02T00:00:00Z")

        contribution = await _hook(ctx)

        assert contribution is not None
        assert contribution.slot.value == "delta"
        assert len(contribution.messages) == 1
        assert contribution.messages[0]["role"] == "conversation_notes"
        assert "user prefers concise replies" in contribution.messages[0]["content"]
        assert contribution.source_entry is not None
        assert contribution.source_entry.source == "notes"
        assert contribution.source_entry.items_included == 2

    async def test_cursor_advances_and_second_call_is_empty(self, ctx):
        append_note(ctx.config, ctx.conv_id, "note 1", now="2026-01-01T00:00:00Z")
        await _hook(ctx)
        assert ctx.composer.injection_cursors.notes_ts == "2026-01-01T00:00:00Z"

        assert await _hook(ctx) is None
        assert ctx.composer.injection_cursors.notes_ts == "2026-01-01T00:00:00Z"

    async def test_only_new_notes_after_cursor(self, ctx):
        append_note(ctx.config, ctx.conv_id, "note 1", now="2026-01-01T00:00:00Z")
        await _hook(ctx)

        append_note(ctx.config, ctx.conv_id, "note 2", now="2026-01-02T00:00:00Z")
        contribution = await _hook(ctx)

        assert contribution is not None
        content = contribution.messages[0]["content"]
        assert "note 2" in content
        assert "note 1" not in content

    async def test_respects_context_max_entries(self, ctx):
        ctx.config.notes.context_max_entries = 3
        for i in range(10):
            append_note(ctx.config, ctx.conv_id, f"note-{i}", now=f"2026-01-{i + 1:02d}T00:00:00Z")

        contribution = await _hook(ctx)

        assert contribution.source_entry.items_included == 3
        content = contribution.messages[0]["content"]
        assert "note-7" in content
        assert "note-9" in content
        assert "note-0" not in content

    async def test_same_second_note_surfaces_after_injection(self, ctx):
        """Notes record whole seconds, so a later note can share the cursor timestamp."""
        append_note(ctx.config, ctx.conv_id, "first", now="2026-01-01T00:00:00Z")
        await _hook(ctx)

        append_note(ctx.config, ctx.conv_id, "second", now="2026-01-01T00:00:00Z")
        contribution = await _hook(ctx)
        assert "second" in contribution.messages[0]["content"]
        assert "first" not in contribution.messages[0]["content"]

        assert await _hook(ctx) is None

    async def test_compaction_reset_reinjects_baseline(self, ctx):
        append_note(ctx.config, ctx.conv_id, "note 1", now="2026-01-01T00:00:00Z")
        await _hook(ctx)
        ctx.composer.reset_for_compaction()
        assert ctx.composer.injection_cursors.notes_ts == ""

        contribution = await _hook(ctx)
        assert "note 1" in contribution.messages[0]["content"]


class TestGates:
    @pytest.mark.parametrize(
        "mode",
        [ComposerMode.HEARTBEAT, ComposerMode.SCHEDULED, ComposerMode.CHILD_AGENT],
    )
    async def test_non_interactive_modes_skip(self, ctx, mode):
        append_note(ctx.config, ctx.conv_id, "anything")
        ctx.composer.mode = mode
        assert await _hook(ctx) is None
        assert ctx.composer.injection_cursors.notes_ts == ""

    async def test_legacy_notes_disabled_skips(self, ctx):
        ctx.config.notes.enabled = False
        append_note(ctx.config, ctx.conv_id, "anything")
        assert await _hook(ctx) is None

    async def test_options_override_legacy_limits(self, ctx):
        ctx.config.notes.context_max_entries = 10
        for i in range(4):
            append_note(ctx.config, ctx.conv_id, f"note-{i}", now=f"2026-01-{i + 1:02d}T00:00:00Z")
        ext = ScratchpadNotesExtension({"context_max_entries": 2}, ctx.config)

        contribution = await _hook(ctx, ext)

        assert contribution.source_entry.items_included == 2
        assert "note-3" in contribution.messages[0]["content"]
        assert "note-0" not in contribution.messages[0]["content"]

    async def test_fail_open_on_read_error(self, ctx):
        append_note(ctx.config, ctx.conv_id, "note")
        with patch(
            "decafclaw.extensions.scratchpad_notes.read_notes",
            side_effect=OSError("disk unreadable"),
        ):
            assert await _hook(ctx) is None
        assert ctx.composer.injection_cursors.notes_ts == ""


class TestLifecycle:
    def test_enable_subscribes_disable_detaches(self, ctx):
        ext = _extension(ctx)
        bus = EventBus()
        sub_ids = ext.enable(bus)
        assert len(sub_ids) == 1
        assert len(bus.get_lifecycle_hooks(TurnLifecycle.PRE_COMPOSE)) == 1
        ext.disable(bus)
        assert len(bus.get_lifecycle_hooks(TurnLifecycle.PRE_COMPOSE)) == 0

    async def test_registered_and_default_enabled_when_seeded(self, ctx):
        ctx.config.extensions["scratchpad_notes"] = ExtensionConfig(enabled=True, type="scratchpad_notes")
        manager = init_extensions(ctx.config, ctx.event_bus)
        assert manager.is_enabled("scratchpad_notes")


class TestComposerIntegration:
    async def test_compose_injects_archived_delta_and_source(self, ctx):
        ctx.config.extensions["scratchpad_notes"] = ExtensionConfig(enabled=True, type="scratchpad_notes")
        init_extensions(ctx.config, ctx.event_bus)
        append_note(ctx.config, ctx.conv_id, "remember the flaky test", now="2026-01-01T00:00:00Z")

        composed = await ContextComposer().compose(ctx, "hello", [], mode=ComposerMode.INTERACTIVE)

        assert any(m["role"] == "conversation_notes" for m in composed.messages_to_archive)
        assert any(
            m["role"] == "user" and "remember the flaky test" in str(m.get("content", "")) for m in composed.messages
        )
        assert any(s.source == "notes" for s in composed.sources)

    async def test_disabled_extension_stops_injection(self, ctx):
        ctx.event_bus = EventBus()
        ctx.config.extensions["scratchpad_notes"] = ExtensionConfig(enabled=False, type="scratchpad_notes")
        manager = init_extensions(ctx.config, ctx.event_bus)
        assert not manager.is_enabled("scratchpad_notes")
        append_note(ctx.config, ctx.conv_id, "should not appear", now="2026-01-01T00:00:00Z")

        composed = await ContextComposer().compose(ctx, "hello", [], mode=ComposerMode.INTERACTIVE)

        assert not any(m["role"] == "conversation_notes" for m in composed.messages_to_archive)
        assert not any(s.source == "notes" for s in composed.sources)

    async def test_compose_rolls_back_cursor_on_failure(self, ctx):
        ctx.config.extensions["scratchpad_notes"] = ExtensionConfig(enabled=True, type="scratchpad_notes")
        init_extensions(ctx.config, ctx.event_bus)
        append_note(ctx.config, ctx.conv_id, "note 1", now="2026-01-01T00:00:00Z")
        before = replace(ctx.composer.injection_cursors)

        with patch("decafclaw.context_composer.apply_image_budget", side_effect=RuntimeError("boom")):
            with pytest.raises(RuntimeError):
                await ContextComposer().compose(ctx, "hello", [], mode=ComposerMode.INTERACTIVE)

        assert ctx.composer.injection_cursors == before
