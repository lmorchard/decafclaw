# File Attachments & Rich Media

DecafClaw can send files, images, and rich media through Mattermost messages. This includes MCP tool results with images/audio, workspace files shared by the agent, and inline workspace image references.

## How it works

### ToolResult

Tool execution returns a `ToolResult` containing text (for LLM history) and optional media attachments:

```python
@dataclass
class ToolResult:
    text: str                    # Goes into conversation history
    media: list[dict] = []       # Files to upload/attach
```

Media items can be:
- **File data**: `{"type": "file", "filename": "chart.png", "data": bytes, "content_type": "image/png"}`
- **URL reference**: `{"type": "url", "url": "https://...", "alt": "description"}`

### MediaHandler

A thin abstraction for channel-specific media operations:

| Method | Mattermost | Terminal |
|--------|-----------|---------|
| `upload_file` | POST /api/v4/files | Save to workspace/media/ |
| `send_with_media` | Post with file_ids | N/A |
| `format_image_url` | Markdown syntax | Markdown syntax |
| `format_attachment_card` | Slack-style card | Text representation |

## MCP Image/Audio

When MCP tools return image or audio content, the binary data is decoded from base64 and attached as media. In Mattermost, images appear as thumbnails in the message; audio files appear as downloads.

The content type from the MCP server is honored (e.g., `image/jpeg`, `audio/mp3`), with fallbacks to `image/png` and `audio/wav`.

## file_share Tool

The agent can share workspace files as attachments:

```
file_share(path="report.json", message="Here's the analysis")
```

- Reads from workspace (sandboxed, same as workspace_read)
- Content type guessed from file extension
- In Mattermost: uploaded and attached to the message
- In terminal: prints the file path

## Workspace Image References

The agent can reference workspace images in its response using markdown syntax:

```markdown
Here's the chart: ![chart](workspace://charts/daily.png)
```

Before posting to Mattermost:
1. `workspace://` references are detected and stripped from the text
2. Referenced files are read from workspace and uploaded
3. Files are attached to the message as thumbnails

Public URLs (`![alt](https://...)`) are left as-is — Mattermost renders them inline natively.

## Message Attachment Cards

For structured rich responses with images from public URLs, Mattermost supports Slack-style attachment cards:

```json
{
  "props": {
    "attachments": [{
      "title": "Weather Report",
      "text": "Current conditions",
      "image_url": "https://example.com/weather-map.png",
      "thumb_url": "https://example.com/weather-icon.png"
    }]
  }
}
```

The `MediaHandler.format_attachment_card` method builds these structures.

## debug_context with Attachments

The `debug_context` tool now uploads the full JSON context and system prompt as file attachments in Mattermost, making it easy to inspect the full LLM context without reading workspace files.

## Limits

- Max file size: 100MB (Mattermost default, configurable by admin)
- Max files per post: 10 (overflow automatically splits into continuation posts)
- Max image dimensions: 7680x4320

## Interactive Mode

In terminal mode, media files are saved to `workspace/media/` and paths are printed:

```
agent> Here's the analysis
[file saved: media/chart.png]
[file saved: media/report.json]
```
