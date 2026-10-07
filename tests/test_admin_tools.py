"""Tests for admin file tools (issue #955).

Verifies strict containment to config.agent_path, workspace exclusion,
mandatory confirmation with diff preview, and categorical blocking on
unattended runs and child agents.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from decafclaw.confirmations import ConfirmationResponse
from decafclaw.context import Context
from decafclaw.media import ToolResult
from decafclaw.tools.admin_tools import (
    MAX_READ_LINES,
    _resolve_admin_path,
    tool_admin_delete,
    tool_admin_edit,
    tool_admin_list,
    tool_admin_read,
    tool_admin_replace_lines,
    tool_admin_write,
)
from decafclaw.tools.delegate import _ADMIN_WRITE_TOOLS, run_child_turn


def _text(result: str | ToolResult) -> str:
    return result.text if isinstance(result, ToolResult) else result


def _mock_confirm(approved: bool = True):
    return AsyncMock(return_value=ConfirmationResponse(confirmation_id="c1", approved=approved))


@pytest.fixture(autouse=True)
def ensure_agent_path_exists(config):
    config.agent_path.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Path resolution tests
# ---------------------------------------------------------------------------


def test_resolve_admin_path_normal(config):
    path, err = _resolve_admin_path(config, "skills/test/SKILL.md")
    assert err is None
    assert path is not None
    assert path == (config.agent_path / "skills" / "test" / "SKILL.md").resolve()


def test_resolve_admin_path_root(config):
    # allow_root=False
    path, err = _resolve_admin_path(config, ".", allow_root=False)
    assert path is None
    assert err is not None
    assert "refers to the agent directory root" in err

    # allow_root=True
    path, err = _resolve_admin_path(config, ".", allow_root=True)
    assert err is None
    assert path == config.agent_path.resolve()


def test_resolve_admin_path_rejects_escape(config):
    path, err = _resolve_admin_path(config, "../../etc/passwd")
    assert path is None
    assert err is not None
    assert "outside the agent directory" in err


def test_resolve_admin_path_rejects_absolute_outside(config):
    path, err = _resolve_admin_path(config, "/tmp/evil.txt")
    assert path is None
    assert err is not None
    assert "outside the agent directory" in err


def test_resolve_admin_path_rejects_workspace_path(config):
    path, err = _resolve_admin_path(config, "workspace/notes.txt")
    assert path is None
    assert err is not None
    assert "inside the workspace" in err
    assert "workspace_*" in err


def test_resolve_admin_path_rejects_workspace_root(config):
    path, err = _resolve_admin_path(config, "workspace", allow_root=True)
    assert path is None
    assert err is not None
    assert "inside the workspace" in err


# ---------------------------------------------------------------------------
# Read and list tests
# ---------------------------------------------------------------------------


def test_admin_read_file(ctx):
    target = ctx.config.agent_path / "skills" / "custom" / "SKILL.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("line 1\nline 2\nline 3\n")

    result = tool_admin_read(ctx, "skills/custom/SKILL.md")
    text = _text(result)
    assert "1| line 1" in text
    assert "2| line 2" in text
    assert "3| line 3" in text


def test_admin_read_range(ctx):
    target = ctx.config.agent_path / "prompts" / "test.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(f"row {i}" for i in range(1, 11)))

    result = tool_admin_read(ctx, "prompts/test.txt", start_line=3, end_line=5)
    text = _text(result)
    assert "Lines 3-5 of 10:" in text
    assert "row 3" in text
    assert "row 5" in text
    assert "row 2" not in text


def test_admin_read_caps_large_files(ctx):
    target = ctx.config.agent_path / "large.txt"
    target.write_text("\n".join(f"line {i}" for i in range(MAX_READ_LINES + 50)))

    result = tool_admin_read(ctx, "large.txt")
    text = _text(result)
    assert f"showing first {MAX_READ_LINES}" in text


def test_admin_read_not_found(ctx):
    result = tool_admin_read(ctx, "nonexistent.txt")
    assert "file not found" in _text(result)


def test_admin_read_directory_fails(ctx):
    sub = ctx.config.agent_path / "somedir"
    sub.mkdir(parents=True, exist_ok=True)
    result = tool_admin_read(ctx, "somedir")
    assert "is a directory" in _text(result)


def test_admin_read_rejects_workspace(ctx):
    ws_file = ctx.config.workspace_path / "inside.txt"
    ws_file.parent.mkdir(parents=True, exist_ok=True)
    ws_file.write_text("hello")

    result = tool_admin_read(ctx, "workspace/inside.txt")
    assert "inside the workspace" in _text(result)


def test_admin_list(ctx):
    (ctx.config.agent_path / "skills").mkdir(parents=True, exist_ok=True)
    (ctx.config.agent_path / "prompts").mkdir(parents=True, exist_ok=True)
    (ctx.config.agent_path / "config.yaml").write_text("agent: test")

    result = tool_admin_list(ctx, ".")
    text = _text(result)
    assert "skills/" in text
    assert "prompts/" in text
    assert "config.yaml" in text


def test_admin_list_subdir(ctx):
    sub = ctx.config.agent_path / "skills" / "demo"
    sub.mkdir(parents=True, exist_ok=True)
    (sub / "SKILL.md").write_text("name: demo")

    result = tool_admin_list(ctx, "skills")
    text = _text(result)
    assert "demo/" in text


# ---------------------------------------------------------------------------
# Mutation tests (write, replace_lines, edit, delete)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_write_approved(ctx):
    confirm_mock = _mock_confirm(approved=True)
    ctx.request_confirmation = confirm_mock

    result = await tool_admin_write(ctx, "skills/new_skill/SKILL.md", "# New Skill\nContent\n")
    assert "Wrote 20 characters" in _text(result)

    target = ctx.config.agent_path / "skills" / "new_skill" / "SKILL.md"
    assert target.exists()
    assert target.read_text() == "# New Skill\nContent\n"

    # Verify confirmation was requested with preview diff
    assert confirm_mock.call_count == 1
    req = confirm_mock.call_args[0][0]
    assert "admin_write" in req.action_data["command"]
    assert "new file" in req.action_data["command"]
    assert req.approve_label == "Approve"
    assert req.deny_label == "Deny"


@pytest.mark.asyncio
async def test_admin_write_existing_diff(ctx):
    target = ctx.config.agent_path / "config.yaml"
    target.write_text("old_key: old_value\n")

    confirm_mock = _mock_confirm(approved=True)
    ctx.request_confirmation = confirm_mock

    result = await tool_admin_write(ctx, "config.yaml", "old_key: new_value\n")
    text = _text(result)
    assert "Wrote" in text
    assert "-old_key: old_value" in text
    assert "+old_key: new_value" in text


@pytest.mark.asyncio
async def test_admin_write_denied(ctx):
    confirm_mock = _mock_confirm(approved=False)
    ctx.request_confirmation = confirm_mock

    result = await tool_admin_write(ctx, "skills/denied/SKILL.md", "content")
    assert "denied by user" in _text(result)

    target = ctx.config.agent_path / "skills" / "denied" / "SKILL.md"
    assert not target.exists()


@pytest.mark.asyncio
async def test_admin_write_blocked_on_unattended_turn(ctx):
    ctx.task_mode = "scheduled"
    ctx.request_confirmation = _mock_confirm(approved=True)

    result = await tool_admin_write(ctx, "skills/hack/SKILL.md", "content")
    assert "unattended turns" in _text(result)
    assert ctx.request_confirmation.call_count == 0


@pytest.mark.asyncio
async def test_admin_write_blocked_on_child_agent(ctx):
    ctx.is_child = True
    ctx.request_confirmation = _mock_confirm(approved=True)

    result = await tool_admin_write(ctx, "skills/hack/SKILL.md", "content")
    assert "child agents" in _text(result)
    assert ctx.request_confirmation.call_count == 0


@pytest.mark.asyncio
async def test_admin_write_rejects_workspace_path(ctx):
    ctx.request_confirmation = _mock_confirm(approved=True)
    result = await tool_admin_write(ctx, "workspace/hack.txt", "content")
    assert "inside the workspace" in _text(result)
    assert ctx.request_confirmation.call_count == 0


@pytest.mark.asyncio
async def test_admin_replace_lines(ctx):
    target = ctx.config.agent_path / "prompts" / "p.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("A\nB\nC\nD\n")

    confirm_mock = _mock_confirm(approved=True)
    ctx.request_confirmation = confirm_mock

    result = await tool_admin_replace_lines(ctx, "prompts/p.md", 2, 3, "X\nY\n")
    assert "Replaced lines 2-3" in _text(result)
    assert target.read_text() == "A\nX\nY\nD\n"


@pytest.mark.asyncio
async def test_admin_replace_lines_delete(ctx):
    target = ctx.config.agent_path / "prompts" / "p.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("A\nB\nC\nD\n")

    ctx.request_confirmation = _mock_confirm(approved=True)
    result = await tool_admin_replace_lines(ctx, "prompts/p.md", 2, 3, "")
    assert "Deleted lines 2-3" in _text(result)
    assert target.read_text() == "A\nD\n"


@pytest.mark.asyncio
async def test_admin_replace_lines_denied(ctx):
    target = ctx.config.agent_path / "prompts" / "p.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("A\nB\nC\n")

    ctx.request_confirmation = _mock_confirm(approved=False)
    result = await tool_admin_replace_lines(ctx, "prompts/p.md", 2, 2, "X\n")
    assert "denied by user" in _text(result)
    assert target.read_text() == "A\nB\nC\n"


@pytest.mark.asyncio
async def test_admin_edit(ctx):
    target = ctx.config.agent_path / "prompts" / "p.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("hello foo world\n")

    ctx.request_confirmation = _mock_confirm(approved=True)
    result = await tool_admin_edit(ctx, "prompts/p.md", "foo", "bar")
    assert "replaced 1 occurrence(s)" in _text(result)
    assert target.read_text() == "hello bar world\n"


@pytest.mark.asyncio
async def test_admin_edit_not_found(ctx):
    target = ctx.config.agent_path / "prompts" / "p.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("hello foo world\n")

    ctx.request_confirmation = _mock_confirm(approved=True)
    result = await tool_admin_edit(ctx, "prompts/p.md", "nonexistent", "bar")
    assert "text not found" in _text(result)
    assert ctx.request_confirmation.call_count == 0


@pytest.mark.asyncio
async def test_admin_delete_file(ctx):
    target = ctx.config.agent_path / "skills" / "old" / "SKILL.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("to delete")

    ctx.request_confirmation = _mock_confirm(approved=True)
    result = await tool_admin_delete(ctx, "skills/old/SKILL.md")
    assert "Deleted admin file" in _text(result)
    assert not target.exists()


@pytest.mark.asyncio
async def test_admin_delete_dir(ctx):
    sub = ctx.config.agent_path / "skills" / "deleteme"
    sub.mkdir(parents=True, exist_ok=True)
    (sub / "file.txt").write_text("data")

    ctx.request_confirmation = _mock_confirm(approved=True)
    # Non-empty directory without recursive=True fails before confirmation
    result_fail = await tool_admin_delete(ctx, "skills/deleteme", recursive=False)
    assert "recursive=true" in _text(result_fail)
    assert ctx.request_confirmation.call_count == 0

    # With recursive=True, succeeds after confirmation
    result = await tool_admin_delete(ctx, "skills/deleteme", recursive=True)
    assert "Deleted admin directory" in _text(result)
    assert not sub.exists()


@pytest.mark.asyncio
async def test_admin_delete_denied(ctx):
    target = ctx.config.agent_path / "important.txt"
    target.write_text("keep me")

    ctx.request_confirmation = _mock_confirm(approved=False)
    result = await tool_admin_delete(ctx, "important.txt")
    assert "denied by user" in _text(result)
    assert target.exists()


# ---------------------------------------------------------------------------
# Delegation isolation test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delegate_excludes_admin_write_tools(ctx):
    """Child agents must never have admin write tools in their allowed set."""
    parent_ctx = ctx
    parent_ctx.tools.allowed = None  # all tools available

    captured_child_ctx = None

    async def fake_enqueue_turn(conv_id, kind, prompt, history, context_setup, user_id=None):
        nonlocal captured_child_ctx
        child = Context(config=parent_ctx.config, event_bus=parent_ctx.event_bus)
        context_setup(child)
        captured_child_ctx = child
        fut = AsyncMock()
        fut.return_value = "done"
        return fut

    mock_mgr = MagicMock()
    mock_mgr.enqueue_turn = AsyncMock(side_effect=fake_enqueue_turn)
    parent_ctx.manager = mock_mgr

    await run_child_turn(parent_ctx, "do something")

    assert captured_child_ctx is not None
    child_allowed = captured_child_ctx.tools.allowed
    assert child_allowed is not None

    for write_tool in _ADMIN_WRITE_TOOLS:
        assert write_tool not in child_allowed, f"{write_tool} should be excluded from child agent"

    # admin_read and admin_list are allowed
    assert "admin_read" in child_allowed
    assert "admin_list" in child_allowed
