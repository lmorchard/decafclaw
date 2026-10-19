"""Tests for the memory-cap wiring in opencode_send (issue #994).

Uses a fake subprocess — no real opencode or systemd-run is executed.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import contrib.skills.opencode.tools as opencode_tools
import decafclaw.subprocess_limits as subprocess_limits
from decafclaw.subprocess_limits import memory_cap_prefix

FAKE_SYSTEMD_RUN = "/usr/bin/systemd-run"


class _FakeReader:
    """asyncio StreamReader stand-in: fixed lines, then EOF."""

    def __init__(self, lines):
        self._lines = list(lines)

    async def readline(self):
        return self._lines.pop(0) if self._lines else b""


class _FakeProc:
    """asyncio.subprocess.Process stand-in with a fixed exit code."""

    def __init__(self, returncode=0, stdout_lines=(), stderr_lines=()):
        self.returncode = returncode
        self.stdout = _FakeReader(stdout_lines)
        self.stderr = _FakeReader(stderr_lines)

    async def wait(self):
        return self.returncode


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
    """Make memory_cap_prefix return the cap prefix (systemd available)."""
    monkeypatch.setattr(
        subprocess_limits.shutil,
        "which",
        lambda name: FAKE_SYSTEMD_RUN if name == "systemd-run" else None,
    )
    monkeypatch.setattr(subprocess_limits, "_memory_controller_delegated", lambda: True)


@pytest.fixture
def session(tmp_path, monkeypatch):
    """Skill module state with one allowlisted session (no confirmation)."""
    config = SimpleNamespace(workspace_path=tmp_path, agent_path=tmp_path)
    (config.agent_path / "opencode_allow_patterns.json").write_text('["opencode_send"]')
    manager = opencode_tools.SessionManager(timeout_sec=300, budget_default=2.0, budget_max=10.0)
    sess = manager.create(str(tmp_path / "repo"), description="test")
    monkeypatch.setattr(opencode_tools, "_config", config)
    monkeypatch.setattr(opencode_tools, "_skill_config", opencode_tools.SkillConfig())
    monkeypatch.setattr(opencode_tools, "_session_manager", manager)
    return sess


def _patch_exec(monkeypatch, proc: _FakeProc):
    captured = {}

    async def fake_exec(*args, **kwargs):
        captured["args"] = args
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    return captured


async def test_cap_prefix_comes_before_opencode_run(monkeypatch, enabled_cap, session, ctx):
    captured = _patch_exec(monkeypatch, _FakeProc(returncode=0))

    result = await opencode_tools.tool_opencode_send(ctx, session.session_id, "do a thing", include_diff=False)

    args = list(captured["args"])
    prefix = memory_cap_prefix("50%", "40%")
    assert args[: len(prefix)] == prefix
    assert args[len(prefix) : len(prefix) + 2] == ["opencode", "run"]
    assert result.data is not None
    assert result.data["exit_status"] == "success"


async def test_no_cap_runs_plain_command(monkeypatch, session, ctx):
    # Both limits empty -> no prefix, no host probe, command runs as before.
    monkeypatch.setattr(
        opencode_tools,
        "_skill_config",
        opencode_tools.SkillConfig(memory_max="", memory_high=""),
    )
    captured = _patch_exec(monkeypatch, _FakeProc(returncode=0))

    result = await opencode_tools.tool_opencode_send(ctx, session.session_id, "do a thing", include_diff=False)

    assert captured["args"][:2] == ("opencode", "run")
    assert result.data is not None
    assert result.data["exit_status"] == "success"


async def test_sigkill_returncode_gives_error_result(monkeypatch, session, ctx):
    _patch_exec(monkeypatch, _FakeProc(returncode=-9))

    result = await opencode_tools.tool_opencode_send(ctx, session.session_id, "do a thing", include_diff=False)

    assert result.text.startswith("[error:")
    assert "killed" in result.text
    assert "memory cap" in result.text
    assert result.data is not None
    assert result.data["exit_status"] == "error"
    assert result.data["exit_code"] == -9
    # Session stays usable for a new send.
    assert opencode_tools._session_manager is not None
    assert opencode_tools._session_manager.get(session.session_id) is not None


async def test_other_nonzero_returncode_gives_error_result(monkeypatch, session, ctx):
    _patch_exec(monkeypatch, _FakeProc(returncode=1))

    result = await opencode_tools.tool_opencode_send(ctx, session.session_id, "do a thing", include_diff=False)

    assert result.text.startswith("[error:")
    assert "exited with code 1" in result.text
    assert "killed" not in result.text
    assert result.data is not None
    assert result.data["exit_status"] == "error"
    assert result.data["exit_code"] == 1
