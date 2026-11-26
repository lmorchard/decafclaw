"""Native SSH tool tests (#1750)."""

from pathlib import Path

import pytest

from decafclaw.media import EndTurnConfirm
from decafclaw.tools import ssh_tools


def test_ssh_config_hosts_reads_concrete_aliases(tmp_path):
    config = tmp_path / "config"
    config.write_text("# comment\nHost sandbox edge-*\n  HostName 10.0.0.1\nHost 192.168.1.5\nHost !blocked alias2\n")
    assert ssh_tools._ssh_config_hosts(config) == {"sandbox", "192.168.1.5", "alias2"}


def test_host_allowed_rejects_raw_ip_and_unknown():
    assert ssh_tools._host_allowed("sandbox", {"sandbox"}) is True
    assert ssh_tools._host_allowed("192.168.1.5", {"192.168.1.5"}) is False
    assert ssh_tools._host_allowed("unknown", {"sandbox"}) is False


@pytest.mark.asyncio
async def test_unlisted_host_returns_confirmation(ctx, monkeypatch):
    monkeypatch.setattr(ssh_tools, "_ssh_config_hosts", lambda: set())
    result = await ssh_tools.tool_ssh_exec(ctx, "raw-host", "uptime")
    assert isinstance(result.end_turn, EndTurnConfirm)
    assert "not a concrete alias" in result.text
    assert result.data["error"] == "unlisted_host"


@pytest.mark.asyncio
async def test_exec_runs_and_returns_structured_output(ctx, monkeypatch):
    monkeypatch.setattr(ssh_tools, "_ssh_config_hosts", lambda: {"sandbox"})
    calls = []

    async def fake_run(args, timeout):
        calls.append((args, timeout))
        return 0, "hello\n", "", False

    monkeypatch.setattr(ssh_tools, "_run_process", fake_run)
    result = await ssh_tools.tool_ssh_exec(ctx, "sandbox", "echo hello")
    assert result.data == {"host": "sandbox", "exit_code": 0, "stdout": "hello\n", "stderr": ""}
    assert "hello" in result.text
    assert calls[0][0][:5] == ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5"]


@pytest.mark.asyncio
async def test_exec_nonzero_and_timeout(ctx, monkeypatch):
    monkeypatch.setattr(ssh_tools, "_ssh_config_hosts", lambda: {"sandbox"})

    async def failing(args, timeout):
        return 2, "", "permission denied", False

    monkeypatch.setattr(ssh_tools, "_run_process", failing)
    failed = await ssh_tools.tool_ssh_exec(ctx, "sandbox", "false")
    assert failed.data["exit_code"] == 2
    assert "[error: ssh exited 2" in failed.text

    async def slow(args, timeout):
        return None, "", "timed out after 1s", True

    monkeypatch.setattr(ssh_tools, "_run_process", slow)
    timed = await ssh_tools.tool_ssh_exec(ctx, "sandbox", "sleep 10", timeout=1)
    assert timed.data["timed_out"] is True
    assert "timed out" in timed.text


@pytest.mark.asyncio
async def test_copy_requires_workspace_local_path(ctx, monkeypatch):
    monkeypatch.setattr(ssh_tools, "_ssh_config_hosts", lambda: {"sandbox"})
    result = await ssh_tools.tool_ssh_copy(ctx, "sandbox", "/etc/passwd", "/tmp/passwd")
    assert "inside the workspace" in result.text


@pytest.mark.asyncio
async def test_copy_up_and_down(ctx, monkeypatch):
    monkeypatch.setattr(ssh_tools, "_ssh_config_hosts", lambda: {"sandbox"})
    local = Path(ctx.config.workspace_path) / "file.txt"
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_text("x")
    calls = []

    async def fake_run(args, timeout):
        calls.append(args)
        return 0, "", "", False

    monkeypatch.setattr(ssh_tools, "_run_process", fake_run)
    up = await ssh_tools.tool_ssh_copy(ctx, "sandbox", "file.txt", "/tmp/file.txt", "up")
    assert up.data["direction"] == "up"
    assert calls[0][-2].endswith("file.txt")
    assert calls[0][-1] == "sandbox:/tmp/file.txt"

    down = await ssh_tools.tool_ssh_copy(ctx, "sandbox", "file.txt", "/tmp/file.txt", "down")
    assert down.data["direction"] == "down"
    assert calls[1][-2] == "sandbox:/tmp/file.txt"
