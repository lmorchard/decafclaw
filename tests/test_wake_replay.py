"""Silent wake replay (#1852) — persisted suppression, not text re-derivation."""

import pytest

from decafclaw.archive import append_message, read_archive
from decafclaw.wake_replay import (
    normalize_suppressed_wakes,
    silent_wake_body,
    suppressed_wake_body,
)


def test_silent_wake_body_strips_only_the_leading_sentinel():
    assert silent_wake_body("BACKGROUND_WAKE_OK") == ""
    assert silent_wake_body("  background_wake_ok — log rotation completed.") == "log rotation completed."
    assert silent_wake_body("BACKGROUND_WAKE_OK: 3 files rotated") == "3 files rotated"
    assert silent_wake_body("BACKGROUND_WAKE_OKAY") is None
    assert silent_wake_body("Here is a result") is None
    assert silent_wake_body(None) is None


def test_persisted_flag_is_authoritative():
    message = {"role": "assistant", "content": "body", "suppress_user_message": True}
    assert suppressed_wake_body(message) == "body"
    assert normalize_suppressed_wakes([message])[0]["suppress_user_message"] is True


def test_legacy_sentinel_is_stripped_only_inside_a_wake_turn():
    messages = [
        {"role": "user", "content": "normal question"},
        {"role": "assistant", "content": "BACKGROUND_WAKE_OK is just a word here"},
        {"role": "wake_trigger", "content": "reply with BACKGROUND_WAKE_OK to stay silent"},
        {"role": "assistant", "content": "BACKGROUND_WAKE_OK — log rotation completed."},
        {"role": "user", "content": "next question"},
        {"role": "assistant", "content": "BACKGROUND_WAKE_OK mentioned after a user turn"},
    ]

    normalized = normalize_suppressed_wakes(messages)

    assert normalized[1]["content"] == "BACKGROUND_WAKE_OK is just a word here"
    assert "suppress_user_message" not in normalized[1]
    assert normalized[3]["content"] == "log rotation completed."
    assert normalized[3]["suppress_user_message"] is True
    assert normalized[5]["content"] == "BACKGROUND_WAKE_OK mentioned after a user turn"
    assert "suppress_user_message" not in normalized[5]


def test_archive_flag_is_stripped_before_a_provider_request():
    from decafclaw.context_pipeline import _strip_archive_metadata

    stripped = _strip_archive_metadata(
        {"role": "assistant", "content": "body", "suppress_user_message": True, "working_memory": {}}
    )
    assert stripped == {"role": "assistant", "content": "body"}


def test_a_wake_with_real_output_keeps_its_text():
    messages = [
        {"role": "wake_trigger", "content": "job done"},
        {"role": "assistant", "content": "The build failed on the lint step."},
    ]
    normalized = normalize_suppressed_wakes(messages)
    assert normalized[1]["content"] == "The build failed on the lint step."
    assert "suppress_user_message" not in normalized[1]


def test_read_archive_normalizes_legacy_silent_wake(config):
    """A transcript written before the flag existed replays without the sentinel."""
    conv_id = "legacy-silent-wake"
    append_message(config, conv_id, {"role": "wake_trigger", "content": "reply with BACKGROUND_WAKE_OK"})
    append_message(config, conv_id, {"role": "assistant", "content": "BACKGROUND_WAKE_OK — log rotation completed."})

    messages = read_archive(config, conv_id)

    assistant = next(m for m in messages if m.get("role") == "assistant")
    assert assistant["content"] == "log rotation completed."
    assert assistant["suppress_user_message"] is True
    assert all("BACKGROUND_WAKE_OK" not in (m.get("content") or "") for m in messages if m.get("role") == "assistant")


@pytest.mark.parametrize(
    "content,expected",
    [
        ("BACKGROUND_WAKE_OK", ""),
        ("BACKGROUND_WAKE_OK — 3 files rotated", "3 files rotated"),
        ("BACKGROUND_WAKE_OK\nnothing to report", "nothing to report"),
    ],
)
def test_silent_body_shapes(content, expected):
    assert silent_wake_body(content) == expected
