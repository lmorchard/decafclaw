# Web Terminal as Canvas Tab — Design Spec

**Status:** brainstormed + gap-reviewed 2026-07-23; ready to plan
**Date:** 2026-05-06 (revised 2026-07-23)
**Owner:** Les
**Audience:** human only — agent has no access by design
**Issue:** [#442](https://github.com/lmorchard/decafclaw/issues/442)

> ## Revisions — 2026-07-23 gap review
>
> The original brainstorm below is preserved as-is. A gap review against the
> current codebase (post-#576 sidecar dirs, current widget/canvas system)
> found four items. Where a decision below is superseded, **this block wins.**
>
> 1. **Canvas sidecar path (stale).** Decision #1 and the data-flow section say
>    `{conv_id}.canvas.json`. Post-#576 the real path is
>    `workspace/conversations/{conv_id}/canvas.json` via
>    `conversation_paths.sidecar_path`. No flat file is read at runtime.
>
> 2. **Command routing (revised → server-side).** The "side-effect command"
>    endpoint `POST /api/commands/{name}` and the client-side
>    `SIDE_EFFECT_COMMANDS` table are **dropped.** There is no client-side
>    slash-command interception today — every `/command` travels over the chat
>    WS to server-side `_handle_send` → `dispatch_command`, which already
>    returns a `COMMAND_ACK` **without forcing an LLM turn.** `/terminal` will
>    be a server-side command handler that spawns the PTY + creates the canvas
>    tab inline. The "never triggers a turn / never writes the archive"
>    invariant still holds; it's enforced in the command handler, not a
>    separate HTTP route. One command layer, not two.
>
> 3. **Close-tab confirmation (revised → client-side dialog).** The persistent
>    confirmation infra is conversation-scoped and **always writes two rows to
>    the JSONL archive**, contradicting this spec's "never writes the archive"
>    constraint; its reload-survival also relies on a recovery handler that
>    cannot resurrect an in-memory PTY. Replace with a **plain client-side
>    confirm dialog** in the canvas close-tab path for `widget_type ==
>    "terminal"`. The server `close_tab` still detects the terminal type and
>    kills the PTY; the confirmation is UI-only.
>
> 4. **Widget lifecycle on tab switch (resolved open question → keep-alive).**
>    The canvas renders only the active tab through one shared
>    `<dc-widget-host>`; switching widget types tears the widget down, and two
>    same-type tabs alias onto one instance (a correctness bug). **Resolution:
>    add keep-alive/hidden-mount to the canvas host** — mount every tab's
>    widget, toggle visibility, key instances by tab id. This fixes aliasing
>    structurally and keeps the terminal's WebSocket live across switches. The
>    reconnect + ring-buffer replay below is **retained regardless** (reload
>    and standalone-window paths still cold-start), so keep-alive is additive
>    and self-de-risking.

## Summary

Add a websocket-based shell terminal to the DecafClaw web UI, rendered as a canvas tab using xterm.js. Each terminal is a live PTY session attached over a dedicated WebSocket. Terminals are **per-conversation**, **persistent across browser reloads**, and explicitly **human-only** — the agent has no tool definitions for spawning, attaching to, or reading from terminal sessions. Multiple terminals can coexist in the same conversation's canvas; multiple browser views can attach to the same session.

The feature also establishes a generalizable **side-effect slash-command** pattern (`/terminal` is the first instance) — slash commands that produce immediate UI/server side-effects without going through the LLM or the conversation archive.

## Goals

- A user with a valid web auth token can open a fully interactive shell in their browser by typing `/terminal` in the chat input.
- The shell is a real PTY running the user's `$SHELL` (fallback `/bin/sh`) in their workspace path (or a CWD they specify).
- Browser reload restores the terminal — process keeps running, scrollback is replayed.
- The same terminal can be popped into a standalone window (`/canvas/{conv}/{tab}`) and viewed alongside the in-canvas view; both attach to the same PTY (multi-attach broadcast).
- Multiple terminals per conversation work without interference.
- Agent has zero ability to spawn, attach to, or observe terminals.

## Non-goals

- Cross-conversation / user-global terminals.
- Detached-then-reattachable sessions (tmux-style).
- Disk-persisted scrollback that survives server restart.
- Mobile onscreen-key helpers (Esc/Ctrl/arrows).
- Read-only agent access to the shell.
- Recording / replay.

(All of the above are captured in **Future ideas** below.)

## Design decisions (from brainstorm)

| # | Decision | Choice |
|---|----------|--------|
| 1 | Session scoping | Per-conversation (lives in `{conv_id}.canvas.json` like other tabs) |
| 2 | Lifecycle | Close-tab kills the PTY; no idle TTL; server restart kills (PTY is a child process) |
| 3 | Scrollback | In-memory ring buffer (default 10 MB), replayed on attach |
| 4 | Spawn entry point | Slash command `/terminal [cwd]` in chat input; agent cannot spawn |
| 5 | Slash-command processing | Side-effect path that bypasses LLM/archive/turn — generalizable |
| 6 | Argument support | Optional CWD argument; default = workspace path; allowed-roots whitelist |
| 7 | Multi-window attach | Multi-attach broadcast (tmux-style); smallest-viewport-wins for resize |
| 8 | Mobile | Render but no onscreen-key helpers (option B) |
| 9 | Architecture | Terminal-as-widget + dedicated WS endpoint + stdlib `pty.fork()` |

## Architecture

Three components, talking via established channels.

### Server: `decafclaw/terminals.py` (new)

In-memory `TerminalSession` registry keyed by `(conv_id, tab_id)`. Each session owns:

```python
@dataclass
class TerminalSession:
    conv_id: str
    tab_id: str
    session_id: str           # uuid4, also embedded in widget data
    pid: int
    fd: int                   # PTY master fd
    cwd: str                  # resolved CWD at spawn
    buffer: bytearray         # ring buffer, capped at config.terminal.buffer_bytes
    attached: set[WebSocket]  # currently attached views
    viewports: dict[WebSocket, tuple[int, int]]  # per-connection cols/rows
    reader_task: asyncio.Task
    exit_status: int | None = None
```

**Spawning** uses `pty.fork()` (in `asyncio.to_thread` so the fork doesn't block the event loop). Child branch: `os.chdir(cwd)`, `os.execvpe($SHELL, [$SHELL], env)`. Parent branch: register an asyncio reader on the PTY fd via `loop.add_reader(fd, _on_pty_readable)`. Inherits the server's environment unmodified (this is human-only — env-scrubbing would just break tools like `ssh-agent` and `direnv`), with one override: `TERM=xterm-256color` so programs negotiate against xterm.js's actual capabilities rather than whatever the parent process inherited. The kernel's PTY size starts at the kernel default (typically 80×24) and is corrected via `TIOCSWINSZ` when the first WS connection sends its initial `resize`.

**Reader task** reads up to 64 KB per wakeup, appends to ring buffer (drop-from-front when over cap, log debug but never raise), and broadcasts `await ws.send_bytes(chunk)` to each attached WS in `asyncio.gather(..., return_exceptions=True)` so a slow client can't stall the others (a failing send removes that WS from the attached set). On EOF (PTY closed, child exited): `os.waitpid(pid, 0)` to reap, set `exit_status`, broadcast `{type: "session_ended", exit_status}`, remove from registry, close all attached WS.

**Multi-attach + smallest-viewport-wins resize:** every `resize` message updates `session.viewports[ws]`. Server then computes `(min_cols, min_rows)` across all viewports, and if it differs from current PTY size, calls `fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))` and broadcasts `{type: "size_changed", cols, rows}` so each client can render letterboxing if its own viewport is larger. On detach (WS close), recompute without that viewport.

**Replay on attach:** when a new WS attaches, server sends the current `buffer` as one or more binary frames, then a `{type: "buffer_replay_done"}` text frame. xterm.js handles ANSI escape sequences in the replay correctly (it reconstructs cursor position, colors, etc. from the stream).

**Lifecycle and cleanup:**

- Close-tab via `closeTabFromUi` → existing `close_tab` REST handler → server checks if `widget_type == "terminal"`, and if so:
  - Computes confirmation message based on `attached_count` and foreground-process detection (`os.tcgetpgrp(fd) != session.pid`):
    - Idle, single view: silent close.
    - Idle, multiple views: "Tab is open in {N} window(s). Close session?"
    - Foreground process, single view: "Foreground process running. Close session?"
    - Foreground process, multiple views: "Foreground process running, and tab is open in {N} window(s). Close session?"
  - On approval: `os.killpg(pgid, SIGHUP)`, then `SIGKILL` after a short grace period if still alive; remove from registry; close attached WS.
- Confirmation flows through the existing confirmation-request infra (persistent across reload).
- Server shutdown: `runner.py` shutdown hook walks the registry, SIGHUPs each session.
- Conversation archive/delete: hook in `conversation_folders.py` walks any sessions for that conv and kills them.

### Server: WebSocket route `GET /ws/terminal/{conv_id}/{tab_id}`

Registered alongside `/ws/chat` in the web app. Validates the session cookie via `get_current_user`; rejects with 4001 if missing. Looks up the session in the registry; if not found, sends `{type: "session_ended"}` frame and closes. If found:
1. Adds the WS to `session.attached`.
2. Replays ring buffer (binary frames + `buffer_replay_done` text frame).
3. Bidirectional message pump until disconnect.

**WebSocket message protocol** (text frames for control, binary for data):

| Direction | Type | Shape |
|-----------|------|-------|
| Client → server | `input` | `{"type": "input", "data": "<utf-8 keystrokes>"}` |
| Client → server | `resize` | `{"type": "resize", "cols": N, "rows": M}` |
| Client → server | `ping` | `{"type": "ping"}` (optional liveness) |
| Server → client | _binary_ | Raw stdout/stderr bytes — xterm.js wants raw bytes, no JSON wrapping |
| Server → client | `session_ended` | `{"type": "session_ended", "exit_status": N}` |
| Server → client | `size_changed` | `{"type": "size_changed", "cols": N, "rows": M}` |
| Server → client | `buffer_replay_done` | `{"type": "buffer_replay_done"}` |

### Server: side-effect command endpoint `POST /api/commands/{name}`

Single mounted route. Looks up `name` in the side-effect-command registry, validates auth, builds a `CommandContext`, parses JSON body, dispatches. Returns `{ok, ...result}` or `{ok: false, error}`. Errors are HTTP 200 with `ok: false` so the client UI can show toasts without distinguishing transport from command failures.

### Client: terminal widget at `web/static/widgets/terminal/`

`widget.json`:

```json
{
  "name": "terminal",
  "description": "Live PTY shell session attached over WebSocket. Created by /terminal slash command — agent cannot spawn.",
  "modes": ["canvas"],
  "accepts_input": false,
  "data_schema": {
    "type": "object",
    "required": ["session_id"],
    "properties": {
      "session_id": {"type": "string", "minLength": 1},
      "cwd":        {"type": "string"},
      "shell":      {"type": "string"}
    },
    "additionalProperties": false
  }
}
```

`widget.js` — `<dc-widget-terminal>` Lit component. Lifecycle:

- `connectedCallback`: mounts xterm.js into a container div, applies fit-addon, opens WS to `/ws/terminal/{conv}/{tab}`. Subscribes to `ResizeObserver` on the container; on resize → fit-addon recomputes cols/rows → sends `{type: "resize", cols, rows}` over WS.
- WS `onmessage`: binary frames go to `term.write(data)`; text frames are JSON control messages.
- xterm.js `onData`: sends `{type: "input", data: chunk}`.
- `disconnectedCallback`: closes WS, disposes xterm.
- On `session_ended` text frame: render styled `[session ended · exit N]` banner; stop accepting input. Tab-close still works.

**Connection state machine:**

```
[mounting] → [connecting] → [replaying] → [attached]
                ↓                ↓             ↓
            [error]          [error]      [session_ended]
                                              ↓
                                         [disconnected — show banner]
```

`replaying`: server is streaming the ring buffer; client doesn't enable input until `buffer_replay_done`.

WS close not preceded by `session_ended` (e.g. network blip) → auto-reconnect with exponential backoff (1s, 2s, 4s, capped at 30s, max 5 attempts). On reconnect server replays buffer; lost input is lost (no retry queue).

**Open implementation question:** does `<dc-widget-host>` re-mount the widget on tab switch? If so, the widget would thrash WS connections every time the user clicks between tabs. Will be resolved during implementation — either the host preserves widget instances across tab switches, or the terminal widget detects "still in DOM, just hidden" via `visibility` events and doesn't tear down.

**Theming:** xterm.js theme object populated from CSS custom props (`--pico-color`, `--pico-card-background-color`, etc.). Subscribes to theme changes via the existing `theme-toggle.js` event so a switch updates the terminal live.

### Standalone window (`/canvas/{conv_id}/{tab_id}`)

Works identically to existing widgets — same widget renders at full viewport, no tab strip. The standalone view holds its own WS connection (multi-attach with the in-canvas view if both are open). When the canvas-side tab is closed, the standalone view receives `session_ended` and renders the banner cleanly rather than freezing or crashing.

### Side-effect commands as a generalizable pattern

`decafclaw/web/side_effect_commands.py` (new module) exposes a registry decorator:

```python
@side_effect_command("terminal")
async def cmd_terminal(ctx: CommandContext, args: dict) -> dict:
    cwd = args.get("cwd") or str(ctx.config.workspace_path)
    _validate_cwd_against_allowed_roots(ctx.config, cwd)
    session = await terminals.spawn(ctx, conv_id=args["conv_id"], cwd=cwd)
    result = await canvas.new_tab(
        ctx.config, args["conv_id"],
        widget_type="terminal",
        data={"session_id": session.session_id, "cwd": cwd},
        emit=ctx.emit_canvas,
    )
    return {"tab_id": result.tab_id, "session_id": session.session_id}
```

**Constraints baked into the pattern:**

- Side-effect commands **never** trigger an agent turn, **never** write to the conversation archive, **never** invoke the LLM.
- They run with the caller's session privileges (web auth) but they have **no access to the agent's tool registry** — no accidental escalation through "well, this command uses the bash tool internally."
- They get a `CommandContext` not a normal `Context`, so type-level confusion with agent code paths is impossible.

**Client side:** `chat-input.js` has a `SIDE_EFFECT_COMMANDS` table mapping `/name` to `{endpoint, parseArgs}`. On submit, if input starts with a key in the table: parse args, POST to endpoint, clear input, do **not** append to message stream or send chat WS message. Show a transient toast on success/error.

**Distinction from existing user-invocable skill commands** (e.g. `/dream`, `/garden`):

| | User-invocable skill command | Side-effect command |
|--|------------------------------|---------------------|
| Triggers a turn? | Yes | No |
| LLM involved? | Yes | No |
| Written to archive? | Yes (as user message) | No |
| Use case | "Have the agent do something" | "Have the UI/server do something deterministically" |

Adding a new side-effect command later is one decorator on the server + one table entry on the client.

## Data flow for a new terminal

```
user types "/terminal" in chat-input
  → POST /api/commands/terminal {conv_id}
  → server: spawn PTY in workspace
  → server: new_tab(widget_type=terminal, data={session_id, cwd})
  → server: emit canvas_update kind=new_tab over chat WS
  → client canvas-state.js applies event → renders terminal widget
  → widget connects to /ws/terminal/{conv}/{tab}
  → server replays empty buffer, attaches connection
  → bidirectional flow active
```

## Configuration

New `TerminalConfig` dataclass in `config_types.py`, attached to `Config` as `config.terminal`:

```python
@dataclass
class TerminalConfig:
    enabled: bool = True
    buffer_bytes: int = 10 * 1024 * 1024     # ring buffer cap per session
    default_cwd: str | None = None            # falls back to workspace_path
    allowed_cwd_roots: list[str] = field(default_factory=list)  # empty → [workspace, $HOME]
    shell_override: str | None = None         # falls back to $SHELL or /bin/sh
    max_sessions_per_conv: int = 8            # belt-and-suspenders DoS guard
```

Resolution follows the project pattern: dataclass defaults → `data/{agent_id}/config.json` → env (`TERMINAL_ENABLED`, `TERMINAL_BUFFER_BYTES`, etc.). `enabled=false` makes `/terminal` return a "feature disabled" toast and the WS route 404 — useful for environments where shell access shouldn't be exposed at all.

## Security model

- `enabled=true` is the trust boundary. The web auth cookie is the gate; this is human-only by design.
- The agent has **zero tool definitions** for spawning, attaching to, listing, killing, or reading from terminal sessions. The `decafclaw/terminals.py` module is not imported anywhere in `decafclaw/tools/` or `decafclaw/skills/`.
- The WS endpoint `/ws/terminal/{conv}/{tab}` validates the session cookie via the same `get_current_user` path as `/ws/chat`. No token-in-URL fallback.
- CWD validation: `Path(requested_cwd).resolve()` must be within one of `allowed_cwd_roots` (default `[workspace_path, $HOME]`). Rejected requests return `{ok: false, error: "cwd not allowed"}`. This is defense-in-depth, not a security guarantee — anyone with a web token already has shell access via the terminal. The point is to make accidental exposure of, say, `/etc` from an autocomplete typo a non-issue.
- Per-conv session cap (`max_sessions_per_conv`) prevents accidental fork-bomb from a stuck client retry loop. Counts only live sessions in the registry — exited sessions are removed at PTY EOF, so the cap is a steady-state ceiling, not a lifetime quota. `/api/commands/terminal` returns `{ok: false, error: "max sessions reached"}` when the cap is hit.
- Logging: spawning a terminal logs at INFO level (`username, conv_id, tab_id, cwd, shell, pid`); session exit logs at INFO with exit status. Buffer contents are never logged.

## Testing

Tests live in `tests/test_terminals.py` (new) and `tests/web/test_side_effect_commands.py` (new).

- **Unit, no PTY:** `TerminalSession` accepts dependency-injected `pty_module`/`os_module`. Tests stub `pty.fork()` to return a fake fd, stub `os.read()` to return canned bytes (or block on an `asyncio.Event`), and assert on ring-buffer contents, viewport-min calculation, broadcast fan-out, attach/detach bookkeeping.
- **Integration, real PTY:** one test that actually spawns `/bin/echo hello` (not `$SHELL` — too slow and shell-startup is noisy), reads the output, asserts the session cleans up on PTY EOF.
- **WS protocol:** Starlette `TestClient` + `WebSocketTestSession` (already used in `test_websocket.py`); reuse the pattern for `/ws/terminal/{conv}/{tab}`. Stub the registry to inject pre-canned sessions; assert auth rejection, session-not-found behavior, replay, input/resize handling.
- **Side-effect command:** `tests/web/test_side_effect_commands.py` covers the registry, the auth gate, the CWD allowed-roots check, the "no archive write" invariant.
- **No-agent-turn invariant:** regression test asserts that calling `/api/commands/terminal` does not invoke `ConversationManager.enqueue_turn`. This is the load-bearing assertion for the side-effect-command guarantee.
- **Test speed:** patch `terminals.spawn` in any test that uses `make_app()` so test fixtures don't accidentally spawn real PTYs.

## File-level change summary

**New:**
- `src/decafclaw/terminals.py` — `TerminalSession`, registry, spawn/attach/detach/kill, reader task.
- `src/decafclaw/web/side_effect_commands.py` — registry decorator, `CommandContext`, `POST /api/commands/{name}` route.
- `src/decafclaw/web/static/widgets/terminal/widget.json` — descriptor.
- `src/decafclaw/web/static/widgets/terminal/widget.js` — `<dc-widget-terminal>` Lit component.
- `tests/test_terminals.py` — unit + integration coverage.
- `tests/web/test_side_effect_commands.py` — registry + invariants.
- `docs/web-terminal.md` — feature reference.
- `docs/side-effect-commands.md` — pattern doc.

**Modified:**
- `src/decafclaw/config_types.py` — `TerminalConfig` dataclass.
- `src/decafclaw/config.py` — wire env var resolution.
- `src/decafclaw/runner.py` — register `/ws/terminal/...` and `/api/commands/...` routes; shutdown hook to SIGHUP active sessions.
- `src/decafclaw/web/websocket.py` — register the new WS route alongside chat.
- `src/decafclaw/web/conversation_folders.py` — hook to kill sessions on conv archive/delete.
- `src/decafclaw/web/static/components/chat-input.js` — `SIDE_EFFECT_COMMANDS` table + interception logic.
- `src/decafclaw/web/static/components/canvas-panel.js` — verify widget-host doesn't tear down on tab switch (may need adjustment).
- `src/decafclaw/canvas.py` — close-tab path: detect `widget_type == "terminal"` and route through terminal kill flow.
- `package.json` (vendor build inputs) — add `xterm`, `@xterm/addon-fit`, `@xterm/addon-serialize`, `@xterm/addon-web-links`.
- `docs/web-ui.md` — Terminals section under Features.
- `docs/index.md` — link the two new docs.
- `CLAUDE.md` — sibling sentence under "User-invokable commands" pointing at side-effect commands; add `terminals.py` to key-files list.

## Future ideas (deferred)

1. **Read-only agent access to the shell** — let the conversation see scrollback (with redaction policy, opt-in per-session, agent-side tool). Its own design problem.
2. **Disk-persisted scrollback** — survives server restart; durable log under `workspace/conversations/{conv_id}/terminals/{tab_id}.log`; rotation; cleanup-on-close-tab.
3. **Detached-terminals model** — close-tab-detaches; separate kill action; "reattach" UI for orphaned sessions (tmux-style).
4. **Cross-conversation user-global terminals** — terminals as user-scoped entities surfaced via canvas attachments.
5. **Onscreen key helpers for mobile** — Esc/Ctrl/arrow buttons row.
6. **Vendor bundle splitting / lazy-loading per widget** — broader concern than just terminals; xterm.js addon weight is a small contributor (~150 KB). Lazy-load widget JS via dynamic `import()` only when first rendered is a reasonable cheap intermediate step.
7. **Standalone-window-only terminals** — open `/terminal` directly in a separate browser window, skipping the canvas tab entirely.
8. **Recording / replay** — `script(1)`-style session recording to a vault page or workspace artifact.

## Open questions / risks

- **Widget-host re-mount on tab switch:** unknown until implementation. If the host re-mounts widgets when `activeTabId` changes, the terminal widget would thrash its WS connection on every tab click. Mitigation: either preserve widget instances across tab switches in `<dc-widget-host>`, or have the terminal widget detect "hidden but still in DOM" and skip teardown. Will resolve during implementation.
- **Standalone-window widget loading:** the standalone `/canvas/{conv}/{tab}` page is a separate document — needs to verify it loads the same widget bundle as the embedded canvas (it almost certainly does, since `iframe_sandbox` works in both contexts). If lazy-loading widget JS lands as a follow-up, the standalone page must trigger the same dynamic import.
- **PTY behavior on macOS vs Linux:** `pty.fork()` is POSIX, but small differences in `TIOCSWINSZ` semantics or signal delivery may surface. Primary dev target is macOS (Les's environment); Linux deploy target is the existing `decafclaw@lmorchard` host. Test on both.
- **Multi-attach input interleaving:** with broadcast input, accidentally typing in a background window corrupts the foreground session's command line. Acceptable for power-user use but worth flagging in `docs/web-terminal.md`.
- **Shell-startup noise in tests:** real-PTY integration test should use `/bin/echo` not `$SHELL` to avoid shell-rc-file variability and slow startup.
