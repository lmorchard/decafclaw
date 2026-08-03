# Plan — #139 web UI command autocomplete

**Tier:** `auto-ok`. **Freeze:** `a0a95e2`. **Checks:** `checks.md` (C1, C2, C3 + G1–G5).

The frozen checks define the observable contract, so this plan builds to them rather than
re-deriving a design. Slices run server → transport → component, each crossing its own layers and
each independently valuable: after Phase 1 the server can answer, after Phase 2 the client holds a
list, after Phase 3 the user sees a menu.

## Phase 0 — Freeze (done)

Checks authored pre-implementation, adjudicated by an independent reviewer, frozen at `a0a95e2`.
The three check files are read-only from here.

## Phase 1 — Server: enumerate commands over the socket

**Advances:** C2. Also G4 (the manifest half).

- Add `list_commands` (client→server) and `command_list` (server→client) to
  `src/decafclaw/web/message_types.json`; run `make gen-message-types`. Never hand-edit the four
  generated files.
- Add a command-enumeration helper in `commands.py` returning
  `[{name, description, argument_hint}]` — skill commands via `list_commands()` plus MCP prompts
  via `_get_mcp_prompt_commands()`. Reuse both; do not reimplement the hint builder.
  - `get_registry` must resolve at call time (the call-time import `commands.py` already uses).
    A module-level binding in `websocket.py` dodges C2's monkeypatch.
  - Only `user_invocable` skills. C2 asserts a non-invocable skill is absent.
  - **This reads `config.discovered_skills`.** Decide the trust tier deliberately and record it in
    `tests/test_discovered_skills_consumers.py` — G5 fails until every consumer has one. This
    enumeration is display-only (it feeds an autocomplete menu; invoking still goes through
    `dispatch_command`, which does its own resolution), so it does not grant capability. Say that
    in the recorded decision rather than leaving it implied.
- Add `_handle_list_commands(ws_send, index, username, msg, state)` to `web/websocket.py` and
  register it in `_HANDLERS` under `WSMessageType.LIST_COMMANDS`. No `conv_id` requirement — C2
  sends a bare `{"type": "list_commands"}`.

**Verify:** `uv run pytest tests/test_web_command_list.py` · `make check-message-types`

## Phase 2 — Transport: store surface + app glue

**Advances:** C3.

- `ConversationStore`: a `commands` getter defaulting to `[]`; a `command_list` frame populates it
  and calls `#emitChange()`. Mirror the existing `availableModels` / `MODELS_AVAILABLE` pair.
- Send `{type: LIST_COMMANDS}` from `selectConversation()`, and from `#resubscribe()` so a
  reconnect refreshes (picking up a newly connected MCP server). `#resubscribe()` already returns
  early when nothing is selected, which is exactly the Option B invariant — do not add the send
  to the `open` handler, and do not touch `lib/conversation-store.test.js`.
- `app.js`: one line in the existing `store.addEventListener('change', …)` block —
  `chatInput.commands = store.commands;`.

**Verify:** `cd src/decafclaw/web/static && npx vitest run lib/command-list.test.js`

## Phase 3 — Component: the suggestion menu

**Advances:** C1. Also G2 (menu-closed behaviour must survive).

- `commands` property (Array) on `chat-input`.
- Open when the text from the start of the **current line** to the caret begins with `/` or `!`.
  Not `value.startsWith` — C1 has a `hello\n/mc` case.
- Hand-rolled ordered-subsequence scorer. **No new direct npm dependency** — promoting
  `@codemirror/autocomplete` to a direct dep would fire the tier's risk trigger and force a
  re-tier. C1 has an out-of-order negative case, so bag-of-characters will not pass.
- Render `.command-menu` (only while open) with `.command-menu-item` rows carrying
  `data-command`; exactly one `.highlighted`, index 0 on open.
- Keydown routing, conditional on menu state: ArrowDown/ArrowUp move the highlight; Tab commits
  the **highlighted** entry (not `matches[0]`) as `<prefix><name> ` and closes; Esc dismisses
  without clearing typed text; Enter keeps sending. With the menu **closed**, Enter/Shift+Enter/
  Escape-while-busy are untouched and Tab is not `preventDefault`ed.

**Verify:** `cd src/decafclaw/web/static && npx vitest run components/chat-input.test.js`

## Phase 4 — Docs

**Advances:** no criterion — required by the repo's "update the docs page in the same PR" rule.

- `docs/websocket-messages.md` regenerates itself; do not hand-edit.
- Note the autocomplete in `docs/web-ui.md`, and resolve `docs/commands.md:52`'s
  `argument-hint` "(future: UI autocomplete)" — that future is now.

**Verify:** `make check`

## Full gate

`make check` · `make test` · `npx vitest run` · per-criterion checks by name · tamper diff
`git diff a0a95e2 -- <the three check files>` must be empty.
