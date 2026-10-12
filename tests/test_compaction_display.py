"""Tests for the shared compaction notice wording (compaction_display).

These pin the exact literals the Python transports render, so a wording
change is a deliberate, per-file review instead of a silent drift.
"""

from decafclaw.compaction_display import compaction_notice


def test_failure_notice_pins_exact_text():
    assert compaction_notice(False) == "Compaction failed — conversation unchanged"


def test_failure_notice_ignores_details():
    assert compaction_notice(False, before=10, after=10, elapsed=5.0, est_tokens=1000) == (
        "Compaction failed — conversation unchanged"
    )


def test_success_notice_seconds_and_message_counts():
    assert compaction_notice(True, before=42, after=10, elapsed=12.3) == (
        "Conversation compacted (12s, 42 → 10 messages)"
    )


def test_success_notice_minutes():
    assert compaction_notice(True, before=42, after=10, elapsed=125.0) == (
        "Conversation compacted (2.1m, 42 → 10 messages)"
    )


def test_success_notice_includes_token_estimate():
    assert compaction_notice(True, before=42, after=10, elapsed=12.3, est_tokens=50000) == (
        "Conversation compacted (12s, 42 → 10 messages, ~50,000 tokens compacted)"
    )


def test_success_notice_omits_missing_details():
    assert compaction_notice(True) == "Conversation compacted (0s)"
    # before without after → no message-count segment
    assert compaction_notice(True, before=42) == "Conversation compacted (0s)"
    assert compaction_notice(True, est_tokens=50000) == ("Conversation compacted (0s, ~50,000 tokens compacted)")


def test_missing_success_flag_renders_success_notice():
    # Transports pass event.get("success", True); a missing or None flag is
    # not a failure (matches the pre-refactor `is False` checks).
    assert compaction_notice(None, before=42, after=10) == "Conversation compacted (0s, 42 → 10 messages)"
    assert compaction_notice(True, before=42, after=10) == "Conversation compacted (0s, 42 → 10 messages)"
