"""Unit tests for the client TurnRecorder (pure event reducer)."""

from decafclaw.client.recorder import TurnRecorder


def _drive(events):
    rec = TurnRecorder(conv_id="web-test")
    for e in events:
        rec.record(e)
    return rec


def test_assistant_text_joined_from_message_complete():
    rec = _drive([
        {"type": "turn_start", "conv_id": "web-test"},
        {"type": "chunk", "conv_id": "web-test", "text": "ignored "},
        {"type": "message_complete", "conv_id": "web-test", "role": "assistant",
         "text": "Hello there.", "usage": {"input_tokens": 5}},
        {"type": "turn_complete", "conv_id": "web-test"},
    ])
    s = rec.finalize("turn_complete")
    assert s.conv_id == "web-test"
    assert s.assistant_text == "Hello there."
    assert s.usage == {"input_tokens": 5}
    assert s.status == "complete"
    assert s.raw_event_count == 4


def test_tool_calls_ordered_and_completed():
    rec = _drive([
        {"type": "tool_start", "conv_id": "web-test", "tool": "vault_read",
         "tool_call_id": "a"},
        {"type": "tool_start", "conv_id": "web-test", "tool": "http_get",
         "tool_call_id": "b"},
        {"type": "tool_status", "conv_id": "web-test", "tool": "http_get",
         "tool_call_id": "b", "message": "fetching"},
        {"type": "tool_end", "conv_id": "web-test", "tool": "vault_read",
         "tool_call_id": "a", "result_text": "page body"},
        {"type": "tool_end", "conv_id": "web-test", "tool": "http_get",
         "tool_call_id": "b", "result_text": "<html>"},
        {"type": "turn_complete", "conv_id": "web-test"},
    ])
    s = rec.finalize("turn_complete")
    assert [t.name for t in s.tool_calls] == ["vault_read", "http_get"]
    assert [t.tool_call_id for t in s.tool_calls] == ["a", "b"]
    assert all(t.status == "done" for t in s.tool_calls)
    assert s.tool_calls[0].result_text == "page body"
    assert s.tool_calls[0].status_message == ""        # vault_read got no tool_status
    assert s.tool_calls[1].result_text == "<html>"
    assert s.tool_calls[1].status_message == "fetching"  # http_get got a tool_status


def test_tool_end_without_prior_start_is_recorded():
    rec = _drive([
        {"type": "tool_end", "conv_id": "web-test", "tool": "orphan",
         "tool_call_id": "z", "result_text": "late"},
        {"type": "turn_complete", "conv_id": "web-test"},
    ])
    s = rec.finalize("turn_complete")
    assert len(s.tool_calls) == 1
    assert s.tool_calls[0].name == "orphan"
    assert s.tool_calls[0].status == "done"


def test_reflection_recorded():
    rec = _drive([
        {"type": "reflection_result", "conv_id": "web-test", "passed": False,
         "critique": "missed a step", "retry_number": 1},
        {"type": "turn_complete", "conv_id": "web-test"},
    ])
    s = rec.finalize("turn_complete")
    assert s.reflection == {"passed": False, "critique": "missed a step",
                            "retry_number": 1}


def test_model_changed_recorded():
    rec = _drive([
        {"type": "model_changed", "conv_id": "web-test", "model": "gemini-pro"},
        {"type": "turn_complete", "conv_id": "web-test"},
    ])
    s = rec.finalize("turn_complete")
    assert s.model == "gemini-pro"


def test_status_confirmation_overrides_complete():
    rec = _drive([
        {"type": "confirm_request", "conv_id": "web-test",
         "confirmation_id": "c1", "action_type": "shell",
         "tool": "shell_exec", "command": "rm -rf /tmp/x", "message": "OK?"},
    ])
    s = rec.finalize("confirmation")
    assert s.status == "halted_confirmation"
    assert s.confirmations[0].confirmation_id == "c1"
    assert s.confirmations[0].command == "rm -rf /tmp/x"


def test_status_error_when_error_event_seen():
    rec = _drive([
        {"type": "error", "conv_id": "web-test", "message": "boom"},
        {"type": "turn_complete", "conv_id": "web-test"},
    ])
    s = rec.finalize("turn_complete")
    assert s.status == "error"
    assert s.errors == ["boom"]


def test_status_timeout():
    rec = _drive([{"type": "turn_start", "conv_id": "web-test"}])
    s = rec.finalize("timeout")
    assert s.status == "timeout"


def test_status_disconnect_is_error():
    rec = _drive([{"type": "turn_start", "conv_id": "web-test"}])
    s = rec.finalize("disconnect")
    assert s.status == "error"


def test_tool_status_does_not_overwrite_lifecycle_status():
    rec = _drive([
        {"type": "tool_start", "conv_id": "web-test", "tool": "http_get",
         "tool_call_id": "x"},
        {"type": "tool_status", "conv_id": "web-test", "tool": "http_get",
         "tool_call_id": "x", "message": "fetching"},
        {"type": "confirm_request", "conv_id": "web-test",
         "confirmation_id": "c1", "action_type": "shell", "tool": "shell_exec",
         "command": "ls", "message": "ok?"},
    ])
    s = rec.finalize("confirmation")
    assert s.tool_calls[0].status == "started"
    assert s.tool_calls[0].status_message == "fetching"


def test_status_unknown_stop_reason_is_error():
    s = TurnRecorder().finalize("some_future_reason")
    assert s.status == "error"
