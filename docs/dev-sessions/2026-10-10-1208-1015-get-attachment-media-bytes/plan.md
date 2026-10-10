# Implementation Plan: Fix get_attachment media data bytes and avoid duplicate uploads

**Issue:** #1015 (https://github.com/lmorchard/decafclaw/issues/1015)

## Overview

1. Add `read_attachment_bytes(config, attachment: dict) -> bytes | None` to `src/decafclaw/attachments.py`.
   Refactor `read_attachment_base64` to use `read_attachment_bytes`.
2. Add `uploads_to_platform: bool = True` to `MediaHandler` in `src/decafclaw/media.py`, and `uploads_to_platform: bool = False` to `LocalFileMediaHandler`.
3. In `src/decafclaw/tools/attachment_tools.py`:
   - Use `read_attachment_bytes` instead of `read_attachment_base64` in the image branch of `tool_get_attachment`.
   - Provide `media=[{"type": "file", "filename": filename, "data": raw_bytes, "content_type": mime}]` only when `_should_provide_media(ctx.media_handler)` is True (i.e. `handler is not None and not isinstance(handler, LocalFileMediaHandler) and getattr(handler, "uploads_to_platform", True)`).
4. Tests:
   - First, write a test in `tests/test_attachment_tools.py` reproducing the failure: calling `tool_get_attachment` on a PNG with `LocalFileMediaHandler` and passing the result to `process_tool_media` records the `TypeError` and warning log.
   - Test that after fix:
     - With `LocalFileMediaHandler`, `process_tool_media` produces no warnings, does not save a duplicate file in `uploads/`, and result text has exactly one image markdown reference.
     - With a platform media handler (Mattermost-style fake), `save_media` receives `bytes` matching the stored file's bytes.
     - Text and non-image branches of `get_attachment` are unchanged.
   - Add unit tests for `read_attachment_bytes` in `tests/test_attachments.py`.
5. Verify with `make check` and `make test`.

## Steps (TDD)

- Step 1: Write reproducing test in `tests/test_attachment_tools.py` and run pytest to observe failure.
- Step 2: Implement `read_attachment_bytes` in `attachments.py` and `uploads_to_platform` in `media.py`.
- Step 3: Implement fix in `attachment_tools.py`.
- Step 4: Run tests to verify they now pass. Add any additional tests for edge cases (missing file, non-image files, etc.).
- Step 5: Run `make check` and `make test`.
