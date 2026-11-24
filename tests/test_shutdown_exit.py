"""Process-exit guarantees for a completed graceful shutdown (#1799).

``runner.run_all`` can finish while asyncio's own teardown still waits on a
worker thread stuck in a sync tool call. These tests cover the bounded exit:
diagnostics, the watchdog, and the real subprocess proof that a blocked
non-daemon thread no longer keeps the daemon alive.
"""

import asyncio
import logging
import subprocess
import sys
import textwrap
import threading
import time

import pytest

import decafclaw.runner as runner


def test_exit_process_flushes_logging_then_exits(monkeypatch):
    calls = []

    monkeypatch.setattr(runner.os, "_exit", lambda code: calls.append(code))
    monkeypatch.setattr(runner.logging, "shutdown", lambda: calls.append("logging"))
    monkeypatch.setattr(runner, "_flush_telemetry", lambda: calls.append("telemetry"))

    runner.exit_process("unit test", 3)

    assert calls == ["telemetry", "logging", 3]


def test_watchdog_forces_exit_after_grace(monkeypatch, caplog):
    fired = threading.Event()
    monkeypatch.setattr(runner, "exit_process", lambda reason, code=0: fired.set())
    monkeypatch.setattr(runner, "log_lingering_work", lambda **kwargs: None)

    with caplog.at_level(logging.WARNING):
        runner.arm_shutdown_watchdog(grace=0.05)
        assert fired.wait(2.0), "watchdog did not force an exit"

    assert "forcing exit" in caplog.text


def test_process_exits_with_blocked_non_daemon_thread():
    """A completed shutdown must not wait on a stuck non-daemon thread.

    Interpreter exit joins every non-daemon thread, which is what kept the
    daemon alive after ``Shutdown complete``. Without the watchdog this script
    blocks until the test timeout.
    """
    script = textwrap.dedent(
        """
        import threading, time
        from decafclaw.runner import arm_shutdown_watchdog

        threading.Thread(target=threading.Event().wait, daemon=False).start()
        arm_shutdown_watchdog(grace=0.2)
        time.sleep(60)
        """
    )

    started = time.monotonic()
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0
    assert time.monotonic() - started < 5
    assert "forcing exit" in result.stderr


@pytest.mark.asyncio
async def test_log_lingering_work_reports_pending_task(caplog):
    gate = asyncio.Event()

    async def stubborn():
        await gate.wait()

    task = asyncio.create_task(stubborn(), name="stubborn-test-task")
    await asyncio.sleep(0)

    try:
        with caplog.at_level(logging.WARNING):
            runner.log_lingering_work()
        assert "stubborn-test-task" in caplog.text
        assert "1 asyncio task(s) still pending" in caplog.text
    finally:
        gate.set()
        await task


@pytest.mark.asyncio
async def test_log_lingering_work_quiet_when_nothing_pending(caplog):
    with caplog.at_level(logging.WARNING):
        runner.log_lingering_work()
    assert "still pending" not in caplog.text
    assert "still alive" not in caplog.text


def test_log_lingering_work_reports_threads_only_when_requested(caplog):
    gate = threading.Event()
    thread = threading.Thread(target=gate.wait, daemon=False, name="blocked-test-thread")
    thread.start()
    try:
        with caplog.at_level(logging.WARNING):
            runner.log_lingering_work()
        assert "blocked-test-thread" not in caplog.text

        caplog.clear()
        with caplog.at_level(logging.WARNING):
            runner.log_lingering_work(include_threads=True)
        assert "blocked-test-thread" in caplog.text
        assert "Non-daemon thread stacks" in caplog.text
    finally:
        gate.set()
        thread.join(2.0)
