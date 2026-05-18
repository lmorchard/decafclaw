# Spec — copy conversation as JSONL / markdown

Issue: [#519](https://github.com/lmorchard/decafclaw/issues/519)

## Goal

Add a UI affordance in the web chat that copies the active conversation to the
clipboard in two formats:

- **JSONL** — raw archive bytes, lossless. For pasting back to an LLM for
  diagnosis.
- **Markdown** — human-readable rendering with roles, content, tool calls, and
  tool results. For Obsidian, sharing, or PR descriptions.

Workaround today is `cat`-ing the archive from disk, which only works from the
host and loses formatting when pasted into Obsidian.

## Backend

### New endpoint

`GET /api/conversations/{id}/export?format=jsonl|markdown`

- Handler lives in `http_server.py` next to `get_context_diagnostics` (the
  closest precedent — same auth shape, same `ConversationIndex` ownership
  check). Use `@_authenticated`; 404 if `conv.user_id != username` or no
  archive exists.
- `format=jsonl`: returns the raw archive file bytes. `Content-Type:
  application/x-ndjson; charset=utf-8`. Reads the **full archive only**, never
  the compacted sidecar — lossless is the JSONL contract.
- `format=markdown`: returns the rendered markdown. `Content-Type:
  text/markdown; charset=utf-8`.
- 400 on missing or unknown `format` — explicit, diagnostic API.
- Route registered alongside `/context` in the routes list at
  `http_server.py:~1791`.
- No streaming for v1 — full body in one response. Archives big enough to
  matter for memory (tens of MB) are rare; revisit if it bites.

### Markdown renderer

New module `src/decafclaw/conversation_export.py` (or similar) with one entry
point `render_markdown(messages: list[dict]) -> str`. Pure function over the
list-of-dicts archive shape — no config needed. Easy to unit-test.

**Inclusion (opinionated default, no query params):**

The archive contains many roles. We export only the conversation-flow roles
and skip metadata / auto-injected context.

| Archive role                                  | Behavior                                                                                            |
| --------------------------------------------- | --------------------------------------------------------------------------------------------------- |
| `user`                                        | `## User\n\n<content>`                                                                              |
| `assistant` (text, no tool_calls)             | `## Assistant\n\n<content>`                                                                         |
| `assistant` (with `tool_calls`)               | Assistant text if any, then each tool call as `### Tool call: <name>\n\n` + fenced JSON args        |
| `tool`                                        | `### Tool result: <tool_name>` (from `tool` field on the record) + text body in a fenced block      |
| `background_event` (wake from scheduled task) | `> [background event] <short summary or kind>` blockquote — short, so context is visible            |
| `system`, `model`, `reflection`, `confirmation_request`, `confirmation_response`, `cancel_marker`, `wake_trigger` | skipped — metadata, not conversation flow                                |

Notes:

- **Compaction summaries are not in the archive** — `compaction.py` writes
  them only to the `.compacted.jsonl` sidecar. The export reads `read_archive`
  (raw archive) only, so the markdown is a faithful record of what actually
  happened. No compaction-summary handling needed.
- **Auto-injected context** (`vault_retrieval`, `vault_references`,
  `conversation_notes`) is built by the composer at compose-time and **not
  archived** — so no skip rule needed; they simply won't appear.
- **Widget payloads**: assistant turns may include a `widget` field. Skip the
  raw widget JSON. Exception: if `widget.type == "code_block"`, render the
  payload as a fenced block with the language hint from the widget.
- **Image attachments on a message** (`attachments` field): emit
  `![](<path-or-id>)` references at the end of the message; never inline
  base64.
- **Tool `data` field**: tool results may have both `text` and `data`. Use
  `text` for the fenced block. Include `data` only if `text` is empty/missing.
  Truncate any single fenced body over ~16KB with a trailing
  `... [truncated, original was N bytes]` marker.
- **Triple-backtick escape**: if a fenced body contains a triple-backtick run,
  use a fence one backtick longer than the longest run in the body.
- **Header at the top**: `# Conversation <conv_id>\n\nExported <ISO timestamp>`.

### Tests

Unit tests in `tests/test_conversation_export.py`:

- Fixture archive with each message type → assert markdown output contains /
  excludes the right pieces.
- Triple-backtick escape works.
- Oversize `data` truncation works.
- Empty archive → returns the header only (no body sections).

API tests in `tests/test_web_export.py` (or extend the closest existing web
tests file):

- `GET /export?format=jsonl` returns raw archive bytes for an existing
  conversation.
- `GET /export?format=markdown` returns markdown for the same fixture.
- Missing / unknown `format` → 400.
- Unknown `conv_id` → 404.
- Cross-user access denied (mirrors the existing per-user conv access tests).

## Frontend

### UI placement

Refactor the existing `canvas-resummon-pill` (floating absolute upper-right in
`#chat-main`) into a small **action cluster** in the same upper-right slot.
The cluster holds:

- `Canvas` pill (existing behavior — only shown when canvas has tabs and is
  collapsed)
- New `Copy ▾` dropdown button (new — shown whenever a conversation is active)

Both use `.dc-floating-btn` for visual consistency. The cluster needs to:

- Lay them out side-by-side with a small gap, both staying anchored to the
  upper-right corner.
- On mobile, drop labels to icon-only (matching how Canvas already collapses).
- Not break when only one of the two is present (e.g., no canvas tabs).

Concretely: introduce a container `.dc-chat-actions` (or reuse the existing
positioning by stacking buttons inside a flex row), defined in
`styles/chat.css` or `styles/primitives.css`. Move the Canvas pill's
absolute-positioning rules from `canvas.css` onto the container so the cluster
is positioned once and individual buttons just flow inside it.

### Copy dropdown behavior

- `Copy ▾` opens a small menu with two items: `Copy as JSONL` and `Copy as
  markdown`. Menu uses an existing pattern if there is one — otherwise a
  simple `<details>` / `<summary>` or absolutely-positioned `<ul>` below the
  pill. Closes on outside click and after item selection.
- Each item: fetch `/api/conversations/<id>/export?format=<fmt>`, then write to
  the clipboard via `navigator.clipboard.writeText(text)`.
- Success → toast `Copied as <format>` via the existing `showToast` infra in
  `app.js`. (Export `showToast` from `app.js` if it isn't already, or move to a
  shared `lib/toast.js`.)
- Clipboard error / fetch error → toast with the failure reason.
- Permission denied / unsecure context: toast with `Clipboard unavailable —
  download instead?` and link to the same URL (browser will save the response
  as a file). For oversize payloads where clipboard write hangs or fails, the
  same fallback applies.

### Mobile

Hide the `Copy ▾` label, keep an icon (clipboard glyph or `📋`). Tap target
≥ 44px, matching the canvas pill's mobile size.

## UX feedback

- Success: toast.
- Failure: toast with cause (`Copied failed: ...` or `Clipboard unavailable —
  use 'Download' instead`).
- Large payloads (multi-MB JSONL): browser's clipboard API may take a few
  hundred ms; show no spinner for v1 (fast enough on local). Don't block
  further interaction.

## Out of scope

- Per-message-type query params (`?include=...`) — committed to one
  opinionated default for v1.
- Streaming responses.
- Download-as-file dedicated route — fallback link reuses the same export URL.
- Mattermost / terminal transports — web UI only for v1.

## Acceptance criteria

- [ ] `Copy as JSONL` copies full archive bytes for the active conversation.
- [ ] `Copy as markdown` copies a rendered markdown form.
- [ ] Markdown matches the inclusion table above; widget payloads (except
      `code_block`) are skipped; reflection / confirmations are skipped.
- [ ] Toast confirms success; toast surfaces clipboard failures.
- [ ] Multi-MB conversations don't lock the UI (fetch + clipboard write are
      async; no synchronous parsing on the main thread).
- [ ] No regression in existing chat / canvas / context-inspector layout —
      Canvas pill still appears in the same corner and behaves the same.
- [ ] Backend tests cover renderer output, oversize truncation,
      triple-backtick escape, and the API endpoint (200 / 400 / 404 /
      cross-user 403).

## References

- Issue #519
- Adjacent endpoint: `GET /api/conversations/{id}/context`
  (`src/decafclaw/http_server.py:1791`,
  `src/decafclaw/web/conversations.py:564`)
- Archive helpers: `src/decafclaw/archive.py` (`read_archive`, `archive_path`)
- Canvas pill: `src/decafclaw/web/static/app.js:687-721`, styles in
  `src/decafclaw/web/static/styles/canvas.css:90-122`
- Toast: `src/decafclaw/web/static/app.js:399`,
  `src/decafclaw/web/static/styles/toast.css`
