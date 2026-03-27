"""Tests for _process_tool_media — per-tool-call media save and placeholder replacement."""

from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock

import pytest

from decafclaw.agent import _process_tool_media
from decafclaw.media import MediaSaveResult, ToolResult


@dataclass
class _FakeCtx:
    conv_id: str = "conv1"
    channel_id: str = ""
    media_handler: object = None


def _make_handler(workspace_ref=None, file_id=None, saved_filename=None):
    handler = AsyncMock()
    handler.save_media = AsyncMock(return_value=MediaSaveResult(
        workspace_ref=workspace_ref,
        file_id=file_id,
        saved_filename=saved_filename,
    ))
    return handler


# -- Workspace ref replacement --


@pytest.mark.asyncio
async def test_image_placeholder_replaced_with_markdown_image():
    handler = _make_handler(
        workspace_ref="workspace://conversations/conv1/uploads/img-20260327.png",
        saved_filename="img-20260327.png",
    )
    ctx = _FakeCtx(media_handler=handler)
    result = ToolResult(
        text="[file attached: img.png (image/png) — will appear as an attachment on your reply]",
        media=[{"type": "file", "filename": "img.png", "data": b"png", "content_type": "image/png"}],
    )
    file_ids = await _process_tool_media(ctx, result)
    assert file_ids == []
    assert "![img.png](workspace://conversations/conv1/uploads/img-20260327.png)" in result.text
    assert "[file attached:" not in result.text
    assert result.media == []


@pytest.mark.asyncio
async def test_non_image_placeholder_replaced_with_markdown_link():
    handler = _make_handler(
        workspace_ref="workspace://conversations/conv1/uploads/doc-20260327.pdf",
        saved_filename="doc-20260327.pdf",
    )
    ctx = _FakeCtx(media_handler=handler)
    result = ToolResult(
        text="[file attached: doc.pdf (application/pdf) — will appear as an attachment on your reply]",
        media=[{"type": "file", "filename": "doc.pdf", "data": b"pdf", "content_type": "application/pdf"}],
    )
    file_ids = await _process_tool_media(ctx, result)
    assert file_ids == []
    assert "[doc.pdf](workspace://conversations/conv1/uploads/doc-20260327.pdf)" in result.text
    assert "![" not in result.text  # not an image ref


# -- Mattermost file_id --


@pytest.mark.asyncio
async def test_file_id_collected():
    handler = _make_handler(file_id="mm-file-123")
    ctx = _FakeCtx(media_handler=handler)
    result = ToolResult(
        text="[file attached: img.png (image/png) — will appear as an attachment on your reply]",
        media=[{"type": "file", "filename": "img.png", "data": b"png", "content_type": "image/png"}],
    )
    file_ids = await _process_tool_media(ctx, result)
    assert file_ids == ["mm-file-123"]
    assert result.media == []


# -- No media handler --


@pytest.mark.asyncio
async def test_no_handler_logs_warning_leaves_text(caplog):
    ctx = _FakeCtx(media_handler=None)
    result = ToolResult(
        text="[file attached: img.png (image/png) — will appear as an attachment on your reply]",
        media=[{"type": "file", "filename": "img.png", "data": b"png", "content_type": "image/png"}],
    )
    file_ids = await _process_tool_media(ctx, result)
    assert file_ids == []
    assert "[file attached:" in result.text
    assert "No media handler" in caplog.text


# -- Failed save --


@pytest.mark.asyncio
async def test_failed_save_logs_warning_preserves_placeholder(caplog):
    handler = AsyncMock()
    handler.save_media = AsyncMock(side_effect=RuntimeError("upload failed"))
    ctx = _FakeCtx(media_handler=handler)
    result = ToolResult(
        text="[file attached: img.png (image/png) — will appear as an attachment on your reply]",
        media=[{"type": "file", "filename": "img.png", "data": b"png", "content_type": "image/png"}],
    )
    file_ids = await _process_tool_media(ctx, result)
    assert file_ids == []
    assert "[file attached:" in result.text
    assert "Failed to save media" in caplog.text
    assert result.media == []


# -- Multiple media items --


@pytest.mark.asyncio
async def test_multiple_media_items():
    call_count = 0

    async def _save(conv_id, filename, data, content_type):
        nonlocal call_count
        call_count += 1
        return MediaSaveResult(
            workspace_ref=f"workspace://uploads/{filename.replace('.', f'-{call_count}.')}",
            saved_filename=f"{filename.replace('.', f'-{call_count}.')}",
        )

    handler = MagicMock()
    handler.save_media = _save
    ctx = _FakeCtx(media_handler=handler)
    result = ToolResult(
        text=(
            "[file attached: a.png (image/png) — will appear as an attachment on your reply]\n"
            "[file attached: b.txt (text/plain) — will appear as an attachment on your reply]"
        ),
        media=[
            {"type": "file", "filename": "a.png", "data": b"a", "content_type": "image/png"},
            {"type": "file", "filename": "b.txt", "data": b"b", "content_type": "text/plain"},
        ],
    )
    await _process_tool_media(ctx, result)
    assert "![a.png](workspace://uploads/a-1.png)" in result.text
    assert "[b.txt](workspace://uploads/b-2.txt)" in result.text
    assert "[file attached:" not in result.text
    assert result.media == []


# -- No placeholder — append ref --


@pytest.mark.asyncio
async def test_no_placeholder_appends_ref():
    handler = _make_handler(
        workspace_ref="workspace://conversations/conv1/uploads/img-20260327.png",
        saved_filename="img-20260327.png",
    )
    ctx = _FakeCtx(media_handler=handler)
    result = ToolResult(
        text="Here's a test image (200x200 gradient):",
        media=[{"type": "file", "filename": "img.png", "data": b"png", "content_type": "image/png"}],
    )
    await _process_tool_media(ctx, result)
    assert "Here's a test image" in result.text
    assert "![img.png](workspace://conversations/conv1/uploads/img-20260327.png)" in result.text
    assert result.media == []


@pytest.mark.asyncio
async def test_no_handler_clears_media():
    ctx = _FakeCtx(media_handler=None)
    result = ToolResult(
        text="some text",
        media=[{"type": "file", "filename": "img.png", "data": b"png", "content_type": "image/png"}],
    )
    await _process_tool_media(ctx, result)
    assert result.media == []


# -- Empty media --


@pytest.mark.asyncio
async def test_empty_media_noop():
    ctx = _FakeCtx(media_handler=None)
    result = ToolResult(text="no media here")
    file_ids = await _process_tool_media(ctx, result)
    assert file_ids == []
    assert result.text == "no media here"
