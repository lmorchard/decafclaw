"""Tests for the shared tool-output truncation utilities (#922).

Pure-function coverage: head/tail retention, line+byte limits (whichever is
hit first), single-enormous-line bounding, multibyte safety (no U+FFFD),
metadata counts, and the per-tool retention registry.
"""

from decafclaw.output_truncation import (
    TruncationResult,
    format_size,
    retention_for_tool,
    truncate_head,
    truncate_tail,
    truncation_note,
)

REPLACEMENT = "\ufffd"


# -- no-truncation cases -------------------------------------------------------


def test_head_within_limits_is_unchanged():
    r = truncate_head("a\nb", max_lines=10, max_bytes=100)
    assert r == TruncationResult("a\nb", False, None, 2, 3, 2, 3)


def test_tail_within_limits_is_unchanged():
    r = truncate_tail("a\nb", max_lines=10, max_bytes=100)
    assert r == TruncationResult("a\nb", False, None, 2, 3, 2, 3)


def test_trailing_newline_normalization_is_not_truncation():
    """splitlines() drops a trailing newline; that is normalization, not a
    limit being hit — must not report truncated."""
    r = truncate_head("a\n", max_lines=1, max_bytes=5)
    assert r.content == "a"
    assert not r.truncated

    r = truncate_tail("a\n", max_lines=1, max_bytes=5)
    assert r.content == "a"
    assert not r.truncated


def test_exact_fit_is_not_truncation():
    r = truncate_head("a\nb", max_lines=2, max_bytes=3)
    assert not r.truncated and r.content == "a\nb"


def test_empty_string():
    for fn in (truncate_head, truncate_tail):
        r = fn("", max_lines=10, max_bytes=10)
        assert r == TruncationResult("", False, None, 0, 0, 0, 0)


# -- line limit -----------------------------------------------------------------


def test_head_line_limit_keeps_first_lines():
    text = "\n".join(f"L{i}" for i in range(10))
    r = truncate_head(text, max_lines=3, max_bytes=10_000)
    assert r.content == "L0\nL1\nL2"
    assert r.truncated_by == "lines"
    assert (r.total_lines, r.output_lines) == (10, 3)


def test_tail_line_limit_keeps_last_lines():
    text = "\n".join(f"L{i}" for i in range(10))
    r = truncate_tail(text, max_lines=3, max_bytes=10_000)
    assert r.content == "L7\nL8\nL9"
    assert r.truncated_by == "lines"


def test_zero_line_limit_keeps_nothing():
    for fn in (truncate_head, truncate_tail):
        r = fn("a\nb", max_lines=0, max_bytes=100)
        assert r.content == ""
        assert r.truncated_by == "lines"


# -- byte limit -----------------------------------------------------------------


def test_head_byte_limit_drops_from_end():
    text = "\n".join(f"line{i}" for i in range(10))
    r = truncate_head(text, max_lines=10_000, max_bytes=20)
    assert r.content == "line0\nline1\nline2"  # 17 bytes; adding line3 would be 23
    assert r.truncated_by == "bytes"
    assert r.output_bytes == 17


def test_tail_byte_limit_drops_from_front():
    text = "\n".join(f"line{i}" for i in range(10))
    r = truncate_tail(text, max_lines=10_000, max_bytes=20)
    assert r.content == "line7\nline8\nline9"  # 17 bytes
    assert r.truncated_by == "bytes"


def test_line_limit_takes_precedence_in_metadata():
    """When both limits are hit, the line limit (applied first) is reported."""
    text = "\n".join(f"L{i}" for i in range(10))
    r = truncate_head(text, max_lines=3, max_bytes=7)
    assert r.content == "L0\nL1"  # byte-trimmed after the line limit
    assert r.truncated_by == "lines"


def test_tail_retention_keeps_final_diagnostics():
    """The whole point of tail retention: the last line survives byte trimming."""
    r = truncate_tail("head\nmiddle\ntail-error", max_lines=10_000, max_bytes=15)
    assert r.content == "tail-error"
    assert r.truncated_by == "bytes"


# -- single enormous line --------------------------------------------------------


def test_head_single_huge_line_is_bounded():
    r = truncate_head("y" * 100_000, max_lines=10, max_bytes=1024)
    assert r.content == "y" * 1024
    assert (r.total_lines, r.output_lines) == (1, 1)
    assert r.truncated_by == "bytes"
    assert r.output_bytes == 1024


def test_tail_single_huge_line_is_bounded():
    r = truncate_tail("y" * 100_000, max_lines=10, max_bytes=1024)
    assert r.content == "y" * 1024
    assert r.truncated_by == "bytes"


# -- multibyte safety -------------------------------------------------------------


def test_head_multibyte_cut_never_splits_a_character():
    # 10 x 'é' = 20 UTF-8 bytes; a 5-byte limit fits exactly two (4 bytes).
    r = truncate_head("é" * 10, max_lines=10, max_bytes=5)
    assert r.content == "éé"
    assert REPLACEMENT not in r.content
    assert r.output_bytes == 4


def test_tail_multibyte_cut_never_splits_a_character():
    r = truncate_tail("é" * 10, max_lines=10, max_bytes=5)
    assert r.content == "éé"
    assert REPLACEMENT not in r.content


def test_multibyte_boundary_is_character_aligned():
    # Units of "xxé" (4 bytes each); a 9-byte limit must land on a char edge.
    text = "xxé" * 5  # 20 bytes, one line
    head = truncate_head(text, max_lines=10, max_bytes=9)
    assert head.content == "xxéxxéx"  # 9 bytes, ends mid-unit but on a char edge
    assert head.output_bytes == 9

    # The suffix starting at the 'é' (char 8) would be 10 bytes — over the
    # limit — so tail keeps from char 9: two full units, 8 bytes.
    tail = truncate_tail(text, max_lines=10, max_bytes=9)
    assert tail.content == "xxéxxé"
    assert tail.output_bytes == 8


def test_emoji_cut_never_splits_a_character():
    # '😀' is 4 UTF-8 bytes; a 9-byte limit fits exactly two (8 bytes).
    for fn in (truncate_head, truncate_tail):
        r = fn("😀" * 10, max_lines=10, max_bytes=9)
        assert r.content == "😀😀"
        assert REPLACEMENT not in r.content


def test_multibyte_byte_counts_are_utf8_bytes():
    r = truncate_head("é" * 10, max_lines=10, max_bytes=10_000)
    assert r.total_bytes == 20  # not 10 chars


# -- retention registry -----------------------------------------------------------


def test_retention_shell_is_tail():
    assert retention_for_tool("shell") == "tail"


def test_retention_default_is_head():
    for name in ("workspace_read", "vault_read", "conversation_search", "mcp__x__y"):
        assert retention_for_tool(name) == "head"


# -- helpers -----------------------------------------------------------------------


def test_truncation_note_wording():
    assert (
        truncation_note("/tmp/tool_outputs/abc.txt")
        == "\n\n[Output truncated. Full output saved to /tmp/tool_outputs/abc.txt. Use the 'read' tool to inspect it.]"
    )


def test_format_size():
    assert format_size(0) == "0B"
    assert format_size(512) == "512B"
    assert format_size(6000) == "5.9KB"
    assert format_size(1_200_000) == "1.1MB"
    assert format_size(5_000_000_000) == "4.7GB"
