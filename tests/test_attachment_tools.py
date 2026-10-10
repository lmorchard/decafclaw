"""Tests for attachment tools (list_attachments, get_attachment)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from decafclaw.attachments import save_attachment, uploads_dir
from decafclaw.media import (
    LocalFileMediaHandler,
    MattermostMediaHandler,
    MediaHandler,
    MediaSaveResult,
    ToolResult,
)
from decafclaw.tool_execution import process_tool_media
from decafclaw.tools.attachment_tools import (
    _should_provide_media,
    tool_get_attachment,
    tool_list_attachments,
)


class FakeMattermostMediaHandler(MediaHandler):
    """Fake platform media handler that records calls to save_media."""

    def __init__(self):
        self.saved: list[tuple[str, str, bytes | str, str]] = []

    async def save_media(self, conv_id: str, filename: str, data: bytes, content_type: str) -> MediaSaveResult:
        self.saved.append((conv_id, filename, data, content_type))
        return MediaSaveResult(file_id="mm-file-123")


@pytest.mark.asyncio
async def test_get_attachment_image_local_handler_no_warning_no_duplicate(ctx, caplog):
    # Save a test PNG
    png_bytes = b"\x89PNG\r\n\x1a\nfake-image-bytes"
    saved = save_attachment(ctx.config, ctx.conv_id, "test.png", png_bytes, "image/png")
    filename = saved["filename"]

    ctx.media_handler = LocalFileMediaHandler(ctx.config)

    # Count files in uploads before get_attachment
    up_dir = uploads_dir(ctx.config, ctx.conv_id)
    files_before = list(up_dir.iterdir())
    assert len(files_before) == 1

    with caplog.at_level("WARNING"):
        result = await tool_get_attachment(ctx, filename)
        assert isinstance(result, ToolResult)
        file_ids = await process_tool_media(ctx, result)

    # Verify no warning logged (previously: Failed to save media ... TypeError: memoryview: a bytes-like object is required, not 'str')
    assert not any("Failed to save media" in record.message for record in caplog.records)
    assert file_ids == []

    # Verify no second file was created in uploads/
    files_after = list(up_dir.iterdir())
    assert len(files_after) == 1

    # Verify result text has exactly one image reference to the stored file
    assert result.text.count("![") == 1
    assert saved["path"] in result.text


@pytest.mark.asyncio
async def test_get_attachment_image_platform_handler_receives_bytes(ctx):
    png_bytes = b"\x89PNG\r\n\x1a\nfake-image-bytes"
    saved = save_attachment(ctx.config, ctx.conv_id, "photo.png", png_bytes, "image/png")
    filename = saved["filename"]

    handler = FakeMattermostMediaHandler()
    ctx.media_handler = handler

    result = await tool_get_attachment(ctx, filename)
    assert isinstance(result, ToolResult)
    # The media item should be populated
    assert len(result.media) == 1
    item = result.media[0]
    assert item["type"] == "file"
    assert item["filename"] == filename
    assert item["content_type"] == "image/png"
    assert isinstance(item["data"], bytes)
    assert item["data"] == png_bytes

    file_ids = await process_tool_media(ctx, result)
    assert file_ids == ["mm-file-123"]
    assert len(handler.saved) == 1
    _conv_id, _name, data, _mime = handler.saved[0]
    assert isinstance(data, bytes)
    assert data == png_bytes


@pytest.mark.asyncio
async def test_get_attachment_text_file(ctx):
    saved = save_attachment(ctx.config, ctx.conv_id, "note.txt", b"Hello world\nSecond line", "text/plain")
    filename = saved["filename"]

    result = await tool_get_attachment(ctx, filename)
    assert isinstance(result, str)
    assert "File: " in result
    assert "Hello world\nSecond line" in result


@pytest.mark.asyncio
async def test_get_attachment_binary_non_image(ctx):
    pdf_bytes = b"%PDF-1.4 fake pdf"
    saved = save_attachment(ctx.config, ctx.conv_id, "doc.pdf", pdf_bytes, "application/pdf")
    filename = saved["filename"]

    result = await tool_get_attachment(ctx, filename)
    assert isinstance(result, str)
    assert "base64 string length" in result


@pytest.mark.asyncio
async def test_get_attachment_not_found(ctx):
    result = await tool_get_attachment(ctx, "nonexistent.png")
    assert isinstance(result, ToolResult)
    assert "[error: attachment not found: nonexistent.png]" in result.text


@pytest.mark.asyncio
async def test_list_attachments_tool(ctx):
    save_attachment(ctx.config, ctx.conv_id, "a.png", b"img", "image/png")
    result = await tool_list_attachments(ctx)
    assert isinstance(result, str)
    assert "a-" in result


@pytest.mark.asyncio
async def test_get_attachment_image_no_handler(ctx, caplog):
    png_bytes = b"\x89PNG\r\n\x1a\nfake-image-bytes"
    saved = save_attachment(ctx.config, ctx.conv_id, "photo.png", png_bytes, "image/png")
    filename = saved["filename"]

    ctx.media_handler = None

    with caplog.at_level("WARNING"):
        result = await tool_get_attachment(ctx, filename)
        assert isinstance(result, ToolResult)
        assert result.media == []
        file_ids = await process_tool_media(ctx, result)

    assert not any("No media handler" in record.message for record in caplog.records)
    assert file_ids == []


@pytest.mark.asyncio
async def test_get_attachment_image_mattermost_handler(ctx):
    png_bytes = b"\x89PNG\r\n\x1a\nfake-image-bytes"
    saved = save_attachment(ctx.config, ctx.conv_id, "photo.png", png_bytes, "image/png")
    filename = saved["filename"]

    http = AsyncMock()
    upload_resp = MagicMock()
    upload_resp.status_code = 200
    upload_resp.json.return_value = {"file_infos": [{"id": "mm-file-456"}]}
    upload_resp.raise_for_status = MagicMock()
    http.post.return_value = upload_resp

    handler = MattermostMediaHandler(http, channel_id="test-channel")
    ctx.media_handler = handler

    result = await tool_get_attachment(ctx, filename)
    assert isinstance(result, ToolResult)
    assert len(result.media) == 1
    assert result.media[0]["data"] == png_bytes

    file_ids = await process_tool_media(ctx, result)
    assert file_ids == ["mm-file-456"]
    assert http.post.called


@pytest.mark.asyncio
async def test_get_attachment_image_missing_file_on_disk(ctx, monkeypatch):
    saved = save_attachment(ctx.config, ctx.conv_id, "gone.png", b"bytes", "image/png")
    filename = saved["filename"]

    monkeypatch.setattr("decafclaw.tools.attachment_tools.read_attachment_bytes", lambda *args: None)

    result = await tool_get_attachment(ctx, filename)
    assert isinstance(result, ToolResult)
    assert f"[error: could not read file: {filename}]" in result.text


def test_should_provide_media_helper(ctx):
    assert _should_provide_media(None) is False
    assert _should_provide_media(LocalFileMediaHandler(ctx.config)) is False

    class NonUploadingHandler(MediaHandler):
        uploads_to_platform = False

    assert _should_provide_media(NonUploadingHandler()) is False
    assert _should_provide_media(MediaHandler()) is True
    assert _should_provide_media(MattermostMediaHandler(AsyncMock())) is True
