"""Tests for Claude Code budget warning threshold logic."""

from decafclaw.skills.claude_code.tools import _check_budget_warnings


def test_fires_at_thresholds():
    """Warnings fire at 50%, 75%, 90% thresholds."""
    fired = set()
    # At 50%
    warnings = _check_budget_warnings(1.0, 2.0, fired)
    assert len(warnings) == 1
    assert "50%" in warnings[0]
    assert "$1.00" in warnings[0]
    # At 75%
    warnings = _check_budget_warnings(1.5, 2.0, fired)
    assert len(warnings) == 1
    assert "75%" in warnings[0]
    # At 90%
    warnings = _check_budget_warnings(1.8, 2.0, fired)
    assert len(warnings) == 1
    assert "90%" in warnings[0]


def test_no_duplicates():
    """Same threshold doesn't fire twice."""
    fired = set()
    _check_budget_warnings(1.0, 2.0, fired)
    assert 0.5 in fired
    warnings = _check_budget_warnings(1.0, 2.0, fired)
    assert len(warnings) == 0


def test_jumps_fire_multiple():
    """Jumping from 0% to 80% fires both 50% and 75% with actual percentage."""
    fired = set()
    warnings = _check_budget_warnings(1.6, 2.0, fired)
    assert len(warnings) == 2
    assert "exceeded 50%" in warnings[0]
    assert "80% used" in warnings[0]  # actual percentage, not threshold
    assert "exceeded 75%" in warnings[1]


def test_zero_budget():
    """No warnings when budget is 0 (avoids division issues)."""
    fired = set()
    warnings = _check_budget_warnings(1.0, 0, fired)
    assert len(warnings) == 0


def test_below_threshold():
    """No warnings when cost is below 50%."""
    fired = set()
    warnings = _check_budget_warnings(0.5, 2.0, fired)
    assert len(warnings) == 0


def test_all_thresholds_at_once():
    """Cost at 100% fires all three thresholds."""
    fired = set()
    warnings = _check_budget_warnings(2.0, 2.0, fired)
    assert len(warnings) == 3
    assert "50%" in warnings[0]
    assert "75%" in warnings[1]
    assert "90%" in warnings[2]
