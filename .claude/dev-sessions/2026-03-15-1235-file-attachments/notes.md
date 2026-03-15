# File Attachments & Rich Media — Session Notes & Retro

## What we built

Full media pipeline from tool results to Mattermost attachments:

- **ToolResult** — richer return type (text + media) replacing plain strings from execute_tool
- **MediaHandler abstraction** — MattermostMediaHandler (upload + file_ids), TerminalMediaHandler (save to workspace)
- **MCP image/audio** — base64 decoded, uploaded as file attachments instead of `[image: N bytes]` placeholders
- **`file_share` tool** — agent shares workspace files as Mattermost attachments
- **`workspace://` image refs** — agent references workspace images in markdown, auto-uploaded and attached
- **`debug_context` upgrade** — uploads JSON and system prompt as file attachments
- **Message attachment cards** — Slack-style `props.attachments` support in the handler
- **Overflow handling** — >10 files splits into continuation posts
- **Auto-restart on crash** — Mattermost bot restarts up to 10 times with 5s backoff
- **MCP restart fix** — fresh registry approach avoids anyio cancel scope contamination
- **Test image MCP server** — `scripts/test-image-mcp.py` for testing media pipeline

## What went well

- **ToolResult migration was clean.** Changing execute_tool's return type from str to ToolResult touched many files but the `_to_tool_result` normalizer made backward compat smooth. All 180 tests passed on first try.
- **The MediaHandler abstraction works.** Mattermost and terminal paths are cleanly separated. Adding a new channel (Discord, Slack) would just be a new handler.
- **Test image MCP server was invaluable.** Custom 200x200 gradient PNG let us verify the full pipeline without external dependencies.
- **Auto-restart loop gives real operational resilience.** Bot survives crashes now.

## What could be better

- **anyio/asyncio interop is a real pain.** The MCP SDK uses anyio internally, and cancel scope leaking caused multiple crash iterations. We went through asyncio.shield (didn't work — task mismatch), BaseException catches (helped but not enough), before landing on "create fresh registry, don't disconnect." This ate significant debugging time.
- **MCP restart still has rough edges.** Old connections are leaked (GC'd, not cleanly closed). Single-server restart just marks as "failed" for auto-reconnect, doesn't immediately reconnect. Good enough but not elegant.
- **Content-Type header conflict.** The Mattermost HTTP client had `Content-Type: application/json` as a default header, which broke multipart file uploads. Took a deploy cycle to find.
- **Placeholder deletion for media.** Initially tried delete+recreate, changed to edit+separate message. Should have thought through the Mattermost UX constraints earlier.

## Bugs found during live testing

- **Content-Type conflict**: default `application/json` header on httpx client prevented multipart file uploads. Fixed by removing the default.
- **MCP restart crash (anyio cancel scopes)**: disconnecting MCP servers from a tool call corrupted anyio's cancel scope tracking, crashing the process. Fixed by creating fresh registry instead of disconnect/reconnect.
- **CancelledError is BaseException**: `except Exception` doesn't catch asyncio.CancelledError in Python 3.9+. Required explicit `BaseException` handling.
- **Placeholder editing with media**: Mattermost can't add file_ids to an existing post via edit. Changed to edit text + send separate files message.

## Design decisions worth noting

- **ToolResult replaces str everywhere.** No backward compat shim — all callers updated. Clean break.
- **`_to_tool_result` normalizer**: existing tools return strings, normalizer wraps them. New tools can return ToolResult directly with media.
- **Fresh registry on MCP reload**: avoid anyio's cross-task cancel scope restrictions by never disconnecting from a tool call. Old connections are GC'd.
- **Auto-restart is Mattermost-only**: interactive mode crashes are visible to the user, no restart needed.
- **No deletions in Mattermost for media**: edit placeholder text, send files as separate message. Avoids "(message deleted)" ghosts.

## 180 tests, 15+ commits on file-attachments branch
