"""Tests for human-only self-update module (#981).

Guarded update logic wrapping git pull, uv sync, and systemctl restart.
Includes the load-bearing import-boundary test.
"""

from __future__ import annotations

import pathlib
import re
from unittest.mock import MagicMock

import pytest

from decafclaw.self_update import (
    UpdateResult,
    find_repo_dir,
    get_busy_conversations,
    perform_self_update,
)


def test_no_agent_side_update_imports():
    """self_update.py must not be reachable from tools/ or skills/ — the
    load-bearing 'agent cannot update itself' guarantee."""
    root = pathlib.Path(__file__).resolve().parent.parent / "src" / "decafclaw"
    import_line_re = re.compile(r"^\s*(import|from)\s+.*\bself_update\b", re.MULTILINE)
    offenders = []
    for sub in ("tools", "skills"):
        for py in (root / sub).rglob("*.py"):
            text = py.read_text(encoding="utf-8")
            if import_line_re.search(text):
                offenders.append(str(py))
    assert not offenders, f"self_update.py imported by agent-side code: {offenders}"


def test_find_repo_dir(tmp_path):
    # Within repo checkout
    repo = find_repo_dir()
    assert repo is not None
    assert (repo / ".git").exists()

    # Outside repo checkout
    outside = tmp_path / "not_a_repo"
    outside.mkdir()
    assert find_repo_dir(outside) is None


def test_get_busy_conversations():
    # Manager is None
    assert get_busy_conversations(None) == []

    # Mock manager with idle and busy states
    manager = MagicMock()
    idle_state = MagicMock(busy=False, agent_task=None)
    busy_state = MagicMock(busy=True, agent_task=None)
    task = MagicMock()
    task.done.return_value = False
    task_busy_state = MagicMock(busy=False, agent_task=task)

    manager._conversations = {
        "conv-idle": idle_state,
        "conv-busy-1": busy_state,
        "conv-busy-2": task_busy_state,
    }

    index = MagicMock()
    conv1 = MagicMock(title="Tackle Issue")
    index.get.side_effect = lambda cid: conv1 if cid == "conv-busy-1" else None

    busy = get_busy_conversations(manager, index)
    assert len(busy) == 2
    assert "'Tackle Issue' (conv-bus)" in busy
    assert "conv-bus" in busy


@pytest.mark.asyncio
async def test_refuses_when_active_turns_present(tmp_path):
    manager = MagicMock()
    manager._conversations = {"c1": MagicMock(busy=True, agent_task=None)}

    runner_called = False

    async def fake_runner(cmd, cwd=None, env=None):
        nonlocal runner_called
        runner_called = True
        return 0, "", ""

    res = await perform_self_update(
        manager=manager,
        repo_dir=tmp_path,
        cmd_runner=fake_runner,
    )
    assert not res.ok
    assert "active turn(s)" in res.message
    assert not runner_called


@pytest.mark.asyncio
async def test_aborts_when_working_tree_dirty(tmp_path):
    async def fake_runner(cmd, cwd=None, env=None):
        if cmd[:3] == ["git", "status", "--porcelain"]:
            return 0, " M src/decafclaw/agent.py\n", ""
        return 0, "", ""

    res = await perform_self_update(
        repo_dir=tmp_path,
        cmd_runner=fake_runner,
    )
    assert not res.ok
    assert "working tree is dirty" in res.message


@pytest.mark.asyncio
async def test_aborts_when_no_upstream_branch(tmp_path):
    async def fake_runner(cmd, cwd=None, env=None):
        if cmd[:3] == ["git", "status", "--porcelain"]:
            return 0, "", ""
        if cmd[:2] == ["git", "rev-parse"]:
            return 1, "", "fatal: no upstream configured"
        return 0, "", ""

    res = await perform_self_update(
        repo_dir=tmp_path,
        cmd_runner=fake_runner,
    )
    assert not res.ok
    assert "no upstream tracking branch" in res.message


@pytest.mark.asyncio
async def test_aborts_when_branch_diverged(tmp_path):
    async def fake_runner(cmd, cwd=None, env=None):
        if cmd[:3] == ["git", "status", "--porcelain"]:
            return 0, "", ""
        if cmd[:2] == ["git", "rev-parse"]:
            return 0, "origin/main", ""
        if cmd[:2] == ["git", "fetch"]:
            return 0, "", ""
        if cmd[:3] == ["git", "merge-base", "--is-ancestor"]:
            return 1, "", ""
        return 0, "", ""

    res = await perform_self_update(
        repo_dir=tmp_path,
        cmd_runner=fake_runner,
    )
    assert not res.ok
    assert "diverged" in res.message


@pytest.mark.asyncio
async def test_already_up_to_date(tmp_path):
    async def fake_runner(cmd, cwd=None, env=None):
        if cmd[:3] == ["git", "status", "--porcelain"]:
            return 0, "", ""
        if cmd[:2] == ["git", "rev-parse"]:
            return 0, "origin/main", ""
        if cmd[:2] == ["git", "fetch"]:
            return 0, "", ""
        if cmd[:3] == ["git", "merge-base", "--is-ancestor"]:
            return 0, "", ""
        if cmd[:2] == ["git", "log"]:
            return 0, "", ""
        return 0, "", ""

    res = await perform_self_update(
        repo_dir=tmp_path,
        cmd_runner=fake_runner,
    )
    assert res.ok
    assert "already up to date" in res.message.lower()


@pytest.mark.asyncio
async def test_shows_incoming_commits_when_unconfirmed(tmp_path):
    incoming = "abc1234 feat: add cool feature\ndef5678 fix: resolve bug"

    async def fake_runner(cmd, cwd=None, env=None):
        if cmd[:3] == ["git", "status", "--porcelain"]:
            return 0, "", ""
        if cmd[:2] == ["git", "rev-parse"]:
            return 0, "origin/main", ""
        if cmd[:2] == ["git", "fetch"]:
            return 0, "", ""
        if cmd[:3] == ["git", "merge-base", "--is-ancestor"]:
            return 0, "", ""
        if cmd[:2] == ["git", "log"]:
            return 0, incoming, ""
        return 0, "", ""

    res = await perform_self_update(
        confirmed=False,
        repo_dir=tmp_path,
        cmd_runner=fake_runner,
    )
    assert res.ok
    assert not res.restarted
    assert "Incoming commits:" in res.message
    assert incoming in res.message
    assert "/update confirm" in res.message


@pytest.mark.asyncio
async def test_pull_failure_aborts(tmp_path):
    async def fake_runner(cmd, cwd=None, env=None):
        if cmd[:3] == ["git", "status", "--porcelain"]:
            return 0, "", ""
        if cmd[:2] == ["git", "rev-parse"]:
            return 0, "origin/main", ""
        if cmd[:2] == ["git", "fetch"]:
            return 0, "", ""
        if cmd[:3] == ["git", "merge-base", "--is-ancestor"]:
            return 0, "", ""
        if cmd[:2] == ["git", "log"]:
            return 0, "abc1234 feat", ""
        if cmd[:3] == ["git", "pull", "--ff-only"]:
            return 1, "", "fatal: Not possible to fast-forward, aborting."
        return 0, "", ""

    res = await perform_self_update(
        confirmed=True,
        repo_dir=tmp_path,
        cmd_runner=fake_runner,
    )
    assert not res.ok
    assert not res.restarted
    assert "git pull --ff-only" in res.message


@pytest.mark.asyncio
async def test_uv_sync_failure_aborts(tmp_path):
    called = []

    async def fake_runner(cmd, cwd=None, env=None):
        called.append(cmd[0])
        if cmd[:3] == ["git", "status", "--porcelain"]:
            return 0, "", ""
        if cmd[:2] == ["git", "rev-parse"]:
            return 0, "origin/main", ""
        if cmd[:2] == ["git", "fetch"]:
            return 0, "", ""
        if cmd[:3] == ["git", "merge-base", "--is-ancestor"]:
            return 0, "", ""
        if cmd[:2] == ["git", "log"]:
            return 0, "abc1234 feat", ""
        if cmd[:3] == ["git", "pull", "--ff-only"]:
            return 0, "Fast-forward", ""
        if cmd[:2] == ["uv", "sync"]:
            return 1, "", "error: failed to resolve dependencies"
        return 0, "", ""

    res = await perform_self_update(
        confirmed=True,
        repo_dir=tmp_path,
        cmd_runner=fake_runner,
    )
    assert not res.ok
    assert not res.restarted
    assert "uv sync" in res.message
    assert "NOT restarted" in res.message
    assert "systemctl" not in called


@pytest.mark.asyncio
async def test_restart_failure_reports_error(tmp_path):
    async def fake_runner(cmd, cwd=None, env=None):
        if cmd[:3] == ["git", "status", "--porcelain"]:
            return 0, "", ""
        if cmd[:2] == ["git", "rev-parse"]:
            return 0, "origin/main", ""
        if cmd[:2] == ["git", "fetch"]:
            return 0, "", ""
        if cmd[:3] == ["git", "merge-base", "--is-ancestor"]:
            return 0, "", ""
        if cmd[:2] == ["git", "log"]:
            return 0, "abc1234 feat", ""
        if cmd[:3] == ["git", "pull", "--ff-only"]:
            return 0, "Fast-forward", ""
        if cmd[:2] == ["uv", "sync"]:
            return 0, "Resolved 5 packages", ""
        if cmd[:5] == ["systemctl", "--user", "restart", "--no-block", "decafclaw"]:
            return 1, "", "Failed to connect to bus"
        return 0, "", ""

    res = await perform_self_update(
        confirmed=True,
        repo_dir=tmp_path,
        cmd_runner=fake_runner,
    )
    assert not res.ok
    assert not res.restarted
    assert "service restart failed" in res.message


@pytest.mark.asyncio
async def test_successful_update_and_restart(tmp_path):
    executed = []

    async def fake_runner(cmd, cwd=None, env=None):
        executed.append(cmd)
        if cmd[:3] == ["git", "status", "--porcelain"]:
            return 0, "", ""
        if cmd[:2] == ["git", "rev-parse"]:
            return 0, "origin/main", ""
        if cmd[:2] == ["git", "fetch"]:
            return 0, "", ""
        if cmd[:3] == ["git", "merge-base", "--is-ancestor"]:
            return 0, "", ""
        if cmd[:2] == ["git", "log"]:
            return 0, "abc1234 feat", ""
        if cmd[:3] == ["git", "pull", "--ff-only"]:
            return 0, "Fast-forward", ""
        if cmd[:2] == ["uv", "sync"]:
            return 0, "Resolved 5 packages", ""
        if cmd[:5] == ["systemctl", "--user", "restart", "--no-block", "decafclaw"]:
            return 0, "", ""
        return 0, "", ""

    res = await perform_self_update(
        confirmed=True,
        repo_dir=tmp_path,
        cmd_runner=fake_runner,
    )
    assert res.ok
    assert res.restarted
    assert "Restarting DecafClaw service" in res.message
    assert ["git", "pull", "--ff-only"] in executed
    assert ["uv", "sync"] in executed
    assert ["systemctl", "--user", "restart", "--no-block", "decafclaw"] in executed
