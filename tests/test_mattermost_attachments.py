"""Tests for Mattermost inbound file attachment handling."""

from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from decafclaw.mattermost import MattermostClient


@dataclass
class _FakeConfig:
    workspace_path: Path


def _make_client(http_mock=None):
    """Create a MattermostClient with mocked internals for testing."""
    client = MattermostClient.__new__(MattermostClient)
    client._http = http_mock or AsyncMock()
    client.bot_user_id = "bot123"
    client.bot_username = "testbot"
    client.ignore_bots = True
    client.ignore_webhooks = True
    client.require_mention = False
    client.channel_blocklist = set()
    return client


# -- _handle_posted: file_ids extraction --


def test_handle_posted_extracts_file_ids():
    """file_ids and file_metadata are passed through in the message dict."""
    client = _make_client()
    captured = []

    post = {
        "id": "post1",
        "message": "check this out",
        "channel_id": "ch1",
        "user_id": "user1",
        "root_id": "",
        "type": "",
        "props": {},
        "file_ids": ["fid1", "fid2"],
        "metadata": {
            "files": [
                {"id": "fid1", "name": "photo.jpg", "mime_type": "image/jpeg"},
                {"id": "fid2", "name": "doc.pdf", "mime_type": "application/pdf"},
            ],
        },
    }
    import json
    evt = {
        "data": {
            "post": json.dumps(post),
            "sender_name": "user",
            "channel_type": "D",
        },
    }
    client._handle_posted(evt, lambda msg: captured.append(msg))

    assert len(captured) == 1
    msg = captured[0]
    assert msg["file_ids"] == ["fid1", "fid2"]
    assert msg["file_metadata"]["fid1"]["name"] == "photo.jpg"
    assert msg["file_metadata"]["fid2"]["mime_type"] == "application/pdf"


def test_handle_posted_file_only_message():
    """A message with files but no text should still be processed."""
    client = _make_client()
    captured = []

    post = {
        "id": "post1",
        "message": "",
        "channel_id": "ch1",
        "user_id": "user1",
        "root_id": "",
        "type": "",
        "props": {},
        "file_ids": ["fid1"],
        "metadata": {
            "files": [{"id": "fid1", "name": "image.png", "mime_type": "image/png"}],
        },
    }
    import json
    evt = {
        "data": {
            "post": json.dumps(post),
            "sender_name": "user",
            "channel_type": "D",
        },
    }
    client._handle_posted(evt, lambda msg: captured.append(msg))

    assert len(captured) == 1
    assert captured[0]["text"] == ""
    assert captured[0]["file_ids"] == ["fid1"]


def test_handle_posted_no_text_no_files_ignored():
    """A message with no text and no files should be ignored."""
    client = _make_client()
    captured = []

    post = {
        "id": "post1",
        "message": "",
        "channel_id": "ch1",
        "user_id": "user1",
        "root_id": "",
        "type": "",
        "props": {},
    }
    import json
    evt = {
        "data": {
            "post": json.dumps(post),
            "sender_name": "user",
            "channel_type": "D",
        },
    }
    client._handle_posted(evt, lambda msg: captured.append(msg))

    assert len(captured) == 0


# -- _download_attachments --


@pytest.mark.asyncio
async def test_download_attachments_saves_files(tmp_path):
    """Files are downloaded from Mattermost API and saved to conversation uploads."""
    http = AsyncMock()
    resp = MagicMock()
    resp.content = b"fake-image-data"
    resp.raise_for_status = MagicMock()
    http.get.return_value = resp

    client = _make_client(http)
    config = _FakeConfig(workspace_path=tmp_path)

    msgs = [{
        "text": "check this",
        "file_ids": ["fid1"],
        "file_metadata": {
            "fid1": {"name": "photo.jpg", "mime_type": "image/jpeg"},
        },
    }]

    attachments = await client._download_attachments(msgs, "conv1", config)

    assert len(attachments) == 1
    assert attachments[0]["mime_type"] == "image/jpeg"
    assert "photo" in attachments[0]["filename"]
    # Verify file was saved
    saved = tmp_path / attachments[0]["path"]
    assert saved.exists()
    assert saved.read_bytes() == b"fake-image-data"
    # Verify API was called correctly
    http.get.assert_called_once_with("/files/fid1")


@pytest.mark.asyncio
async def test_download_attachments_multiple_files(tmp_path):
    """Multiple files across messages are all downloaded."""
    http = AsyncMock()
    resp = MagicMock()
    resp.content = b"data"
    resp.raise_for_status = MagicMock()
    http.get.return_value = resp

    client = _make_client(http)
    config = _FakeConfig(workspace_path=tmp_path)

    msgs = [
        {
            "text": "msg1",
            "file_ids": ["fid1"],
            "file_metadata": {"fid1": {"name": "a.png", "mime_type": "image/png"}},
        },
        {
            "text": "msg2",
            "file_ids": ["fid2", "fid3"],
            "file_metadata": {
                "fid2": {"name": "b.jpg", "mime_type": "image/jpeg"},
                "fid3": {"name": "c.txt", "mime_type": "text/plain"},
            },
        },
    ]

    attachments = await client._download_attachments(msgs, "conv2", config)

    assert len(attachments) == 3
    assert http.get.call_count == 3


@pytest.mark.asyncio
async def test_download_attachments_failure_continues(tmp_path):
    """Failed downloads are logged but don't block other files."""
    http = AsyncMock()
    # First call fails, second succeeds
    fail_resp = MagicMock()
    fail_resp.raise_for_status.side_effect = RuntimeError("download failed")
    ok_resp = MagicMock()
    ok_resp.content = b"ok-data"
    ok_resp.raise_for_status = MagicMock()
    http.get.side_effect = [fail_resp, ok_resp]

    client = _make_client(http)
    config = _FakeConfig(workspace_path=tmp_path)

    msgs = [{
        "text": "files",
        "file_ids": ["bad", "good"],
        "file_metadata": {
            "bad": {"name": "fail.bin", "mime_type": "application/octet-stream"},
            "good": {"name": "ok.png", "mime_type": "image/png"},
        },
    }]

    attachments = await client._download_attachments(msgs, "conv3", config)

    assert len(attachments) == 1
    assert "ok" in attachments[0]["filename"]


@pytest.mark.asyncio
async def test_download_attachments_no_files(tmp_path):
    """Messages without file_ids return empty list."""
    client = _make_client()
    config = _FakeConfig(workspace_path=tmp_path)

    msgs = [{"text": "no files"}]
    attachments = await client._download_attachments(msgs, "conv4", config)

    assert attachments == []


@pytest.mark.asyncio
async def test_download_attachments_missing_metadata(tmp_path):
    """Files without metadata use fallback filename and MIME type."""
    http = AsyncMock()
    resp = MagicMock()
    resp.content = b"mystery-data"
    resp.raise_for_status = MagicMock()
    http.get.return_value = resp

    client = _make_client(http)
    config = _FakeConfig(workspace_path=tmp_path)

    msgs = [{
        "text": "mysterious file",
        "file_ids": ["fid1"],
        "file_metadata": {},  # no metadata for this file
    }]

    attachments = await client._download_attachments(msgs, "conv5", config)

    assert len(attachments) == 1
    assert attachments[0]["mime_type"] == "application/octet-stream"
