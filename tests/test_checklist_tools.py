"""Tests for checklist tools."""

from unittest.mock import AsyncMock

import pytest

from decafclaw.media import ToolResult
from decafclaw.tools.checklist_tools import (
    _progress_data_from_checklist,
    tool_checklist_abort,
    tool_checklist_create,
    tool_checklist_status,
    tool_checklist_step_done,
)

# --- pure mapping ---------------------------------------------------------

def test_progress_data_maps_first_unchecked_to_in_progress():
    items = [
        {"text": "A", "done": True, "note": "did a"},
        {"text": "B", "done": False, "note": ""},
        {"text": "C", "done": False, "note": ""},
    ]
    data = _progress_data_from_checklist(items)
    assert [s["status"] for s in data["steps"]] == ["done", "in_progress", "pending"]
    assert data["steps"][0]["note"] == "did a"
    assert data["title"] == "Checklist"
    assert data["summary"] == "1/3 · B"


def test_progress_data_all_done_summary_has_no_current():
    items = [{"text": "A", "done": True, "note": ""}]
    data = _progress_data_from_checklist(items)
    assert data["steps"][0]["status"] == "done"
    assert data["summary"] == "1/1"


# --- existing behavior (now async) ---------------------------------------

@pytest.mark.asyncio
async def test_checklist_create(ctx):
    result = await tool_checklist_create(ctx, steps=["Step A", "Step B", "Step C"])
    assert isinstance(result, ToolResult)
    assert "3 steps" in result.text
    assert "Step A" in result.text


@pytest.mark.asyncio
async def test_checklist_create_empty(ctx):
    result = await tool_checklist_create(ctx, steps=[])
    assert "error" in result.text


@pytest.mark.asyncio
async def test_checklist_step_done_advances(ctx):
    await tool_checklist_create(ctx, steps=["First", "Second", "Third"])
    result = await tool_checklist_step_done(ctx, note="done with first")
    assert result.end_turn is False
    assert "Second" in result.text


@pytest.mark.asyncio
async def test_checklist_step_done_all_complete(ctx):
    await tool_checklist_create(ctx, steps=["Only step"])
    result = await tool_checklist_step_done(ctx)
    assert result.end_turn is True
    assert "complete" in result.text.lower()


@pytest.mark.asyncio
async def test_checklist_step_done_no_checklist(ctx):
    result = await tool_checklist_step_done(ctx)
    assert "error" in result.text.lower() or "no active" in result.text.lower()


@pytest.mark.asyncio
async def test_checklist_abort(ctx):
    await tool_checklist_create(ctx, steps=["Step 1", "Step 2"])
    result = await tool_checklist_abort(ctx, reason="changed my mind")
    assert "aborted" in result.text.lower()
    assert "changed my mind" in result.text
    status = tool_checklist_status(ctx)
    assert "No active" in status.text


@pytest.mark.asyncio
async def test_checklist_abort_empty(ctx):
    result = await tool_checklist_abort(ctx)
    assert "No active" in result.text


@pytest.mark.asyncio
async def test_checklist_status(ctx):
    await tool_checklist_create(ctx, steps=["A", "B", "C"])
    await tool_checklist_step_done(ctx)
    status = tool_checklist_status(ctx)
    assert "[x]" in status.text
    assert "[ ]" in status.text
    assert "current" in status.text
    assert "1/3 complete" in status.text


@pytest.mark.asyncio
async def test_checklist_status_empty(ctx):
    result = tool_checklist_status(ctx)
    assert "No active" in result.text


# --- sticky auto-emit wiring (monkeypatch sticky funcs) -------------------

@pytest.mark.asyncio
async def test_create_emits_set_sticky(ctx, monkeypatch):
    set_mock = AsyncMock()
    clear_mock = AsyncMock()
    monkeypatch.setattr("decafclaw.sticky.set_sticky", set_mock)
    monkeypatch.setattr("decafclaw.sticky.clear_sticky", clear_mock)
    await tool_checklist_create(ctx, steps=["A", "B"])
    assert set_mock.await_count == 1
    args, kwargs = set_mock.await_args
    # (config, conv_id, widget_type, data)
    assert args[2] == "progress_tracker"
    assert args[3]["steps"][0]["status"] == "in_progress"
    clear_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_final_step_done_clears_sticky(ctx, monkeypatch):
    clear_mock = AsyncMock()
    monkeypatch.setattr("decafclaw.sticky.set_sticky", AsyncMock())
    monkeypatch.setattr("decafclaw.sticky.clear_sticky", clear_mock)
    await tool_checklist_create(ctx, steps=["only"])
    await tool_checklist_step_done(ctx)
    assert clear_mock.await_count >= 1


@pytest.mark.asyncio
async def test_abort_clears_sticky(ctx, monkeypatch):
    clear_mock = AsyncMock()
    monkeypatch.setattr("decafclaw.sticky.set_sticky", AsyncMock())
    monkeypatch.setattr("decafclaw.sticky.clear_sticky", clear_mock)
    await tool_checklist_create(ctx, steps=["A", "B"])
    await tool_checklist_abort(ctx)
    assert clear_mock.await_count >= 1


@pytest.mark.asyncio
async def test_step_done_no_checklist_does_not_clear_sticky(ctx, monkeypatch):
    from unittest.mock import AsyncMock
    clear_mock = AsyncMock()
    monkeypatch.setattr("decafclaw.sticky.set_sticky", AsyncMock())
    monkeypatch.setattr("decafclaw.sticky.clear_sticky", clear_mock)
    result = await tool_checklist_step_done(ctx)  # no active checklist
    assert "no active" in result.text.lower() or "[error" in result.text
    clear_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_sticky_failure_is_fail_open(ctx, monkeypatch):
    monkeypatch.setattr("decafclaw.sticky.set_sticky",
                        AsyncMock(side_effect=RuntimeError("boom")))
    monkeypatch.setattr("decafclaw.sticky.clear_sticky",
                        AsyncMock(side_effect=RuntimeError("boom")))
    # Must not raise; checklist still works.
    result = await tool_checklist_create(ctx, steps=["A"])
    assert "1 step" in result.text or "1 steps" in result.text


@pytest.mark.asyncio
async def test_ok_false_result_is_logged(ctx, monkeypatch, caplog):
    from unittest.mock import AsyncMock

    from decafclaw.sticky import StickyOpResult
    monkeypatch.setattr("decafclaw.sticky.set_sticky",
                        AsyncMock(return_value=StickyOpResult(ok=False, error="boom")))
    monkeypatch.setattr("decafclaw.sticky.clear_sticky", AsyncMock(return_value=StickyOpResult(ok=True)))
    with caplog.at_level("WARNING"):
        await tool_checklist_create(ctx, steps=["A", "B"])
    assert any("boom" in r.message or "sticky set failed" in r.message for r in caplog.records)
