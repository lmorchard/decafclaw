# Web Terminal

A real interactive shell (PTY) rendered as a canvas tab in the web UI. This is
a **human-only** surface — the agent has no tools, no code path, and no
import route to a running terminal session. It exists for Les to pop open a
shell alongside a conversation, not for the agent to run commands.

Tracking issue: [#442](https://github.com/lmorchard/decafclaw/issues/442).
Dev-session artifacts: [`docs/dev-sessions/2026-05-06-2042-web-terminal-canvas/`](dev-sessions/2026-05-06-2042-web-terminal-canvas/).

## Opening a terminal

Two ways, both human-only:

- Click the **`>_` button** in the chat input row (next to the 📎 attach
  button). It appears once a conversation is active and sends `/terminal`
  over the normal chat WebSocket — the same path as typing it, no dedicated
  endpoint. Opens in the default CWD.
- Type `/terminal` or `/terminal <cwd>` in the chat input (use the typed form
  to pass a specific CWD).

`/terminal` is a
**server-side side-effect command** — it is intercepted in `_handle_send`
(`web/websocket.py`) before command dispatch, spawns the PTY, and opens a
canvas tab, all without an LLM turn and without writing anything to the
conversation's JSONL archive. The client only sees a `COMMAND_ACK` frame and
the new canvas tab appearing.

- No argument → CWD defaults to `terminal.default_cwd` (falls back to the
  agent's workspace path).
- An argument is resolved as a path and checked against
  `terminal.allowed_cwd_roots` (see [Security](#security) below); a
  disallowed CWD returns an inline error message instead of opening a tab.
- Each conversation is capped at `terminal.max_sessions_per_conv` concurrent
  sessions.

`/terminal` is the first instance of a more general "side-effect slash
command" pattern: a command that produces an immediate UI/server effect
without going through the LLM or the archive. There's no separate
side-effect-command registry or HTTP route for this — it's a single `if
text.startswith("/terminal")` branch ahead of normal command dispatch.

## Architecture

Three pieces, all human-only:

- **`terminals.py`** — `TerminalRegistry`, an in-memory registry of
  `TerminalSession` keyed by `(conv_id, tab_id)`. One registry instance lives
  on `app.state.terminal_registry` for the life of the HTTP server process.
- **`GET /ws/terminal/{conv_id}/{tab_id}`** — a dedicated WebSocket route
  (`websocket_terminal` in `web/websocket.py`) that attaches a browser
  connection to a session: replays the ring buffer, then relays `input` /
  `resize` control frames from the client and raw PTY bytes back.
- **Terminal widget** — `web/static/widgets/terminal/widget.js`, an
  xterm.js-backed canvas widget. It owns reconnect-with-backoff for the WS
  (server restart, network blip) and renders a `[reconnecting…]` /
  `[session ended]` status line for terminal states the PTY itself can't
  render.

### Spawning: `pty.fork()`

The registry's `spawn()` uses `pty.fork()`. The child gets its own session
with the PTY as its controlling terminal (`login_tty` = `setsid` +
`TIOCSCTTY`), which is what makes job control work and lets `killpg()` tear
down the whole shell tree without touching the server. The child only
`chdir`s to the CWD and `execvpe`s the shell between fork and exec (the
environment is built in the parent to keep child-side work minimal).

We first tried `os.posix_spawn(..., setsid=True)` to avoid forking a
multi-threaded interpreter (which trips CPython's `DeprecationWarning: ...
forkpty() may lead to deadlocks in the child`). But `posix_spawn`'s `setsid`
is not available on all platforms — it raises `NotImplementedError` on Linux
(CI and the deploy target), which would break `/terminal` entirely there. So
we fork. Per project decision the fork's `DeprecationWarning` is **not
suppressed**; it is ignored by Python's default warning filter in normal runs
and only shows in pytest's warnings summary (it does not fail tests).

### Output and replay

The registry adds an `asyncio` reader on the PTY master fd. Each readable
wakeup reads up to 64 KB, appends it to a per-session in-memory ring buffer
(capped at `terminal.buffer_bytes`, oldest bytes dropped first), and
broadcasts the chunk to every attached WebSocket. When a new WebSocket
attaches (fresh connection, reload, or a second viewer), the server replays
the current ring buffer as binary frames followed by a `buffer_replay_done`
text frame — xterm.js reconstructs cursor position, colors, etc. from the
replayed ANSI stream.

On PTY EOF (child process exited), the registry reaps the child with
`waitpid`, broadcasts `{"type": "session_ended", "exit_status": ...}` to
every attached connection, and removes the session from the registry.

## Security model

- **`terminal.enabled` is the trust boundary.** The `/terminal` command
  handler and the `/ws/terminal/...` route both check it and refuse (command:
  inline error; WebSocket: close code 4003) when disabled.
- **Web session cookie gate.** The WS route calls the same
  `get_current_user(websocket, config)` used by `/ws/chat`; missing/invalid
  auth closes with code 4001.
- **`allowed_cwd_roots` is defense-in-depth, not the primary boundary** — the
  shell itself can `cd` anywhere the OS user can. It only constrains where
  `/terminal <cwd>` is allowed to *start*. Resolution is a
  `Path.resolve()` + `Path.parents` containment check (`_is_within` in
  `web/websocket.py`) against each configured root; empty config defaults to
  `[workspace_path, $HOME]`.
- **Per-conversation session cap** (`terminal.max_sessions_per_conv`) bounds
  how many PTYs one conversation can accumulate.
- **The agent has zero access, structurally, not just by convention.**
  `terminals.py` is never imported by anything under `tools/` or `skills/` —
  there are no terminal tools, no terminal skill, and no way for an agent
  turn to reach the registry. This is enforced by
  `tests/test_terminals.py::test_no_agent_side_imports`, which greps every
  file under `tools/` and `skills/` for an import of `terminals` and fails
  the build if one appears. The always-loaded canvas tools
  (`canvas_close_tab`, etc.) can still *close* a terminal tab — see
  [Known limitations](#known-limitations) for what that does and doesn't do.

## Lifecycle and kills

A session's PTY process is killed (`SIGHUP`, then `SIGKILL` after a short
grace period via `killpg`) in exactly three places:

1. **Closing the tab.** The canvas panel's `[×]` on a terminal tab shows a
   plain client-side `window.confirm("Close tab \"...\"?")` dialog before
   calling `closeTabFromUi`. This is *not* the persistent server-side
   confirmation infrastructure used elsewhere (shell-command approvals,
   skill activation) — that infra always writes two rows to the JSONL
   archive and can't resurrect an in-memory PTY on reload, both of which
   contradict this feature's "no archive writes" and "process survives
   reload" goals. `canvas.close_tab()` detects `widget_type == "terminal"`
   and kills the underlying session when a `TerminalRegistry` is passed in.
2. **Server shutdown.** `shutdown_http_server()` calls
   `registry.shutdown_all()` before uvicorn stops, killing every live
   session — they're child processes of the server, not the OS session, and
   would otherwise be orphaned.
3. **Conversation deletion.** The delete-conversation REST handler calls
   `registry.kill_sessions_for_conv(conv_id)` before removing the
   conversation's files out from under a shell that might have its CWD
   inside them.

**No disk-persisted scrollback.** The ring buffer is in-memory only. A
browser reload or a second viewer replays it fine (the process is still
running); a server restart kills the process and the buffer along with it —
there is no cold-restart replay by design (non-goal, see spec).

## Multi-attach and resize

Multiple browser views — the in-canvas tab and a popped-out standalone
window (`/canvas/{conv_id}/{tab_id}`) — can attach to the same session
simultaneously, tmux-style. Each attached WebSocket registers its own
viewport size; the server computes the smallest `(cols, rows)` across all
attached viewports and applies that to the PTY via `TIOCSWINSZ`, then
broadcasts `size_changed` so every client can letterbox if its own viewport
is larger. This means a small popped-out window can force the in-canvas
view (and any program relying on terminal size) down to its dimensions.

The canvas host keeps every open tab's widget mounted (not just the active
one) and toggles visibility instead of tearing widgets down on tab switch —
this is what lets the terminal's WebSocket survive switching to another tab
and back. Reconnect-with-backoff and ring-buffer replay in the widget still
cover the cold-start cases (page reload, opening the standalone window
fresh) regardless.

### Known limitations

- **Input interleaving across multi-attach.** All attached views send raw
  keystrokes to the same PTY; there's no per-view input arbitration. Typing
  in a background (non-focused) view while another view is also live can
  interleave keystrokes into the shell's current input line. Treat
  multi-attach as "watch together," not "type from two keyboards at once."
- **`canvas_close_tab` (agent tool) does not kill a PTY.** The agent cannot
  create terminals, but if a human asks the agent to close a canvas tab and
  it happens to be a terminal, the LLM-facing `canvas_close_tab` tool calls
  `canvas.close_tab()` without a `TerminalRegistry`, so the tab disappears
  from the canvas but the underlying shell process is orphaned until the
  next server shutdown or conversation delete. Only the client-side
  close-tab path (with its confirm dialog) actually kills the process today.
- **No disk-persisted scrollback across server restart** (see above) — an
  accepted non-goal, not a bug, but worth remembering if you're relying on
  scrollback for anything durable.

## Configuration

`TerminalConfig` (`config_types.py`), resolved via `TERMINAL_*` env vars
(env vars take priority over `data/{agent_id}/config.json`, per the usual
[resolution order](config.md)):

| Field | Env var | Default | Meaning |
|---|---|---|---|
| `enabled` | `TERMINAL_ENABLED` | `true` | Master switch — the trust boundary. `/terminal` and the WS route both refuse when `false`. |
| `buffer_bytes` | `TERMINAL_BUFFER_BYTES` | `10 * 1024 * 1024` (10 MiB) | Per-session ring buffer cap; oldest bytes dropped first. |
| `default_cwd` | `TERMINAL_DEFAULT_CWD` | `None` (→ workspace path) | CWD used when `/terminal` is invoked with no argument. |
| `allowed_cwd_roots` | `TERMINAL_ALLOWED_CWD_ROOTS` | `[]` (→ `[workspace_path, $HOME]`) | Allow-list of roots a requested CWD must resolve under. |
| `shell_override` | `TERMINAL_SHELL_OVERRIDE` | `None` (→ `$SHELL` or `/bin/sh`) | Force a specific shell regardless of the server process's environment. |
| `max_sessions_per_conv` | `TERMINAL_MAX_SESSIONS_PER_CONV` | `8` | Per-conversation cap on concurrent sessions. |

## Storage

Terminal tabs live in the same per-conversation canvas sidecar as every
other canvas tab: `workspace/conversations/{conv_id}/canvas.json` (see
[Canvas panel](web-ui.md#canvas-panel)). The tab's `data` is
`{session_id, cwd, shell}` — enough to identify the session, but the PTY
itself (fd, buffer, live process) is never persisted; it only exists in the
`TerminalRegistry`'s memory for the life of the server process.
