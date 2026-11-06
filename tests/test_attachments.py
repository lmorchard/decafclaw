"""Tests for attachment storage utilities."""

import base64

from decafclaw.attachments import (
    list_conversation_attachments,
    read_attachment_base64,
    read_attachment_bytes,
    save_attachment,
    uploads_dir,
)
from decafclaw.conversation_paths import sessions_root


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


def test_read_attachment_bytes(config):
    data = b"hello bytes"
    result = save_attachment(config, "conv1", "file.bin", data, "application/octet-stream")

    raw = read_attachment_bytes(config, result)
    assert raw == data


def test_read_attachment_bytes_missing_file(config):
    result = read_attachment_bytes(config, {"path": "conversations/conv1/uploads/gone.bin"})
    assert result is None


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


# --- sandboxing (#587) ------------------------------------------------------


def test_uploads_dir_traversal_stays_under_root(config):
    # conv_id from web routes is user-controlled; a traversal id must not
    # escape the sessions root (#1112: brand-new ids resolve there).
    root = sessions_root(config)
    d = uploads_dir(config, "../../etc")
    assert d.resolve().is_relative_to(root)


def test_uploads_dir_empty_id_resolves_to_invalid(config):
    root = sessions_root(config)
    d = uploads_dir(config, "")
    assert d == root / "_invalid" / "uploads"


# -- Legacy stored-path remap (#1220 follow-up) --------------------------------
#
# Messages saved before `make migrate-sessions` (#1112) carry attachment
# paths like `conversations/{conv_id}/uploads/{name}`. The migration moved
# those dirs under `sessions/` — standalone at `sessions/{conv_id}/`,
# children at `sessions/{parent}/{child}/` — so stored paths went stale.
# Reads must follow the file to its session location.


def test_read_attachment_bytes_legacy_path_follows_session_layout(config):
    data = b"scanned pdf bytes"
    result = save_attachment(config, "conv1", "scan.pdf", data, "application/pdf")

    # Simulate the pre-migration stored path: same tail, legacy root.
    new_rel = result["path"]  # sessions/conv1/uploads/scan-<ts>.pdf
    tail = new_rel.split("/", 1)[1]
    legacy_attachment = {"filename": result["filename"], "path": f"conversations/{tail}"}

    assert read_attachment_bytes(config, legacy_attachment) == data


def test_read_attachment_base64_legacy_path_follows_session_layout(config):
    data = b"child pdf bytes"
    result = save_attachment(config, "parent1--child-abc123", "scan.pdf", data, "application/pdf")

    # New rel: sessions/parent1/parent1--child-abc123/uploads/<file> —
    # legacy rel: conversations/parent1--child-abc123/uploads/<file>.
    parts = result["path"].split("/")
    legacy_rel = "/".join(["conversations", parts[2], *parts[3:]])
    assert legacy_rel.startswith("conversations/parent1--child-abc123/uploads/")

    assert read_attachment_base64(config, {"path": legacy_rel}) == base64.b64encode(data).decode("ascii")


def test_read_attachment_bytes_missing_everywhere_returns_none(config):
    assert read_attachment_bytes(config, {"path": "conversations/ghost/uploads/x.pdf"}) is None
    assert read_attachment_bytes(config, {"path": "vault/pages/Nope.md"}) is None
