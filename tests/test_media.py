"""Tests for media handling — ToolResult, workspace image scanning, save_media."""

from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from decafclaw.media import (
    LocalFileMediaHandler,
    MattermostMediaHandler,
    MediaHandler,
    MediaSaveResult,
    ToolResult,
    WidgetRequest,
    extract_workspace_media,
    upload_and_collect,
)

# -- ToolResult tests --


def test_tool_result_text_only():
    r = ToolResult(text="hello")
    assert r.text == "hello"
    assert r.media == []


def test_tool_result_with_media():
    r = ToolResult(text="image attached", media=[
        {"type": "file", "filename": "test.png", "data": b"png", "content_type": "image/png"}
    ])
    assert len(r.media) == 1
    assert r.media[0]["filename"] == "test.png"


def test_tool_result_display_text():
    r = ToolResult(text="result", display_text="custom display")
    assert r.display_text == "custom display"
    # Default is None
    r2 = ToolResult(text="result")
    assert r2.display_text is None


def test_tool_result_from_text():
    r = ToolResult.from_text("simple")
    assert r.text == "simple"
    assert r.media == []


def test_tool_result_widget_default_none():
    r = ToolResult(text="no widget here")
    assert r.widget is None


def test_tool_result_with_widget():
    req = WidgetRequest(widget_type="data_table",
                        data={"columns": [], "rows": []})
    r = ToolResult(text="table rendered", widget=req)
    assert r.widget is req
    assert r.widget.widget_type == "data_table"
    assert r.widget.target == "inline"  # default
    assert r.widget.on_response is None
    assert r.widget.response_message is None


def test_widget_request_canvas_target():
    req = WidgetRequest(widget_type="markdown_document",
                        data={"content": "# Summary"},
                        target="canvas")
    assert req.target == "canvas"


def test_widget_request_with_on_response():
    def _cb(_payload):
        return None
    req = WidgetRequest(widget_type="multiple_choice",
                        data={"options": []},
                        on_response=_cb,
                        response_message="Pick one")
    assert req.on_response is _cb
    assert req.response_message == "Pick one"


# -- extract_workspace_media tests --


def test_extract_workspace_media_finds_ref(tmp_path):
    # Create a fake image file
    img_path = tmp_path / "chart.png"
    img_path.write_bytes(b"fake-png-data")

    text = "Here's the chart: ![my chart](workspace://chart.png) enjoy!"
    cleaned, media = extract_workspace_media(text, tmp_path)

    assert "workspace://" not in cleaned
    assert "enjoy!" in cleaned
    assert len(media) == 1
    assert media[0]["filename"] == "chart.png"
    assert media[0]["data"] == b"fake-png-data"
    assert media[0]["content_type"] == "image/png"


def test_extract_workspace_media_leaves_public_urls(tmp_path):
    text = "Look: ![photo](https://example.com/photo.jpg) nice!"
    cleaned, media = extract_workspace_media(text, tmp_path)

    assert "https://example.com/photo.jpg" in cleaned
    assert media == []


def test_extract_workspace_media_handles_missing_file(tmp_path):
    text = "Missing: ![gone](workspace://missing.png)"
    cleaned, media = extract_workspace_media(text, tmp_path)

    assert "workspace://" not in cleaned
    assert media == []


def test_extract_workspace_media_multiple_refs(tmp_path):
    (tmp_path / "a.png").write_bytes(b"aaa")
    (tmp_path / "b.jpg").write_bytes(b"bbb")

    text = "![A](workspace://a.png) and ![B](workspace://b.jpg)"
    cleaned, media = extract_workspace_media(text, tmp_path)

    assert len(media) == 2
    assert "workspace://" not in cleaned


def test_extract_workspace_media_no_refs(tmp_path):
    text = "Just plain text, no images."
    cleaned, media = extract_workspace_media(text, tmp_path)

    assert cleaned == text
    assert media == []


# -- MediaSaveResult tests --


def test_media_save_result_defaults():
    r = MediaSaveResult()
    assert r.workspace_ref is None
    assert r.file_id is None
    assert r.saved_filename is None


def test_media_save_result_workspace():
    r = MediaSaveResult(workspace_ref="workspace://path/file.png",
                        saved_filename="file-20260327.png")
    assert r.workspace_ref == "workspace://path/file.png"
    assert r.saved_filename == "file-20260327.png"


def test_media_save_result_file_id():
    r = MediaSaveResult(file_id="abc123")
    assert r.file_id == "abc123"


# -- Base MediaHandler tests --


@pytest.mark.asyncio
async def test_base_handler_save_media_raises():
    handler = MediaHandler()
    with pytest.raises(NotImplementedError):
        await handler.save_media("conv1", "file.png", b"data", "image/png")


def test_base_handler_strips_workspace_refs_default():
    handler = MediaHandler()
    assert handler.strips_workspace_refs is True


# -- Helper: fake config for save_attachment --


@dataclass
class _FakeConfig:
    workspace_path: Path


# -- LocalFileMediaHandler tests --


def test_local_handler_strips_workspace_refs_default():
    config = _FakeConfig(workspace_path=Path("/tmp"))
    handler = LocalFileMediaHandler(config)
    assert handler.strips_workspace_refs is False


def test_local_handler_strips_workspace_refs_enabled():
    config = _FakeConfig(workspace_path=Path("/tmp"))
    handler = LocalFileMediaHandler(config, strips_workspace_refs=True)
    assert handler.strips_workspace_refs is True


@pytest.mark.asyncio
async def test_local_handler_save_media(tmp_path):
    config = _FakeConfig(workspace_path=tmp_path)
    handler = LocalFileMediaHandler(config)
    result = await handler.save_media("conv1", "test.png", b"png-data", "image/png")
    assert result.workspace_ref is not None
    assert result.workspace_ref.startswith("workspace://conversations/conv1/uploads/")
    assert result.saved_filename is not None
    assert "test" in result.saved_filename
    # Verify file was actually saved
    saved_path = tmp_path / result.workspace_ref.replace("workspace://", "")
    assert saved_path.exists()
    assert saved_path.read_bytes() == b"png-data"


@pytest.mark.asyncio
async def test_local_handler_upload_legacy_raises(tmp_path):
    config = _FakeConfig(workspace_path=tmp_path)
    handler = LocalFileMediaHandler(config)
    with pytest.raises(NotImplementedError):
        await handler.upload_file("ch", "test.png", b"data", "image/png")


# -- MattermostMediaHandler tests --


def _make_mock_http():
    """Create a mock HTTP client that returns expected Mattermost responses."""
    http = AsyncMock()

    upload_resp = MagicMock()
    upload_resp.status_code = 200
    upload_resp.json.return_value = {"file_infos": [{"id": "file-id-123"}]}
    upload_resp.raise_for_status = MagicMock()
    http.post.return_value = upload_resp

    return http


def test_mattermost_handler_strips_workspace_refs():
    assert MattermostMediaHandler.strips_workspace_refs is True


@pytest.mark.asyncio
async def test_mattermost_save_media():
    http = _make_mock_http()
    handler = MattermostMediaHandler(http, channel_id="ch1")
    result = await handler.save_media("conv1", "test.png", b"data", "image/png")
    assert result.file_id == "file-id-123"
    assert result.workspace_ref is None


@pytest.mark.asyncio
async def test_mattermost_upload_file():
    http = _make_mock_http()
    handler = MattermostMediaHandler(http, channel_id="ch1")
    file_id = await handler.upload_file("ch1", "test.png", b"data", "image/png")
    assert file_id == "file-id-123"
    # Verify the upload was called with correct params
    call_args = http.post.call_args
    assert "channel_id=ch1" in call_args[0][0]


@pytest.mark.asyncio
async def test_mattermost_send_with_media_single_batch():
    http = AsyncMock()
    post_resp = MagicMock()
    post_resp.json.return_value = {"id": "post-123"}
    post_resp.raise_for_status = MagicMock()
    http.post.return_value = post_resp

    handler = MattermostMediaHandler(http, channel_id="ch1")
    post_id = await handler.send_with_media("ch1", "Hello", ["f1", "f2"])
    assert post_id == "post-123"
    # Should be a single post call
    assert http.post.call_count == 1
    body = http.post.call_args[1]["json"]
    assert body["file_ids"] == ["f1", "f2"]
    assert body["message"] == "Hello"


@pytest.mark.asyncio
async def test_mattermost_send_with_media_overflow():
    http = AsyncMock()
    post_resp = MagicMock()
    post_resp.json.return_value = {"id": "post-123"}
    post_resp.raise_for_status = MagicMock()
    http.post.return_value = post_resp

    handler = MattermostMediaHandler(http, channel_id="ch1")
    file_ids = [f"f{i}" for i in range(15)]  # 15 files, needs 2 posts
    await handler.send_with_media("ch1", "Many files", file_ids)
    assert http.post.call_count == 2  # first 10 + remaining 5


@pytest.mark.asyncio
async def test_upload_and_collect():
    handler = AsyncMock(spec=MattermostMediaHandler)
    handler.upload_file = AsyncMock(side_effect=["id1", "id2"])

    items = [
        {"type": "file", "filename": "a.png", "data": b"a", "content_type": "image/png"},
        {"type": "file", "filename": "b.jpg", "data": b"b", "content_type": "image/jpeg"},
        {"type": "url", "url": "https://example.com/c.png", "alt": "c"},  # skipped
    ]
    ids = await upload_and_collect(handler, "ch1", items)
    assert ids == ["id1", "id2"]
    assert handler.upload_file.call_count == 2


def test_mattermost_format_attachment_card():
    handler = MattermostMediaHandler(None, channel_id="")
    card = handler.format_attachment_card("Title", "Body", image_url="https://img.png")
    assert card["title"] == "Title"
    assert card["text"] == "Body"
    assert card["image_url"] == "https://img.png"
