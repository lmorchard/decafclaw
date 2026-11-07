#!/usr/bin/env python3
"""Stop a worktree dev server via its PIDfile (#1208).

`make stop-dev` backs the stop path of `make run-dev`: it reads the PIDfile
the launcher wrote and SIGTERMs exactly that process. Missing file or dead
PID -> clear no-op message, never a port scan.
"""

from __future__ import annotations

import argparse
import os
import signal
import time
from pathlib import Path

DEFAULT_DATA_DIR_NAME = "data-dev"
PIDFILE_NAME = ".pid"
DEFAULT_STOP_TIMEOUT_SEC = 10


def default_pidfile() -> Path:
    return Path(DEFAULT_DATA_DIR_NAME) / PIDFILE_NAME


def read_pid(pidfile: Path) -> int | None:
    try:
        text = pidfile.read_text(encoding="utf-8").strip()
        pid = int(text)
    except (OSError, ValueError):
        return None
    return pid if pid > 0 else None


def process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _signal_and_wait(pid: int, sig: int, timeout: float) -> bool:
    """Send ``sig`` to ``pid`` and wait up to ``timeout`` seconds for exit.

    Returns True when the process is no longer alive, False on timeout.
    """
    os.kill(pid, sig)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not process_alive(pid):
            return True
        time.sleep(0.05)
    return not process_alive(pid)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="stop_dev", description="Stop a worktree dev server via its PIDfile.")
    parser.add_argument("--pidfile", type=Path, default=None, help="PIDfile to read (default: data-dev/.pid).")
    parser.add_argument(
        "--stop-timeout", type=float, default=DEFAULT_STOP_TIMEOUT_SEC, help="SIGTERM grace period before SIGKILL."
    )
    args = parser.parse_args(argv)
    pidfile = args.pidfile if args.pidfile is not None else default_pidfile()

    pid = read_pid(pidfile)
    if pid is None:
        print(f"stop-dev: no pidfile at {pidfile} (or unreadable); nothing to stop.")
        return 0
    if not process_alive(pid):
        print(f"stop-dev: pid {pid} from {pidfile} is not running; nothing to stop.")
        return 0

    exited = _signal_and_wait(pid, signal.SIGTERM, args.stop_timeout)
    if not exited:
        _signal_and_wait(pid, signal.SIGKILL, args.stop_timeout)
        exited = not process_alive(pid)
    if exited:
        print(f"stop-dev: stopped dev server pid {pid} from {pidfile}.")
    else:
        print(f"stop-dev: failed to stop dev server pid {pid} from {pidfile}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
