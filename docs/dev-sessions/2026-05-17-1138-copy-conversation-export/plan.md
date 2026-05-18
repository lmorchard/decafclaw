# Plan — copy conversation as JSONL / markdown

Spec: [spec.md](spec.md) · Issue: [#519](https://github.com/lmorchard/decafclaw/issues/519)

Six small steps, each its own commit. Backend first, then frontend, ending
with a quick smoke test in the browser.

---

## Step 1 — Markdown renderer module + unit tests

**Goal:** pure-function markdown renderer over a list of archive records.

- New module `src/decafclaw/conversation_export.py` with:
  - `render_markdown(messages: list[dict], conv_id: str) -> str`
  - Internal helpers per role (`_render_user`, `_render_assistant`,
    `_render_tool`, `_render_background_event`).
  - Triple-backtick escape helper (find longest run, fence one longer).
  - Oversize-body truncation helper (16KB threshold, marker says original
    size).
- Renders the inclusion table from the spec:
  - Header with conv_id + ISO timestamp.
  - `user` → `## User\n\n<content>`.
  - `assistant` (text) → `## Assistant\n\n<content>`.
  - `assistant` with `tool_calls` → text first if present, then per call:
    `### Tool call: <name>\n\n` + fenced JSON (function args).
  - `tool` → `### Tool result: <tool name from "tool" field>\n\n` + fenced
    body. Use `text` if present, else stringified `data`.
  - `background_event` → `> [background event] <kind or short summary>`.
  - Widget on assistant: skip raw widget JSON; `widget.type == "code_block"`
    rendered as fenced block with language hint.
  - Attachments: `![](<path or id>)` references at end of message.
  - Other roles (`system`, `model`, `reflection`, `confirmation_*`,
    `cancel_marker`, `wake_trigger`): silently skipped.
- New tests in `tests/test_conversation_export.py`:
  - Each role kind rendered or skipped per the table.
  - Triple-backtick body produces longer fence.
  - Oversize body truncated with marker.
  - Empty list → header-only output.
  - Widget `code_block` rendered; other widget types skipped.
  - Attachments referenced as markdown links.

Commit: `feat(export): markdown renderer for conversation archives`

---

## Step 2 — Export HTTP endpoint + API tests

**Goal:** wire the renderer to a per-user, per-conversation HTTP route.

- In `src/decafclaw/http_server.py`, add `export_conversation` next to
  `get_context_diagnostics`:
  - Decorated `@_authenticated`.
  - Look up conv via `ConversationIndex`; 404 if missing or `user_id !=
    username`.
  - Read `?format=...` query: `jsonl` or `markdown`. Anything else → 400.
  - `jsonl`: read `archive_path(config, conv_id)` raw bytes. 404 if the file
    doesn't exist. Return `Response(body, media_type="application/x-ndjson",
    headers={"Content-Type": "application/x-ndjson; charset=utf-8"})`.
  - `markdown`: read via `read_archive`, call `render_markdown`, return
    `Response(text, media_type="text/markdown; charset=utf-8")`.
- Register route at `http_server.py:~1791` alongside `/context`:
  `Route("/api/conversations/{id}/export", export_conversation, methods=["GET"])`.
- New tests in `tests/test_web_export.py` (or extend nearest existing web
  test file if pattern matches):
  - Seed a fixture conversation in the `ConversationIndex`, write a small
    archive on disk.
  - `GET /export?format=jsonl` → 200, raw bytes match the archive on disk.
  - `GET /export?format=markdown` → 200, body starts with `# Conversation `.
  - `GET /export` (no format) → 400.
  - `GET /export?format=html` → 400.
  - Unknown conv_id → 404.
  - Cross-user (auth as different username) → 404.

Commit: `feat(web): GET /api/conversations/{id}/export?format=jsonl|markdown`

---

## Step 3 — Extract `showToast` into a shared module

**Goal:** small, mechanical refactor so frontend Copy code can `import { showToast }` without dragging in `app.js`.

- New `src/decafclaw/web/static/lib/toast.js` exporting `showToast(message,
  duration = 5000)`. Body is the existing implementation verbatim — query
  `#toast-container`, create `.toast`, append, remove on timeout.
- `app.js`: delete the local `showToast` function, import from the new
  module.
- No other call sites today; just the one inside `app.js`'s WS error
  handler.

Commit: `refactor(web): extract showToast into lib/toast.js`

---

## Step 4 — Action cluster container for upper-right of `#chat-main`

**Goal:** make the Canvas pill share its corner with new buttons without each
one fighting for absolute offsets.

- In `app.js`'s `setupCanvasResummonPill`, render into a single container
  `.dc-chat-actions` (created lazily as a child of `#chat-main` if absent)
  instead of appending the pill directly to `#chat-main`. The Canvas pill is
  still inserted/removed by the existing subscriber logic; it just lives
  inside the container now.
- Move absolute-positioning rules in `styles/canvas.css` (the
  `#chat-main > .canvas-resummon-pill` blocks for desktop + mobile) onto
  `#chat-main > .dc-chat-actions`. The pill keeps its sizing/font/padding
  rules. Container is a flex row, small gap (`.4rem`), right-aligned.
- Verify Canvas pill still shows/hides correctly with no other action present
  (container can be empty — fine, it's transparent and zero-size when empty).

Commit: `refactor(web): floating action cluster for upper-right of chat`

---

## Step 5 — Copy ▾ button + dropdown wired to export endpoint

**Goal:** the actual feature.

- New `src/decafclaw/web/static/components/copy-conversation-menu.js`:
  - Lit element `<copy-conversation-menu>` with one property `convId`.
  - Renders a `<details class="copy-menu dc-floating-btn">` with
    `<summary>📋<span class="copy-label"> Copy</span></summary>` and a
    `<ul>` of two items: `Copy as JSONL`, `Copy as markdown`.
  - On item click: fetch `/api/conversations/<convId>/export?format=<fmt>`,
    `await res.text()`, write to clipboard via
    `navigator.clipboard.writeText`. Close the `<details>` on selection and
    on outside click.
  - On success → `showToast('Copied as <jsonl|markdown>')`.
  - On HTTP failure or clipboard error → `showToast('Copy failed: <reason>')`.
- Mount: in `app.js` near `setupCanvasResummonPill`, add a small
  `setupCopyConversationMenu()` that:
  - Appends a `<copy-conversation-menu>` into `.dc-chat-actions` once.
  - Subscribes to the conversation store and sets `.convId` on conv switch;
    removes the element when no conv is active.
- Minimal styling in a new tiny block in `styles/chat.css` (or
  `styles/primitives.css` if it feels primitive-ish): dropdown `<ul>`
  absolutely positioned below the `<summary>`, list-style none, padded,
  same shadow/border-radius as the floating buttons. Hide the label
  `.copy-label` at mobile breakpoint (mirror Canvas pill).

Commit: `feat(web): Copy ▾ menu for JSONL / markdown export`

---

## Step 6 — Smoke test in the browser + doc touch-up

**Goal:** verify the feature end-to-end and update docs.

- Local smoke test (manual, with `make dev` if not already running): open the
  web UI, pick a conversation with at least one assistant + tool turn, click
  `Copy ▾ → Copy as markdown`, paste somewhere, eyeball format. Same for
  `Copy as JSONL` (paste into a scratch file, confirm it's valid newline-
  delimited JSON). Test on mobile breakpoint via devtools (label hides).
- Confirm Canvas pill still appears in the same corner with the cluster
  refactor.
- Add a short paragraph to `docs/web-ui.md` describing the Copy menu and
  endpoint URL — one paragraph, links to `/api/conversations/{id}/export`.
  Update key-files list in `CLAUDE.md` only if a new module crossed the
  bar (probably not — `conversation_export.py` is small, mention it in
  passing only if it fits).
- Final `make check` + `make test` clean run.

Commit: `docs(web-ui): describe Copy ▾ menu and export endpoint`

---

## Out-of-scope reminders (for self-discipline during execution)

- No `?include=` query params.
- No streaming response.
- No dedicated download endpoint.
- No Mattermost / terminal transport equivalents.
- No new menu component library — `<details>` is fine, keep it simple.
- No restructuring of `web/conversations.py` to absorb `get_context_diagnostics`
  + the new endpoint together (that's a separate cleanup if it ever
  matters).
