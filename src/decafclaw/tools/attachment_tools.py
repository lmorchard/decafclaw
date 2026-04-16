"""Attachment tools — list and retrieve conversation file attachments."""

import json

from ..attachments import list_conversation_attachments, read_attachment_base64
from ..media import ToolResult


async def tool_list_attachments(ctx) -> str | ToolResult:
    """List files uploaded to the current conversation."""
    conv_id = ctx.conv_id or ctx.channel_id
    if not conv_id:
        return ToolResult(text="[error: no conversation context]")

    items = list_conversation_attachments(ctx.config, conv_id)
    if not items:
        return "No attachments in this conversation."

    return json.dumps(items, indent=2)


async def tool_get_attachment(ctx, filename: str) -> str | ToolResult:
    """Retrieve a file's content from the conversation uploads."""
    conv_id = ctx.conv_id or ctx.channel_id
    if not conv_id:
        return ToolResult(text="[error: no conversation context]")

    items = list_conversation_attachments(ctx.config, conv_id)
    match = next((i for i in items if i["filename"] == filename), None)
    if not match:
        return ToolResult(text=f"[error: attachment not found: {filename}]")

    mime = match.get("mime_type", "application/octet-stream")

    if mime.startswith("image/"):
        b64 = read_attachment_base64(ctx.config, match)
        if b64 is None:
            return ToolResult(text=f"[error: could not read file: {filename}]")
        # Include workspace path as markdown image so the assistant can
        # embed it in responses (web UI rewrites to /api/workspace/ URLs)
        path = match["path"]
        return ToolResult(
            text=f"Image attachment: {filename} ({mime})\n\n![{filename}]({path})",
            media=[{
                "type": "file",
                "filename": filename,
                "data": b64,
                "content_type": mime,
            }],
        )

    if mime.startswith("text/"):
        full_path = ctx.config.workspace_path / match["path"]
        max_lines = 200  # same cap as workspace_read
        try:
            lines = full_path.read_text().splitlines(keepends=True)
            if len(lines) > max_lines:
                content = "".join(lines[:max_lines])
                return (
                    f"File: {filename} (showing first {max_lines} of "
                    f"{len(lines)} lines)\n\n{content}"
                )
            return f"File: {filename}\n\n{''.join(lines)}"
        except Exception as e:
            return ToolResult(text=f"[error reading {filename}: {e}]")

    b64 = read_attachment_base64(ctx.config, match)
    if b64 is None:
        return ToolResult(text=f"[error: could not read file: {filename}]")
    return f"File: {filename} ({mime}), base64 string length {len(b64)} characters."


ATTACHMENT_TOOLS = {
    "list_attachments": tool_list_attachments,
    "get_attachment": tool_get_attachment,
}

ATTACHMENT_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "priority": "normal",
        "function": {
            "name": "list_attachments",
            "description": (
                "List all files uploaded to the current conversation. "
                "Returns filename, path, MIME type, and size for each. "
                "Useful after compaction to rediscover attachment files "
                "that were dropped from the summary."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "priority": "normal",
        "function": {
            "name": "get_attachment",
            "description": (
                "Retrieve a specific attachment from the current conversation "
                "by filename. For images, returns the image data. For text files, "
                "returns the file content. Use after list_attachments to re-examine "
                "a file that was dropped by compaction."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {
                        "type": "string",
                        "description": "The filename to retrieve (from list_attachments output)",
                    },
                },
                "required": ["filename"],
            },
        },
    },
]
