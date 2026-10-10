# Notes: 1015-get-attachment-media-bytes

- Issue: https://github.com/lmorchard/decafclaw/issues/1015
- Branch: `fix-1015-get-attachment-media-bytes`
- Worktree: `.claude/worktrees/fix-1015-get-attachment-media-bytes`

## Summary of Changes

1. Added `read_attachment_bytes(config, attachment: dict) -> bytes | None` to `src/decafclaw/attachments.py` and refactored `read_attachment_base64` to reuse it.
2. Added `uploads_to_platform: bool = True` to `MediaHandler` base class in `src/decafclaw/media.py`, and `uploads_to_platform: bool = False` to `LocalFileMediaHandler`.
3. Updated `src/decafclaw/tools/attachment_tools.py`:
   - Image branch of `tool_get_attachment` now reads raw bytes using `read_attachment_bytes`.
   - Media items are only returned when `_should_provide_media(ctx.media_handler)` is True (i.e. handler is non-null, not a `LocalFileMediaHandler`, and `uploads_to_platform` is True).
   - For `LocalFileMediaHandler` (web UI / terminal) and when no media handler is active, `media` is empty, avoiding duplicate files in `uploads/` and duplicate markdown image references.
4. Added tests:
   - `tests/test_attachment_tools.py` covering local handler, Mattermost handler, no handler, text attachments, non-image binaries, and error paths.
   - `tests/test_attachments.py` testing `read_attachment_bytes`.
   - `tests/test_media.py` verifying `uploads_to_platform` property on handlers.

## Verification

- `make check` passed (format check, lint, pyright, typescript, message-types, static graph).
- `make test` passed (4438 passed, 2 skipped).
