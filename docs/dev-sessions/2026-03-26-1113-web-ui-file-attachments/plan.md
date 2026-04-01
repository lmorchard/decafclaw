# File/Image Attachment — Implementation Plan

**Issue:** #58
**Branch:** `web-ui-file-attachments`
**Worktree:** `../decafclaw-web-ui-file-attachments/`

## Phasing

The spec identified 5 phases. This plan covers **Phase 1 (Core)** and **Phase 3 (Agent tools)** as they're tightly coupled — the agent needs tools to interact with attachments. Phases 2 (Mattermost inbound), 4 (media unification), and 5 (cleanup/deletion) are deferred to follow-up PRs.

**Phase 1+3 scope:**
- Upload endpoint (backend)
- Storage with b64.gz sidecar
- Archive format extension
- LLM multimodal message construction
- Web UI: input (paperclip, drag-drop, paste), preview strip, display
- Agent tools: `list_attachments`, `get_attachment`
- WebSocket send message extension
- Conversation history loading with attachments

---

## Step 1: Attachment Storage Utilities

**Goal:** Create a shared module for file storage, b64.gz sidecar generation, and path resolution. This is the foundation everything else builds on.

**Files:** New `src/decafclaw/attachments.py`

### Prompt

```
Create a new module `src/decafclaw/attachments.py` that provides attachment storage utilities.

Functions to implement:

1. `uploads_dir(config, conv_id: str) -> Path`
   - Returns `config.workspace_path / "conversations" / conv_id / "uploads"`
   - Does NOT create the directory (caller decides)

2. `save_attachment(config, conv_id: str, filename: str, data: bytes, content_type: str) -> dict`
   - Creates uploads dir if needed
   - Handles filename collisions by appending a timestamp suffix before the extension
     (e.g., `screenshot.png` → `screenshot-20260326-120000.png`)
   - Writes the original file
   - Generates a `.b64.gz` sidecar: base64-encode the data, gzip it, write to `{filename}.b64.gz`
   - Returns attachment metadata dict: `{"filename": actual_filename, "path": workspace_relative_path, "mime_type": content_type}`
   - The `path` should be relative to workspace_path (e.g., `conversations/{conv_id}/uploads/screenshot.png`)

3. `read_attachment_base64(config, attachment: dict) -> str | None`
   - Given an attachment metadata dict (with `path` field), read the base64 data
   - Try `.b64.gz` sidecar first: read, gunzip, return string
   - Fall back to reading original file and base64-encoding it
   - Return None if file not found (log warning, don't crash)

4. `list_attachments(config, conv_id: str) -> list[dict]`
   - List all files in the uploads dir (excluding `.b64.gz` sidecars)
   - Return list of `{"filename": name, "path": workspace_relative_path, "mime_type": guessed_type, "size_bytes": size}`
   - Return empty list if uploads dir doesn't exist

5. `delete_conversation_uploads(config, conv_id: str) -> None`
   - Remove the entire `conversations/{conv_id}/uploads/` directory if it exists
   - Used for cleanup when conversations are deleted (future use)

Use `mimetypes.guess_type()` for MIME detection. Use `gzip` and `base64` stdlib modules.
Keep it simple — no classes, just functions. Follow the existing pattern of `config` as first param.

Write a test file `tests/test_attachments.py` with tests for:
- save_attachment writes file + sidecar
- filename collision handling
- read_attachment_base64 from sidecar
- read_attachment_base64 fallback when sidecar missing
- list_attachments filters out sidecars
- delete_conversation_uploads removes directory
```

---

## Step 2: Upload HTTP Endpoint

**Goal:** Add `POST /api/upload/{conv_id}` route to the HTTP server. Auth-gated, multipart file upload, 100MB limit.

**Files:** Modify `src/decafclaw/http_server.py`

### Prompt

```
Add a file upload endpoint to `src/decafclaw/http_server.py`.

Route: `POST /api/upload/{conv_id}`

Implementation:
1. Auth-gate using the existing `_require_auth(request)` pattern
2. Read the conv_id from path params
3. Validate that the conv_id belongs to the authenticated user's conversations
   (check web_conversations index — look at how other endpoints validate this)
4. Parse multipart form data: `form = await request.form()` then `upload = form["file"]`
5. Check file size against limit (100MB default — use `config.web.max_upload_bytes`
   if available, otherwise hardcode 100 * 1024 * 1024 for now)
6. Read file bytes: `data = await upload.read()`
7. Call `save_attachment(config, conv_id, upload.filename, data, upload.content_type)`
8. Return JSON response with the attachment metadata dict

Error cases:
- 401 if not authenticated
- 403 if conv_id doesn't belong to user
- 400 if no file in form data
- 413 if file too large (check Content-Length header first for early rejection,
  then verify actual read size)

Register the route in the routes list:
`Route("/api/upload/{conv_id}", handle_upload, methods=["POST"])`

Add it near the existing workspace file route.

Import `save_attachment` from `decafclaw.attachments`.
```

---

## Step 3: WebSocket Send with Attachments

**Goal:** Extend the WebSocket `send` handler to accept and archive attachments alongside message text.

**Files:** Modify `src/decafclaw/web/websocket.py`

### Prompt

```
Extend the WebSocket `send` message handler in `src/decafclaw/web/websocket.py`
to support attachments.

The client will send:
```json
{
  "type": "send",
  "conv_id": "web-user-xxx",
  "text": "Please analyze this",
  "attachments": [
    {"filename": "screenshot.png", "path": "conversations/web-user-xxx/uploads/screenshot.png", "mime_type": "image/png"}
  ]
}
```

Changes to `_handle_send()`:
1. Extract `attachments = msg.get("attachments", [])` from the incoming message
2. Pass attachments through to `_start_agent_turn()` and `_run_agent_turn()`
   (add `attachments=None` parameter to both)

Changes to `_run_agent_turn()`:
1. Accept `attachments` parameter (default None, meaning no attachments)
2. When archiving the user message, include attachments if present:
   ```python
   user_msg = {"role": "user", "content": text}
   if attachments:
       user_msg["attachments"] = attachments
   ```
3. Pass attachments through when calling `run_agent_turn()` — but the agent
   function itself doesn't need to change yet (attachments are in the archived
   history, which gets loaded on next turn)

Also update the `load_history` handler to pass attachments through when sending
history to the client. When sending message_complete events for user messages,
include the attachments field if present so the frontend can render them.

The key insight: attachments are stored in the archive as metadata on the user
message dict. The LLM multimodal construction (Step 4) reads them from history.
```

---

## Step 4: LLM Multimodal Message Construction

**Goal:** When building `llm_history` in `agent.py`, transform messages with attachments into multimodal content arrays.

**Files:** Modify `src/decafclaw/agent.py`, use `src/decafclaw/attachments.py`

### Prompt

```
Modify the LLM history building in `src/decafclaw/agent.py` to handle
attachments as multimodal content.

In `run_agent_turn()`, find where `llm_history` is built from `history`
(the loop that filters by LLM_ROLES). Add a transformation step:

1. After building `llm_history`, iterate over it and transform any message
   that has an `attachments` field:

```python
def _resolve_attachments(config, message: dict) -> dict:
    """Transform a message with attachments into multimodal content."""
    attachments = message.get("attachments")
    if not attachments:
        return message

    content_parts = []
    text = message.get("content", "")
    if text:
        content_parts.append({"type": "text", "text": text})

    for att in attachments:
        b64_data = read_attachment_base64(config, att)
        if b64_data is None:
            # File missing — add a text note instead of crashing
            content_parts.append({"type": "text", "text": f"[attachment missing: {att.get('filename', '?')}]"})
            continue

        mime = att.get("mime_type", "application/octet-stream")
        if mime.startswith("image/"):
            content_parts.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{b64_data}"}
            })
        else:
            # For non-image files (PDFs, text, etc.), use the text or
            # document format that the LLM provider supports.
            # OpenAI-compatible: use image_url for images, or include
            # as text content for text files.
            # For now, include as a text block with the filename noted.
            content_parts.append({
                "type": "text",
                "text": f"[file: {att.get('filename', '?')} ({mime})]\n{b64_data[:1000]}..."
            })

    return {**message, "content": content_parts, "attachments": None}
```

2. Apply this to llm_history before passing to call_llm:
```python
llm_history = [_resolve_attachments(config, m) for m in llm_history]
```

3. Strip the `attachments` key from the transformed messages so only
   `role` and `content` go to the LLM (remove it or set to None and
   filter it out).

Import `read_attachment_base64` from `decafclaw.attachments`.

Keep the function private to agent.py (prefix with `_`). Fail open —
if a file can't be read, include a text note, never crash the turn.

Write tests in `tests/test_agent.py` (or a new test file) for:
- Message without attachments passes through unchanged
- Message with image attachment becomes multimodal content array
- Message with missing file gets placeholder text
```

---

## Step 5: Frontend — Upload Service

**Goal:** Add a client-side upload service that handles `POST /api/upload/{conv_id}`.

**Files:** New `src/decafclaw/web/static/lib/upload-client.js`

### Prompt

```
Create a new file `src/decafclaw/web/static/lib/upload-client.js` that
provides a simple upload function.

```javascript
/**
 * Upload a file to the server for a given conversation.
 * @param {string} convId — conversation ID
 * @param {File} file — File object from input/drop/paste
 * @returns {Promise<{filename: string, path: string, mime_type: string}>}
 */
export async function uploadFile(convId, file) {
    const form = new FormData();
    form.append('file', file);
    const resp = await fetch(`/api/upload/${encodeURIComponent(convId)}`, {
        method: 'POST',
        body: form,
    });
    if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.error || `Upload failed: ${resp.status}`);
    }
    return resp.json();
}
```

Keep it minimal — just the fetch call. No retry logic, no progress
tracking. The component layer will handle UX.
```

---

## Step 6: Frontend — Chat Input with Attachments

**Goal:** Extend `chat-input.js` with paperclip button, drag-and-drop, clipboard paste, and attachment preview strip.

**Files:** Modify `src/decafclaw/web/static/components/chat-input.js`

### Prompt

```
Extend the `chat-input.js` Lit component to support file attachments.

Add these capabilities:

1. **New reactive property:** `pendingAttachments` (Array, internal state)
   — list of `{filename, path, mime_type, file, previewUrl}` objects

2. **New reactive property:** `convId` (String) — needed for upload calls

3. **Paperclip button** in the input area (next to send/stop button):
   - Renders a 📎 or SVG clip icon button
   - Clicking opens a hidden `<input type="file" multiple>` element
   - On file select, calls `_handleFiles(fileList)`

4. **Drag-and-drop:**
   - Add dragover/dragleave/drop handlers to the component
   - Visual feedback on dragover (border highlight or overlay)
   - On drop, extract files and call `_handleFiles(e.dataTransfer.files)`

5. **Clipboard paste:**
   - Listen for 'paste' event on the textarea
   - Check `e.clipboardData.files` for image content
   - If files found, call `_handleFiles(e.clipboardData.files)`
   - If no files, let normal text paste proceed

6. **_handleFiles(fileList) method:**
   - For each file, call `uploadFile(convId, file)` from upload-client.js
   - On success, add to `pendingAttachments` with the returned metadata
   - For image files, also create a preview URL: `URL.createObjectURL(file)`
   - On failure, show brief error (console.warn for now)

7. **Preview strip** — rendered above the textarea when pendingAttachments
   is non-empty:
   - For images: small thumbnail (48x48, object-fit cover) + remove (×) button
   - For non-images: filename text + remove (×) button
   - Remove button removes from pendingAttachments array
   - Revoke object URLs on removal

8. **Modified send behavior:**
   - The 'send' custom event detail changes from `{text}` to
     `{text, attachments}` where attachments is the metadata array
     (filename, path, mime_type — NOT the File objects or preview URLs)
   - After send, clear pendingAttachments

9. **Disabled state:** When `disabled` or `busy`, also disable file input
   and hide the paperclip button

Import `uploadFile` from `../lib/upload-client.js`.

Keep styles inline in the component's static styles. Match the existing
visual style of the component.
```

---

## Step 7: Frontend — ConversationStore Send with Attachments

**Goal:** Wire the store's `sendMessage` to include attachments in the WebSocket payload.

**Files:** Modify `src/decafclaw/web/static/lib/conversation-store.js`

### Prompt

```
Update `ConversationStore.sendMessage()` in conversation-store.js to
accept and forward attachments.

Changes:

1. Update `sendMessage(text)` signature to `sendMessage(text, attachments = [])`

2. When sending the WebSocket message, include attachments:
   ```javascript
   this.#ws.send({
       type: 'send',
       conv_id: this.#currentConvId,
       text,
       attachments: attachments.length > 0 ? attachments : undefined,
   });
   ```

3. When adding the user message to local state (for immediate display),
   include attachments:
   ```javascript
   this.#addMessage({
       role: 'user',
       content: text,
       attachments: attachments.length > 0 ? attachments : undefined,
       timestamp: new Date().toISOString(),
   });
   ```

4. Update wherever `sendMessage` is called from the UI — the chat-input
   'send' event listener in the parent component (likely `chat-view.js`
   or similar). It should pass `e.detail.attachments` through:
   ```javascript
   store.sendMessage(e.detail.text, e.detail.attachments);
   ```

5. When loading history from the server (`load_history` response), ensure
   attachment metadata on messages is preserved in the local message array
   so the display components can access it.
```

---

## Step 8: Frontend — Display Attachments in Messages

**Goal:** Render attachments on user messages (inline images, file links).

**Files:** Modify user message component, possibly `src/decafclaw/web/static/components/messages/`

### Prompt

```
Update the user message component to render attachments.

Find the user message component (likely in
`src/decafclaw/web/static/components/messages/user-message.js` or similar).

Changes:

1. Add `attachments` property: `{type: Array}` — the attachment metadata array

2. Render attachments below the message text:
   - **Images** (mime_type starts with "image/"): render as `<img>` tags
     - src: `/api/workspace/${attachment.path}`
     - max-width: 300px, border-radius, clickable to open full-size
   - **Non-images**: render as a styled block with filename and mime type
     - Link to `/api/workspace/${attachment.path}` for download
     - Simple pill/chip style: `[📄 document.pdf]`

3. Style the attachment area:
   - Flex wrap for multiple attachments
   - Small gap between items
   - Images have subtle border/shadow
   - Match the existing message styling

4. Also check if `chat-view.js` or the message list component passes
   `attachments` from the message data to the user message component.
   If not, wire it through.

5. For history-loaded messages (not just live sends), attachments come
   from the message dict in the store — ensure the rendering path works
   for both live and reloaded conversations.
```

---

## Step 9: Agent Attachment Tools

**Goal:** Add `list_attachments` and `get_attachment` tools so the agent can interact with uploaded files.

**Files:** New `src/decafclaw/tools/attachment_tools.py`, modify tool registration

### Prompt

```
Create agent tools for attachment management in
`src/decafclaw/tools/attachment_tools.py`.

Follow the existing tool pattern (see other files in `src/decafclaw/tools/`).

Tool definitions (list of dicts):

1. `list_attachments` — list files uploaded to the current conversation
   - No parameters needed (uses conv_id from ctx)
   - Returns: JSON list of `{filename, path, mime_type, size_bytes}`
   - Uses `list_attachments()` from `decafclaw.attachments`

2. `get_attachment` — retrieve a file's content to re-examine it
   - Parameters: `filename` (string, required)
   - Reads the file from the conversation uploads dir
   - For images: returns the base64 data as a ToolResult with media
     (so the LLM gets it as an image in the tool result)
   - For text files: returns file content as text
   - For other types: returns base64 data with a note about the type
   - Uses `read_attachment_base64()` from `decafclaw.attachments`

Export:
- `TOOL_DEFINITIONS` — list of tool definition dicts
- `async def execute(ctx, tool_name, args) -> ToolResult` — dispatcher

Register in the tool system:
- Import in `src/decafclaw/tools/__init__.py` (or wherever tools are collected)
- Add to the combined tool definitions list
- Add dispatch case in `execute_tool()`

The tools should use `ctx.conv_id` for the conversation ID and
`ctx.config` for the config. Follow the existing `ToolResult` return pattern.
```

---

## Step 10: Integration Testing & Polish

**Goal:** End-to-end verification, edge case handling, and cleanup.

### Prompt

```
Review and test the full attachment pipeline end-to-end.

Verification checklist:

1. **Upload endpoint:**
   - Upload an image via curl or the UI
   - Verify file + .b64.gz sidecar written to correct location
   - Verify 413 for oversized files
   - Verify 401 without auth
   - Verify filename collision handling

2. **Archive format:**
   - Send a message with attachment via web UI
   - Read the .jsonl file — confirm attachments array present
   - Confirm content is still plain text string

3. **LLM multimodal:**
   - Add logging or a test to verify the content array sent to LLM
     contains image_url blocks with base64 data
   - Verify messages without attachments are unchanged

4. **Frontend:**
   - Paperclip button opens file picker
   - Drag and drop shows preview strip
   - Clipboard paste captures images
   - Preview shows thumbnails for images, filename for others
   - Remove button works
   - Send clears preview strip
   - Attachments display on sent and history-loaded messages

5. **Agent tools:**
   - Agent can call list_attachments and see uploaded files
   - Agent can call get_attachment and receive file content

6. **Compaction:**
   - Compact a conversation with attachments
   - Verify attachments silently dropped from summary
   - Verify files still on disk
   - Verify agent can list_attachments to find them

Run `make lint`, `make typecheck`, `make check-js`, and `make test`
after all changes. Fix any issues found.
```

---

## Step Summary

| Step | What | Key Files |
|------|------|-----------|
| 1 | Storage utilities | `attachments.py` (new), `tests/test_attachments.py` (new) |
| 2 | Upload HTTP endpoint | `http_server.py` |
| 3 | WebSocket send + archive | `websocket.py` |
| 4 | LLM multimodal construction | `agent.py` |
| 5 | Frontend upload service | `upload-client.js` (new) |
| 6 | Frontend chat input | `chat-input.js` |
| 7 | Frontend store wiring | `conversation-store.js`, parent component |
| 8 | Frontend display | user message component |
| 9 | Agent tools | `attachment_tools.py` (new), tool registration |
| 10 | Integration testing | All of the above |

## Deferred to Follow-up PRs

- **Phase 2:** Mattermost inbound file attachments (extract `file_ids`, download via API)
- **Phase 4:** Media unification (migrate `media.py` workspace:// refs)
- **Phase 5:** Conversation deletion with file cleanup
- Upload progress bars in UI
- Content type mapping refinements per LLM provider
