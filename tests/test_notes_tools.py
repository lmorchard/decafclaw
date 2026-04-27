"""Tool wrappers for the per-conversation scratchpad (#299)."""

from __future__ import annotations

from decafclaw.notes import read_notes
from decafclaw.tools import TOOL_DEFINITIONS, TOOLS
from decafclaw.tools.notes_tools import tool_notes_append, tool_notes_read


def _names(defs):
    return {d["function"]["name"] for d in defs}


# -- Registration --------------------------------------------------------------


class TestToolRegistration:
    def test_tools_present_in_registry(self):
        assert "notes_append" in TOOLS
        assert "notes_read" in TOOLS

    def test_definitions_present(self):
        names = _names(TOOL_DEFINITIONS)
        assert "notes_append" in names
        assert "notes_read" in names

    def test_definitions_marked_critical(self):
        """Always-loaded tools must declare priority=critical so they
        don't get deferred behind tool_search."""
        for d in TOOL_DEFINITIONS:
            if d["function"]["name"] in ("notes_append", "notes_read"):
                assert d.get("priority") == "critical", (
                    f"{d['function']['name']} should be critical priority"
                )


# -- tool_notes_append ---------------------------------------------------------


class TestNotesAppend:
    def test_appends_via_tool(self, ctx):
        result = tool_notes_append(ctx, "remember this")
        assert "Saved note" in result.text
        notes = read_notes(ctx.config, ctx.conv_id)
        assert len(notes) == 1
        assert notes[0].text == "remember this"

    def test_rejects_empty(self, ctx):
        result = tool_notes_append(ctx, "")
        assert result.text.startswith("[error:")
        assert read_notes(ctx.config, ctx.conv_id) == []

    def test_truncates_oversized_text(self, ctx):
        ctx.config.notes.max_entry_chars = 10
        result = tool_notes_append(ctx, "x" * 5000)
        assert "Saved note" in result.text
        notes = read_notes(ctx.config, ctx.conv_id)
        assert len(notes[0].text) == 10

    def test_disabled_returns_error(self, ctx):
        ctx.config.notes.enabled = False
        result = tool_notes_append(ctx, "anything")
        assert result.text.startswith("[error:")
        assert "disabled" in result.text

    def test_uses_channel_id_when_conv_id_empty(self, ctx):
        """conv_id fallback chain matches `_compose_notes` so writes
        and reads always target the same file."""
        from decafclaw.notes import notes_path
        ctx.conv_id = ""
        ctx.channel_id = "channel-123"
        tool_notes_append(ctx, "via channel")
        # File should land at the channel-id path, not "default".
        path = notes_path(ctx.config, "channel-123")
        assert path.exists()
        default_path = notes_path(ctx.config, "default")
        assert not default_path.exists()


# -- tool_notes_read -----------------------------------------------------------


class TestNotesRead:
    def test_empty_returns_no_notes_marker(self, ctx):
        result = tool_notes_read(ctx)
        assert result.text == "[no notes yet]"

    def test_returns_recent_notes(self, ctx):
        tool_notes_append(ctx, "first")
        tool_notes_append(ctx, "second")
        result = tool_notes_read(ctx)
        assert "first" in result.text
        assert "second" in result.text
        assert "Conversation notes" in result.text

    def test_limit_caps_count(self, ctx):
        for i in range(5):
            tool_notes_append(ctx, f"note-{i}")
        result = tool_notes_read(ctx, limit=2)
        assert "note-3" in result.text
        assert "note-4" in result.text
        assert "note-0" not in result.text

    def test_limit_zero_uses_default(self, ctx):
        for i in range(3):
            tool_notes_append(ctx, f"note-{i}")
        # limit <= 0 falls back to the 20-default; we have 3 entries so all returned.
        result = tool_notes_read(ctx, limit=0)
        for i in range(3):
            assert f"note-{i}" in result.text

    def test_disabled_returns_error(self, ctx):
        ctx.config.notes.enabled = False
        result = tool_notes_read(ctx)
        assert result.text.startswith("[error:")
        assert "disabled" in result.text
