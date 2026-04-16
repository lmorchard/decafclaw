"""Tests for background process management — BackgroundJobManager lifecycle."""

import asyncio

import pytest

from decafclaw.skills.background.tools import BackgroundJobManager


@pytest.mark.asyncio
async def test_start_job(tmp_path):
    """Start a job and verify initial state."""
    manager = BackgroundJobManager()
    job = await manager.start("sleep 10", cwd=str(tmp_path))

    assert job.job_id
    assert len(job.job_id) == 12
    assert job.pid > 0
    assert job.status == "running"
    assert job.command == "sleep 10"
    assert job.exit_code is None

    await manager.cleanup_all()


@pytest.mark.asyncio
async def test_status_after_exit(tmp_path):
    """Job that exits normally shows completed status with output."""
    manager = BackgroundJobManager()
    job = await manager.start("echo hello", cwd=str(tmp_path))

    # Wait for process to finish
    await asyncio.sleep(0.5)

    assert job.status == "completed"
    assert job.exit_code == 0
    stdout = list(job.stdout_buffer)
    assert any("hello" in line for line in stdout)

    await manager.cleanup_all()


@pytest.mark.asyncio
async def test_status_error_exit(tmp_path):
    """Job with non-zero exit shows error status."""
    manager = BackgroundJobManager()
    job = await manager.start("exit 1", cwd=str(tmp_path))

    await asyncio.sleep(0.5)

    assert job.status == "error"
    assert job.exit_code == 1

    await manager.cleanup_all()


@pytest.mark.asyncio
async def test_stop_running_job(tmp_path):
    """Stop a running job."""
    manager = BackgroundJobManager()
    job = await manager.start("sleep 60", cwd=str(tmp_path))

    assert job.status == "running"

    stopped = await manager.stop(job.job_id)
    assert stopped is not None
    assert stopped.status != "running"

    await manager.cleanup_all()


@pytest.mark.asyncio
async def test_stop_nonexistent():
    """Stop returns None for unknown job_id."""
    manager = BackgroundJobManager()
    result = await manager.stop("nonexistent")
    assert result is None


@pytest.mark.asyncio
async def test_output_buffering(tmp_path):
    """Output buffer is capped at 500 lines."""
    manager = BackgroundJobManager()
    # Generate 600 lines of output
    job = await manager.start(
        "for i in $(seq 1 600); do echo line$i; done",
        cwd=str(tmp_path),
    )

    await asyncio.sleep(1.0)

    assert job.status == "completed"
    stdout = list(job.stdout_buffer)
    assert len(stdout) == 500
    # Should have the last 500 lines (line101 through line600)
    assert stdout[-1] == "line600"
    assert stdout[0] == "line101"  # first 100 lines evicted

    await manager.cleanup_all()


@pytest.mark.asyncio
async def test_cleanup_expired(tmp_path):
    """Expired jobs are killed during cleanup."""
    manager = BackgroundJobManager()
    job = await manager.start(
        "sleep 60", cwd=str(tmp_path), max_lifetime=0.1,
    )

    await asyncio.sleep(0.3)
    expired = await manager.cleanup_expired()

    assert len(expired) == 1
    assert expired[0].job_id == job.job_id
    assert expired[0].status == "expired"

    await manager.cleanup_all()


@pytest.mark.asyncio
async def test_cleanup_expired_ignores_finished(tmp_path):
    """Cleanup doesn't touch already-finished jobs."""
    manager = BackgroundJobManager()
    job = await manager.start("echo done", cwd=str(tmp_path), max_lifetime=0.1)

    await asyncio.sleep(0.5)
    assert job.status == "completed"

    expired = await manager.cleanup_expired()
    assert len(expired) == 0  # already completed, not expired

    await manager.cleanup_all()


@pytest.mark.asyncio
async def test_list_active(tmp_path):
    """List returns all tracked jobs."""
    manager = BackgroundJobManager()
    job1 = await manager.start("sleep 10", cwd=str(tmp_path))
    job2 = await manager.start("sleep 10", cwd=str(tmp_path))

    jobs = manager.list_jobs()
    assert len(jobs) == 2
    job_ids = {j.job_id for j in jobs}
    assert job1.job_id in job_ids
    assert job2.job_id in job_ids

    await manager.cleanup_all()


@pytest.mark.asyncio
async def test_get_job(tmp_path):
    """Get returns job by ID or None."""
    manager = BackgroundJobManager()
    job = await manager.start("sleep 10", cwd=str(tmp_path))

    assert manager.get(job.job_id) is job
    assert manager.get("nonexistent") is None

    await manager.cleanup_all()


@pytest.mark.asyncio
async def test_stderr_captured(tmp_path):
    """Stderr output is captured in stderr_buffer."""
    manager = BackgroundJobManager()
    job = await manager.start("echo error >&2", cwd=str(tmp_path))

    await asyncio.sleep(0.5)

    stderr = list(job.stderr_buffer)
    assert any("error" in line for line in stderr)

    await manager.cleanup_all()


@pytest.mark.asyncio
async def test_cleanup_all(tmp_path):
    """Cleanup all stops all running jobs."""
    manager = BackgroundJobManager()
    await manager.start("sleep 60", cwd=str(tmp_path))
    await manager.start("sleep 60", cwd=str(tmp_path))

    stopped = await manager.cleanup_all()
    assert len(stopped) == 2

    for job in stopped:
        assert job.status != "running"
