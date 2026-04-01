# Session Notes

## Log

- Session started 2026-03-27
- Addresses #141 (web UI agent media delivery) and #135 (unify media storage)
- Phase 1: Added MediaSaveResult, save_media() to all handlers — commit 7270842
- Phase 2: Per-tool-call media processing, removed pending_media — commit 34e2908
- Phase 3: Wired handlers on all channels (web, terminal, mattermost) — commit 4fe0aea
- Phase 4: Frontend workspace:// link rewriting for images and links — commit 91418be
- Phase 5: Cleanup — removed process_media_for_terminal, workspace/media/ refs

## Key Decisions

- **Critical gap found during plan review**: `extract_workspace_media` strips `workspace://` refs from agent text. For web UI this is harmful — the frontend needs those refs to render. Fixed by making extraction conditional via `strips_workspace_refs` flag on MediaHandler.
- **Mattermost media attachment**: Changed from batching media at end-of-turn to per-tool-call via `tool_media_uploaded` event. Mattermost subscriber posts file_ids as thread replies.
- **Terminal simplification**: Terminal no longer saves to `workspace/media/`. Uses same `save_attachment()` path as web. Raw workspace:// refs shown in terminal output.
