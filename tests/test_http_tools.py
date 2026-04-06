"""Tests for HTTP request tool — allowlist and request logic."""

import json

import pytest

from decafclaw.tools.http_tools import (
    _load_allow_patterns,
    _save_allow_pattern,
    _suggest_pattern,
    _url_matches_pattern,
)


def test_url_matches_pattern():
    """URL glob matching works."""
    patterns = ["http://localhost:*", "https://api.example.com/*"]
    assert _url_matches_pattern("http://localhost:8080/api/test", patterns)
    assert _url_matches_pattern("https://api.example.com/v1/users", patterns)
    assert not _url_matches_pattern("https://evil.com/steal", patterns)


def test_url_matches_empty_patterns():
    """No patterns means no match."""
    assert not _url_matches_pattern("http://localhost:8080", [])


def test_suggest_pattern():
    """Pattern suggestion keeps scheme+host+port, wildcards path."""
    assert _suggest_pattern("http://localhost:8080/api/v1/users") == "http://localhost:8080/*"
    assert _suggest_pattern("https://example.com/foo") == "https://example.com/*"
    # Standard ports omitted
    assert _suggest_pattern("https://example.com:443/foo") == "https://example.com/*"
    assert _suggest_pattern("http://example.com:80/foo") == "http://example.com/*"


def test_load_allow_patterns_missing(tmp_path, config):
    """Returns empty list when file doesn't exist."""
    patterns = _load_allow_patterns(config)
    assert patterns == []


def test_save_and_load_patterns(tmp_path, config):
    """Save and load round-trips correctly."""
    _save_allow_pattern(config, "http://localhost:*")
    _save_allow_pattern(config, "https://api.example.com/*")
    # Duplicate should not be added
    _save_allow_pattern(config, "http://localhost:*")

    patterns = _load_allow_patterns(config)
    assert patterns == ["http://localhost:*", "https://api.example.com/*"]


def test_load_allow_patterns_corrupt(config):
    """Returns empty list for corrupt file."""
    path = config.agent_path / "http_allow_patterns.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not json")
    assert _load_allow_patterns(config) == []
