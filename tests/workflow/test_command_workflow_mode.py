"""Tests for commands.dispatch_command returning mode="workflow" for kind:workflow skills.

Verifies that workflow skills are dispatched via the new workflow mode rather
than inline or fork, and that arguments are correctly parsed.
"""

import pytest

from decafclaw.commands import CommandResult, _parse_args, dispatch_command
from decafclaw.skills import SkillInfo


@pytest.fixture
def workflow_skill():
    """A minimal workflow skill."""
    return SkillInfo(
        name="my_workflow",
        description="A test workflow",
        location=__import__("pathlib").Path("/fake/path"),
        kind="workflow",
        user_invocable=True,
    )


@pytest.fixture
def regular_skill():
    """A regular (non-workflow) skill for contrast."""
    return SkillInfo(
        name="my_skill",
        description="A regular skill",
        location=__import__("pathlib").Path("/fake/path"),
        kind="skill",
        user_invocable=True,
        body="Do the thing.",
    )


def _make_ctx(skills):
    """Build a minimal ctx with the given discovered_skills."""
    from unittest.mock import MagicMock
    ctx = MagicMock()
    ctx.config.discovered_skills = skills
    return ctx


# ---------------------------------------------------------------------------
# _parse_args unit tests
# ---------------------------------------------------------------------------

def test_parse_args_empty():
    assert _parse_args("") == {}


def test_parse_args_single_kv():
    assert _parse_args("topic=sleep") == {"topic": "sleep"}


def test_parse_args_multiple_kv():
    result = _parse_args("topic=sleep days=7")
    assert result == {"topic": "sleep", "days": "7"}


def test_parse_args_positional():
    result = _parse_args("foo bar")
    assert result == {"_positional": ["foo", "bar"]}


def test_parse_args_mixed():
    result = _parse_args("topic=sleep extra")
    assert result == {"topic": "sleep", "_positional": ["extra"]}


def test_parse_args_value_with_spaces_not_supported():
    """Values don't support spaces (simple split; quoted-value support is future work)."""
    result = _parse_args("topic=first second")
    # "first" goes to topic, "second" is positional
    assert result["topic"] == "first"
    assert "second" in result.get("_positional", [])


# ---------------------------------------------------------------------------
# dispatch_command tests for workflow mode
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_workflow_skill_returns_workflow_mode(workflow_skill):
    """dispatch_command returns mode='workflow' for kind:workflow skills."""
    ctx = _make_ctx([workflow_skill])
    result = await dispatch_command(ctx, "/my_workflow topic=foo")

    assert result.mode == "workflow"
    assert result.workflow_name == "my_workflow"
    assert result.args == {"topic": "foo"}
    assert result.skill is workflow_skill


@pytest.mark.asyncio
async def test_workflow_skill_no_args(workflow_skill):
    """Workflow commands with no args produce empty dict."""
    ctx = _make_ctx([workflow_skill])
    result = await dispatch_command(ctx, "/my_workflow")

    assert result.mode == "workflow"
    assert result.workflow_name == "my_workflow"
    assert result.args == {}


@pytest.mark.asyncio
async def test_workflow_skill_multiple_args(workflow_skill):
    """Multiple key=value args are all parsed into the dict."""
    ctx = _make_ctx([workflow_skill])
    result = await dispatch_command(ctx, "/my_workflow topic=sleep days=7")

    assert result.mode == "workflow"
    assert result.args == {"topic": "sleep", "days": "7"}


@pytest.mark.asyncio
async def test_regular_skill_does_not_return_workflow_mode(regular_skill):
    """Regular skills (kind='skill') do not return mode='workflow'."""
    ctx = _make_ctx([regular_skill])
    # Inline mode: dispatch_command calls execute_command internally
    # which calls activate_skill_internal — mock that out.
    from unittest.mock import AsyncMock, patch
    with patch(
        "decafclaw.commands.execute_command",
        new=AsyncMock(return_value=("inline", "Do the thing.")),
    ):
        result = await dispatch_command(ctx, "/my_skill")

    assert result.mode != "workflow"
    assert result.mode == "inline"


@pytest.mark.asyncio
async def test_unknown_command_still_returns_unknown(workflow_skill):
    """Unknown commands return mode='unknown' regardless of workflow skill presence."""
    ctx = _make_ctx([workflow_skill])
    result = await dispatch_command(ctx, "/no_such_command")

    assert result.mode == "unknown"


@pytest.mark.asyncio
async def test_workflow_mode_not_affected_by_mattermost_prefix(workflow_skill):
    """Workflow mode works with the ! prefix (Mattermost)."""
    ctx = _make_ctx([workflow_skill])
    result = await dispatch_command(ctx, "!my_workflow topic=bar", prefixes=["!"])

    assert result.mode == "workflow"
    assert result.workflow_name == "my_workflow"
    assert result.args.get("topic") == "bar"
