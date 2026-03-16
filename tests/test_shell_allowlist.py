"""Tests for shell command allowlist."""

import json

from decafclaw.tools.shell_tools import (
    _command_matches_pattern,
    _load_allow_patterns,
    _save_allow_pattern,
    _suggest_pattern,
)

# -- pattern matching tests --


def test_match_exact():
    assert _command_matches_pattern("git status", ["git status"]) is True


def test_match_glob():
    assert _command_matches_pattern("git diff HEAD~1", ["git diff *"]) is True


def test_match_wildcard():
    assert _command_matches_pattern("python scripts/foo.py --arg val", ["python scripts/foo.py *"]) is True


def test_no_match():
    assert _command_matches_pattern("rm -rf /", ["git *", "make *"]) is False


def test_match_multiple_patterns():
    patterns = ["git *", "make *", "pytest *"]
    assert _command_matches_pattern("make test", patterns) is True
    assert _command_matches_pattern("pytest -v", patterns) is True
    assert _command_matches_pattern("rm foo", patterns) is False


# -- pattern suggestion tests --


def test_suggest_pattern_script():
    assert _suggest_pattern("python scripts/foo.py --arg val") == "python scripts/foo.py *"


def test_suggest_pattern_simple():
    assert _suggest_pattern("git status") == "git status"


def test_suggest_pattern_subcommand_with_args():
    assert _suggest_pattern("git diff HEAD~1") == "git diff *"


def test_suggest_pattern_single_command():
    assert _suggest_pattern("ls") == "ls"


def test_suggest_pattern_make():
    assert _suggest_pattern("make test") == "make test"


def test_suggest_pattern_long_script():
    cmd = "python skills/obsidian-notes/scripts/add_todo.py --todo_text 'foo' --date '2026-03-15'"
    assert _suggest_pattern(cmd) == "python skills/obsidian-notes/scripts/add_todo.py *"


# -- persistence tests --


def test_load_patterns_missing_file(config):
    patterns = _load_allow_patterns(config)
    assert patterns == []


def test_save_and_load_pattern(config):
    _save_allow_pattern(config, "git status")
    _save_allow_pattern(config, "make *")
    patterns = _load_allow_patterns(config)
    assert "git status" in patterns
    assert "make *" in patterns


def test_save_pattern_no_duplicates(config):
    _save_allow_pattern(config, "git status")
    _save_allow_pattern(config, "git status")
    patterns = _load_allow_patterns(config)
    assert patterns.count("git status") == 1
