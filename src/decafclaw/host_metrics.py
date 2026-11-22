"""Host capacity gauges for the VM DecafClaw runs on (VM 302).

The agent spawns builds, test suites, subagent CLIs, and background shell jobs
on the same 4 vCPU / 3.85 GB box that runs this process. When one of those goes
sideways the symptom is visible on the host minutes before it is visible in the
conversation: available memory drains, swap fills, load climbs past the core
count. This module exposes that view.

Deliberately dependency-free: ``psutil`` is not installed, and a compiled
dependency for four numbers on a memory-constrained host is a poor trade. The
kernel already publishes everything needed in ``/proc`` plus ``statvfs``.

CPU is the one metric ``/proc`` cannot answer in a single read. ``/proc/stat``
carries since-boot counters, so a per-request read yields a lifetime average,
which is useless as a gauge — on a box up 8 hours it would read the same
number forever. A sampler therefore owns the previous counters and reports the
delta, which is why this module has a lifespan and an in-process singleton
rather than being a pure function like ``tools/health.py``.

Fail-open by design: a gauge that raises must not take a page load with it.
Any unreadable source degrades its own field to ``None``.
"""

import asyncio
import logging
import os
import time
from collections import deque

log = logging.getLogger(__name__)

# 2s matches a human glance at a strip without spending measurable CPU on a
# 4-core box; 90 samples is three minutes of history for the detail panel.
DEFAULT_INTERVAL = 2.0
DEFAULT_HISTORY = 90

_MEMINFO_PATH = "/proc/meminfo"
_STAT_PATH = "/proc/stat"
_LOADAVG_PATH = "/proc/loadavg"
_UPTIME_PATH = "/proc/uptime"
_PROC_DIR = "/proc"
_ROOT_PATH = "/"
# tmpfs is RAM-backed, so a full /tmp is a memory problem wearing a disk
# costume. VM 302 mounts a 4 GB tmpfs there and agent scratch work lands in it.
_TMPFS_PATH = "/tmp"


def _read_text(path: str) -> str | None:
    """Read a /proc file, returning None instead of raising."""
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except OSError as exc:
        log.debug("host_metrics: cannot read %s: %s", path, exc)
        return None


def read_cpu_times() -> tuple[int, int] | None:
    """Return (idle_ticks, total_ticks) from the aggregate ``cpu`` line.

    Idle counts iowait as idle — the CPU is not executing anyone's work while
    it waits on the disk, and on a host that thrashes into swap that is
    exactly the stall an operator cares about, not something to hide inside
    "busy".
    """
    text = _read_text(_STAT_PATH)
    if not text:
        return None
    for line in text.splitlines():
        if not line.startswith("cpu "):
            continue
        fields = line.split()[1:]
        try:
            ticks = [int(value) for value in fields]
        except ValueError:
            return None
        if len(ticks) < 4:
            return None
        idle = ticks[3] + (ticks[4] if len(ticks) > 4 else 0)
        return idle, sum(ticks)
    return None


def read_cpu_count() -> int | None:
    """Logical CPU count — the denominator that makes load average meaningful."""
    try:
        return os.cpu_count()
    except OSError:
        return None


def read_memory() -> dict | None:
    """Return memory and swap totals in bytes, from ``/proc/meminfo``.

    ``used`` is derived from ``MemAvailable``, not ``MemFree``: page cache is
    reclaimable, so MemFree reads alarmingly low on a healthy box and would
    teach the reader to ignore the gauge.
    """
    text = _read_text(_MEMINFO_PATH)
    if not text:
        return None

    values: dict[str, int] = {}
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        parts = rest.split()
        if not parts:
            continue
        try:
            values[key] = int(parts[0]) * 1024  # kB -> bytes
        except ValueError:
            continue

    total = values.get("MemTotal")
    available = values.get("MemAvailable")
    if total is None or available is None:
        return None

    used = max(0, total - available)
    swap_total = values.get("SwapTotal", 0)
    swap_free = values.get("SwapFree", 0)
    swap_used = max(0, swap_total - swap_free)

    return {
        "total_bytes": total,
        "available_bytes": available,
        "used_bytes": used,
        "used_percent": round(used / total * 100, 1) if total else 0.0,
        "swap_total_bytes": swap_total,
        "swap_used_bytes": swap_used,
        # Zero-swap hosts must read 0%, not divide by zero.
        "swap_used_percent": round(swap_used / swap_total * 100, 1) if swap_total else 0.0,
    }


def read_load() -> dict | None:
    """Return 1/5/15 minute load averages and the runnable/total task counts.

    The task counts are optional in the response model and are only parsed
    when present, so a shape surprise in the fourth field costs one detail
    rather than the whole gauge.
    """
    text = _read_text(_LOADAVG_PATH)
    if not text:
        return None
    parts = text.split()
    if len(parts) < 3:
        return None
    try:
        one, five, fifteen = (float(parts[0]), float(parts[1]), float(parts[2]))
    except ValueError:
        return None

    running = total_tasks = None
    tasks = parts[3].split("/") if len(parts) > 3 else []
    if len(tasks) == 2:
        try:
            running, total_tasks = int(tasks[0]), int(tasks[1])
        except ValueError:
            pass

    return {
        "one": one,
        "five": five,
        "fifteen": fifteen,
        "running": running,
        "total": total_tasks,
    }


def read_uptime() -> float | None:
    text = _read_text(_UPTIME_PATH)
    if not text:
        return None
    parts = text.split()
    if not parts:
        return None
    try:
        return round(float(parts[0]), 1)
    except ValueError:
        return None


def read_disk(path: str) -> dict | None:
    """Return capacity for one filesystem, using df's arithmetic.

    ``f_bavail`` (not ``f_bfree``) is the space an unprivileged process can
    actually claim, which is what a build about to fail cares about.
    """
    try:
        stat = os.statvfs(path)
    except OSError as exc:
        log.debug("host_metrics: statvfs failed for %s: %s", path, exc)
        return None

    block = stat.f_frsize or stat.f_bsize
    if not block:
        return None
    total = stat.f_blocks * block
    free = stat.f_bfree * block
    available = stat.f_bavail * block
    used = max(0, total - free)
    denominator = used + available
    return {
        "path": path,
        "total_bytes": total,
        "used_bytes": used,
        "available_bytes": available,
        "used_percent": round(used / denominator * 100, 1) if denominator else 0.0,
    }


def read_process_count() -> int | None:
    """Count entries in /proc that are numeric — a cheap process census.

    This is the number that makes agent litter visible: shell jobs, subagent
    CLIs, and language servers are all rows here.
    """
    try:
        entries = os.listdir(_PROC_DIR)
    except OSError as exc:
        log.debug("host_metrics: cannot list %s: %s", _PROC_DIR, exc)
        return None
    return sum(1 for name in entries if name.isdigit())


def _safe(gather, label: str):
    """Run one source, returning None on any failure.

    The individual readers already swallow OSError from /proc, but a malformed
    file can surface as ValueError/IndexError and a mocked or patched source
    can raise anything at all. A gauge is not worth a 500: the field degrades
    to None and the rest of the snapshot renders.
    """
    try:
        return gather()
    except Exception:
        log.exception("host_metrics: %s gauge failed", label)
        return None


def collect_static() -> dict:
    """Gather the metrics that need no delta between samples."""
    return {
        "timestamp": time.time(),
        "cpu_count": _safe(read_cpu_count, "cpu_count"),
        "memory": _safe(read_memory, "memory"),
        "load": _safe(read_load, "load"),
        "disk_root": _safe(lambda: read_disk(_ROOT_PATH), "disk_root"),
        "disk_tmpfs": _safe(lambda: read_disk(_TMPFS_PATH), "disk_tmpfs"),
        "process_count": _safe(read_process_count, "process_count"),
        "uptime_seconds": _safe(read_uptime, "uptime"),
    }


def compute_cpu_percent(previous: tuple[int, int] | None, current: tuple[int, int] | None) -> float | None:
    """Turn two ``/proc/stat`` readings into a busy percentage.

    Returns None when there is no usable delta — a first sample, a counter
    that did not move, or a source that vanished. None is honest here;
    rendering a fabricated 0% would read as a calm host.
    """
    if previous is None or current is None:
        return None
    prev_idle, prev_total = previous
    idle, total = current
    total_delta = total - prev_total
    if total_delta <= 0:
        return None
    idle_delta = max(0, idle - prev_idle)
    busy_delta = max(0, total_delta - idle_delta)
    return round(min(100.0, busy_delta / total_delta * 100), 1)


class HostSampler:
    """Owns the previous CPU counters and a short history of readings.

    Not started automatically on import: the HTTP server starts it, and tests
    drive :meth:`sample_once` directly so nothing depends on wall-clock ticks.
    """

    def __init__(self, interval: float = DEFAULT_INTERVAL, history: int = DEFAULT_HISTORY) -> None:
        self.interval = interval
        self._previous_cpu: tuple[int, int] | None = None
        self._latest: dict | None = None
        self._history: deque[dict] = deque(maxlen=history)
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    def sample_once(self) -> dict:
        """Take one reading, advancing the CPU delta. Never raises."""
        current_cpu = read_cpu_times()
        cpu_percent = compute_cpu_percent(self._previous_cpu, current_cpu)
        if current_cpu is not None:
            self._previous_cpu = current_cpu

        snapshot = collect_static()
        snapshot["cpu_percent"] = cpu_percent

        self._latest = snapshot
        # The panel (not yet built) reads this for sparklines; the strip only
        # needs _latest, so the deque costs a few hundred bytes and no payload.
        self._history.append({"timestamp": snapshot["timestamp"], "cpu_percent": cpu_percent})
        return snapshot

    def snapshot(self) -> dict:
        """Return the most recent reading, sampling on demand if none exists.

        A cold call (endpoint hit before the loop ticked) still returns real
        data, with ``cpu_percent`` None until a second sample exists.
        """
        if self._latest is None:
            return self.sample_once()
        # Refresh the cheap host-wide fields but keep the last CPU reading:
        # sampling here would reset the delta window and shrink it to the
        # request rate, which is both noisy and wrong.
        fresh = collect_static()
        snapshot = dict(self._latest)
        snapshot.update(fresh)
        snapshot["cpu_percent"] = self._latest.get("cpu_percent")
        return snapshot

    def history(self) -> list[dict]:
        return list(self._history)

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.sample_once()
            except Exception:  # fail-open: a gauge must not kill the loop
                log.exception("host_metrics: sample failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval)
            except TimeoutError:
                continue

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._stop.set()
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


_sampler: HostSampler | None = None


def get_sampler() -> HostSampler:
    """Return the process-wide sampler, creating it on first use."""
    global _sampler
    if _sampler is None:
        _sampler = HostSampler()
    return _sampler


def reset_sampler() -> None:
    """Drop the singleton — test hook so each test gets a clean delta window."""
    global _sampler
    _sampler = None
