# File/Image Attachment — Spec

**Issue:** #58
**Branch:** `web-ui-file-attachments`
**Scope:** Unified file attachment pipeline for web UI and Mattermost, covering user uploads, agent-generated media, archive storage, and LLM multimodal support.

## Overview

Allow users to attach files to messages across all channels (web UI, Mattermost). Unify user uploads and agent-generated media into a single per-conversation storage and delivery pipeline. Files are stored on disk, referenced in the archive by path, and assembled into multimodal LLM content at call time.

## Storage Layout

Files are co-located with their conversation archive:

```
workspace/conversations/
  {conv_id}.jsonl                          ← message archive
  {conv_id}.compacted.jsonl                ← compacted working history
  {conv_id}/
    uploads/
      {filename}                           ← original file
      {filename}.b64.gz                    ← gzipped base64 sidecar cache
```

Both user uploads and agent-generated media (Tabstack screenshots, tool output, etc.) use this same location.

## Upload Endpoint (Web UI)

- **Route:** `POST /api/upload/{conv_id}`
- Auth-gated (same cookie auth as other endpoints)
- Accepts multipart file upload
- Writes to `workspace/conversations/{conv_id}/uploads/`
- Generates `.b64.gz` sidecar on upload
- Returns workspace-relative path for the client to reference
- **File size limit:** 100MB (configurable)
- **File types:** Any type the LLM can handle — images (jpg, png, gif, webp), PDFs, text files, etc. No artificial restriction.
- Filename collision handling: append timestamp or counter to deduplicate

## Archive Format

User messages with attachments store content as a plain string with a separate `attachments` array:

```json
{
  "role": "user",
  "content": "Please analyze this image",
  "attachments": [
    {
      "filename": "screenshot.png",
      "path": "conversations/web-user-xxx/uploads/screenshot.png",
      "mime_type": "image/png"
    }
  ],
  "timestamp": "2026-03-26T12:00:00"
}
```

- `content` stays a plain string for readability and compatibility
- `attachments` is metadata only — no inline binary data
- Same format regardless of channel (web UI or Mattermost)

## LLM Message Construction

Happens in `agent.py` before calling `call_llm()` (which is a pure pass-through to the LLM endpoint). When building `llm_history`, messages with `attachments` are transformed into multimodal content arrays:

```json
{
  "role": "user",
  "content": [
    {"type": "text", "text": "Please analyze this image"},
    {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}
  ]
}
```

- Read base64 from `.b64.gz` sidecar if available, fall back to re-reading and encoding the original file
- Let the LLM API enforce its own context/size limits — don't try to pre-calculate token cost
- Messages without attachments continue to use plain string `content`
- The archive always stores the plain text + attachments metadata format; multimodal arrays are ephemeral, built per-call

## Frontend (Web UI)

### Input (`chat-input.js`)

- **Paperclip button** — opens native file picker
- **Drag-and-drop** — onto the chat input area
- **Clipboard paste** — Cmd/Ctrl+V for screenshots and copied images
- **Preview strip** above the textarea showing pending attachments:
  - Images: thumbnail preview + remove button
  - Non-image files: filename + type icon + remove button
- Attachments cleared on send

### Upload Flow

1. User selects/drops/pastes file(s)
2. Immediately upload via `POST /api/upload/{conv_id}`
3. Store returned path reference in pending attachments list
4. On send: include `attachments` array in WebSocket `send` message alongside `text`

### WebSocket Send Message

```json
{
  "type": "send",
  "conv_id": "web-user-xxx",
  "text": "Please analyze this image",
  "attachments": [
    {
      "filename": "screenshot.png",
      "path": "conversations/web-user-xxx/uploads/screenshot.png",
      "mime_type": "image/png"
    }
  ]
}
```

### Display

- User messages with image attachments render images inline
- Non-image attachments render as filename + icon links
- Agent-generated media served via existing `/api/workspace/` route

## Mattermost Inbound

- When a Mattermost message includes file attachments, download them via the Mattermost API
- Store in the same `conversations/{conv_id}/uploads/` location
- Archive in the same format as web UI attachments
- Same LLM multimodal construction pipeline

## Agent-Generated Media Unification

- Migrate existing `media.py` / `workspace://` media handling to use the conversation uploads pipeline
- Tool-generated media (screenshots, file output) stored in `conversations/{conv_id}/uploads/`
- Removes the separate `workspace/media/` path for conversation-bound media

## Agent Tools

Two new tools for attachment management:

- **`list_attachments(conv_id)`** — list all files in the conversation's uploads directory with metadata
- **`get_attachment(conv_id, filename)`** — re-inject a file's content into the current LLM context (useful after compaction drops the original multimodal content)

## Compaction Behavior

- Compaction drops attachment references — the summary is text-only
- Original files remain on disk
- Agent can use `list_attachments` / `get_attachment` to re-access files after compaction

## Compaction Details

Verified safe: `flatten_messages()` in `compaction.py` only extracts `role`, `content`, `tool_calls`, and `tool_call_id`. The `attachments` field is silently ignored — no errors, no attachment data in the summary. This matches our design intent.

## Cleanup

- **Conversation deleted:** uploads directory and all files are deleted
- **Conversation archived:** files are preserved on disk
- **Note:** Conversation deletion does not currently exist in the WebSocket handler — only archiving. Deletion (with file cleanup) would need to be added. This can be deferred if needed; archiving preserves files, which is the safe default.

## Implementation Notes

- **Mattermost inbound:** `_handle_posted()` currently ignores `file_ids` on incoming posts. Must be extended to extract file IDs, fetch file content via Mattermost REST API (`GET /api/v4/files/{file_id}`), and store locally.
- **Media unification scope:** The existing `media.py` `workspace://` ref pattern is used for agent-generated tool output. Unifying this with conversation uploads touches the tool result pipeline — may be best done as a follow-up phase rather than blocking the core upload feature.

## Open Questions

- Exact content type mapping for LLM multimodal format (images vs documents vs text — may vary by LLM provider)
- Whether Mattermost outbound should also support file attachments from the agent (e.g., sending generated images back) — defer for now
- Upload progress indication in the web UI (spinner vs progress bar)

## Phasing Suggestion

Given scope, consider splitting into phases:
1. **Core:** Upload endpoint + storage + archive format + LLM construction + web UI input/display
2. **Mattermost inbound:** Download and store incoming file attachments
3. **Agent tools:** `list_attachments` / `get_attachment`
4. **Media unification:** Migrate `media.py` / `workspace://` to conversation uploads
5. **Cleanup:** Conversation deletion with file removal
