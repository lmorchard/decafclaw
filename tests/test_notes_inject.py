"""Tests for the per-conversation scratchpad context auto-inject (#299)."""

from __future__ import annotations

from decafclaw.context_composer import ComposerMode, ContextComposer
from decafclaw.notes import append_note

# -- _compose_notes (unit-style) -----------------------------------------------


class TestComposeNotes:
    def test_returns_empty_when_no_notes(self, ctx):
        composer = ContextComposer()
        msgs, entry = composer._compose_notes(ctx, ctx.config, ComposerMode.INTERACTIVE)
        assert msgs == []
        assert entry is None

    def test_injects_when_notes_present(self, ctx):
        append_note(ctx.config, ctx.conv_id, "user prefers concise replies")
        append_note(ctx.config, ctx.conv_id, "decided to use vertex provider")

        composer = ContextComposer()
        msgs, entry = composer._compose_notes(ctx, ctx.config, ComposerMode.INTERACTIVE)
        assert len(msgs) == 1
        assert msgs[0]["role"] == "conversation_notes"
        assert "user prefers concise replies" in msgs[0]["content"]
        assert "decided to use vertex provider" in msgs[0]["content"]
        assert entry is not None
        assert entry.source == "notes"
        assert entry.items_included == 2

    def test_skipped_in_heartbeat_mode(self, ctx):
        append_note(ctx.config, ctx.conv_id, "anything")
        composer = ContextComposer()
        msgs, entry = composer._compose_notes(ctx, ctx.config, ComposerMode.HEARTBEAT)
        assert msgs == []
        assert entry is None

    def test_skipped_in_scheduled_mode(self, ctx):
        append_note(ctx.config, ctx.conv_id, "anything")
        composer = ContextComposer()
        msgs, entry = composer._compose_notes(ctx, ctx.config, ComposerMode.SCHEDULED)
        assert msgs == []
        assert entry is None

    def test_skipped_in_child_agent_mode(self, ctx):
        append_note(ctx.config, ctx.conv_id, "anything")
        composer = ContextComposer()
        msgs, entry = composer._compose_notes(ctx, ctx.config, ComposerMode.CHILD_AGENT)
        assert msgs == []
        assert entry is None

    def test_disabled_returns_empty(self, ctx):
        ctx.config.notes.enabled = False
        append_note(ctx.config, ctx.conv_id, "anything")
        composer = ContextComposer()
        msgs, entry = composer._compose_notes(ctx, ctx.config, ComposerMode.INTERACTIVE)
        assert msgs == []
        assert entry is None

    def test_respects_context_max_entries(self, ctx):
        ctx.config.notes.context_max_entries = 3
        for i in range(10):
            append_note(ctx.config, ctx.conv_id, f"note-{i}", now=f"2026-01-{i + 1:02d}T00:00:00Z")
        composer = ContextComposer()
        msgs, entry = composer._compose_notes(ctx, ctx.config, ComposerMode.INTERACTIVE)
        assert entry.items_included == 3
        # Most-recent three.
        assert "note-7" in msgs[0]["content"]
        assert "note-8" in msgs[0]["content"]
        assert "note-9" in msgs[0]["content"]
        assert "note-0" not in msgs[0]["content"]

    def test_delta_injection_multi_turn(self, ctx):
        append_note(ctx.config, ctx.conv_id, "note 1")
        composer = ContextComposer(state=ctx.composer)
        msgs, entry = composer._compose_notes(ctx, ctx.config, ComposerMode.INTERACTIVE)
        assert len(msgs) == 1
        assert "note 1" in msgs[0]["content"]
        assert ctx.composer.last_notes_count == 1

        # Turn 2: no new notes -> nothing injected
        msgs, entry = composer._compose_notes(ctx, ctx.config, ComposerMode.INTERACTIVE)
        assert msgs == []
        assert entry is None
        assert ctx.composer.last_notes_count == 1

        # Add a new note -> Turn 3 only injects the new note
        append_note(ctx.config, ctx.conv_id, "note 2")
        msgs, entry = composer._compose_notes(ctx, ctx.config, ComposerMode.INTERACTIVE)
        assert len(msgs) == 1
        assert "note 2" in msgs[0]["content"]
        assert "note 1" not in msgs[0]["content"]
        assert ctx.composer.last_notes_count == 2

    def test_compaction_resets_notes_delta(self, ctx):
        append_note(ctx.config, ctx.conv_id, "note 1")
        composer = ContextComposer(state=ctx.composer)
        composer._compose_notes(ctx, ctx.config, ComposerMode.INTERACTIVE)
        assert ctx.composer.last_notes_count == 1

        # Simulate compaction
        ctx.composer.reset_for_compaction()
        assert ctx.composer.last_notes_count == 0

        # Next turn re-injects full baseline
        msgs, entry = composer._compose_notes(ctx, ctx.config, ComposerMode.INTERACTIVE)
        assert len(msgs) == 1
        assert "note 1" in msgs[0]["content"]
        assert ctx.composer.last_notes_count == 1
