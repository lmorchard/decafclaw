# File Attachments & Rich Media — Spec

## Goal

Enable DecafClaw to send files, images, and rich media through Mattermost. Wire MCP image/audio results into actual file attachments. Add a `file_share` tool for the agent to share workspace files. Support inline workspace image references in agent responses.

## Design Principles

- **Thin abstraction**: `MediaHandler` interface for channel-specific media operations, anticipating future channels
- **Richer tool results**: `execute_tool` returns `ToolResult` (text + media) instead of plain strings
- **Standard markdown**: agent uses `![alt](workspace://path)` for inline workspace images, standard `![alt](url)` for public URLs
- **No deletions from workspace**: media is uploaded to Mattermost, workspace files stay intact

## MediaHandler Abstraction

```python
class MediaHandler:
    """Interface for channel-specific media operations."""

    async def upload_file(self, channel_id, filename, data, content_type) -> str:
        """Upload raw bytes, return an opaque file reference (e.g., file_id)."""

    async def send_with_media(self, channel_id, message, media_refs, root_id=None) -> str:
        """Send a message with attached media references."""

    def format_image_url(self, url) -> str:
        """Format a URL for inline display (e.g., markdown image syntax)."""

    def format_attachment_card(self, title, text, image_url=None, thumb_url=None) -> dict:
        """Build a rich attachment card structure."""
```

### Implementations

- **`MattermostMediaHandler`**: uploads via `POST /api/v4/files`, attaches via `file_ids` on posts, formats Slack-style `props.attachments` for cards
- **`TerminalMediaHandler`**: saves files to workspace, prints paths. No upload capability.

The handler lives on `ctx.media_handler` — set during context creation in both Mattermost and interactive modes. Forked contexts in Mattermost inherit it.

## ToolResult

Replace plain string returns from `execute_tool` with a richer type:

```python
@dataclass
class ToolResult:
    text: str
    media: list[dict] = field(default_factory=list)
```

Each media item is one of:
- `{"type": "file", "filename": str, "data": bytes, "content_type": str}` — raw binary (from MCP image/audio)
- `{"type": "url", "url": str, "alt": str}` — public URL (for markdown inline or attachment card)

Built-in tools return `ToolResult(text="...")` with no media. MCP tools return media when the server provides image/audio content.

### Backward compatibility

`execute_tool` currently returns `str`. All callers need updating:
- Agent loop (`run_agent_turn`) — uses `result.text` for history, collects `result.media`
- Interactive mode — same
- Tests — update assertions

## MCP Image/Audio Handling

Currently `_convert_mcp_response` returns placeholders like `[image: N bytes]`. With `ToolResult`:

- `text` content items → concatenated as before
- `image` content items → added to `media` list as `{"type": "file", "filename": "mcp-image-{i}.{ext}", "data": base64_decoded_bytes, "content_type": mimeType_from_server}`. Content type and extension derived from the MCP server's `mimeType` field, falling back to `image/png`.
- `audio` content items → same pattern, falling back to `audio/wav`.
- Text description still included (e.g., "Generated image attached." instead of "[image: N bytes]")

The MCP tool caller wrapper in `mcp_client.py` returns `ToolResult` instead of `str`.

## Workspace Image References in Agent Response

The agent can write `![alt text](workspace://path/to/image.png)` in its final response. Before posting:

1. Scan response text for `![...](workspace://...)` patterns
2. For each match:
   - Read the file from workspace
   - Upload via media handler
   - Strip the markdown image reference from the text
3. Attach all uploaded file_ids to the post

Public URLs (`![alt](https://...)`) are left as-is — Mattermost renders them natively.

## file_share Tool

```json
{
  "name": "file_share",
  "description": "Share a file from the workspace as an attachment in the conversation.",
  "parameters": {
    "path": "Workspace-relative file path",
    "message": "Optional message to include with the file"
  }
}
```

- Reads the file from workspace (sandboxed, same as `workspace_read`)
- Uploads via media handler
- Returns `ToolResult` with the message text and the file as media
- Content type guessed from file extension (`mimetypes` module)

## Mattermost Integration

### Upload flow

1. `POST /api/v4/files?channel_id={id}` with `multipart/form-data`
2. Response includes `file_infos[0].id` — the file_id
3. Include `file_ids` array in `POST /api/v4/posts` body

### Message attachment cards

For structured rich responses, use `props.attachments`:

```json
{
  "channel_id": "...",
  "message": "optional text",
  "props": {
    "attachments": [
      {
        "title": "Card title",
        "text": "Card body",
        "image_url": "https://...",
        "thumb_url": "https://..."
      }
    ]
  }
}
```

Useful when the agent has a public URL for an image but wants a structured card layout.

### Limits

- Max file size: 100MB (Mattermost default)
- Max files per post: 10
- Max image dimensions: 7680x4320

### Overflow handling

If more than 10 files need to be attached, split into multiple posts. First post gets the message text + first 10 files. Subsequent posts are replies in the same thread with remaining files (batched in groups of 10).

## Interactive Mode

- **File media from tool results**: saved to workspace, path printed: `[file saved: workspace/mcp-image.png]`
- **`file_share` tool**: prints path since there's no channel
- **Workspace image references**: left as-is in terminal output (no upload)

## Agent Loop Changes

In `run_agent_turn`:

1. `execute_tool` returns `ToolResult` instead of `str`
2. Tool message in history uses `result.text`
3. Media items from tool results are accumulated in a list
4. When composing the final response:
   - Scan for `workspace://` image references, add to media list
   - Strip workspace references from text
   - Pass accumulated media to the posting layer
5. Mattermost: upload media via handler, attach file_ids to post
6. Interactive: save media to workspace, append paths to output

## Testing

1. **ToolResult** — creation, text-only backward compat
2. **MCP response conversion** — image/audio content produces ToolResult with media
3. **Workspace image scanning** — detects `workspace://` refs, strips from text, leaves public URLs
4. **MattermostMediaHandler** — upload, send_with_media (mocked HTTP)
5. **TerminalMediaHandler** — saves to workspace, returns path
6. **file_share tool** — reads workspace file, returns ToolResult with media
7. **Agent loop** — media accumulation across tool calls, workspace ref processing

## debug_context Upgrade

Currently writes JSON to workspace. With file attachments:
- Upload `debug_context.json` and `debug_system_prompt.md` as file attachments
- Return a brief summary in the message text
- Falls back to workspace files if no media handler available (interactive mode)

## Out of Scope

- Video content from MCP
- Image generation tools (just the plumbing to display results)
- Mattermost message reactions or interactive buttons
- Serving files via HTTP (no web server)
- Heartbeat media (deferred — heartbeat uses its own HTTP client)
