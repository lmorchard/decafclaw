"""Tests for Claude Code session output logger."""

import json

from claude_code_sdk import (
    AssistantMessage,
    ResultMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)

from decafclaw.skills.claude_code.output import SessionLogger


def test_log_creates_file(tmp_path):
    logger = SessionLogger(tmp_path, "test-session")
    msg = AssistantMessage(
        content=[TextBlock(text="Hello")],
        model="claude-sonnet-4-6",
        parent_tool_use_id=None,
    )
    logger.log_message(msg)
    assert logger.path.exists()


def test_log_appends_jsonl(tmp_path):
    logger = SessionLogger(tmp_path, "test-session")
    msg1 = AssistantMessage(
        content=[TextBlock(text="First")],
        model="claude-sonnet-4-6",
        parent_tool_use_id=None,
    )
    msg2 = AssistantMessage(
        content=[TextBlock(text="Second")],
        model="claude-sonnet-4-6",
        parent_tool_use_id=None,
    )
    logger.log_message(msg1)
    logger.log_message(msg2)

    lines = logger.path.read_text().strip().split("\n")
    assert len(lines) == 2
    assert json.loads(lines[0])["type"] == "assistant"
    assert json.loads(lines[1])["type"] == "assistant"


def test_tracks_files_changed(tmp_path):
    logger = SessionLogger(tmp_path, "test-session")
    msg = AssistantMessage(
        content=[
            ToolUseBlock(id="tc1", name="Edit", input={"file_path": "agent.py", "old_string": "x", "new_string": "y"}),
            ToolUseBlock(id="tc2", name="Write", input={"file_path": "new_file.py", "content": "..."}),
            ToolUseBlock(id="tc3", name="Read", input={"file_path": "config.py"}),
        ],
        model="claude-sonnet-4-6",
        parent_tool_use_id=None,
    )
    logger.log_message(msg)
    assert logger.files_changed == ["agent.py", "new_file.py"]
    assert "config.py" not in logger.files_changed  # Read doesn't count


def test_tracks_tools_used(tmp_path):
    logger = SessionLogger(tmp_path, "test-session")
    msg = AssistantMessage(
        content=[
            ToolUseBlock(id="tc1", name="Read", input={"file_path": "x.py"}),
            ToolUseBlock(id="tc2", name="Edit", input={"file_path": "x.py"}),
        ],
        model="claude-sonnet-4-6",
        parent_tool_use_id=None,
    )
    logger.log_message(msg)
    assert logger.tools_used == ["Read", "Edit"]


def test_tracks_cost_from_result(tmp_path):
    logger = SessionLogger(tmp_path, "test-session")
    msg = ResultMessage(
        subtype="success",
        duration_ms=5000,
        duration_api_ms=4000,
        is_error=False,
        num_turns=3,
        session_id="sdk-session-123",
        total_cost_usd=0.45,
        usage=None,
        result="All done!",
    )
    logger.log_message(msg)
    assert logger.total_cost_usd == 0.45
    assert logger.duration_ms == 5000
    assert logger.num_turns == 3
    assert logger.result_text == "All done!"


def test_tracks_errors(tmp_path):
    logger = SessionLogger(tmp_path, "test-session")
    msg = ResultMessage(
        subtype="error",
        duration_ms=1000,
        duration_api_ms=500,
        is_error=True,
        num_turns=1,
        session_id="sdk-session-123",
        total_cost_usd=0.01,
        usage=None,
        result="Something went wrong",
    )
    logger.log_message(msg)
    assert len(logger.errors) == 1
    assert "Something went wrong" in logger.errors[0]


def test_build_summary_full(tmp_path):
    logger = SessionLogger(tmp_path, "test-session")
    # Simulate a session with tool calls and result
    logger.log_message(AssistantMessage(
        content=[
            ToolUseBlock(id="tc1", name="Read", input={"file_path": "x.py"}),
            ToolUseBlock(id="tc2", name="Edit", input={"file_path": "x.py", "old_string": "a", "new_string": "b"}),
        ],
        model="claude-sonnet-4-6",
        parent_tool_use_id=None,
    ))
    logger.log_message(ResultMessage(
        subtype="success",
        duration_ms=3000,
        duration_api_ms=2500,
        is_error=False,
        num_turns=2,
        session_id="sdk-123",
        total_cost_usd=0.25,
        usage=None,
        result="Fixed the bug in x.py",
    ))

    summary = logger.build_summary("abc123def456")
    assert "Claude Code completed" in summary
    assert "$0.25" in summary
    assert "x.py" in summary
    assert "Read" in summary
    assert "Edit" in summary
    assert "3.0s" in summary
    assert "Fixed the bug" in summary


def test_build_summary_empty(tmp_path):
    logger = SessionLogger(tmp_path, "test-session")
    summary = logger.build_summary()
    assert "Claude Code completed" in summary
    assert "No tool calls" in summary


def test_deduplicates_files_changed(tmp_path):
    logger = SessionLogger(tmp_path, "test-session")
    msg = AssistantMessage(
        content=[
            ToolUseBlock(id="tc1", name="Edit", input={"file_path": "x.py", "old_string": "a", "new_string": "b"}),
            ToolUseBlock(id="tc2", name="Edit", input={"file_path": "x.py", "old_string": "c", "new_string": "d"}),
        ],
        model="claude-sonnet-4-6",
        parent_tool_use_id=None,
    )
    logger.log_message(msg)
    assert logger.files_changed == ["x.py"]  # deduplicated
