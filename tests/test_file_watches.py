"""Unit tests for one-shot file watch wake turns (#1560)."""

import asyncio
import os
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from decafclaw.context import TaskMode
from decafclaw.conversation_paths import delete_conversation_files, file_watches_path
from decafclaw.file_watches import (
    FileWatch,
    FileWatchManager,
    load_file_watches,
    save_file_watches,
)
from decafclaw.media import WidgetRequest
from decafclaw.tools.watch_tools import tool_watch_cancel, tool_watch_file, tool_watch_list


@pytest.fixture
async def mock_manager(config):
    """ConversationManager stand-in with a real FileWatchManager attached."""
    mgr = MagicMock()
    mgr.config = config
    mgr.enqueue_turn = AsyncMock()
    mgr.get_state = MagicMock(return_value=None)
    mgr.file_watches = FileWatchManager(config, manager=mgr)
    yield mgr
    await mgr.file_watches.shutdown()


def _write(path, text="{}"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _touch_mtime(path):
    """Force a distinct mtime so signature comparison is deterministic."""
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))


def _entry(
    conv_id,
    path,
    *,
    event="modify",
    seconds=120,
    baseline_exists=True,
    mtime_ns=1,
    size=1,
    inode=1,
):
    now = datetime.now(timezone.utc)
    fire = now + timedelta(seconds=seconds)
    return FileWatch(
        watch_id="watch_manual",
        conv_id=conv_id,
        path=str(path),
        event=event,
        reason="manual",
        timeout_seconds=seconds,
        created_at=now.isoformat(),
        expires_at=fire.isoformat(),
        expires_at_ts=fire.timestamp(),
        baseline_exists=baseline_exists,
        baseline_mtime_ns=mtime_ns if baseline_exists else None,
        baseline_size=size if baseline_exists else None,
        baseline_inode=inode if baseline_exists else None,
    )


@pytest.mark.asyncio
async def test_watch_file_persists_and_returns_watch(ctx, mock_manager):
    ctx.manager = mock_manager
    ctx.conv_id = "conv-watch"
    _write(ctx.config.workspace_path / "board" / "content.json")

    result = await tool_watch_file(
        ctx,
        "board/content.json",
        event="modify",
        timeout=120,
        reason="User may approve the board",
    )

    assert "[error" not in result.text
    assert result.data["event"] == "modify"
    assert result.data["path"] == "board/content.json"
    assert result.data["reason"] == "User may approve the board"
    assert result.data["timeout_seconds"] == 120
    watch_id = result.data["watch_id"]
    assert watch_id.startswith("watch_")

    watches = load_file_watches(ctx.config, "conv-watch")
    assert len(watches) == 1
    assert watches[0].watch_id == watch_id
    assert watches[0].baseline_exists is True
    assert watches[0].path == str((ctx.config.workspace_path / "board" / "content.json").resolve())
    assert watch_id in mock_manager.file_watches._tasks


@pytest.mark.asyncio
async def test_modify_event_fires_wake_and_disarms(ctx, mock_manager):
    ctx.config.file_watch.poll_interval_sec = 0.01
    ctx.manager = mock_manager
    ctx.conv_id = "conv-modify"
    path = _write(ctx.config.workspace_path / "content.json")

    result = await tool_watch_file(ctx, "content.json", reason="Check approval")
    watch_id = result.data["watch_id"]

    _write(path, '{"approved": true}')
    _touch_mtime(path)

    await asyncio.wait_for(mock_manager.file_watches._tasks[watch_id], timeout=2)

    mock_manager.enqueue_turn.assert_awaited_once()
    kwargs = mock_manager.enqueue_turn.await_args.kwargs
    assert kwargs["conv_id"] == "conv-modify"
    assert kwargs["kind"].value == "wake"
    assert kwargs["prompt"].startswith("File modified: content.json. Reason: Check approval.")
    assert "BACKGROUND_WAKE_OK" in kwargs["prompt"]
    assert kwargs["metadata"]["watch_id"] == watch_id
    assert kwargs["metadata"]["event"] == "modified"
    assert kwargs["metadata"]["path"] == "content.json"
    assert callable(kwargs["context_setup"])

    assert load_file_watches(ctx.config, "conv-modify") == []
    assert watch_id not in mock_manager.file_watches._tasks


@pytest.mark.asyncio
async def test_create_event_fires_when_file_appears(ctx, mock_manager):
    ctx.config.file_watch.poll_interval_sec = 0.01
    ctx.manager = mock_manager
    ctx.conv_id = "conv-create"

    result = await tool_watch_file(ctx, "new-sentinel.txt", event="create", reason="Build finished")
    watch_id = result.data["watch_id"]
    _write(ctx.config.workspace_path / "new-sentinel.txt", "done")

    await asyncio.wait_for(mock_manager.file_watches._tasks[watch_id], timeout=2)

    kwargs = mock_manager.enqueue_turn.await_args.kwargs
    assert kwargs["prompt"].startswith("File created: new-sentinel.txt.")
    assert kwargs["metadata"]["event"] == "created"


@pytest.mark.asyncio
async def test_delete_event_fires_when_file_removed(ctx, mock_manager):
    ctx.config.file_watch.poll_interval_sec = 0.01
    ctx.manager = mock_manager
    ctx.conv_id = "conv-delete"
    path = _write(ctx.config.workspace_path / "temp.txt")

    result = await tool_watch_file(ctx, "temp.txt", event="delete")
    watch_id = result.data["watch_id"]
    path.unlink()

    await asyncio.wait_for(mock_manager.file_watches._tasks[watch_id], timeout=2)

    kwargs = mock_manager.enqueue_turn.await_args.kwargs
    assert kwargs["prompt"].startswith("File deleted: temp.txt.")
    assert "Reason:" not in kwargs["prompt"]
    assert kwargs["metadata"]["event"] == "deleted"


@pytest.mark.asyncio
async def test_timeout_expires_silently_by_default(ctx, mock_manager):
    ctx.config.file_watch.poll_interval_sec = 0.01
    ctx.manager = mock_manager
    ctx.conv_id = "conv-timeout"
    path = _write(ctx.config.workspace_path / "quiet.txt")

    result = await tool_watch_file(ctx, "quiet.txt", timeout=1)
    watch_id = result.data["watch_id"]

    await asyncio.wait_for(mock_manager.file_watches._tasks[watch_id], timeout=3)

    mock_manager.enqueue_turn.assert_not_awaited()
    assert load_file_watches(ctx.config, "conv-timeout") == []
    assert path.exists()


@pytest.mark.asyncio
async def test_timeout_notice_when_configured(ctx, mock_manager):
    ctx.config.file_watch.poll_interval_sec = 0.01
    ctx.config.file_watch.notify_on_timeout = True
    ctx.manager = mock_manager
    ctx.conv_id = "conv-timeout-notice"
    _write(ctx.config.workspace_path / "quiet.txt")

    result = await tool_watch_file(ctx, "quiet.txt", timeout=1, reason="Waiting for approval")
    watch_id = result.data["watch_id"]

    await asyncio.wait_for(mock_manager.file_watches._tasks[watch_id], timeout=3)

    kwargs = mock_manager.enqueue_turn.await_args.kwargs
    assert "File watch expired without a matching event: quiet.txt" in kwargs["prompt"]
    assert "Waiting for approval" in kwargs["prompt"]
    assert kwargs["metadata"]["event"] == "timeout"


@pytest.mark.asyncio
async def test_cancel_prevents_fire(ctx, mock_manager):
    ctx.config.file_watch.poll_interval_sec = 0.01
    ctx.manager = mock_manager
    ctx.conv_id = "conv-cancel"
    path = _write(ctx.config.workspace_path / "cancel.txt")

    result = await tool_watch_file(ctx, "cancel.txt", reason="Never mind")
    watch_id = result.data["watch_id"]

    cancel = await tool_watch_cancel(ctx, watch_id)
    assert cancel.data["cancelled"] is True
    assert load_file_watches(ctx.config, "conv-cancel") == []
    assert watch_id not in mock_manager.file_watches._tasks

    _write(path, "changed")
    _touch_mtime(path)
    await asyncio.sleep(0.05)
    mock_manager.enqueue_turn.assert_not_awaited()

    again = await tool_watch_cancel(ctx, watch_id)
    assert "[error:" in again.text
    assert again.data["cancelled"] is False


@pytest.mark.asyncio
async def test_cross_conversation_cancellation_prevented(ctx, mock_manager):
    ctx.manager = mock_manager
    ctx.conv_id = "conv-A"
    _write(ctx.config.workspace_path / "a.txt")

    result = await tool_watch_file(ctx, "a.txt")
    watch_id = result.data["watch_id"]

    ctx.conv_id = "conv-B"
    cancel = await tool_watch_cancel(ctx, watch_id)
    assert cancel.data["cancelled"] is False
    assert len(load_file_watches(ctx.config, "conv-A")) == 1
    assert watch_id in mock_manager.file_watches._tasks


@pytest.mark.asyncio
async def test_watch_list(ctx, mock_manager):
    ctx.manager = mock_manager
    ctx.conv_id = "conv-list"

    empty = tool_watch_list(ctx)
    assert "No pending file watches" in empty.text
    assert empty.data["watches"] == []
    assert empty.widget is None

    _write(ctx.config.workspace_path / "one.json")
    await tool_watch_file(ctx, "one.json", event="modify", reason="First")
    await tool_watch_file(ctx, "two.json", event="create", reason="Second", timeout=300)

    listed = tool_watch_list(ctx)
    assert "Found 2 pending file watch(es):" in listed.text
    # Sorted by expiry: the 300s watch comes before the default 3600s one.
    assert [w["reason"] for w in listed.data["watches"]] == ["Second", "First"]
    assert [w["path"] for w in listed.data["watches"]] == ["two.json", "one.json"]
    assert isinstance(listed.widget, WidgetRequest)
    assert listed.widget.widget_type == "data_table"
    assert listed.widget.target == "inline"
    assert "2 pending file watch(es)" in listed.widget.data["caption"]
    assert len(listed.widget.data["rows"]) == 2
    assert listed.widget.data["columns"] == [
        {"key": "watch_id", "label": "Watch ID"},
        {"key": "path", "label": "Path"},
        {"key": "event", "label": "Event"},
        {"key": "remaining", "label": "Remaining (s)", "type": "duration"},
        {"key": "reason", "label": "Reason"},
    ]


@pytest.mark.asyncio
async def test_watch_file_validation_errors(ctx, mock_manager):
    ctx.manager = mock_manager
    ctx.conv_id = "conv-validation"
    _write(ctx.config.workspace_path / "exists.txt")

    escape = await tool_watch_file(ctx, "../outside.txt")
    assert "path must stay inside the workspace" in escape.text

    missing = await tool_watch_file(ctx, "missing.txt")
    assert "path does not exist" in missing.text

    already = await tool_watch_file(ctx, "exists.txt", event="create")
    assert "path already exists" in already.text

    bad_event = await tool_watch_file(ctx, "exists.txt", event="explode")
    assert "event must be one of" in bad_event.text

    zero = await tool_watch_file(ctx, "exists.txt", timeout=0)
    assert "timeout must be a positive integer" in zero.text

    too_long = await tool_watch_file(ctx, "exists.txt", timeout=ctx.config.file_watch.max_timeout_sec + 1)
    assert "timeout exceeds" in too_long.text

    missing_path = await tool_watch_file(ctx, "")
    assert "path is required" in missing_path.text

    ctx.conv_id = ""
    no_conv = await tool_watch_file(ctx, "exists.txt")
    assert "No active conversation ID" in no_conv.text


@pytest.mark.asyncio
async def test_watch_file_refuses_secret_paths(ctx, mock_manager):
    ctx.manager = mock_manager
    ctx.conv_id = "conv-secret"
    _write(ctx.config.workspace_path / "keys" / "id.key", "secret")

    result = await tool_watch_file(ctx, "keys/id.key")
    assert "protected secret" in result.text
    assert result.data["error"] == "path_refused"


@pytest.mark.asyncio
async def test_watch_file_rejected_for_child_and_unattended(ctx, mock_manager):
    ctx.manager = mock_manager
    ctx.conv_id = "conv-child"
    _write(ctx.config.workspace_path / "f.txt")
    ctx.is_child = True

    child = await tool_watch_file(ctx, "f.txt")
    assert "only available in interactive user conversations" in child.text
    assert child.data["error"] == "unattended_or_child_not_permitted"

    ctx.is_child = False
    ctx.task_mode = TaskMode.SCHEDULED
    scheduled = await tool_watch_file(ctx, "f.txt")
    assert "only available in interactive user conversations" in scheduled.text

    ctx.task_mode = TaskMode.BACKGROUND_WAKE
    wake = await tool_watch_file(ctx, "f.txt")
    assert "[error" not in wake.text


@pytest.mark.asyncio
async def test_watch_limit_and_disabled_config(ctx, mock_manager):
    ctx.manager = mock_manager
    ctx.conv_id = "conv-limit"
    ctx.config.file_watch.max_watches_per_conversation = 1
    _write(ctx.config.workspace_path / "a.txt")
    _write(ctx.config.workspace_path / "b.txt")

    first = await tool_watch_file(ctx, "a.txt")
    assert "[error" not in first.text
    second = await tool_watch_file(ctx, "b.txt")
    assert "too many active file watches" in second.text

    ctx.conv_id = "conv-disabled"
    ctx.config.file_watch.enabled = False
    disabled = await tool_watch_file(ctx, "a.txt")
    assert "file watches are disabled" in disabled.text


@pytest.mark.asyncio
async def test_startup_scan_fires_change_that_landed_offline(ctx, mock_manager):
    conv_id = "conv-offline-fire"
    path = _write(ctx.config.workspace_path / "offline.txt", "new")
    save_file_watches(ctx.config, conv_id, [_entry(conv_id, path)])

    resumed = await mock_manager.file_watches.startup_scan()

    assert resumed == 0
    kwargs = mock_manager.enqueue_turn.await_args.kwargs
    assert kwargs["prompt"].startswith("File modified: offline.txt.")
    assert load_file_watches(ctx.config, conv_id) == []


@pytest.mark.asyncio
async def test_startup_scan_resumes_unchanged_watch(ctx, mock_manager):
    conv_id = "conv-offline-wait"
    path = _write(ctx.config.workspace_path / "unchanged.txt", "same")
    st = path.stat()
    save_file_watches(
        ctx.config,
        conv_id,
        [
            _entry(
                conv_id,
                path,
                mtime_ns=st.st_mtime_ns,
                size=st.st_size,
                inode=st.st_ino,
            )
        ],
    )

    resumed = await mock_manager.file_watches.startup_scan()

    assert resumed == 1
    assert "watch_manual" in mock_manager.file_watches._tasks
    mock_manager.enqueue_turn.assert_not_awaited()


@pytest.mark.asyncio
async def test_startup_scan_drops_expired_watch_without_notice(ctx, mock_manager):
    conv_id = "conv-offline-expired"
    path = _write(ctx.config.workspace_path / "old.txt")
    entry = _entry(conv_id, path, seconds=1)
    entry.expires_at_ts = time.time() - 5
    save_file_watches(ctx.config, conv_id, [entry])

    resumed = await mock_manager.file_watches.startup_scan()

    assert resumed == 0
    mock_manager.enqueue_turn.assert_not_awaited()
    assert load_file_watches(ctx.config, conv_id) == []


@pytest.mark.asyncio
async def test_startup_scan_notifies_expired_watch_when_configured(ctx, mock_manager):
    ctx.config.file_watch.notify_on_timeout = True
    conv_id = "conv-offline-notice"
    path = _write(ctx.config.workspace_path / "old.txt")
    entry = _entry(conv_id, path, seconds=1)
    entry.expires_at_ts = time.time() - 5
    save_file_watches(ctx.config, conv_id, [entry])

    await mock_manager.file_watches.startup_scan()

    kwargs = mock_manager.enqueue_turn.await_args.kwargs
    assert "File watch expired without a matching event" in kwargs["prompt"]
    assert kwargs["metadata"]["event"] == "timeout"


@pytest.mark.asyncio
async def test_delete_conversation_files_removes_watch_sidecar_for_real_conv(ctx, mock_manager):
    ctx.manager = mock_manager
    ctx.conv_id = "conv-delete-real"
    _write(ctx.config.workspace_path / "gone.txt")
    result = await tool_watch_file(ctx, "gone.txt")
    watch_id = result.data["watch_id"]
    assert file_watches_path(ctx.config, "conv-delete-real").exists()

    await mock_manager.file_watches.cancel_all_for_conversation("conv-delete-real")
    delete_conversation_files(ctx.config, "conv-delete-real")

    assert watch_id not in mock_manager.file_watches._tasks
    assert not file_watches_path(ctx.config, "conv-delete-real").exists()


@pytest.mark.asyncio
async def test_any_event_fires_on_modify(ctx, mock_manager):
    ctx.config.file_watch.poll_interval_sec = 0.01
    ctx.manager = mock_manager
    ctx.conv_id = "conv-any"
    path = _write(ctx.config.workspace_path / "any.txt")

    result = await tool_watch_file(ctx, "any.txt", event="any")
    watch_id = result.data["watch_id"]
    _write(path, "changed")
    _touch_mtime(path)

    await asyncio.wait_for(mock_manager.file_watches._tasks[watch_id], timeout=2)

    kwargs = mock_manager.enqueue_turn.await_args.kwargs
    assert kwargs["prompt"].startswith("File modified: any.txt.")
    assert kwargs["metadata"]["event"] == "modified"


@pytest.mark.asyncio
async def test_startup_scan_is_idempotent(ctx, mock_manager):
    conv_id = "conv-scan-twice"
    path = _write(ctx.config.workspace_path / "same.txt")
    st = path.stat()
    save_file_watches(
        ctx.config,
        conv_id,
        [_entry(conv_id, path, mtime_ns=st.st_mtime_ns, size=st.st_size, inode=st.st_ino)],
    )

    first = await mock_manager.file_watches.startup_scan()
    second = await mock_manager.file_watches.startup_scan()

    assert (first, second) == (1, 0)
    assert len(mock_manager.file_watches._tasks) == 1
    mock_manager.enqueue_turn.assert_not_awaited()


def test_wake_context_setup_inherits_and_overrides():
    from decafclaw.scheduled_wakes import compose_wake_context_setup

    seen = []

    def base_setup(target):
        seen.append("base")
        target.channel_id = "inherited-channel"
        target.thread_id = "inherited-thread"

    state = SimpleNamespace(last_context_setup=base_setup)
    manager = SimpleNamespace(get_state=lambda conv_id: state)

    setup = compose_wake_context_setup(manager, "conv", channel_id="watch-channel", transport="web")
    target = SimpleNamespace(channel_id="", thread_id="", transport="")

    setup(target)

    assert seen == ["base"]
    assert target.channel_id == "watch-channel"
    assert target.thread_id == "inherited-thread"
    assert target.transport == "web"


def test_stat_error_during_poll_is_not_a_delete(monkeypatch, config):
    def denied(path):
        raise PermissionError("denied")

    monkeypatch.setattr("decafclaw.file_watches.os", SimpleNamespace(stat=denied))
    watch = _entry("conv-stat", config.workspace_path / "locked.txt")

    assert FileWatchManager(config)._check_triggered(watch) is None


@pytest.mark.asyncio
async def test_watch_file_reports_unstattable_path(monkeypatch, ctx, mock_manager):
    ctx.manager = mock_manager
    ctx.conv_id = "conv-unstattable"

    def denied(path):
        raise PermissionError("denied")

    monkeypatch.setattr("decafclaw.file_watches.os", SimpleNamespace(stat=denied))
    result = await tool_watch_file(ctx, "locked.txt")

    assert "cannot stat path" in result.text
    assert load_file_watches(ctx.config, "conv-unstattable") == []
