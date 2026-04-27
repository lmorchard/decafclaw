"""Unit tests for the per-conversation scratchpad core (#299)."""

from __future__ import annotations

import pytest

from decafclaw.notes import (
    Note,
    _parse_line,
    append_note,
    format_notes_for_context,
    notes_path,
    read_notes,
)

# -- Note primitive ------------------------------------------------------------


class TestNote:
    def test_to_line_roundtrip(self):
        n = Note(timestamp="2026-04-27T15:00:00Z", text="hello world")
        line = n.to_line()
        parsed = _parse_line(line + "\n")
        assert parsed == n

    def test_parse_line_rejects_garbage(self):
        assert _parse_line("not a note line") is None
        assert _parse_line("- missing separator") is None
        assert _parse_line("-  — empty timestamp\n") is None
        assert _parse_line("- 2026-01-01T00:00:00Z — \n") is None
        assert _parse_line("\n") is None


# -- Path sandboxing -----------------------------------------------------------


class TestNotesPath:
    def test_returns_path_under_conversations_dir(self, config):
        p = notes_path(config, "abc")
        assert p.parent.name == "conversations"
        assert p.name == "abc.notes.md"

    def test_traversal_strips_to_safe_name(self, config):
        """`/` and `..` get stripped, the leftover letters land
        inside the conversations dir — not outside."""
        p = notes_path(config, "../../etc/passwd")
        # ../../etc/passwd → etcpasswd (slashes + dots stripped) → safe
        assert p.parent.name == "conversations"
        assert p.is_relative_to(p.parent.parent)
        assert p.name == "etcpasswd.notes.md"

    def test_pure_traversal_falls_back_to_sentinel(self, config):
        """Input that sanitizes to empty → sentinel filename."""
        assert notes_path(config, "../..").name == "_invalid.notes.md"

    def test_empty_falls_back_to_sentinel(self, config):
        assert notes_path(config, "").name == "_invalid.notes.md"


# -- Append + read -------------------------------------------------------------


class TestAppendNote:
    def test_appends_one_note(self, config):
        n = append_note(config, "c1", "first note", now="2026-04-27T10:00:00Z")
        assert n.text == "first note"
        assert n.timestamp == "2026-04-27T10:00:00Z"
        notes = read_notes(config, "c1")
        assert len(notes) == 1
        assert notes[0] == n

    def test_appends_in_order(self, config):
        append_note(config, "c1", "a", now="2026-01-01T00:00:00Z")
        append_note(config, "c1", "b", now="2026-01-02T00:00:00Z")
        append_note(config, "c1", "c", now="2026-01-03T00:00:00Z")
        notes = read_notes(config, "c1")
        assert [n.text for n in notes] == ["a", "b", "c"]

    def test_truncates_at_max_chars(self, config):
        n = append_note(config, "c1", "x" * 5000, max_chars=100)
        assert len(n.text) == 100

    def test_max_chars_zero_disables_cap(self, config):
        big = "y" * 5000
        n = append_note(config, "c1", big, max_chars=0)
        assert n.text == big

    def test_collapses_newlines(self, config):
        n = append_note(config, "c1", "line1\nline2\rline3\r\nline4")
        assert "\n" not in n.text
        assert "\r" not in n.text
        assert "line1" in n.text and "line4" in n.text

    def test_rejects_empty_text(self, config):
        with pytest.raises(ValueError, match="empty"):
            append_note(config, "c1", "")
        with pytest.raises(ValueError, match="empty"):
            append_note(config, "c1", "   \n  ")

    def test_max_total_entries_trims_oldest(self, config):
        """When the file would exceed max_total_entries on append,
        oldest entries are dropped so the file stays bounded."""
        # Seed 5 entries.
        for i in range(5):
            append_note(config, "c1", f"note-{i}",
                        now=f"2026-01-{i+1:02d}T00:00:00Z")
        # Cap of 3 → after this append the file has 6 entries; trim to 3.
        append_note(config, "c1", "newest", now="2026-02-01T00:00:00Z",
                    max_total_entries=3)
        notes = read_notes(config, "c1")
        assert [n.text for n in notes] == ["note-3", "note-4", "newest"]

    def test_max_total_entries_zero_disables_cap(self, config):
        """The default value of 0 means no cap (steady-state cheap append)."""
        for i in range(5):
            append_note(config, "c1", f"n-{i}",
                        now=f"2026-01-{i+1:02d}T00:00:00Z",
                        max_total_entries=0)
        assert len(read_notes(config, "c1")) == 5

    def test_max_total_entries_under_cap_uses_cheap_append(self, config, monkeypatch):
        """Steady state: when the file is well under the cap, no
        rewrite happens — the function uses simple append."""
        # Seed 2 entries.
        for i in range(2):
            append_note(config, "c1", f"early-{i}")

        # Watch for os.replace calls (which only happen on the trim path).
        replace_calls = []
        from decafclaw import notes as notes_mod
        original = notes_mod.os.replace
        monkeypatch.setattr(notes_mod.os, "replace",
                            lambda src, dst: replace_calls.append((src, dst)) or original(src, dst))

        # Cap of 100, currently at 2 → +1 = 3, well under. Should be cheap append.
        append_note(config, "c1", "fresh", max_total_entries=100)
        assert replace_calls == []  # no rewrite


class TestReadNotes:
    def _seed(self, config, *texts):
        for i, t in enumerate(texts):
            append_note(config, "c1", t, now=f"2026-01-{i+1:02d}T00:00:00Z")

    def test_empty_returns_empty(self, config):
        assert read_notes(config, "missing") == []

    def test_returns_in_disk_order(self, config):
        self._seed(config, "first", "second", "third")
        notes = read_notes(config, "c1")
        assert [n.text for n in notes] == ["first", "second", "third"]

    def test_limit_keeps_most_recent(self, config):
        self._seed(config, "a", "b", "c", "d", "e")
        notes = read_notes(config, "c1", limit=2)
        assert [n.text for n in notes] == ["d", "e"]

    def test_limit_zero_returns_all(self, config):
        """limit=0 / None means no limit — returns full set."""
        self._seed(config, "a", "b", "c")
        assert len(read_notes(config, "c1", limit=0)) == 3
        assert len(read_notes(config, "c1", limit=None)) == 3

    def test_max_chars_drops_oldest(self, config):
        # 5 entries, 4 chars each → 20 total. Cap at 12 → drop oldest until ≤ 12.
        for i, t in enumerate(["aaaa", "bbbb", "cccc", "dddd", "eeee"]):
            append_note(config, "c1", t, now=f"2026-01-{i+1:02d}T00:00:00Z")
        notes = read_notes(config, "c1", max_chars=12)
        # Each is 4 chars; 12 chars allows 3 entries; oldest 2 dropped.
        assert [n.text for n in notes] == ["cccc", "dddd", "eeee"]

    def test_skips_malformed_lines(self, config):
        """Manually-edited file with garbage lines: clean lines parse,
        garbage lines are skipped."""
        path = notes_path(config, "c1")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "garbage line one\n"
            "- 2026-01-01T00:00:00Z — clean note\n"
            "another garbage line\n"
            "- 2026-01-02T00:00:00Z — second clean\n",
        )
        notes = read_notes(config, "c1")
        assert [n.text for n in notes] == ["clean note", "second clean"]


# -- Format helper -------------------------------------------------------------


class TestFormatNotesForContext:
    def test_empty_returns_empty(self):
        assert format_notes_for_context([]) == ""

    def test_renders_header_and_lines(self):
        notes = [
            Note(timestamp="2026-04-27T10:00:00Z", text="first"),
            Note(timestamp="2026-04-27T10:01:00Z", text="second"),
        ]
        out = format_notes_for_context(notes)
        assert "[Conversation notes" in out
        assert "first" in out
        assert "second" in out
        # Both lines emitted in order.
        first_pos = out.index("first")
        second_pos = out.index("second")
        assert first_pos < second_pos
