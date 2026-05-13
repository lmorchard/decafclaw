# TUI Network Client — Spike

**Status:** Spec approved 2026-05-13. Implementation not started.
**Issue:** [#464](https://github.com/lmorchard/decafclaw/issues/464) (reframed from original "Ink TUI over JSON-RPC stdio")
**Type:** Prototype / spike. Throwaway-friendly until promoted.

## Context

Issue 464 was originally filed during a hermes-agent inspiration scan: hermes's TUI is an Ink (React-for-terminal, TypeScript) frontend that spawns its own Python backend over JSON-RPC stdio. We deprioritized it because `interactive_terminal.py` is sufficient for current use and the hermes model would duplicate session/tool/model state and conflict with the "one bot instance per token" Mattermost rule.

The reframing: build the Ink TUI as a **thin network client to the running decafclaw bot** over the existing WebSocket gateway — the same surface the web UI already drives. This sidesteps both problems:

- No competing bot instance — the TUI is a viewer/driver, not its own agent.
- No duplicate state — sessions, tools, model calls, confirmations, compaction all stay on the running daemon.
- No new transport — reuses `src/decafclaw/web/websocket.py` and the wire contracts in `src/decafclaw/web/message_types.json`.

Hermes remains a reference for terminal-UX patterns (composer behavior, prompt flows, markdown rendering) but **not** a template for the architecture. This is decafclaw-native: WebSocket + cookie auth + per-conversation event subscription, not stdio + JSON-RPC + spawned backend.

## Goals

A working spike that lets a developer:

1. Run `decafclaw-tui --token <t>` against `make dev` on localhost.
2. Pick or specify a conversation.
3. Send messages and watch streaming assistant responses.
4. See tool activity (`tool_call_start` / `tool_start` / `tool_status`) in an activity lane.
5. Approve or deny inline confirmation requests (shell commands, end-turn gates).
6. Hit `Ctrl+C` to cancel an in-flight turn or exit cleanly.

The bar for "spike validated": the TUI is good enough to drive a normal coding-assistance turn end-to-end without falling back to the web UI.

## Non-goals (Phase 2 candidates)

These are **explicitly deferred from the spike**, not rejected. If the spike validates the idea, several of these are likely first follow-ups — captured here so we don't re-derive them.

| Deferred item | Notes |
|---|---|
| Markdown rendering of assistant text | Hermes has a small Markdown-to-Ink renderer. Likely first Phase 2 add — plain text gets old fast. |
| Multi-line composer / queued input | Hermes-style queue while agent is busy, `\` + Enter newline, Shift+Enter, `$EDITOR` integration. |
| Input history (persistent) | `~/.decafclaw/tui_history` or similar. |
| Tab completion | Slash commands, `@[[Page]]` mentions, file paths. |
| Theme/skin support | Match decafclaw's web UI theme tokens if we promote. |
| Canvas panel mirror | Render a `canvas_update` view in a side pane. |
| Files / vault sidebars | Browse and open via REST + WS. |
| Notification inbox | Bell push over WS already exists; surface it in a status line. |
| Widget inputs | `widget_input` flow — needed for skills that emit widgets. |
| Scrollback past current session | Currently relies on terminal scrollback; would need internal pager. |
| Mouse support | Stock Ink supports it; we're just not wiring it. |
| OSC52 clipboard copy | Convenient for grabbing assistant output. |
| Conversation folders / move/rename | REST endpoints exist; not part of the chat surface. |
| Model picker UI | `set_model` wire message exists; spike just uses whatever's active. |
| Reflection / context inspector views | Diagnostic surfaces in the web UI; not core to chat. |
| Windows support | Mac/Linux only for the spike. |

If the spike succeeds, the natural Phase 2 sequence is roughly: **markdown rendering → multi-line composer + history → tab completion → theme → model picker**, with widgets/canvas/files/vault later if there's appetite.

### Cross-cutting Phase 2 concern: client capability hints

Several deferred items — **widget inputs**, **canvas panel**, and possibly file/wiki editing surfaces — share an architectural prerequisite that's missing from today's wire protocol: **decafclaw assumes the connected client can render full HTML.** The web UI's widget host, canvas panel, and Milkdown editor all emit and consume rich HTML/JS surfaces. A TUI can't, and the spike currently dodges this only by deferring those features.

Today's wire protocol has no way for the bot to know what the connected client can render. Phase 2 work on widgets or canvas should resolve this first — naively adding widget rendering to the TUI without a capability story will either break silently (HTML widget emitted to a terminal) or force a protocol break later.

Possible directions (not chosen here — flagged for Phase 2 design discussion):

- **Client capability handshake.** TUI sends a `client_capabilities` message on connect declaring supported surfaces (e.g., `html_widgets: false`, `canvas: false`). Skills/tools read this off `ctx` and pick a degradation path when emitting a widget.
- **Per-widget TUI fallback in the widget definition.** Each widget declares a TUI-equivalent form (text prompt, picker, masked input — hermes's fixed vocabulary is a reference point). Widget host on TUI renders the fallback; web renders HTML. Skill author writes one widget, both surfaces work.
- **Capability gate at emit time.** Skill author opts into "widget" only when the active client supports it; otherwise emits a plain text question or confirm prompt.

Markdown rendering is *not* in this bucket — it's a pure client-side rendering concern, not a backend-protocol one. Same for theme/skin: the bot doesn't need to know.

## Architecture

```
┌────────────────────┐   WebSocket (cookie auth)   ┌─────────────────────────┐
│  decafclaw-tui     │  ──────────────────────►   │  decafclaw bot (running) │
│  (Node + Ink)      │  ◄──────────────────────    │  src/decafclaw/web/      │
│                    │                              │  websocket.py            │
│  - WS client       │                              │  - existing handler      │
│  - dispatcher      │                              │  - existing auth         │
│  - Ink UI          │                              │  - ConversationManager   │
└────────────────────┘                              └─────────────────────────┘
                                                              │
                                                              ▼
                                                         Mattermost / heartbeat /
                                                         schedules / web UI users
                                                         (all unaffected)
```

- Network boundary: `ws://<host>:<port>/api/ws` with `Cookie: decafclaw_session=<token>` on upgrade.
- Conversation selection: REST `GET /api/conversations` for the picker; WS `select_conv` to subscribe.
- No bot-side changes. The TUI is a pure client.

## Components

```
tui/
  package.json          # ink, ink-text-input, react, ws, tsx, typescript
  tsconfig.json
  README.md
  src/
    entry.tsx           # TTY gate, argv/env parsing, auth resolution, render <App/>
    App.tsx             # Ink tree: transcript + activity + composer + confirm prompt
    wsClient.ts         # connect, send, onMessage, reconnect w/ backoff, close
    types.ts            # hand-typed WS message discriminated union (codegen-shaped)
    conversationPicker.tsx  # initial list/create picker when --conv is absent
```

Five files. The spike's structural budget is "everything lives in one of these five." When a file needs to split, it's a signal to revisit Option B (layered structure, codegen).

### File responsibilities

- **`entry.tsx`** — TTY check, argv parsing (`--token` / `--conv` / `--host`), env fallback (`DECAFCLAW_TOKEN`, `DECAFCLAW_HOST`), construct `WSClient`, render `<App/>`. Exits early on missing token or non-TTY stdin.
- **`wsClient.ts`** — Owns the socket. Exposes `connect()`, `send(msg)`, `on(handler)`, `close()`. Reconnect with exponential backoff (1s, 2s, 4s, … cap 30s). Surfaces `reconnected` events to the dispatcher so the UI can mark `[reconnected]` and re-issue `select_conv`.
- **`types.ts`** — One TS type per wire message, keyed on `type` field. Exports `WSMessage` discriminated union. Field names match `src/decafclaw/web/message_types.json` verbatim. Includes both directions; client→server types are used by `wsClient.send()` callers, server→client by the dispatcher.
- **`App.tsx`** — React `useState` for: transcript array, in-flight assistant draft text, activity-lane state (current tool name/status), confirm-prompt state (request_id + payload), connection state. Single `dispatch(msg: WSMessage)` reducer-style function with an exhaustive `switch` (TS `never` guard on default). Composer is a stock `ink-text-input`. Confirm prompt suspends the composer and accepts `y` / `n` / `a` (always).
- **`conversationPicker.tsx`** — Hits `/api/conversations` via `fetch` (Node 18+ has it), shows up to N recent conversations, lets user pick or trigger "new conversation" (server creates lazily on first `user_message` to a fresh `conv_id`, so picker just generates one).

## Data flow

1. `entry.tsx` resolves token → constructs `WSClient({host, token})` → renders `<App/>`.
2. `WSClient` opens `/api/ws` with `Cookie: decafclaw_session=<token>` header. On open, emits `ready` to dispatcher.
3. If `--conv <id>` provided: dispatcher sends `select_conv` immediately. Otherwise: render `<ConversationPicker/>`, fetch list via REST, on selection send `select_conv`.
4. Server responds with `conv_selected` (+ initial state) and `conv_history` (recent messages). Dispatcher populates transcript.
5. **Message handling** (server → client):
   - `chunk` → append to in-flight assistant draft.
   - `message_complete` → finalize draft into transcript, clear draft.
   - `tool_call_start` / `tool_start` / `tool_status` → update activity-lane state.
   - `tool_end` (if surfaced; otherwise next `chunk` clears it) → clear activity lane.
   - `confirm_request` → set confirm-prompt state, suspend composer.
   - `compaction_done` → show `[compaction complete]` line; optionally reload history.
   - `model_changed` → show `[model: …]` line.
   - `error` → push error line.
   - Unknown `type` → log to stderr, ignore. (Forward-compat.)
6. **Message handling** (client → server):
   - Composer submit → `{type: "user_message", conv_id, text, attachments: []}`.
   - Confirm `y` → `{type: "confirmation_response", conv_id, request_id, decision: "approve", extras: {}}`. `n` → `"deny"`. `a` → `"always"`.
   - `Ctrl+C` while turn is in flight → `{type: "cancel_turn", conv_id}`. While idle → close WS cleanly, exit.

## Error handling

| Failure | Behavior |
|---|---|
| Missing token (no flag, no env) | Print error, exit 1. |
| WS upgrade rejected (401/403) | Print "auth failed" + URL, exit 1. |
| WS dropped mid-session | Reconnect with backoff. On success: re-`select_conv`, request fresh `conv_history`, mark `[reconnected]` in transcript. |
| Malformed JSON line | Log to stderr (above the Ink frame), ignore. |
| Unknown wire `type` | Log to stderr, ignore. Don't crash on protocol additions. |
| Non-TTY stdin | Exit early with a message — same gate hermes uses. |
| `Ctrl+C` while WS busy | Send `cancel_turn`. Press again to exit. |

No retry loops with hidden state, no silent fallbacks. If something keeps failing, the user sees it on stderr above the UI.

## Testing

- **Vitest unit test** for the dispatcher: feed sample WS messages → assert state transitions. Pure-function reducer is the only thing worth testing in a spike.
- **Manual smoke**:
  - Run `make dev` (already running per Les's workflow) and `cd tui && npm run dev` in another shell.
  - Send a message → see streaming chunks → see `message_complete`.
  - Trigger `run_shell_command` via the agent → see `confirm_request` → approve → see tool output.
  - Force a compaction → see `compaction_done` line.
  - Drop the WS (kill `make dev`, restart) → see reconnect + `[reconnected]` marker.
- **No Ink rendering tests, no end-to-end tests.** Promoting the spike includes adding those.

## Promotion path (A → B, no rework)

The spike is Option A (hand-typed minimal Ink). The successor is Option B (codegen from `message_types.json`, optionally split into layered directories). The discipline that makes A→B free:

1. **`types.ts` is shaped exactly like codegen output would be.** One type alias per message, field names matching `message_types.json` verbatim, `WSMessage` discriminated union exported. When we promote: write `tui/scripts/gen-types.ts`, extend `make gen-message-types` to also emit `tui/src/types.generated.ts`, `git mv` the hand-written file out. Zero consumer churn.
2. **Dispatcher switch is exhaustive against `WSMessage["type"]`.** TS `never` guard on default. A new wire type added in `message_types.json` becomes a compile error in the TUI rather than silent drift.
3. **No reaching into message internals from `wsClient.ts`.** It's a dumb pipe — `send(msg)` and `on(handler)`. State decisions live in `App.tsx`. This means splitting `App.tsx` later doesn't require restructuring the transport.

If the spike grows past five files organically, that's the trigger to split — not premature.

## Acceptance criteria

The spike is "validated" when all of the following work against a locally running `make dev`:

- [ ] Connect with `--token <t>` and either pick a conversation or pass `--conv <id>`.
- [ ] Send a user message, see streamed assistant response, see `message_complete` finalize it.
- [ ] Trigger a confirm-gated tool (shell command), approve inline, see tool output continue.
- [ ] Trigger a confirm-gated tool, deny inline, see the agent recover.
- [ ] `Ctrl+C` mid-turn cancels via `cancel_turn`; transcript reflects the cancel.
- [ ] Kill `make dev`, restart, see TUI reconnect and resume the same conversation.
- [ ] Run `cd tui && npm test` — dispatcher unit test passes.
- [ ] Run `cd tui && npm run lint && npx tsc --noEmit` — clean.

## Open questions

None at spec time. Items that may surface during implementation:

- Whether `tool_end` is currently emitted to the WS (not in the message_types.json grep). If not, activity-lane clear-on-next-`chunk` heuristic stands.
- Whether `/api/conversations` returns a shape the picker can use directly, or we need a different REST endpoint for the listing.

Both are implementation-time verifications, not blockers.
