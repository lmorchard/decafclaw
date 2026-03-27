"""Tests for _resolve_attachments in agent.py."""

import pytest

from decafclaw.agent import _resolve_attachments
from decafclaw.attachments import save_attachment


def test_message_without_attachments_passes_through(config):
    """Messages without attachments are returned unchanged."""
    msg = {"role": "user", "content": "hello"}
    result = _resolve_attachments(config, msg)
    assert result is msg


def test_image_attachment_becomes_multimodal(config):
    """A message with an image attachment becomes a multimodal content array."""
    # Create a real attachment file via save_attachment
    pixel = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
        b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00"
        b"\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00"
        b"\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    att = save_attachment(config, "test-conv", "photo.png", pixel, "image/png")

    msg = {"role": "user", "content": "look at this", "attachments": [att]}
    result = _resolve_attachments(config, msg)

    assert "attachments" not in result
    assert isinstance(result["content"], list)
    # First part is the text
    assert result["content"][0] == {"type": "text", "text": "look at this"}
    # Second part is the image_url
    img_part = result["content"][1]
    assert img_part["type"] == "image_url"
    assert img_part["image_url"]["url"].startswith("data:image/png;base64,")


def test_missing_file_gets_placeholder(config):
    """An attachment whose file is missing gets a textual placeholder."""
    att = {
        "filename": "gone.png",
        "path": "conversations/test-conv/uploads/gone.png",
        "mime_type": "image/png",
    }
    msg = {"role": "user", "content": "see this", "attachments": [att]}
    result = _resolve_attachments(config, msg)

    assert isinstance(result["content"], list)
    # Text content still present
    assert result["content"][0] == {"type": "text", "text": "see this"}
    # Missing file placeholder
    placeholder = result["content"][1]
    assert placeholder["type"] == "text"
    assert "attachment missing" in placeholder["text"]
    assert "gone.png" in placeholder["text"]
