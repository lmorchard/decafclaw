"""Tests for attachment storage utilities."""

import base64

from decafclaw.attachments import (
    delete_conversation_uploads,
    list_conversation_attachments,
    read_attachment_base64,
    save_attachment,
    uploads_dir,
)


def test_save_attachment_writes_file(config):
    data = b"fake png data"
    result = save_attachment(config, "conv1", "photo.png", data, "image/png")

    # Always gets a timestamped name
    assert result["filename"].startswith("photo-")
    assert result["filename"].endswith(".png")
    assert result["mime_type"] == "image/png"

    dest = uploads_dir(config, "conv1") / result["filename"]
    assert dest.exists()
    assert dest.read_bytes() == data


def test_save_attachment_paste_renamed(config):
    data = b"data"
    result = save_attachment(config, "conv1", "image.png", data, "image/png")

    # Generic "image" stem renamed to "paste"
    assert result["filename"].startswith("paste-")
    assert result["filename"].endswith(".png")


def test_save_attachment_collision_handling(config):
    data = b"data"
    r1 = save_attachment(config, "conv1", "test.txt", data, "text/plain")
    r2 = save_attachment(config, "conv1", "test.txt", data, "text/plain")

    # Both get unique names
    assert r1["filename"] != r2["filename"]
    assert r1["filename"].startswith("test-")
    assert r2["filename"].startswith("test-")


def test_read_attachment_base64(config):
    data = b"hello world"
    result = save_attachment(config, "conv1", "file.txt", data, "text/plain")

    b64 = read_attachment_base64(config, result)
    assert b64 is not None
    assert base64.b64decode(b64) == data


def test_read_attachment_base64_missing_file(config):
    result = read_attachment_base64(config, {"path": "conversations/conv1/uploads/gone.png"})
    assert result is None


def test_list_attachments(config):
    save_attachment(config, "conv1", "a.png", b"img", "image/png")
    save_attachment(config, "conv1", "b.txt", b"txt", "text/plain")

    items = list_conversation_attachments(config, "conv1")
    filenames = [i["filename"] for i in items]
    assert len(filenames) == 2
    assert any(f.startswith("a-") and f.endswith(".png") for f in filenames)
    assert any(f.startswith("b-") and f.endswith(".txt") for f in filenames)
    assert all(i["size_bytes"] > 0 for i in items)


def test_list_attachments_empty_for_missing_conv(config):
    assert list_conversation_attachments(config, "nonexistent") == []


def test_delete_conversation_uploads(config):
    save_attachment(config, "conv1", "file.txt", b"data", "text/plain")
    assert uploads_dir(config, "conv1").exists()

    delete_conversation_uploads(config, "conv1")
    assert not uploads_dir(config, "conv1").exists()


def test_delete_conversation_uploads_noop_if_missing(config):
    # Should not raise
    delete_conversation_uploads(config, "nonexistent")
