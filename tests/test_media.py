"""Tests for media handling — ToolResult, workspace image scanning."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from decafclaw.media import (
    MattermostMediaHandler,
    TerminalMediaHandler,
    ToolResult,
    extract_workspace_media,
    process_media_for_terminal,
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


# -- TerminalMediaHandler tests --


@pytest.mark.asyncio
async def test_terminal_handler_upload(tmp_path):
    handler = TerminalMediaHandler(tmp_path)
    path = await handler.upload_file("ch", "test.png", b"data", "image/png")
    assert (tmp_path / "media" / "test.png").exists()
    assert path == "media/test.png"


def test_process_media_for_terminal(tmp_path):
    result = ToolResult(text="Here's the image", media=[
        {"type": "file", "filename": "pic.png", "data": b"png-data", "content_type": "image/png"},
    ])
    output = process_media_for_terminal(result, tmp_path)
    assert "[file saved: media/pic.png]" in output
    assert (tmp_path / "media" / "pic.png").exists()


def test_process_media_for_terminal_url():
    result = ToolResult(text="Check this", media=[
        {"type": "url", "url": "https://example.com/img.png", "alt": "image"},
    ])
    output = process_media_for_terminal(result, Path("/tmp"))
    assert "[image: https://example.com/img.png]" in output


def test_process_media_for_terminal_no_media():
    result = ToolResult(text="plain text")
    output = process_media_for_terminal(result, Path("/tmp"))
    assert output == "plain text"


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


@pytest.mark.asyncio
async def test_mattermost_upload_file():
    http = _make_mock_http()
    handler = MattermostMediaHandler(http)
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

    handler = MattermostMediaHandler(http)
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

    handler = MattermostMediaHandler(http)
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
    handler = MattermostMediaHandler(None)
    card = handler.format_attachment_card("Title", "Body", image_url="https://img.png")
    assert card["title"] == "Title"
    assert card["text"] == "Body"
    assert card["image_url"] == "https://img.png"
