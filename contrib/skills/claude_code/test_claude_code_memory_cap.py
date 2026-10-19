"""Tests for the memory-cap wiring in claude_code_send (issue #994).

Uses a fake SDK query — no real claude CLI or systemd-run is executed.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from claude_code_sdk import ClaudeCodeOptions, ProcessError
from claude_code_sdk._internal.transport.subprocess_cli import SubprocessCLITransport

import contrib.skills.claude_code.tools as claude_code_tools
import decafclaw.subprocess_limits as subprocess_limits
from contrib.skills.claude_code.tools import _CappedSubprocessCLITransport
from decafclaw.subprocess_limits import memory_cap_prefix

FAKE_SYSTEMD_RUN = "/usr/bin/systemd-run"
FAKE_CLAUDE = "/usr/local/bin/claude"


@pytest.fixture(autouse=True)
def _reset_limits_state(monkeypatch):
    monkeypatch.setattr(subprocess_limits, "_delegation_cache", None)
    monkeypatch.setattr(subprocess_limits, "_warned", False)


@pytest.fixture
def ctx():
    """Minimal context mock (the tools await ctx.publish)."""
    c = MagicMock()
    c.publish = AsyncMock()
    return c


@pytest.fixture
def enabled_cap(monkeypatch):
    """Make memory_cap_prefix return the cap prefix (systemd available).

    Also answers shutil.which("claude") so the transport's _find_cli() does
    not need a real CLI on PATH.
    """
    monkeypatch.setattr(
        subprocess_limits.shutil,
        "which",
        lambda name: {"systemd-run": FAKE_SYSTEMD_RUN, "claude": FAKE_CLAUDE}.get(name),
    )
    monkeypatch.setattr(subprocess_limits, "_memory_controller_delegated", lambda: True)


@pytest.fixture
def session(tmp_path, monkeypatch):
    """Skill module state with one allowlisted session (no confirmation)."""
    config = SimpleNamespace(workspace_path=tmp_path, agent_path=tmp_path)
    (config.agent_path / "claude_code_allow_patterns.json").write_text('["claude_code_send"]')
    manager = claude_code_tools.SessionManager(timeout_sec=300, budget_default=2.0, budget_max=10.0)
    sess = manager.create(str(tmp_path / "repo"), description="test")
    monkeypatch.setattr(claude_code_tools, "_config", config)
    monkeypatch.setattr(claude_code_tools, "_skill_config", claude_code_tools.SkillConfig())
    monkeypatch.setattr(claude_code_tools, "_session_manager", manager)
    return sess


def _patch_query(monkeypatch, exc: Exception | None = None):
    """Replace the SDK query() with an async generator that raises or yields nothing."""
    captured = {}

    async def fake_query(prompt=None, options=None, transport=None):
        captured["transport"] = transport
        if exc is not None:
            raise exc
        return
        yield  # pragma: no cover — makes fake_query an async generator

    monkeypatch.setattr(claude_code_tools, "query", fake_query)
    return captured


def test_capped_transport_puts_prefix_before_sdk_command():
    prefix = [
        "systemd-run",
        "--user",
        "--scope",
        "--quiet",
        "-p",
        "MemoryMax=50%",
        "-p",
        "MemoryHigh=40%",
        "--",
    ]
    transport = _CappedSubprocessCLITransport(
        prompt="hello",
        options=ClaudeCodeOptions(cwd="/tmp"),
        cli_path=FAKE_CLAUDE,
        command_prefix=prefix,
    )

    cmd = transport._build_command()

    assert cmd[: len(prefix)] == prefix
    # The SDK's own command follows the prefix, unchanged.
    assert cmd[len(prefix)] == FAKE_CLAUDE
    assert cmd[len(prefix) + 1 : len(prefix) + 3] == ["--output-format", "stream-json"]


def test_capped_transport_without_prefix_matches_base():
    options = ClaudeCodeOptions(cwd="/tmp")
    capped = _CappedSubprocessCLITransport(prompt="hello", options=options, cli_path=FAKE_CLAUDE)
    base = SubprocessCLITransport(prompt="hello", options=options, cli_path=FAKE_CLAUDE)

    assert capped._build_command() == base._build_command()


async def test_process_error_sigkill_gives_error_result(monkeypatch, session, ctx):
    # Deterministic host state: cap unavailable (no systemd-run).
    monkeypatch.setattr(subprocess_limits.shutil, "which", lambda name: None)
    captured = _patch_query(
        monkeypatch,
        ProcessError("Command failed with exit code -9", exit_code=-9, stderr="Check stderr output for details"),
    )

    result = await claude_code_tools.tool_claude_code_send(ctx, session.session_id, "do a thing", include_diff=False)

    assert result.text.startswith("[error:")
    assert "killed" in result.text
    assert "memory cap" in result.text
    assert result.data is not None
    assert result.data["exit_status"] == "error"
    assert captured["transport"] is None


async def test_process_error_137_gives_error_result(monkeypatch, session, ctx):
    monkeypatch.setattr(subprocess_limits.shutil, "which", lambda name: None)
    _patch_query(monkeypatch, ProcessError("Command failed with exit code 137", exit_code=137))

    result = await claude_code_tools.tool_claude_code_send(ctx, session.session_id, "do a thing", include_diff=False)

    assert result.text.startswith("[error:")
    assert "killed" in result.text
    assert "memory cap" in result.text


async def test_other_process_error_keeps_generic_text(monkeypatch, session, ctx):
    monkeypatch.setattr(subprocess_limits.shutil, "which", lambda name: None)
    _patch_query(monkeypatch, ProcessError("Command failed with exit code 1", exit_code=1))

    result = await claude_code_tools.tool_claude_code_send(ctx, session.session_id, "do a thing", include_diff=False)

    assert result.text.startswith("[error: Claude Code failed:")
    assert "killed" not in result.text
    assert result.data is not None
    assert result.data["exit_status"] == "error"


async def test_capped_transport_passed_to_query_when_cap_enabled(monkeypatch, enabled_cap, session, ctx):
    captured = _patch_query(monkeypatch)

    result = await claude_code_tools.tool_claude_code_send(ctx, session.session_id, "do a thing", include_diff=False)

    assert result.data is not None
    assert result.data["exit_status"] == "success"
    transport = captured["transport"]
    assert isinstance(transport, _CappedSubprocessCLITransport)
    assert transport._command_prefix == memory_cap_prefix("50%", "40%")


async def test_no_transport_passed_when_cap_disabled(monkeypatch, session, ctx):
    monkeypatch.setattr(
        claude_code_tools,
        "_skill_config",
        claude_code_tools.SkillConfig(memory_max="", memory_high=""),
    )
    captured = _patch_query(monkeypatch)

    result = await claude_code_tools.tool_claude_code_send(ctx, session.session_id, "do a thing", include_diff=False)

    assert result.data is not None
    assert result.data["exit_status"] == "success"
    assert captured["transport"] is None
