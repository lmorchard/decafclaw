"""Tests for resource caps on agent-spawned shell commands.

The prefix builder is unit-tested in ``test_subprocess_limits.py``; this file
covers the wiring: that ``shell_cap_prefix`` reads ``config.shell``, that the
off switch is honoured without a warning, and that the spawn sites put the
prefix in front of an explicitly passed ``sh -c``.

That explicit ``sh -c`` matters. ``create_subprocess_shell`` supplies its own
``["-c", command]``; handing that to ``systemd-run`` would make systemd-run
parse the command as its own options, so every capped command would fail to
start.
"""

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import decafclaw.subprocess_limits as subprocess_limits
from decafclaw.tools.shell_tools import _execute_command, shell_cap_prefix


def _shell_config(**overrides):
    cfg = MagicMock()
    cfg.resource_limits_enabled = True
    cfg.memory_max = "40%"
    cfg.memory_high = ""
    cfg.memory_swap_max = "0"
    cfg.cpu_weight = 50
    cfg.tasks_max = 512
    for key, value in overrides.items():
        setattr(cfg, key, value)
    return cfg


def _config(**overrides):
    config = MagicMock()
    config.shell = _shell_config(**overrides)
    return config


@pytest.fixture(autouse=True)
def _no_real_systemd(monkeypatch):
    """Never touch real cgroups; pretend every controller is delegated."""
    monkeypatch.setattr(subprocess_limits.shutil, "which", lambda name: "/usr/bin/systemd-run")
    monkeypatch.setattr(subprocess_limits, "_delegation_cache", {"cpu", "memory", "pids"})
    monkeypatch.setattr(subprocess_limits, "_warned", False)


# -- shell_cap_prefix reads config.shell -----------------------------------


def test_prefix_reflects_configured_limits():
    prefix = shell_cap_prefix(_config(), label="shell")

    assert prefix[:5] == ["systemd-run", "--user", "--scope", "--quiet", "-p"]
    assert "MemoryMax=40%" in prefix
    assert "MemorySwapMax=0" in prefix
    assert "CPUWeight=50" in prefix
    assert "TasksMax=512" in prefix
    assert prefix[-1] == "--"


def test_disabled_switch_returns_no_prefix_and_logs_nothing(caplog):
    import logging

    with caplog.at_level(logging.WARNING, logger="decafclaw.subprocess_limits"):
        prefix = shell_cap_prefix(_config(resource_limits_enabled=False))

    assert prefix == []
    # An explicit disable is not a missing capability, so it must stay quiet.
    assert caplog.records == []


def test_missing_shell_section_is_treated_as_disabled():
    config = MagicMock(spec=[])  # no .shell attribute at all
    assert shell_cap_prefix(config) == []


def test_all_limits_blank_yields_no_prefix():
    prefix = shell_cap_prefix(_config(memory_max="", memory_high="", cpu_weight=None, tasks_max=None))
    assert prefix == []


def test_empty_string_limits_do_not_become_none_properties():
    # "" means "unset", not "-p MemoryMax=". A blank systemd property is a
    # startup error for the scope.
    prefix = shell_cap_prefix(
        _config(memory_max="", memory_high="", memory_swap_max="", cpu_weight=None, tasks_max=None)
    )
    assert prefix == []


# -- _execute_command spawn shape ------------------------------------------


@pytest.mark.asyncio
async def test_execute_command_prepends_prefix_before_explicit_sh_c(ctx, monkeypatch):
    captured = {}

    class _FakeProc:
        returncode = 0

        def __init__(self):
            self.stdout = None
            self.stderr = None

        async def wait(self):
            return 0

    async def _fake_exec(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return _FakeProc()

    monkeypatch.setattr("asyncio.create_subprocess_exec", _fake_exec)
    monkeypatch.setattr(
        "decafclaw.tools.shell_tools.shell_cap_prefix",
        lambda config, label="shell": [
            "systemd-run",
            "--",
        ],
    )

    await _execute_command(ctx, "echo hi")

    args = captured["args"]
    assert args[:2] == ("systemd-run", "--")
    # Command text is passed via DECAFCLAW_SHELL_CMD through a Python trampoline
    # so systemd's unit parser never sees it (systemd < 256 unescapes $$ to $).
    assert captured["kwargs"]["env"]["DECAFCLAW_SHELL_CMD"] == "echo hi"
    assert args[2] == sys.executable
    assert args[3] == "-c"
    assert "DECAFCLAW_SHELL_CMD" in args[4]


@pytest.mark.asyncio
async def test_execute_command_without_prefix_still_uses_sh_c(ctx, monkeypatch):
    captured = {}

    class _FakeProc:
        returncode = 0

        def __init__(self):
            self.stdout = None
            self.stderr = None

        async def wait(self):
            return 0

    async def _fake_exec(*args, **kwargs):
        captured["args"] = args
        return _FakeProc()

    monkeypatch.setattr("asyncio.create_subprocess_exec", _fake_exec)
    monkeypatch.setattr("decafclaw.tools.shell_tools.shell_cap_prefix", lambda config, label="shell": [])

    await _execute_command(ctx, "echo hi")

    # Uncapped hosts must behave exactly as before.
    assert captured["args"] == ("/bin/sh", "-c", "echo hi")


@pytest.mark.asyncio
async def test_execute_command_reports_cap_kill(ctx, monkeypatch):
    class _FakeProc:
        returncode = -9

        def __init__(self):
            self.stdout = None
            self.stderr = None

        async def wait(self):
            return -9

    async def _fake_exec(*args, **kwargs):
        return _FakeProc()

    monkeypatch.setattr("asyncio.create_subprocess_exec", _fake_exec)
    monkeypatch.setattr(
        "decafclaw.tools.shell_tools.shell_cap_prefix",
        lambda config, label="shell": ["systemd-run", "--"],
    )

    result = await _execute_command(ctx, "a-runaway")

    # A bare "-9" reads as a mystery crash; the cap must name itself.
    assert "memory cap" in result.text
    assert "OOM-killed" in result.text


@pytest.mark.asyncio
async def test_execute_command_no_cap_note_when_uncapped(ctx, monkeypatch):
    class _FakeProc:
        returncode = -9

        def __init__(self):
            self.stdout = None
            self.stderr = None

        async def wait(self):
            return -9

    async def _fake_exec(*args, **kwargs):
        return _FakeProc()

    monkeypatch.setattr("asyncio.create_subprocess_exec", _fake_exec)
    monkeypatch.setattr("decafclaw.tools.shell_tools.shell_cap_prefix", lambda config, label="shell": [])

    result = await _execute_command(ctx, "a-runaway")

    # No cap was applied, so the tool must not blame one.
    assert "memory cap" not in result.text
    assert "exit code: -9" in result.text


# -- background job manager --------------------------------------------------


@pytest.mark.asyncio
async def test_background_start_prepends_prefix_before_explicit_sh_c(monkeypatch):
    from decafclaw.skills.background.tools import BackgroundJobManager

    captured = {}

    class _FakeProc:
        pid = 4242
        returncode = None

        def __init__(self):
            self.stdout = None
            self.stderr = None

    async def _fake_exec(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return _FakeProc()

    monkeypatch.setattr("asyncio.create_subprocess_exec", _fake_exec)
    monkeypatch.setattr(
        "decafclaw.skills.background.tools.shell_cap_prefix",
        lambda config, label="shell": ["systemd-run", "--"],
    )

    # Cancel the reader task so its coroutine is awaited rather than garbage
    # collected un-run (the fake process never produces output to drain).
    real_create_task = __import__("asyncio").create_task

    def _create_task(coro, **kwargs):
        task = real_create_task(coro, **kwargs)
        task.cancel()
        return task

    monkeypatch.setattr("decafclaw.skills.background.tools.asyncio.create_task", _create_task)

    manager = BackgroundJobManager()
    await manager.start("echo hi", "/tmp", config=_config(), pin_sticky=False)

    args = captured["args"]
    assert args[:2] == ("systemd-run", "--")
    assert captured["kwargs"]["env"]["DECAFCLAW_SHELL_CMD"] == "echo hi"
    assert args[2] == sys.executable
    assert args[3] == "-c"
    assert "DECAFCLAW_SHELL_CMD" in args[4]


@pytest.mark.asyncio
async def test_background_notification_names_a_cap_kill(monkeypatch):
    from decafclaw.skills.background.tools import _notify_job_exit

    sent = {}

    class _Notifications:
        async def notify(self, config, event_bus, **kwargs):
            sent.update(kwargs)

    import decafclaw

    monkeypatch.setattr(decafclaw, "notifications", _Notifications(), raising=False)

    job = MagicMock()
    job.exit_code = -9
    job.command = "a-runaway"
    job.stderr_buffer = []
    job.conv_id = "conv"
    job.event_bus = None
    job.config = _config()

    await _notify_job_exit(job)

    assert "memory cap" in sent["body"]
