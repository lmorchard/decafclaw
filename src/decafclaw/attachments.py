"""Attachment storage — save, read, list, and delete conversation file attachments."""

import base64
import logging
import mimetypes
from datetime import datetime
from pathlib import Path

from decafclaw.conversation_paths import conversation_dir

log = logging.getLogger(__name__)


def uploads_dir(config, conv_id: str) -> Path:
    """Return the uploads directory for a conversation (does not create it).

    Delegates to `conversation_dir` so the user-controlled `conv_id` is
    sanitized and sandboxed under the conversations root (#587).
    """
    return conversation_dir(config, conv_id) / "uploads"


def save_attachment(config, conv_id: str, filename: str, data: bytes,
                    content_type: str) -> dict:
    """Save a file, returning attachment metadata."""
    dest_dir = uploads_dir(config, conv_id)
    dest_dir.mkdir(parents=True, exist_ok=True)

    # Sanitize filename to prevent path traversal
    filename = Path(filename).name
    if not filename:
        filename = "upload"

    # Always generate a unique name with timestamp to avoid collisions
    # and make files distinguishable (e.g., multiple clipboard pastes)
    stem = Path(filename).stem
    suffix = Path(filename).suffix
    # Normalize generic clipboard paste names
    if stem == "image":
        stem = "paste"
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    filename = f"{stem}-{ts}{suffix}"
    dest = dest_dir / filename
    # Verify resolved path stays within dest_dir
    if not dest.resolve().is_relative_to(dest_dir.resolve()):
        raise ValueError(f"Invalid filename: {filename}")
    # Handle sub-second collisions
    counter = 1
    while dest.exists():
        filename = f"{stem}-{ts}-{counter}{suffix}"
        dest = dest_dir / filename
        counter += 1

    # Write original file
    dest.write_bytes(data)

    # Build workspace-relative path
    rel_path = str(dest.relative_to(config.workspace_path))

    return {"filename": filename, "path": rel_path, "mime_type": content_type}


def read_attachment_base64(config, attachment: dict) -> str | None:
    """Read a file and return its base64-encoded content."""
    rel_path = attachment.get("path", "")
    if not rel_path:
        return None
    full_path = config.workspace_path / rel_path

    if full_path.exists():
        return base64.b64encode(full_path.read_bytes()).decode("ascii")

    log.warning(f"Attachment file not found: {full_path}")
    return None


def list_conversation_attachments(config, conv_id: str) -> list[dict]:
    """List all attachments for a conversation."""
    dest_dir = uploads_dir(config, conv_id)
    if not dest_dir.exists():
        return []
    results = []
    for f in sorted(dest_dir.iterdir()):
        if f.is_file():
            mime = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
            rel_path = str(f.relative_to(config.workspace_path))
            results.append({
                "filename": f.name,
                "path": rel_path,
                "mime_type": mime,
                "size_bytes": f.stat().st_size,
            })
    return results


def delete_conversation_uploads(config, conv_id: str) -> None:
    """Remove the entire uploads directory for a conversation."""
    import shutil
    dest_dir = uploads_dir(config, conv_id)
    if dest_dir.exists():
        shutil.rmtree(dest_dir)


def resolve_attachments(config, message: dict) -> dict:
    """Transform a message with attachments into multimodal content for the LLM.

    Messages without attachments pass through unchanged. The archive stores
    plain text + attachment metadata; this builds the ephemeral content array.
    """
    atts = message.get("attachments")
    if not atts:
        return message

    content_parts: list[dict] = []
    text = message.get("content", "")
    if text:
        content_parts.append({"type": "text", "text": text})

    for att in atts:
        b64_data = read_attachment_base64(config, att)
        if b64_data is None:
            content_parts.append({
                "type": "text",
                "text": f"[attachment missing: {att.get('filename', '?')}]",
            })
            continue

        mime = att.get("mime_type", "application/octet-stream")
        # TODO(#137): MIME type is client-supplied — validate with magic bytes
        # server-side to prevent non-images from being base64-embedded
        if mime.startswith("image/"):
            content_parts.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{b64_data}"},
            })
        else:
            # Non-image: represent as a textual placeholder only
            # (binary data is not sent to the LLM)
            content_parts.append({
                "type": "text",
                "text": f"[file: {att.get('filename', '?')} ({mime})]",
            })

    # Return message with multimodal content, stripping attachments key
    result = {k: v for k, v in message.items() if k != "attachments"}
    result["content"] = content_parts
    return result
