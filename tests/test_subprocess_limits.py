"""Tests for decafclaw.subprocess_limits (memory caps for subagent child processes).

No real systemd-run is ever executed: the probe is limited to shutil.which and
the cgroup.controllers read, both of which are patched here.
"""

import logging

import pytest

import decafclaw.subprocess_limits as subprocess_limits
from decafclaw.subprocess_limits import memory_cap_prefix

FAKE_SYSTEMD_RUN = "/usr/bin/systemd-run"

EXPECTED_PREFIX = [
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


@pytest.fixture(autouse=True)
def _reset_module_state(monkeypatch):
    """Reset the process-lifetime cache and warning flag for every test."""
    monkeypatch.setattr(subprocess_limits, "_delegation_cache", None)
    monkeypatch.setattr(subprocess_limits, "_warned", False)


def _allow_systemd_run(monkeypatch):
    monkeypatch.setattr(
        subprocess_limits.shutil,
        "which",
        lambda name: FAKE_SYSTEMD_RUN if name == "systemd-run" else None,
    )


def _set_delegation(monkeypatch, delegated: bool):
    monkeypatch.setattr(subprocess_limits, "_memory_controller_delegated", lambda: delegated)


def _cap_warnings(caplog) -> list:
    return [r for r in caplog.records if r.levelno == logging.WARNING and "Memory cap requested" in r.getMessage()]


def test_prefix_contains_both_properties_when_supported(monkeypatch):
    _allow_systemd_run(monkeypatch)
    _set_delegation(monkeypatch, True)

    assert memory_cap_prefix("50%", "40%", label="opencode_send") == EXPECTED_PREFIX


def test_prefix_empty_when_systemd_run_missing(monkeypatch, caplog):
    monkeypatch.setattr(subprocess_limits.shutil, "which", lambda name: None)

    with caplog.at_level(logging.WARNING, logger="decafclaw.subprocess_limits"):
        prefix = memory_cap_prefix("50%", "40%", label="opencode_send")

    assert prefix == []
    warnings = _cap_warnings(caplog)
    assert len(warnings) == 1
    assert "systemd-run is not on PATH" in warnings[0].getMessage()


def test_prefix_empty_when_memory_controller_not_delegated(monkeypatch, caplog):
    _allow_systemd_run(monkeypatch)
    _set_delegation(monkeypatch, False)

    with caplog.at_level(logging.WARNING, logger="decafclaw.subprocess_limits"):
        prefix = memory_cap_prefix("50%", "40%", label="claude_code_send")

    assert prefix == []
    warnings = _cap_warnings(caplog)
    assert len(warnings) == 1
    assert "memory controller is not delegated" in warnings[0].getMessage()
    # The doc pointer names the deployment.md heading verbatim.
    assert "Subagent Memory Limits" in warnings[0].getMessage()


def test_prefix_empty_when_both_limits_empty(caplog):
    # Disabled cap returns before probing the host, and logs nothing.
    with caplog.at_level(logging.WARNING, logger="decafclaw.subprocess_limits"):
        prefix = memory_cap_prefix("", "", label="opencode_send")

    assert prefix == []
    assert _cap_warnings(caplog) == []


def test_single_limit_produces_only_that_property(monkeypatch):
    _allow_systemd_run(monkeypatch)
    _set_delegation(monkeypatch, True)

    assert memory_cap_prefix("300M", "") == [
        "systemd-run",
        "--user",
        "--scope",
        "--quiet",
        "-p",
        "MemoryMax=300M",
        "--",
    ]
    assert memory_cap_prefix("", "25%") == [
        "systemd-run",
        "--user",
        "--scope",
        "--quiet",
        "-p",
        "MemoryHigh=25%",
        "--",
    ]


def test_warning_logged_once_across_calls(monkeypatch, caplog):
    monkeypatch.setattr(subprocess_limits.shutil, "which", lambda name: None)

    with caplog.at_level(logging.WARNING, logger="decafclaw.subprocess_limits"):
        assert memory_cap_prefix("50%", "40%", label="opencode_send") == []
        assert memory_cap_prefix("50%", "40%", label="opencode_send") == []
        assert memory_cap_prefix("50%", "40%", label="claude_code_send") == []

    assert len(_cap_warnings(caplog)) == 1


def test_delegation_probe_is_cached(monkeypatch):
    class _CountingControllers:
        def __init__(self):
            self.reads = 0

        def read_text(self):
            self.reads += 1
            return "cpu memory pids\n"

    counting = _CountingControllers()
    _allow_systemd_run(monkeypatch)
    monkeypatch.setattr(subprocess_limits, "_user_manager_controllers_path", lambda: counting)

    assert memory_cap_prefix("50%", "40%") == EXPECTED_PREFIX
    assert memory_cap_prefix("50%", "40%") == EXPECTED_PREFIX

    assert counting.reads == 1


def test_delegation_probe_failure_counts_as_not_delegated(monkeypatch, caplog):
    _allow_systemd_run(monkeypatch)

    def _unreadable():
        raise OSError("no such cgroup")

    monkeypatch.setattr(subprocess_limits, "_user_manager_controllers_path", _unreadable)

    with caplog.at_level(logging.WARNING, logger="decafclaw.subprocess_limits"):
        prefix = memory_cap_prefix("50%", "40%", label="opencode_send")

    assert prefix == []
    assert len(_cap_warnings(caplog)) == 1
