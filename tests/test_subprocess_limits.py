"""Tests for decafclaw.subprocess_limits (memory caps for subagent child processes).

No real systemd-run is ever executed: the probe is limited to shutil.which and
the cgroup.controllers read, both of which are patched here.
"""

import logging

import pytest

import decafclaw.subprocess_limits as subprocess_limits
from decafclaw.subprocess_limits import describe_kill, memory_cap_prefix, resource_cap_prefix

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
    # A cgroup's memory.swap.max defaults to "max", which would let a capped
    # child spill into swap instead of dying. Any MemoryMax therefore implies
    # MemorySwapMax=0 unless the caller overrides it.
    "-p",
    "MemorySwapMax=0",
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
    return [r for r in caplog.records if r.levelno == logging.WARNING and "Resource cap requested" in r.getMessage()]


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
        "-p",
        "MemorySwapMax=0",
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


def test_explicit_swap_max_overrides_the_guard(monkeypatch):
    _allow_systemd_run(monkeypatch)
    _set_delegation(monkeypatch, True)

    prefix = memory_cap_prefix("300M", "", memory_swap_max="256M")

    assert "MemorySwapMax=256M" in prefix
    assert "MemorySwapMax=0" not in prefix


def test_no_swap_guard_without_a_memory_max(monkeypatch):
    # MemoryHigh alone does not trip the OOM killer, so there is nothing to
    # guard against and adding the property would be noise.
    _allow_systemd_run(monkeypatch)
    _set_delegation(monkeypatch, True)

    assert "MemorySwapMax=0" not in memory_cap_prefix("", "25%")


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


# --- resource_cap_prefix: the shell-command cap path ------------------------


def _set_controllers(monkeypatch, controllers: str):
    """Pretend the user manager delegates exactly these controllers."""
    monkeypatch.setattr(subprocess_limits, "_user_manager_controllers_path", lambda: _FakePath(controllers))


class _FakePath:
    def __init__(self, text: str):
        self._text = text

    def read_text(self):
        return self._text


def _all_delegated(monkeypatch):
    _allow_systemd_run(monkeypatch)
    _set_controllers(monkeypatch, "cpu memory pids\n")


def test_resource_prefix_carries_every_requested_limit(monkeypatch):
    _all_delegated(monkeypatch)

    assert resource_cap_prefix(memory_max="40%", memory_high="", cpu_weight=50, tasks_max=512, label="shell") == [
        "systemd-run",
        "--user",
        "--scope",
        "--quiet",
        "-p",
        "MemoryMax=40%",
        "-p",
        "MemorySwapMax=0",
        "-p",
        "CPUWeight=50",
        "-p",
        "TasksMax=512",
        "--",
    ]


def test_resource_prefix_empty_when_nothing_requested(caplog):
    # Every knob unset means the caller wants no cap; that is not a missing
    # capability, so nothing is logged either.
    with caplog.at_level(logging.WARNING, logger="decafclaw.subprocess_limits"):
        assert resource_cap_prefix() == []

    assert caplog.records == []


def test_resource_prefix_drops_only_undelegated_limits(monkeypatch, caplog):
    # cpu and pids delegated, memory not: still cap fork bombs, just not memory.
    _allow_systemd_run(monkeypatch)
    _set_controllers(monkeypatch, "cpu pids\n")

    with caplog.at_level(logging.WARNING, logger="decafclaw.subprocess_limits"):
        prefix = resource_cap_prefix(memory_max="40%", cpu_weight=50, tasks_max=512, label="shell")

    assert prefix == [
        "systemd-run",
        "--user",
        "--scope",
        "--quiet",
        "-p",
        "CPUWeight=50",
        "-p",
        "TasksMax=512",
        "--",
    ]
    # "MemoryMax" must not appear anywhere in the prefix.
    assert not any("Memory" in part for part in prefix)
    # The gap is disclosed, not silent.
    assert any("memory" in r.getMessage() for r in caplog.records)


def test_resource_prefix_empty_when_no_requested_controller_delegated(monkeypatch, caplog):
    _allow_systemd_run(monkeypatch)
    _set_controllers(monkeypatch, "cpu\n")

    with caplog.at_level(logging.WARNING, logger="decafclaw.subprocess_limits"):
        prefix = resource_cap_prefix(memory_max="40%", tasks_max=512, label="shell")

    assert prefix == []
    warnings = [r for r in caplog.records if "not delegated" in r.getMessage()]
    assert len(warnings) == 1


def test_resource_prefix_empty_when_systemd_run_missing(monkeypatch, caplog):
    monkeypatch.setattr(subprocess_limits.shutil, "which", lambda name: None)

    with caplog.at_level(logging.WARNING, logger="decafclaw.subprocess_limits"):
        assert resource_cap_prefix(memory_max="40%", cpu_weight=50, tasks_max=512) == []

    assert len([r for r in caplog.records if "systemd-run is not on PATH" in r.getMessage()]) == 1


def test_resource_prefix_explicit_swap_max(monkeypatch):
    _all_delegated(monkeypatch)

    prefix = resource_cap_prefix(memory_max="1G", memory_swap_max="512M")

    assert "MemorySwapMax=512M" in prefix
    assert "MemorySwapMax=0" not in prefix


def test_resource_prefix_none_disables_individual_limits(monkeypatch):
    _all_delegated(monkeypatch)

    prefix = resource_cap_prefix(memory_max="40%", cpu_weight=None, tasks_max=None)

    assert prefix == [
        "systemd-run",
        "--user",
        "--scope",
        "--quiet",
        "-p",
        "MemoryMax=40%",
        "-p",
        "MemorySwapMax=0",
        "--",
    ]


def test_reset_caches_reprobes_delegation(monkeypatch):
    _allow_systemd_run(monkeypatch)
    _set_controllers(monkeypatch, "cpu\n")
    assert resource_cap_prefix(memory_max="40%") == []

    # Host gains memory delegation; without a reset the cached probe hides it.
    _set_controllers(monkeypatch, "cpu memory pids\n")
    assert resource_cap_prefix(memory_max="40%") == []

    subprocess_limits.reset_caches()
    assert "MemoryMax=40%" in resource_cap_prefix(memory_max="40%")


# --- describe_kill ---------------------------------------------------------


@pytest.mark.parametrize("rc", [-9, 137])
def test_describe_kill_reports_oom(rc):
    note = describe_kill(rc)
    assert note is not None
    assert "memory cap" in note


@pytest.mark.parametrize("rc", [-24, 152])
def test_describe_kill_reports_cpu_limit(rc):
    note = describe_kill(rc)
    assert note is not None
    assert "CPU time limit" in note


@pytest.mark.parametrize("rc", [-15, 143])
def test_describe_kill_treats_sigterm_as_possible_cap_kill(rc):
    # OOM inside a scope usually kills a grandchild, so systemd's OOMPolicy=stop
    # leaves the waited-on process with SIGTERM. The note must hedge rather than
    # assert, since a deliberate kill looks identical from here.
    note = describe_kill(rc)
    assert note is not None
    assert "may have" in note
    assert "journalctl" in note


@pytest.mark.parametrize("rc", [0, 1, 2, 127, None])
def test_describe_kill_silent_for_ordinary_exits(rc):
    assert describe_kill(rc) is None
