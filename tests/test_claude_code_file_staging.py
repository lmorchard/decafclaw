"""Tests for Claude Code file staging — push and pull between workspace and session."""

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import decafclaw.skills.claude_code.tools as cc_tools
from decafclaw.skills.claude_code.sessions import SessionManager


@pytest.fixture
def staging_env(tmp_path):
    """Set up workspace, session cwd, and module state for file staging tests."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    session_cwd = tmp_path / "workspace" / "projects" / "myrepo"
    session_cwd.mkdir(parents=True)

    # Create a session manager and session
    manager = SessionManager(timeout_sec=300, budget_default=2.0, budget_max=10.0)
    session = manager.create(cwd=str(session_cwd))

    # Mock module-level _config
    config = MagicMock()
    config.workspace_path = workspace

    # Patch module state
    old_config = cc_tools._config
    old_manager = cc_tools._session_manager
    cc_tools._config = config
    cc_tools._session_manager = manager

    yield {
        "workspace": workspace,
        "session_cwd": session_cwd,
        "session": session,
        "manager": manager,
    }

    cc_tools._config = old_config
    cc_tools._session_manager = old_manager


@pytest.fixture
def ctx():
    """Minimal context mock."""
    return MagicMock()


@pytest.mark.asyncio
async def test_push_file_happy_path(staging_env, ctx):
    """Push a file from workspace to session cwd."""
    workspace = staging_env["workspace"]
    session = staging_env["session"]

    # Create source file in workspace
    (workspace / "spec.md").write_text("# My Spec\n")

    result = await cc_tools.tool_claude_code_push_file(
        ctx, session.session_id, "spec.md"
    )

    assert result.data["status"] == "success"
    assert result.data["size_bytes"] > 0
    dest = Path(result.data["dest"])
    assert dest.exists()
    assert dest.read_text() == "# My Spec\n"
    assert dest.name == "spec.md"


@pytest.mark.asyncio
async def test_push_file_custom_dest(staging_env, ctx):
    """Push with a custom dest_name."""
    workspace = staging_env["workspace"]
    session = staging_env["session"]

    (workspace / "spec.md").write_text("content")

    result = await cc_tools.tool_claude_code_push_file(
        ctx, session.session_id, "spec.md", dest_name="docs/spec.md"
    )

    assert result.data["status"] == "success"
    dest = Path(result.data["dest"])
    assert dest.name == "spec.md"
    assert "docs" in str(dest)
    assert dest.read_text() == "content"


@pytest.mark.asyncio
async def test_push_file_source_not_found(staging_env, ctx):
    """Error when source file doesn't exist."""
    session = staging_env["session"]

    result = await cc_tools.tool_claude_code_push_file(
        ctx, session.session_id, "nonexistent.txt"
    )

    assert result.data["status"] == "error"
    assert "not found" in result.text


@pytest.mark.asyncio
async def test_push_file_source_is_directory(staging_env, ctx):
    """Error when source is a directory."""
    workspace = staging_env["workspace"]
    session = staging_env["session"]

    (workspace / "somedir").mkdir()

    result = await cc_tools.tool_claude_code_push_file(
        ctx, session.session_id, "somedir"
    )

    assert result.data["status"] == "error"
    assert "not a file" in result.text


@pytest.mark.asyncio
async def test_push_file_dest_traversal(staging_env, ctx):
    """Error when dest_name tries to escape session cwd."""
    workspace = staging_env["workspace"]
    session = staging_env["session"]

    (workspace / "spec.md").write_text("content")

    result = await cc_tools.tool_claude_code_push_file(
        ctx, session.session_id, "spec.md", dest_name="../../../../etc/evil"
    )

    assert result.data["status"] == "error"
    assert "must be within" in result.text


@pytest.mark.asyncio
async def test_push_file_session_not_found(staging_env, ctx):
    """Error when session doesn't exist."""
    result = await cc_tools.tool_claude_code_push_file(
        ctx, "nonexistent", "spec.md"
    )

    assert result.data["status"] == "error"
    assert "not found" in result.text


@pytest.mark.asyncio
async def test_pull_file_happy_path(staging_env, ctx):
    """Pull a file from session cwd to workspace."""
    session = staging_env["session"]
    session_cwd = staging_env["session_cwd"]

    # Create source file in session cwd
    (session_cwd / "output.txt").write_text("build result\n")

    result = await cc_tools.tool_claude_code_pull_file(
        ctx, session.session_id, "output.txt"
    )

    assert result.data["status"] == "success"
    assert result.data["size_bytes"] > 0
    dest = Path(result.data["dest"])
    assert dest.exists()
    assert dest.read_text() == "build result\n"


@pytest.mark.asyncio
async def test_pull_file_custom_dest(staging_env, ctx):
    """Pull with a custom dest_path."""
    session = staging_env["session"]
    session_cwd = staging_env["session_cwd"]

    (session_cwd / "output.txt").write_text("content")

    result = await cc_tools.tool_claude_code_pull_file(
        ctx, session.session_id, "output.txt", dest_path="results/output.txt"
    )

    assert result.data["status"] == "success"
    dest = Path(result.data["dest"])
    assert dest.read_text() == "content"
    assert "results" in str(dest)


@pytest.mark.asyncio
async def test_pull_file_source_not_found(staging_env, ctx):
    """Error when source file doesn't exist in session."""
    session = staging_env["session"]

    result = await cc_tools.tool_claude_code_pull_file(
        ctx, session.session_id, "nonexistent.txt"
    )

    assert result.data["status"] == "error"
    assert "not found" in result.text


@pytest.mark.asyncio
async def test_pull_file_source_traversal(staging_env, ctx):
    """Error when source_name tries to escape session cwd."""
    session = staging_env["session"]

    result = await cc_tools.tool_claude_code_pull_file(
        ctx, session.session_id, "../../../../etc/passwd"
    )

    assert result.data["status"] == "error"
    assert "must be within" in result.text


@pytest.mark.asyncio
async def test_push_file_source_traversal(staging_env, ctx):
    """Error when source_path tries to escape workspace."""
    session = staging_env["session"]

    result = await cc_tools.tool_claude_code_push_file(
        ctx, session.session_id, "../../outside.txt"
    )

    assert result.data["status"] == "error"
    assert "must be within" in result.text


@pytest.mark.asyncio
async def test_pull_file_dest_traversal(staging_env, ctx):
    """Error when dest_path tries to escape workspace."""
    session = staging_env["session"]
    session_cwd = staging_env["session_cwd"]

    (session_cwd / "output.txt").write_text("content")

    result = await cc_tools.tool_claude_code_pull_file(
        ctx, session.session_id, "output.txt", dest_path="../../outside.txt"
    )

    assert result.data["status"] == "error"
    assert "must be within" in result.text


@pytest.mark.asyncio
async def test_push_binary_file(staging_env, ctx):
    """Push handles binary files correctly."""
    workspace = staging_env["workspace"]
    session = staging_env["session"]

    binary_data = bytes(range(256))
    (workspace / "image.bin").write_bytes(binary_data)

    result = await cc_tools.tool_claude_code_push_file(
        ctx, session.session_id, "image.bin"
    )

    assert result.data["status"] == "success"
    dest = Path(result.data["dest"])
    assert dest.read_bytes() == binary_data
