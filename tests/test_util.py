"""Tests for decafclaw.util."""

from decafclaw.util import estimate_tokens


def test_empty_string_returns_zero():
    assert estimate_tokens("") == 0


def test_short_string():
    assert estimate_tokens("abcd") == 1


def test_longer_string():
    assert estimate_tokens("a" * 100) == 25


def test_whitespace_is_counted():
    assert estimate_tokens("    ") == 1
