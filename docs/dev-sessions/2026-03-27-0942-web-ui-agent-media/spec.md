# Spec: Web UI Agent Media & Unified Media Storage

Addresses #141 (web UI agent media delivery) and #135 (unify media storage).

## Problem

When a tool returns a `ToolResult` with media (MCP binary resources, images, audio, Tabstack screenshots), the media is silently dropped in the web UI. Mattermost and terminal handle it, but the web UI never sets a `ctx.media_handler` and the websocket handler ignores `result.media`.

Additionally, agent-generated media uses a separate `workspace/media/` path from user uploads (`workspace/conversations/{conv_id}/uploads/`), creating inconsistency.

## Design Decisions

### Storage: conversation-scoped uploads

All agent-generated media saves to `workspace/conversations/{conv_id}/uploads/` via the existing `save_attachment()` function. This unifies agent and user media in one location per conversation. Terminal mode uses `conv_id="interactive"`.

The `workspace/media/` path is retired. No migration of existing files.

### MediaHandler interface: single `save_media()` method

Add a new unified method for per-tool-call media handling:

```python
@dataclass
class MediaSaveResult:
    workspace_ref: str | None = None  # workspace:// path for text injection
    file_id: str | None = None        # platform file ID (Mattermost)
    saved_filename: str | None = None # actual filename after dedup

class MediaHandler:
    async def save_media(self, conv_id, filename, data, content_type) -> MediaSaveResult:
        ...
```

Each channel implements differently:
- **Web/Terminal**: save via `save_attachment()`, return `workspace_ref` with the actual saved path
- **Mattermost**: upload to Mattermost API via channel_id (stored on handler at construction), return `file_id`

Note: The existing `upload_file()` and `send_with_media()` methods are retained for the `extract_workspace_media` end-of-turn flow in Mattermost (which still needs to upload workspace refs from the agent's final response text). They can be removed in a future cleanup once that flow is also migrated.

### Per-tool-call media processing

Media is handled immediately during tool execution, not accumulated to end-of-turn. This replaces the `pending_media` accumulation pattern.

Post-processing step in `execute_tool` (agent.py), after tool function returns:

1. If `result.media` is empty, skip
2. If `ctx.media_handler` is None, log warning, leave placeholder text as-is
3. For each media item, call `ctx.media_handler.save_media()`
4. Based on result:
   - If `workspace_ref`: replace placeholder text in `result.text` with markdown ref
   - If `file_id`: attach to current post (Mattermost tool status message)
5. Clear `result.media` after processing

### Text injection format

Placeholder text like `[file attached: mcp-resource-1.png (image/png) — will appear as an attachment on your reply]` gets replaced with markdown refs.

**Matching**: Each media item has a `filename` field. The post-processor finds the placeholder containing that filename in `result.text` and replaces it. `save_attachment()` may return a different filename (timestamp dedup) — the `workspace_ref` in `MediaSaveResult` contains the actual saved path.

**Replacement format** (based on content_type):
- **Images** (`image/*`): `![mcp-resource-1.png](workspace://conversations/{conv_id}/uploads/20260327-094200-mcp-resource-1.png)`
- **Non-image files**: `[mcp-resource-1.bin](workspace://conversations/{conv_id}/uploads/20260327-094200-mcp-resource-1.bin)`

Full `workspace://` paths from workspace root. No short aliases.

### Concurrency

Tool calls execute concurrently via `asyncio.gather`. Each tool gets its own `ToolResult`, so per-tool placeholder replacement is safe. `save_attachment()` uses timestamp-based filenames, so concurrent saves don't collide.

### Mattermost behavior unchanged

Mattermost keeps its current upload-to-API approach. Per-tool-call means files attach to the tool status progress post rather than batching onto the final response.

`extract_workspace_media()` is now **conditional** — only runs when the media handler has `strips_workspace_refs=True` (Mattermost). Web and Terminal set this to False because their workspace:// refs render in-place. Running extraction on web UI would strip the refs the frontend needs to render, and the extracted media would have nowhere to go.

### Frontend changes

Add `renderer.link` override in `assistant-message.js` to rewrite `workspace://` hrefs to `/api/workspace/` URLs, matching the existing `renderer.image` pattern. DOMPurify already allows `<a>` tags.

### Error handling

- No media handler on ctx → log warning, leave placeholder text unchanged
- Save/upload failure → log warning, leave placeholder text unchanged (fail-open)

## Out of scope (follow-up issues)

- Inline audio/video players in web UI (file as follow-up issue)
- Web UI autocomplete for commands and resources (#139)

## Channels affected

| Channel | Media handler | Behavior |
|---------|--------------|----------|
| Web UI | New `WebMediaHandler` | Save to conversation uploads, inject `workspace://` refs |
| Terminal | Updated `TerminalMediaHandler` | Save to conversation uploads (conv_id=interactive), inject `workspace://` refs |
| Mattermost | Updated `MattermostMediaHandler` | Upload to Mattermost API, attach to tool status post |
