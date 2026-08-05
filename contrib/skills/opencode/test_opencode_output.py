"""Tests for OpenCode session output logger."""

import json

from contrib.skills.opencode.tools import SessionLogger, _build_short_text


def test_log_creates_file(tmp_path):
    logger = SessionLogger(tmp_path, "test-session")
    event = {"type": "step_start", "timestamp": 12345}
    logger.log_event(event)
    assert logger.path.exists()
    assert logger.num_turns == 1


def test_tracks_files_changed(tmp_path):
    logger = SessionLogger(tmp_path, "test-session")
    event1 = {
        "type": "tool_use",
        "part": {"tool": "edit", "state": {"input": {"filePath": "foo.py", "oldString": "a", "newString": "b"}}},
    }
    event2 = {"type": "tool_use", "part": {"tool": "bash", "state": {"input": {"command": "ls"}}}}
    logger.log_event(event1)
    logger.log_event(event2)
    assert logger.files_changed == ["foo.py"]
    assert logger.tools_used == ["edit", "bash"]


def test_build_data_shape(tmp_path):
    logger = SessionLogger(tmp_path, "test-session")
    logger.files_changed = ["src/foo.py", "src/bar.py", "src/foo.py"]
    logger.tools_used = ["read", "edit", "read", "edit", "edit"]
    logger.errors = ["ImportError: no module named foo"]
    logger.total_cost_usd = 0.45
    logger.duration_ms = 5000
    logger.num_turns = 3
    logger.result_text = "Done"

    data = logger.build_data(
        session_id="abc123",
        exit_status="success",
        sdk_session_id="sdk-456",
        send_count=2,
        diff="--- a/foo.py\n+++ b/foo.py\n@@ changed @@",
    )

    assert data["exit_status"] == "success"
    assert data["files_changed"] == ["src/foo.py", "src/bar.py"]  # deduplicated
    assert data["tools_used"] == {"read": 2, "edit": 3}
    assert data["errors"] == [{"message": "ImportError: no module named foo"}]
    assert data["cost_usd"] == 0.45
    assert data["duration_ms"] == 5000
    assert data["send_count"] == 2
    assert data["num_turns"] == 3
    assert data["result_text"] == "Done"
    assert data["result_text_truncated"] is False
    assert data["sdk_session_id"] == "sdk-456"
    assert "test-session" in data["log_path"]
    assert data["diff"] == "--- a/foo.py\n+++ b/foo.py\n@@ changed @@"

    json_str = json.dumps(data)
    assert json.loads(json_str) == data


def test_short_text_success_with_cost_and_files(tmp_path):
    logger = SessionLogger(tmp_path, "test")
    logger.total_cost_usd = 0.36
    logger.files_changed = ["a.py", "b.py", "c.py"]
    result = _build_short_text("success", logger)
    assert result == "success - $0.36 - 3 files changed"
