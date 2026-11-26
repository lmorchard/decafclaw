"""Tests for the VM 302 host capacity gauges.

The parsers read real /proc files, so the module is exercised both against
synthetic fixtures (for the arithmetic that matters) and against this host
(for the shape of the result). The CPU delta gets the most attention: it is
the one metric where a plausible-looking wrong answer — a since-boot average
presented as a live gauge — would be silently believable.
"""

import asyncio
import sys

import pytest

from decafclaw import host_metrics
from decafclaw.host_metrics import (
    HostSampler,
    collect_static,
    compute_cpu_percent,
    get_sampler,
    read_cpu_times,
    read_disk,
    read_load,
    read_memory,
    read_process_count,
    read_uptime,
    reset_sampler,
)

# The host-facing tests read /proc; macOS has no /proc and no memory gauge.
requires_linux_proc = pytest.mark.skipif(
    sys.platform != "linux", reason="host metrics read /proc; this host has no /proc"
)


@pytest.fixture(autouse=True)
def clean_singleton():
    """Each test gets its own sampler so delta windows never leak across tests."""
    reset_sampler()
    yield
    reset_sampler()


# -- synthetic /proc fixtures -------------------------------------------------


def _patch_read(monkeypatch, files: dict[str, str]) -> None:
    """Route _read_text through a dict so parsers see controlled content."""

    def fake_read_text(path: str) -> str | None:
        return files.get(path)

    monkeypatch.setattr(host_metrics, "_read_text", fake_read_text)


def test_read_cpu_times_counts_iowait_as_idle(monkeypatch):
    """iowait is CPU-not-working; folding it into idle is the whole point.

    Hiding a swap-thrash stall inside 'busy' would misreport the exact failure
    mode these gauges exist to catch.
    """
    _patch_read(
        monkeypatch,
        {host_metrics._STAT_PATH: "cpu  100 0 100 700 100 0 0 0 0 0\ncpu0 50 0 50 350 50 0 0 0 0 0\n"},
    )
    idle, total = read_cpu_times()
    assert idle == 800  # 700 idle + 100 iowait
    assert total == 1000


def test_read_cpu_times_missing_file_returns_none(monkeypatch):
    _patch_read(monkeypatch, {})
    assert read_cpu_times() is None


def test_read_cpu_times_rejects_junk(monkeypatch):
    _patch_read(monkeypatch, {host_metrics._STAT_PATH: "cpu  a b c d\n"})
    assert read_cpu_times() is None


def test_compute_cpu_percent_basic():
    # 400 ticks elapsed, 300 of them idle -> 25% busy.
    assert compute_cpu_percent((500, 1000), (800, 1400)) == 25.0


def test_compute_cpu_percent_fully_busy():
    assert compute_cpu_percent((0, 0), (0, 100)) == 100.0


def test_compute_cpu_percent_fully_idle():
    assert compute_cpu_percent((0, 0), (100, 100)) == 0.0


def test_compute_cpu_percent_no_previous_sample_is_none():
    """A cold read must say 'unknown', not 0% — 0% reads as a calm host."""
    assert compute_cpu_percent(None, (800, 1400)) is None


def test_compute_cpu_percent_frozen_counters_is_none():
    """No delta means the counters did not move; a 0% guess would be a lie."""
    assert compute_cpu_percent((800, 1400), (800, 1400)) is None


def test_compute_cpu_percent_backwards_counters_is_none():
    """A counter that appears to go backwards cannot yield a sane percentage."""
    assert compute_cpu_percent((900, 1400), (800, 1300)) is None


def test_compute_cpu_percent_clamps_at_100():
    """Guards against tick accounting quirks producing an out-of-range gauge."""
    assert compute_cpu_percent((0, 0), (0, 10)) == 100.0


def test_read_memory_uses_memavailable_not_memfree(monkeypatch):
    """Page cache is reclaimable, so 'used' must come from MemAvailable.

    Reporting MemFree as free would show a healthy host at ~96% used and teach
    the reader to ignore the gauge.
    """
    _patch_read(
        monkeypatch,
        {
            host_metrics._MEMINFO_PATH: (
                "MemTotal:        4000000 kB\n"
                "MemFree:          100000 kB\n"
                "MemAvailable:    3000000 kB\n"
                "Buffers:          100000 kB\n"
                "Cached:          2000000 kB\n"
                "SwapTotal:       4000000 kB\n"
                "SwapFree:        1000000 kB\n"
            )
        },
    )
    mem = read_memory()
    assert mem["total_bytes"] == 4000000 * 1024
    assert mem["available_bytes"] == 3000000 * 1024
    assert mem["used_bytes"] == 1000000 * 1024
    assert mem["used_percent"] == 25.0
    assert mem["swap_used_bytes"] == 3000000 * 1024
    assert mem["swap_used_percent"] == 75.0


def test_read_memory_handles_zero_swap(monkeypatch):
    """Zero-swap hosts must read 0%, not raise on division by zero."""
    _patch_read(
        monkeypatch,
        {
            host_metrics._MEMINFO_PATH: (
                "MemTotal:        1000 kB\n"
                "MemAvailable:     500 kB\n"
                "SwapTotal:          0 kB\n"
                "SwapFree:           0 kB\n"
            )
        },
    )
    mem = read_memory()
    assert mem["swap_used_percent"] == 0.0
    assert mem["swap_used_bytes"] == 0


def test_read_memory_missing_memavailable_is_none(monkeypatch):
    _patch_read(monkeypatch, {host_metrics._MEMINFO_PATH: "MemTotal: 1000 kB\n"})
    assert read_memory() is None


def test_read_load_parses_averages_and_tasks(monkeypatch):
    _patch_read(monkeypatch, {host_metrics._LOADAVG_PATH: "0.51 0.62 0.40 2/252 25628\n"})
    load = read_load()
    assert load == {"one": 0.51, "five": 0.62, "fifteen": 0.40, "running": 2, "total": 252}


def test_read_load_without_task_counts(monkeypatch):
    """A truncated loadavg yields the averages with task counts unknown."""
    _patch_read(monkeypatch, {host_metrics._LOADAVG_PATH: "1.00 2.00 3.00\n"})
    load = read_load()
    assert load["one"] == 1.0
    assert load["running"] is None


def test_read_uptime(monkeypatch):
    _patch_read(monkeypatch, {host_metrics._UPTIME_PATH: "31204.55 90000.00\n"})
    assert read_uptime() == 31204.5  # round() to one decimal, banker's rounding


def test_read_disk_uses_bavail_for_available(monkeypatch):
    """f_bavail is what an unprivileged build can actually claim."""

    class FakeStat:
        f_frsize = 4096
        f_bsize = 4096
        f_blocks = 1000
        f_bfree = 400
        f_bavail = 300

    monkeypatch.setattr(host_metrics.os, "statvfs", lambda path: FakeStat())
    disk = read_disk("/")
    assert disk["total_bytes"] == 1000 * 4096
    assert disk["used_bytes"] == 600 * 4096
    assert disk["available_bytes"] == 300 * 4096
    # df arithmetic: used / (used + available), not used / total.
    assert disk["used_percent"] == 66.7


def test_read_disk_missing_path_returns_none(monkeypatch):
    def boom(path):
        raise OSError("nope")

    monkeypatch.setattr(host_metrics.os, "statvfs", boom)
    assert read_disk("/nonexistent") is None


def test_read_process_count_counts_numeric_entries(monkeypatch):
    monkeypatch.setattr(
        host_metrics.os,
        "listdir",
        lambda path: ["1", "42", "self", "cpuinfo", "meminfo", "1234"],
    )
    assert read_process_count() == 3


def test_read_process_count_unreadable_returns_none(monkeypatch):
    def boom(path):
        raise OSError("nope")

    monkeypatch.setattr(host_metrics.os, "listdir", boom)
    assert read_process_count() is None


# -- sampler behaviour --------------------------------------------------------


def test_sample_once_reports_no_cpu_percent_on_first_call():
    sampler = HostSampler()
    snapshot = sampler.sample_once()
    assert snapshot["cpu_percent"] is None
    assert snapshot["timestamp"] > 0


def test_sample_once_second_call_produces_cpu_percent(monkeypatch):
    """The delta window must span samples, which is why this is stateful."""
    readings = iter([(100, 1000), (200, 1200)])
    monkeypatch.setattr(host_metrics, "read_cpu_times", lambda: next(readings))

    sampler = HostSampler()
    assert sampler.sample_once()["cpu_percent"] is None
    # 100 idle of 200 total delta -> 50% busy.
    assert sampler.sample_once()["cpu_percent"] == 50.0


def test_snapshot_refreshes_host_fields_without_consuming_delta(monkeypatch):
    """Repeated polls must not shrink the CPU window to the poll rate.

    A snapshot() that resampled /proc/stat each call would measure only the
    sliver of time between requests and swing wildly.
    """
    calls = []

    def counting_read():
        calls.append(1)
        return (len(calls) * 100, len(calls) * 1000)

    monkeypatch.setattr(host_metrics, "read_cpu_times", counting_read)

    sampler = HostSampler()
    sampler.sample_once()
    first = sampler.snapshot()
    first_cpu = first["cpu_percent"]

    # Several polls should not advance the CPU counter at all.
    for _ in range(3):
        assert sampler.snapshot()["cpu_percent"] == first_cpu
    assert len(calls) == 1


@requires_linux_proc
def test_snapshot_without_prior_sample_takes_one():
    sampler = HostSampler()
    snapshot = sampler.snapshot()
    assert snapshot["cpu_percent"] is None
    assert snapshot["memory"] is not None


@requires_linux_proc
def test_sample_once_survives_a_failing_source(monkeypatch):
    """Fail-open: one unreadable source nulls its field, not the reading."""

    def boom():
        raise OSError("disk on fire")

    monkeypatch.setattr(host_metrics, "read_memory", boom)
    snapshot = HostSampler().sample_once()
    assert snapshot["memory"] is None
    assert snapshot["load"] is not None


def test_history_accumulates_and_is_bounded():
    sampler = HostSampler(history=3)
    for _ in range(5):
        sampler.sample_once()
    history = sampler.history()
    assert len(history) == 3
    assert set(history[0]) == {"timestamp", "cpu_percent"}


async def test_start_stop_lifecycle():
    sampler = HostSampler(interval=0.01)
    sampler.start()
    await asyncio.sleep(0.05)
    assert sampler.snapshot()["timestamp"] > 0
    assert len(sampler.history()) >= 1
    await sampler.stop()
    assert sampler._task is None


async def test_start_is_idempotent():
    sampler = HostSampler(interval=0.01)
    sampler.start()
    first_task = sampler._task
    sampler.start()
    assert sampler._task is first_task
    await sampler.stop()


async def test_stop_without_start_is_safe():
    sampler = HostSampler()
    await sampler.stop()


async def test_run_loop_survives_sample_exception(monkeypatch):
    """A gauge that raises must not kill the loop feeding every other gauge."""
    state = {"calls": 0}

    def flaky():
        state["calls"] += 1
        if state["calls"] == 1:
            raise OSError("transient")
        return {"ok": True}

    monkeypatch.setattr(host_metrics, "collect_static", flaky)
    sampler = HostSampler(interval=0.01)
    sampler.start()
    await asyncio.sleep(0.08)
    await sampler.stop()
    assert state["calls"] >= 2


# -- against the real host ----------------------------------------------------


@requires_linux_proc
def test_collect_static_on_this_host_returns_a_full_snapshot():
    snapshot = collect_static()
    assert snapshot["timestamp"] > 0
    assert isinstance(snapshot["cpu_count"], int)
    assert snapshot["cpu_count"] >= 1
    assert snapshot["memory"]["total_bytes"] > 0
    assert snapshot["load"]["one"] >= 0
    assert snapshot["disk_root"]["total_bytes"] > 0
    assert 0 <= snapshot["disk_root"]["used_percent"] <= 100
    assert snapshot["process_count"] >= 1


@requires_linux_proc
def test_memory_available_never_exceeds_total():
    mem = read_memory()
    assert mem["available_bytes"] <= mem["total_bytes"]
    assert mem["used_bytes"] >= 0


def test_singleton_is_shared_and_resettable():
    assert get_sampler() is get_sampler()
    first = get_sampler()
    reset_sampler()
    assert get_sampler() is not first
