"""Tests for Claude Code context injection — prompt assembly and session instructions."""

from decafclaw.skills.claude_code.sessions import Session, SessionManager
from decafclaw.skills.claude_code.tools import _assemble_prompt


def test_assemble_prompt_both():
    """Instructions + context + prompt produces correct XML tags and ordering."""
    result = _assemble_prompt(
        prompt="Fix the bug",
        instructions="Use pytest. Follow PEP 8.",
        context="Here's the spec:\n- Must handle edge cases",
    )
    assert "<instructions>" in result
    assert "Use pytest. Follow PEP 8." in result
    assert "</instructions>" in result
    assert "<context>" in result
    assert "Here's the spec:" in result
    assert "</context>" in result
    assert "Fix the bug" in result
    # Ordering: instructions before context before prompt
    assert result.index("<instructions>") < result.index("<context>")
    assert result.index("</context>") < result.index("Fix the bug")


def test_assemble_prompt_instructions_only():
    """Only instructions + prompt — no context tags."""
    result = _assemble_prompt(prompt="Fix the bug", instructions="Use pytest.")
    assert "<instructions>" in result
    assert "Use pytest." in result
    assert "</instructions>" in result
    assert "<context>" not in result
    assert "Fix the bug" in result


def test_assemble_prompt_context_only():
    """Only context + prompt — no instructions tags."""
    result = _assemble_prompt(prompt="Fix the bug", context="The spec says...")
    assert "<instructions>" not in result
    assert "<context>" in result
    assert "The spec says..." in result
    assert "</context>" in result
    assert "Fix the bug" in result


def test_assemble_prompt_neither():
    """No instructions or context — prompt unchanged."""
    result = _assemble_prompt(prompt="Fix the bug")
    assert result == "Fix the bug"
    assert "<" not in result


def test_session_stores_instructions():
    """Session dataclass stores instructions field."""
    manager = SessionManager(timeout_sec=300, budget_default=2.0, budget_max=10.0)
    session = manager.create(
        cwd="/tmp/test",
        description="test",
        instructions="Always use type hints.",
    )
    assert session.instructions == "Always use type hints."


def test_session_default_instructions():
    """Session instructions default to empty string."""
    session = Session(session_id="abc", cwd="/tmp")
    assert session.instructions == ""
