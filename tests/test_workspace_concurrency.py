"""Tests for workspace file concurrency and per-file locking.

Verifies:
- Concurrent edits to the same file serialize and avoid lost updates.
- All mutation tools participate in the same guard.
- Canonical path resolution and aliases share the same guard.
- Mutations to different files proceed concurrently without contention.
- Exceptions and cancellations cleanly release locks without leaking or overlapping.
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from decafclaw.media import ToolResult
from decafclaw.tools.file_locks import (
    FileLockManager,
    file_lock,
    get_canonical_path,
    is_file_locked,
)
from decafclaw.tools.workspace_tools import (
    tool_workspace_append,
    tool_workspace_copy,
    tool_workspace_delete,
    tool_workspace_edit,
    tool_workspace_insert,
    tool_workspace_move,
    tool_workspace_read,
    tool_workspace_replace_lines,
    tool_workspace_write,
)


def _text(result: str | ToolResult) -> str:
    return result.text if isinstance(result, ToolResult) else result


def test_concurrent_edits_both_survive(ctx):
    """Two concurrent edits to different parts of the same file both survive without lost updates.

    Uses event barrier coordination rather than fixed sleeps to prove that
    the second edit waits for the first edit's read-modify-write to complete,
    ensuring both edits are preserved.
    """
    initial_content = "line 1\nline 2\nline 3\nline 4\nline 5\n"
    tool_workspace_write(ctx, "target.txt", initial_content)

    t1_inside_write = threading.Event()
    t2_blocked = threading.Event()
    orig_write_text = Path.write_text

    def coordinating_write_text(self, *args, **kwargs):
        if self.name == "target.txt" and not t1_inside_write.is_set():
            t1_inside_write.set()
            # Wait for thread 2 to attempt lock acquisition and block
            assert t2_blocked.wait(timeout=5.0), "Thread 2 did not attempt mutation in time"
        return orig_write_text(self, *args, **kwargs)

    t1_result: list[str | ToolResult] = []
    t2_result: list[str | ToolResult] = []

    def worker1():
        # Insert at line 2
        res = tool_workspace_insert(ctx, "target.txt", line_number=2, content="inserted by t1\n")
        t1_result.append(res)

    def worker2():
        # Wait until worker 1 is inside its critical write section
        assert t1_inside_write.wait(timeout=5.0), "Thread 1 did not reach write section in time"
        # Confirm target.txt is locked by thread 1
        resolved = (ctx.config.workspace_path / "target.txt").resolve()
        assert is_file_locked(resolved), "Expected target.txt to be locked by thread 1"
        t2_blocked.set()
        # Thread 2 now attempts to insert at line 5 (in the original file's position)
        res = tool_workspace_insert(ctx, "target.txt", line_number=5, content="inserted by t2\n")
        t2_result.append(res)

    t1 = threading.Thread(target=worker1)
    t2 = threading.Thread(target=worker2)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(Path, "write_text", coordinating_write_text)
        t1.start()
        t2.start()
        t1.join(timeout=5.0)
        t2.join(timeout=5.0)

    assert not t1.is_alive(), "Thread 1 did not finish"
    assert not t2.is_alive(), "Thread 2 did not finish"

    # Both edits should have completed successfully
    assert "error" not in _text(t1_result[0]).lower()
    assert "error" not in _text(t2_result[0]).lower()

    # Read back content and verify BOTH insertions are present
    content = _text(tool_workspace_read(ctx, "target.txt"))
    assert "inserted by t1" in content
    assert "inserted by t2" in content


def test_participating_mutation_tools_acquire_lock(ctx):
    """Write, append, insert, replace_lines, edit, delete, move, and copy participate in the same guard."""
    workspace = ctx.config.workspace_path.resolve()
    target_path = workspace / "guarded.txt"
    dest_path = workspace / "dest.txt"
    dest2_path = workspace / "dest2.txt"

    tool_workspace_write(ctx, "guarded.txt", "line1\nline2\nline3\n")

    locked_during_write = False
    orig_write = Path.write_text

    def check_write_lock(self, *args, **kwargs):
        nonlocal locked_during_write
        if self.resolve() == target_path:
            locked_during_write = is_file_locked(target_path)
        return orig_write(self, *args, **kwargs)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(Path, "write_text", check_write_lock)

        # 1. Full write
        locked_during_write = False
        tool_workspace_write(ctx, "guarded.txt", "new content\n")
        assert locked_during_write, "tool_workspace_write did not hold lock during write"

        # 2. Append
        locked_during_write = False
        tool_workspace_append(ctx, "guarded.txt", "appended\n")
        assert locked_during_write, "tool_workspace_append did not hold lock during write"

        # 3. Insert
        locked_during_write = False
        tool_workspace_insert(ctx, "guarded.txt", 1, "inserted\n")
        assert locked_during_write, "tool_workspace_insert did not hold lock during write"

        # 4. Replace lines
        locked_during_write = False
        tool_workspace_replace_lines(ctx, "guarded.txt", 1, 1, "replaced\n")
        assert locked_during_write, "tool_workspace_replace_lines did not hold lock during write"

        # 5. Edit
        locked_during_write = False
        tool_workspace_edit(ctx, "guarded.txt", "replaced", "edited")
        assert locked_during_write, "tool_workspace_edit did not hold lock during write"

    # 6. Copy (locks both source and destination)
    locked_during_copy = False
    import shutil

    orig_copy2 = shutil.copy2

    def check_copy_lock(src, dst, *args, **kwargs):
        nonlocal locked_during_copy
        if Path(src).resolve() == target_path:
            locked_during_copy = is_file_locked(target_path) and is_file_locked(dest_path)
        return orig_copy2(src, dst, *args, **kwargs)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(shutil, "copy2", check_copy_lock)
        tool_workspace_copy(ctx, "guarded.txt", "dest.txt")
        assert locked_during_copy, "tool_workspace_copy did not hold locks for src and dst"

    # 7. Move (locks both source and destination)
    locked_during_move = False
    orig_rename = Path.rename

    def check_move_lock(self, target, *args, **kwargs):
        nonlocal locked_during_move
        if self.resolve() == dest_path:
            locked_during_move = is_file_locked(dest_path) and is_file_locked(dest2_path)
        return orig_rename(self, target, *args, **kwargs)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(Path, "rename", check_move_lock)
        tool_workspace_move(ctx, "dest.txt", "dest2.txt")
        assert locked_during_move, "tool_workspace_move did not hold locks for src and dst"

    # 8. Delete
    locked_during_delete = False
    orig_unlink = Path.unlink

    def check_delete_lock(self, *args, **kwargs):
        nonlocal locked_during_delete
        if self.resolve() == dest2_path:
            locked_during_delete = is_file_locked(dest2_path)
        return orig_unlink(self, *args, **kwargs)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(Path, "unlink", check_delete_lock)
        tool_workspace_delete(ctx, "dest2.txt")
        assert locked_during_delete, "tool_workspace_delete did not hold lock during delete"


def test_canonical_path_resolution_and_aliases(ctx, tmp_path):
    """Two accepted paths resolving to the same file share the same guard; creation paths have stable keys."""
    ws = ctx.config.workspace_path.resolve()
    ws.mkdir(parents=True, exist_ok=True)

    # Existing file aliases
    existing = ws / "existing.txt"
    existing.write_text("hello")

    p1 = existing
    p2 = ws / "./existing.txt"
    p3 = ws / "sub" / ".." / "existing.txt"

    # Symlink alias
    symlink = ws / "sym_existing.txt"
    symlink.symlink_to(existing)

    assert get_canonical_path(p1) == get_canonical_path(p2) == get_canonical_path(p3) == get_canonical_path(symlink)

    # When locking via p1, all aliases report locked
    with file_lock(p1):
        assert is_file_locked(p1)
        assert is_file_locked(p2)
        assert is_file_locked(p3)
        assert is_file_locked(symlink)

    assert not is_file_locked(existing)

    # Creation paths (file does not yet exist)
    c1 = ws / "not_yet_created.txt"
    c2 = ws / "./not_yet_created.txt"
    c3 = ws / "nested" / ".." / "not_yet_created.txt"

    assert get_canonical_path(c1) == get_canonical_path(c2) == get_canonical_path(c3)

    with file_lock(c1):
        assert is_file_locked(c1)
        assert is_file_locked(c2)
        assert is_file_locked(c3)

    assert not is_file_locked(c1)

    # Symlinked directory creation path
    real_dir = ws / "real_dir"
    real_dir.mkdir()
    link_dir = ws / "link_dir"
    link_dir.symlink_to(real_dir)

    in_real = real_dir / "new_file.txt"
    in_link = link_dir / "new_file.txt"

    assert get_canonical_path(in_real) == get_canonical_path(in_link)
    with file_lock(in_real):
        assert is_file_locked(in_link)


def test_mutations_to_different_files_proceed_concurrently(ctx):
    """Mutations to different files proceed concurrently in parallel without contention."""
    file_a = ctx.config.workspace_path / "file_a.txt"
    file_b = ctx.config.workspace_path / "file_b.txt"

    barrier = threading.Barrier(2)
    both_held_concurrently = False

    def mutate_a():
        nonlocal both_held_concurrently
        with file_lock(file_a):
            # Wait for thread b to also acquire its lock
            barrier.wait(timeout=5.0)
            if is_file_locked(file_a) and is_file_locked(file_b):
                both_held_concurrently = True

    def mutate_b():
        nonlocal both_held_concurrently
        with file_lock(file_b):
            # Wait for thread a to also acquire its lock
            barrier.wait(timeout=5.0)
            if is_file_locked(file_a) and is_file_locked(file_b):
                both_held_concurrently = True

    t_a = threading.Thread(target=mutate_a)
    t_b = threading.Thread(target=mutate_b)

    t_a.start()
    t_b.start()
    t_a.join(timeout=5.0)
    t_b.join(timeout=5.0)

    assert not t_a.is_alive()
    assert not t_b.is_alive()
    assert both_held_concurrently, "Expected locks on different files to be held concurrently"


def test_exception_releases_guard(ctx):
    """An exception raised during mutation cleanly releases the file guard."""
    workspace = ctx.config.workspace_path.resolve()
    target = workspace / "recover_file.txt"

    class CustomMutationError(RuntimeError):
        pass

    with pytest.raises(CustomMutationError):
        with file_lock(target):
            assert is_file_locked(target)
            raise CustomMutationError("Simulated write failure")

    assert not is_file_locked(target), "Lock was not released after exception"

    # A subsequent mutation succeeds immediately without deadlock or block
    result = tool_workspace_write(ctx, "recover_file.txt", "recovered")
    assert not isinstance(result, ToolResult) or not result.text.startswith("[error:")
    assert not is_file_locked(target)


def test_cancellation_of_awaiting_task_releases_guard(ctx):
    """Cancellation of an awaiting task does not leave the lock acquired or prevent subsequent mutations."""
    tool_workspace_write(ctx, "cancel_target.txt", "initial\n")
    workspace = ctx.config.workspace_path.resolve()
    target = workspace / "cancel_target.txt"

    t1_holding = threading.Event()
    t2_waiting = threading.Event()
    t1_can_finish = threading.Event()

    t2_result: list[str | ToolResult] = []

    # Mock context for thread 2 with a cancellation event
    cancel_event = threading.Event()
    ctx2 = MagicMock()
    ctx2.config = ctx.config
    ctx2.cancelled = cancel_event

    def worker1():
        with file_lock(target):
            t1_holding.set()
            assert t1_can_finish.wait(timeout=5.0)

    def worker2():
        assert t1_holding.wait(timeout=5.0)
        t2_waiting.set()
        # Thread 2 calls tool_workspace_append; its context will be cancelled while awaiting the lock
        res = tool_workspace_append(ctx2, "cancel_target.txt", "from worker 2\n")
        t2_result.append(res)

    t1 = threading.Thread(target=worker1)
    t2 = threading.Thread(target=worker2)

    t1.start()
    t2.start()

    assert t2_waiting.wait(timeout=5.0)
    # Cancel ctx2 while worker 2 is blocked awaiting the lock
    cancel_event.set()
    # Now let worker 1 complete and release the lock
    t1_can_finish.set()

    t1.join(timeout=5.0)
    t2.join(timeout=5.0)

    assert not t1.is_alive()
    assert not t2.is_alive()

    # Worker 2 should report interrupted/cancelled without modifying the file
    assert len(t2_result) == 1
    assert "cancelled" in _text(t2_result[0]).lower()

    # The file lock should now be completely free
    assert not is_file_locked(target)

    # Worker 3 can immediately mutate the file
    res3 = tool_workspace_append(ctx, "cancel_target.txt", "from worker 3\n")
    assert not isinstance(res3, ToolResult) or not res3.text.startswith("[error:")
    content = _text(tool_workspace_read(ctx, "cancel_target.txt"))
    assert "from worker 2" not in content
    assert "from worker 3" in content


def test_cancellation_does_not_release_guard_while_worker_write_running(ctx):
    """Cancellation of an outer task does not allow another mutation to overlap an unfinished worker write."""
    tool_workspace_write(ctx, "overlap_target.txt", "start\n")
    workspace = ctx.config.workspace_path.resolve()
    target = workspace / "overlap_target.txt"

    worker1_started = threading.Event()
    worker1_can_finish = threading.Event()
    worker2_overlap_detected = False

    def worker1():
        with file_lock(target):
            worker1_started.set()
            # Simulate slow in-flight write
            assert worker1_can_finish.wait(timeout=5.0)

    def worker2():
        nonlocal worker2_overlap_detected
        assert worker1_started.wait(timeout=5.0)
        # Attempt to acquire lock; while worker 1 is running, it must be locked
        if not is_file_locked(target):
            worker2_overlap_detected = True
        with file_lock(target):
            # Lock acquired only after worker 1 finished
            pass

    t1 = threading.Thread(target=worker1)
    t2 = threading.Thread(target=worker2)

    t1.start()
    t2.start()

    assert worker1_started.wait(timeout=5.0)
    # Target file is still held by worker 1
    assert is_file_locked(target)
    assert not worker2_overlap_detected

    # Worker 1 finishes
    worker1_can_finish.set()
    t1.join(timeout=5.0)
    t2.join(timeout=5.0)

    assert not worker2_overlap_detected
    assert not is_file_locked(target)


def test_deadlock_free_multi_path_locking():
    """Multi-path locking avoids deadlocks regardless of argument order."""
    mgr = FileLockManager()
    path_a = Path("/tmp/test_a.txt")
    path_b = Path("/tmp/test_b.txt")

    barrier = threading.Barrier(2)
    completed_threads = 0
    threads_lock = threading.Lock()

    def worker_ab():
        nonlocal completed_threads
        # Order A then B
        with mgr.lock(path_a, path_b):
            barrier.wait(timeout=5.0)
        with threads_lock:
            completed_threads += 1

    def worker_ba():
        nonlocal completed_threads
        # Order B then A (inverted)
        barrier.wait(timeout=5.0)
        with mgr.lock(path_b, path_a):
            pass
        with threads_lock:
            completed_threads += 1

    t1 = threading.Thread(target=worker_ab)
    t2 = threading.Thread(target=worker_ba)

    t1.start()
    t2.start()
    t1.join(timeout=5.0)
    t2.join(timeout=5.0)

    assert not t1.is_alive(), "Thread 1 deadlocked"
    assert not t2.is_alive(), "Thread 2 deadlocked"
    assert completed_threads == 2


@pytest.mark.asyncio
async def test_async_file_lock_context():
    """file_lock supports async with in an asyncio event loop."""
    target = Path("/tmp/async_test.txt")

    async with file_lock(target):
        assert is_file_locked(target)

    assert not is_file_locked(target)
