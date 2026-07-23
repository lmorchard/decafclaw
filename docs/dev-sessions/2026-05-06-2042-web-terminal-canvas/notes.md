# Notes — Web Terminal as Canvas Tab (#442)

## Session recovery (2026-07-23)

Picked this back up after the `fix/meta-ingest-delegate-tasks-shape` branch
(PR #612) merged. The web-terminal `spec.md` had been sitting untracked in the
main clone since 2026-05-06. Relocated it onto a fresh worktree branch
`442-web-terminal-canvas` off up-to-date `origin/main` (`f8874cc`),
`HTTP_PORT=18900`, clean `make test` baseline (3014 passed).

## Gap review — spec vs. current codebase

Three parallel Explore agents verified the spec's codebase claims. Full
corrections are in the spec's "Revisions — 2026-07-23" block. Summary:

**Confirmed sound (build as specced):**
- WS route + cookie auth: `get_current_user(websocket, config)` works for WS
  exactly as for HTTP (`web/websocket.py:819` pattern). Would be the first
  *parameterized* WS route, but Starlette supports it.
- Standalone `/canvas/{conv}/{tab}` window already exists and works
  (`http_server.py:2074-2075`, `canvas-page.{html,js}`). Multi-attach viable.
- Config: keep `TerminalConfig` flat → `TERMINAL_*` env vars resolve via one
  `load_sub_config` call. Add a post-load type guard for `allowed_cwd_roots`
  (mirror `vault.user_writable_paths`, `config.py:569-578`).
- Vendoring xterm.js: `package.json` dep + `build-vendor.mjs` entry + importmap
  entry in `index.html` + CSS copy + `make vendor`.
- Canvas tab API (`canvas.py`): `new_tab`/`update_tab`/`close_tab`/... all
  `async (config, conv_id, ..., emit=None) -> CanvasOpResult`; emit kinds
  `new_tab|update|close_tab|set_active|clear`.

**Four issues → decisions:**
1. Canvas path stale → `conversations/{conv_id}/canvas.json`. Doc fix.
2. Command routing → **server-side dispatch** (Les's call). Drop the
   `POST /api/commands/{name}` route + client `SIDE_EFFECT_COMMANDS` table.
   Existing `_handle_send` → `dispatch_command` already ACKs without an LLM
   turn.
3. Close-tab confirmation → **client-side confirm dialog** (persistent infra
   forces archive writes + can't resurrect a PTY on reload). UI-only.
4. Widget lifecycle → **keep-alive / hidden-mount host** (Les's call: better
   UX + better long-term; fixes same-type aliasing structurally). Retain
   reconnect + ring-buffer replay regardless.

## Key design decisions locked

- Server PTY registry (`terminals.py`) uses `loop.add_reader(fd, ...)`; the
  data-handling method (`_handle_output`) is separate from the fd read so unit
  tests can drive it directly without a real PTY. One integration test uses a
  real `/bin/echo`.
- `terminals.py` imported by NO code under `tools/` or `skills/` — enforced by
  a test. That import-boundary test is the agent-cannot-touch-terminals guard.
- `/terminal` command handler is the "no turn / no archive" chokepoint;
  regression test asserts it does not call `enqueue_turn`.

## Open items to watch during execution

- JS has no unit-test harness; widget + canvas-host changes verified via
  `make check-js` + manual/Playwright. Plan is honest about this — no fake TDD
  steps for JS.
- macOS vs Linux PTY differences (`TIOCSWINSZ`, EIO-on-exit). Dev on macOS;
  deploy target Linux (`decafclaw@lmorchard`). Integration test must pass on
  both.

## Decision: posix_spawn, not pty.fork (Task 2 review, 2026-07-23)

`pty.fork()` in our multi-threaded server emits CPython's intrinsic
`DeprecationWarning: ...forkpty() may lead to deadlocks in the child`. That
warning is a runtime-safety caution (forkpty/os.forkpty are NOT deprecated
APIs) about forking a multi-threaded interpreter. Rather than suppress it
(against our "never suppress warnings" convention) we heed it: `spawn()` uses
`os.openpty()` + `os.posix_spawn(..., setsid=True)` — no interpreter fork, no
warning, no event-loop-blocking fork. Spiked on macOS: setsid + dup2'd slave
gives the child a working controlling TTY (`/dev/tty` accessible). cwd is set
via a `sh -c 'cd <cwd> && exec <shell>'` trampoline (posix_spawn has no
portable chdir); the exec preserves pid so `session.pid` stays valid for
waitpid/killpg. Linux (deploy target) supports setsid in posix_spawn via glibc.
