"""Tests for the shared text-patching engine (#1771)."""

import pytest

from decafclaw.text_patching import (
    AmbiguousMatch,
    InvalidLineRange,
    PatchError,
    TextNotFound,
    content_version,
    edit_not_found_hint,
    patch_exact_match,
    patch_line_range,
    split_physical_lines,
    unified_diff,
)


class TestPatchExactMatch:
    def test_replaces_first_occurrence_and_returns_diff(self):
        new_content, diff = patch_exact_match("alpha beta gamma\n", "alpha", "ALPHA")
        assert new_content == "ALPHA beta gamma\n"
        assert "-alpha beta gamma\n" in diff
        assert "+ALPHA beta gamma\n" in diff

    def test_replace_all_replaces_every_occurrence(self):
        new_content, _ = patch_exact_match("x x x\n", "x", "y", replace_all=True)
        assert new_content == "y y y\n"

    def test_missing_text_raises_text_not_found(self):
        with pytest.raises(TextNotFound):
            patch_exact_match("alpha\n", "beta", "gamma")

    def test_ambiguous_match_reports_count(self):
        with pytest.raises(AmbiguousMatch) as excinfo:
            patch_exact_match("dup dup dup\n", "dup", "x")
        assert excinfo.value.count == 3
        assert "3 matches" in str(excinfo.value)

    def test_empty_old_text_is_rejected(self):
        with pytest.raises(PatchError, match="old_text must not be empty"):
            patch_exact_match("alpha\n", "", "x")

    def test_lf_old_text_matches_crlf_file_and_keeps_crlf(self):
        new_content, diff = patch_exact_match("one\r\ntwo\r\nthree\r\n", "one\ntwo", "ONE\nTWO")
        assert new_content == "ONE\r\nTWO\r\nthree\r\n"
        # The diff renders normalized text, so it reads as LF.
        assert "+ONE\n+TWO\n" in diff


class TestPatchLineRange:
    def test_replaces_inclusive_range(self):
        new_content, diff = patch_line_range("a\nb\nc\nd\n", 2, 3, "B\nC\n")
        assert new_content == "a\nB\nC\nd\n"
        assert "-b\n" in diff and "-c\n" in diff

    def test_empty_content_deletes_range(self):
        new_content, _ = patch_line_range("a\nb\nc\n", 2, 2)
        assert new_content == "a\nc\n"

    def test_missing_trailing_newline_is_added_to_replacement(self):
        new_content, _ = patch_line_range("a\nb\n", 2, 2, "B")
        assert new_content == "a\nB\n"

    def test_out_of_range_raises_with_message(self):
        with pytest.raises(InvalidLineRange) as excinfo:
            patch_line_range("a\nb\n", 2, 5, "x")
        assert str(excinfo.value) == "invalid line range 2-5. File has 2 lines."

    def test_inverted_range_raises(self):
        with pytest.raises(InvalidLineRange):
            patch_line_range("a\nb\n", 3, 1, "x")


class TestContentVersion:
    def test_crlf_and_lf_share_a_version(self):
        assert content_version("alpha\r\nbeta\n") == content_version("alpha\nbeta\n")

    def test_change_produces_new_version(self):
        assert content_version("alpha\n") != content_version("alpha!\n")


class TestUnifiedDiff:
    def test_identical_is_empty(self):
        assert unified_diff("a\nb\n", "a\nb\n") == ""

    def test_unified_with_path(self):
        diff = unified_diff("a\nb\nc\n", "a\nB\nc\n", "notes/x.md")
        assert diff.startswith("--- a/notes/x.md")
        assert "+++ b/notes/x.md" in diff
        assert "-b\n" in diff
        assert "+B\n" in diff

    def test_without_path_uses_before_after(self):
        diff = unified_diff("a\n", "b\n")
        assert "--- before" in diff
        assert "+++ after" in diff

    def test_uses_three_context_lines(self):
        old = "\n".join(f"line{i}" for i in range(10)) + "\n"
        new = old.replace("line5", "LINE5")
        diff = unified_diff(old, new)
        assert "line2" in diff
        assert "line8" in diff
        assert "line1\n" not in diff
        assert "line9\n" not in diff


class TestEditNotFoundHint:
    def test_whitespace_only_difference_reports_line(self):
        hint = edit_not_found_hint("alpha\nbeta   gamma\n", "beta gamma")
        assert "line 2" in hint
        assert "whitespace differences" in hint

    def test_near_line_reports_similar_line(self):
        hint = edit_not_found_hint("alpha\nbeta gamma delta\n", "beta gamma deltx")
        assert "similar line exists at line 2" in hint.lower()

    def test_unrelated_text_has_no_hint(self):
        assert edit_not_found_hint("alpha\n", "zzzz") == ""


class TestSplitPhysicalLines:
    def test_preserves_mixed_line_endings(self):
        assert split_physical_lines("a\r\nb\nc", keepends=True) == ["a\r\n", "b\n", "c"]
        assert split_physical_lines("a\r\nb\nc") == ["a", "b", "c"]
