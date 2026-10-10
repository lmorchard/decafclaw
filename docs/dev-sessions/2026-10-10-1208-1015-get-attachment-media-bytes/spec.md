# Spec: Fix get_attachment media data bytes and avoid duplicate uploads

**Issue:** #1015 (https://github.com/lmorchard/decafclaw/issues/1015)

## Problem

The image branch of `tool_get_attachment` puts a base64 `str` in `ToolResult.media[].data`. Every `MediaHandler.save_media` implementation (`LocalFileMediaHandler`, `MattermostMediaHandler`) expects `bytes`.

Inside `process_tool_media`, `item["data"]` is passed directly to `handler.save_media`. Because `item["data"]` is a `str`, `save_media` raises:
- `TypeError: memoryview: a bytes-like object is required, not 'str'` in `LocalFileMediaHandler` (calls `dest.write_bytes(data)`)
- `TypeError: a bytes-like object is required, not 'str'` in `MattermostMediaHandler` (calls `io.BytesIO(data)`)

`process_tool_media` catches any exception, logs a warning ("Failed to save media ..."), and drops the media item.

Furthermore:
In Web and terminal contexts (`LocalFileMediaHandler`), the attachment file is *already* stored in `uploads/` (or the attachments dir) and the tool result text already contains a Markdown image reference pointing to the existing file:
```python
text = f"Image attachment stored at: `{rel_path}`\n\n![{filename}]({image_url})"
```
If `media` is populated with `bytes` when using `LocalFileMediaHandler`, `LocalFileMediaHandler.save_media` would re-save the bytes as a *new* file in `uploads/` with a new UUID filename, and `process_tool_media` would append a *second* `![image](url)` reference to the output text (because the text has no `[file attached: ...]` placeholder).

## Proposed Solution

1. In `src/decafclaw/tools/attachment_tools.py`:
   - Raw bytes of the image should be obtained (via `raw_data = path.read_bytes()` or decoding base64 if needed).
   - Media item in `ToolResult(media=...)` should only be returned when the active media handler is NOT a local file handler (or more specifically, when the handler needs to upload media to an external platform like Mattermost: e.g. when `ctx.media_handler` is not `None` and not `isinstance(ctx.media_handler, LocalFileMediaHandler)`, or checking whether the handler uploads).
   Wait, let's inspect `media_handler` implementations across the codebase to see how media handlers are structured.

## Acceptance Criteria

1. Write the failing test first. A test calls `tool_get_attachment` for a stored PNG, then runs `process_tool_media` with a `LocalFileMediaHandler`. Today it shows the `TypeError` path: warning logged and save fails. After the fix, no warning is logged.
2. With a Mattermost-style handler (or handler expecting bytes), the handler receives `bytes` that are equal to the stored file's bytes.
3. With `LocalFileMediaHandler`, a call of `get_attachment` does not create a new file in `uploads/`, and the result text has exactly one image reference to the stored file.
4. Text and non-image branches of `get_attachment` do not change.
5. `make check` and `make test` pass with no new warnings.
