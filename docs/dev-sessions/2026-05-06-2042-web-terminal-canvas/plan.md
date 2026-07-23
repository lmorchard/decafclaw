# Web Terminal as Canvas Tab — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a human-only, WebSocket-backed PTY shell to the DecafClaw web UI, rendered as a canvas tab via xterm.js, spawned by a server-side `/terminal` slash command.

**Architecture:** A server-side in-memory `TerminalRegistry` (`terminals.py`) owns PTY sessions keyed by `(conv_id, tab_id)`, each streaming over a dedicated `/ws/terminal/{conv_id}/{tab_id}` WebSocket with a per-session ring buffer replayed on attach. `/terminal` is handled inline in the existing server-side command path (`_handle_send`) — it spawns the PTY and creates a canvas tab with no LLM turn and no archive write. The client is a `<dc-widget-terminal>` Lit widget; the canvas host is upgraded to keep widgets mounted across tab switches so the live socket survives.

**Tech Stack:** Python stdlib `pty`/`os`/`fcntl`/`termios`, Starlette WebSocket routes, Lit, xterm.js + `@xterm/addon-fit` + `@xterm/addon-serialize` + `@xterm/addon-web-links`.

## Global Constraints

- **Human-only, by design.** `terminals.py` MUST NOT be imported by any module under `src/decafclaw/tools/` or `src/decafclaw/skills/`. The agent gets zero tool definitions for terminals. An import-boundary test enforces this.
- **No LLM turn, no archive write.** The `/terminal` path MUST NOT call `manager.enqueue_turn` and MUST NOT call `archive.append_message`. A regression test enforces this.
- **Auth gate = web session cookie.** WS route validates via `get_current_user(websocket, config)`; reject `4001` if absent. No token-in-URL.
- **Config default path is `conversations/{conv_id}/canvas.json`** via `conversation_paths.sidecar_path` (NOT the pre-#576 flat `{conv_id}.canvas.json`).
- **Keep `TerminalConfig` flat** (all scalar/list fields) so `TERMINAL_*` env vars resolve through a single `load_sub_config` call.
- **Code style:** module-level stdlib imports; `dataclasses.replace`/`copy.copy` idioms (never hand-enumerate fields); `asyncio.Lock` not boolean flags; no bare `except: pass`; zero warning/traceback noise even on shutdown.
- **Commit after each task**, lint + test first (`make lint && make test`; `make check-js` for JS tasks).
- **Platform:** dev on macOS, deploy target Linux. The real-PTY integration test must pass on both.

---

## File Structure

**New (server):**
- `src/decafclaw/terminals.py` — `TerminalSession` dataclass, `TerminalRegistry` (spawn / attach / detach / write_input / resize / kill / count / shutdown_all), PTY reader via `loop.add_reader`, ring buffer, smallest-viewport-wins resize.

**New (client):**
- `src/decafclaw/web/static/widgets/terminal/widget.json` — descriptor (`modes: ["canvas"]`, `accepts_input: false`, `data_schema`).
- `src/decafclaw/web/static/widgets/terminal/widget.js` — `<dc-widget-terminal>` Lit component: xterm mount, WS client, connection state machine, reconnect+replay, theming, session-ended banner.

**New (tests):**
- `tests/test_terminals.py` — registry unit tests (injected `pty`/`os`) + one real-PTY integration test + import-boundary test.
- `tests/web/test_terminal_ws.py` — `/ws/terminal` auth / not-found / replay / input / resize via Starlette `TestClient`.
- `tests/web/test_terminal_command.py` — `/terminal` command handler: spawns + creates tab, no `enqueue_turn`, no `append_message`, disabled-config path, cwd-reject, session-cap.

**New (docs):**
- `docs/web-terminal.md` — feature reference.

**Modified:**
- `src/decafclaw/config_types.py` — `TerminalConfig` dataclass.
- `src/decafclaw/config.py` — `terminal` field on `Config`, `load_sub_config` wiring, post-load list guard.
- `src/decafclaw/http_server.py` — `WebSocketRoute("/ws/terminal/{conv_id}/{tab_id}", ws_terminal)`, `ws_terminal` shim, create `TerminalRegistry` on `app.state`, shutdown hook.
- `src/decafclaw/web/websocket.py` — `/terminal` command interception in `_handle_send`; pass registry into `state`.
- `src/decafclaw/canvas.py` — `close_tab` detects `widget_type == "terminal"` and kills the PTY through the registry.
- `src/decafclaw/web/conversation_folders.py` — archive/delete hook kills any sessions for that conv.
- `src/decafclaw/web/static/components/canvas-panel.js` + `components/widgets/widget-host.js` — keep-alive: mount every tab's widget keyed by tab id, toggle visibility instead of tear-down.
- `src/decafclaw/web/static/lib/canvas-state.js` — client-side confirm dialog before closing a `terminal` tab.
- `src/decafclaw/web/static/package.json`, `build-vendor.mjs`, `index.html`, `canvas-page.html` — vendor xterm.js + addons + CSS + importmap.
- `docs/web-ui.md`, `docs/index.md`, `CLAUDE.md` — feature section, doc index, key-files entry.

---

## Task 1: `TerminalConfig`

**Files:**
- Modify: `src/decafclaw/config_types.py` (add dataclass)
- Modify: `src/decafclaw/config.py` (Config field + `load_config` wiring + list guard)
- Test: `tests/test_config.py` (append cases)

**Interfaces:**
- Produces: `config.terminal: TerminalConfig` with fields `enabled: bool`, `buffer_bytes: int`, `default_cwd: str | None`, `allowed_cwd_roots: list[str]`, `shell_override: str | None`, `max_sessions_per_conv: int`.

- [ ] **Step 1: Write the failing test**

In `tests/test_config.py`:

```python
def test_terminal_config_defaults():
    from decafclaw.config import load_config
    cfg = load_config()
    assert cfg.terminal.enabled is True
    assert cfg.terminal.buffer_bytes == 10 * 1024 * 1024
    assert cfg.terminal.max_sessions_per_conv == 8
    assert cfg.terminal.allowed_cwd_roots == []


def test_terminal_config_env_override(monkeypatch):
    from decafclaw.config import load_config
    monkeypatch.setenv("TERMINAL_ENABLED", "false")
    monkeypatch.setenv("TERMINAL_BUFFER_BYTES", "2048")
    monkeypatch.setenv("TERMINAL_ALLOWED_CWD_ROOTS", "/tmp,/var")
    cfg = load_config()
    assert cfg.terminal.enabled is False
    assert cfg.terminal.buffer_bytes == 2048
    assert cfg.terminal.allowed_cwd_roots == ["/tmp", "/var"]


def test_terminal_config_bad_list_falls_back(monkeypatch):
    # A JSON scalar where a list is expected must not crash config load.
    from decafclaw.config import load_config
    monkeypatch.setenv("TERMINAL_ALLOWED_CWD_ROOTS", "null")
    cfg = load_config()
    assert cfg.terminal.allowed_cwd_roots == []
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/test_config.py -k terminal -v`
Expected: FAIL — `AttributeError: 'Config' object has no attribute 'terminal'`.

- [ ] **Step 3: Add the dataclass**

In `config_types.py`, near the other sub-config dataclasses (e.g. after `HttpConfig`):

```python
@dataclass
class TerminalConfig:
    enabled: bool = True
    buffer_bytes: int = 10 * 1024 * 1024      # ring buffer cap per session
    default_cwd: str | None = None            # falls back to workspace_path
    allowed_cwd_roots: list[str] = field(default_factory=list)  # empty → [workspace, $HOME]
    shell_override: str | None = None         # falls back to $SHELL or /bin/sh
    max_sessions_per_conv: int = 8
```

Confirm `from dataclasses import dataclass, field` is already imported at the top of the file (it is used by other configs).

- [ ] **Step 4: Wire into `Config` and `load_config`**

In `config.py`, add the field to the `Config` dataclass (near `http`):

```python
    terminal: "TerminalConfig" = field(default_factory=TerminalConfig)
```

Add `TerminalConfig` to the `from .config_types import (...)` block. In `load_config`, next to the `http = load_sub_config(...)` line:

```python
    terminal = load_sub_config(TerminalConfig, file_data.get("terminal", {}), "TERMINAL")
    # Guard: a JSON scalar/null where a list is expected passes through
    # load_sub_config untouched (see vault.user_writable_paths precedent).
    if not isinstance(terminal.allowed_cwd_roots, list):
        terminal = dataclasses.replace(terminal, allowed_cwd_roots=[])
```

Pass `terminal=terminal` into the `Config(...)` constructor call. Confirm `import dataclasses` is present (it is).

- [ ] **Step 5: Run to verify pass**

Run: `.venv/bin/pytest tests/test_config.py -k terminal -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Lint + commit**

```bash
make lint
git add src/decafclaw/config_types.py src/decafclaw/config.py tests/test_config.py
git commit -m "feat(terminal): TerminalConfig dataclass + env wiring (#442)"
```

---

## Task 2: `terminals.py` — session registry core

**Files:**
- Create: `src/decafclaw/terminals.py`
- Test: `tests/test_terminals.py`

**Interfaces:**
- Produces:
  - `@dataclass TerminalSession` with: `conv_id, tab_id, session_id, cwd, shell` (str); `pid, fd` (int); `buffer: bytearray`; `attached: set`; `viewports: dict`; `exit_status: int | None = None`.
  - `class TerminalRegistry` constructed as `TerminalRegistry(config, *, loop=None, pty_module=pty, os_module=os)`.
    - `async spawn(conv_id, tab_id, session_id, cwd, shell) -> TerminalSession`
    - `get(conv_id, tab_id) -> TerminalSession | None`
    - `count_for_conv(conv_id) -> int`
    - `async attach(session, send_bytes, send_json)` / `detach(session, send_bytes)` where `send_bytes`/`send_json` are awaitables bound to a WS
    - `async write_input(session, data: bytes)`
    - `async set_viewport(session, key, cols, rows)` / `drop_viewport(session, key)` — recomputes min and applies `TIOCSWINSZ`
    - `async kill(session, grace=1.0)`
    - `async shutdown_all()`
  - `_handle_output(session, chunk: bytes)` — appends to ring buffer (drop-from-front over cap), broadcasts to attached; unit tests call this directly.

- [ ] **Step 1: Write failing unit tests (no real PTY)**

In `tests/test_terminals.py`:

```python
import asyncio
import dataclasses

import pytest

from decafclaw.config import load_config
from decafclaw.terminals import TerminalRegistry, TerminalSession


def _session(**kw) -> TerminalSession:
    base = dict(
        conv_id="c1", tab_id="canvas_1", session_id="s1",
        cwd="/tmp", shell="/bin/sh", pid=123, fd=9,
        buffer=bytearray(), attached=set(), viewports={},
    )
    base.update(kw)
    return TerminalSession(**base)


def test_ring_buffer_caps_from_front():
    cfg = dataclasses.replace(load_config())
    cfg = dataclasses.replace(cfg, terminal=dataclasses.replace(cfg.terminal, buffer_bytes=8))
    reg = TerminalRegistry(cfg)
    s = _session()
    reg._handle_output(s, b"12345")
    reg._handle_output(s, b"6789")   # total 9 bytes → cap 8 → drop 1 from front
    assert bytes(s.buffer) == b"23456789"


@pytest.mark.asyncio
async def test_broadcast_fans_out_and_drops_failing_sink():
    reg = TerminalRegistry(load_config())
    s = _session()
    good, bad = [], []

    async def good_sink(chunk): good.append(chunk)
    async def bad_sink(chunk): raise RuntimeError("client gone")

    s.attached.add(good_sink)
    s.attached.add(bad_sink)
    reg._handle_output(s, b"hello")
    await asyncio.sleep(0)  # let broadcast task run
    assert good == [b"hello"]
    assert bad_sink not in s.attached   # failing sink removed


def test_viewport_min_computation():
    reg = TerminalRegistry(load_config())
    s = _session(viewports={"a": (120, 40), "b": (80, 24)})
    assert reg._min_viewport(s) == (80, 24)


def test_count_for_conv():
    reg = TerminalRegistry(load_config())
    reg._sessions[("c1", "canvas_1")] = _session()
    reg._sessions[("c1", "canvas_2")] = _session(tab_id="canvas_2")
    reg._sessions[("c2", "canvas_1")] = _session(conv_id="c2")
    assert reg.count_for_conv("c1") == 2
    assert reg.count_for_conv("c2") == 1
```

Note: `_handle_output` broadcasts by scheduling `asyncio.create_task` per sink (or gathering); design it so a sink raising removes it from `attached`. The `await asyncio.sleep(0)` yields to those tasks.

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/test_terminals.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'decafclaw.terminals'`.

- [ ] **Step 3: Implement `terminals.py`**

```python
"""In-memory PTY terminal sessions for the web UI (human-only, agent has no access).

NOT imported by anything under decafclaw/tools/ or decafclaw/skills/ — enforced
by tests/test_terminals.py::test_no_agent_side_imports.
"""

import asyncio
import fcntl
import logging
import os
import pty
import signal
import struct
import termios
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

_READ_CHUNK = 64 * 1024


@dataclass
class TerminalSession:
    conv_id: str
    tab_id: str
    session_id: str
    cwd: str
    shell: str
    pid: int
    fd: int
    buffer: bytearray = field(default_factory=bytearray)
    attached: set = field(default_factory=set)
    viewports: dict = field(default_factory=dict)
    exit_status: int | None = None


class TerminalRegistry:
    def __init__(self, config, *, loop=None, pty_module=pty, os_module=os):
        self._config = config
        self._loop = loop
        self._pty = pty_module
        self._os = os_module
        self._sessions: dict[tuple[str, str], TerminalSession] = {}
        self._lock = asyncio.Lock()
        # WS-json senders keyed by session, for control frames (size_changed,
        # session_ended). Parallel to session.attached (raw-byte senders).
        self._json_sinks: dict[int, dict] = {}

    # -- lookup --------------------------------------------------------------
    def get(self, conv_id, tab_id):
        return self._sessions.get((conv_id, tab_id))

    def count_for_conv(self, conv_id) -> int:
        return sum(1 for (c, _t) in self._sessions if c == conv_id)

    # -- spawn ---------------------------------------------------------------
    async def spawn(self, conv_id, tab_id, session_id, cwd, shell) -> TerminalSession:
        loop = self._loop or asyncio.get_running_loop()

        def _fork():
            pid, fd = self._pty.fork()
            if pid == 0:  # child
                try:
                    self._os.chdir(cwd)
                except OSError:
                    pass
                env = dict(self._os.environ)
                env["TERM"] = "xterm-256color"
                self._os.execvpe(shell, [shell], env)  # replaces child image
            return pid, fd

        pid, fd = await asyncio.to_thread(_fork)
        session = TerminalSession(
            conv_id=conv_id, tab_id=tab_id, session_id=session_id,
            cwd=cwd, shell=shell, pid=pid, fd=fd,
        )
        self._sessions[(conv_id, tab_id)] = session
        self._json_sinks[id(session)] = {}
        loop.add_reader(fd, self._on_readable, session)
        log.info("terminal spawned conv=%s tab=%s pid=%s cwd=%s shell=%s",
                 conv_id, tab_id, pid, cwd, shell)
        return session

    # -- output --------------------------------------------------------------
    def _on_readable(self, session):
        try:
            chunk = self._os.read(session.fd, _READ_CHUNK)
        except OSError:
            chunk = b""  # EIO on Linux when child exits → treat as EOF
        if not chunk:
            asyncio.get_running_loop().create_task(self._on_eof(session))
            return
        self._handle_output(session, chunk)

    def _handle_output(self, session, chunk: bytes):
        session.buffer.extend(chunk)
        cap = self._config.terminal.buffer_bytes
        if len(session.buffer) > cap:
            del session.buffer[: len(session.buffer) - cap]
        for sink in list(session.attached):
            asyncio.get_event_loop().create_task(self._send(session, sink, chunk))

    async def _send(self, session, sink, chunk):
        try:
            await sink(chunk)
        except Exception as exc:  # slow/broken client — drop it, never stall others
            log.debug("terminal sink drop conv=%s: %s", session.conv_id, exc)
            session.attached.discard(sink)

    async def _on_eof(self, session):
        loop = asyncio.get_running_loop()
        try:
            loop.remove_reader(session.fd)
        except (OSError, ValueError):
            pass
        try:
            _pid, status = await asyncio.to_thread(self._os.waitpid, session.pid, 0)
            session.exit_status = os.waitstatus_to_exitcode(status)
        except (ChildProcessError, OSError):
            session.exit_status = session.exit_status or -1
        for send_json in list(self._json_sinks.get(id(session), {}).values()):
            try:
                await send_json({"type": "session_ended", "exit_status": session.exit_status})
            except Exception as exc:
                log.debug("terminal ended-notify drop: %s", exc)
        try:
            self._os.close(session.fd)
        except OSError:
            pass
        self._sessions.pop((session.conv_id, session.tab_id), None)
        self._json_sinks.pop(id(session), None)
        log.info("terminal exited conv=%s tab=%s exit=%s",
                 session.conv_id, session.tab_id, session.exit_status)

    # -- attach / detach -----------------------------------------------------
    async def attach(self, session, send_bytes, send_json):
        session.attached.add(send_bytes)
        self._json_sinks.setdefault(id(session), {})[id(send_bytes)] = send_json

    async def detach(self, session, send_bytes):
        session.attached.discard(send_bytes)
        self._json_sinks.get(id(session), {}).pop(id(send_bytes), None)

    # -- input / resize ------------------------------------------------------
    async def write_input(self, session, data: bytes):
        try:
            self._os.write(session.fd, data)
        except OSError as exc:
            log.debug("terminal write drop conv=%s: %s", session.conv_id, exc)

    def _min_viewport(self, session):
        if not session.viewports:
            return None
        cols = min(c for c, _r in session.viewports.values())
        rows = min(r for _c, r in session.viewports.values())
        return cols, rows

    async def set_viewport(self, session, key, cols, rows):
        session.viewports[key] = (cols, rows)
        self._apply_size(session)

    async def drop_viewport(self, session, key):
        session.viewports.pop(key, None)
        self._apply_size(session)

    def _apply_size(self, session):
        size = self._min_viewport(session)
        if not size:
            return
        cols, rows = size
        try:
            fcntl.ioctl(session.fd, termios.TIOCSWINSZ,
                        struct.pack("HHHH", rows, cols, 0, 0))
        except OSError as exc:
            log.debug("terminal resize drop conv=%s: %s", session.conv_id, exc)

    # -- kill / shutdown -----------------------------------------------------
    async def kill(self, session, grace=1.0):
        try:
            pgid = self._os.getpgid(session.pid)
            self._os.killpg(pgid, signal.SIGHUP)
        except (OSError, ProcessLookupError):
            return
        await asyncio.sleep(grace)
        try:
            self._os.killpg(pgid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass

    async def shutdown_all(self):
        for session in list(self._sessions.values()):
            await self.kill(session, grace=0.2)
        self._sessions.clear()
        self._json_sinks.clear()
```

- [ ] **Step 4: Run unit tests to verify pass**

Run: `.venv/bin/pytest tests/test_terminals.py -v`
Expected: PASS (the 4 tests from Step 1).

- [ ] **Step 5: Add real-PTY integration test + import-boundary test**

Append to `tests/test_terminals.py`:

```python
@pytest.mark.asyncio
async def test_real_pty_echo_and_cleanup():
    reg = TerminalRegistry(load_config())
    out = bytearray()
    async def sink(chunk): out.extend(chunk)
    # /bin/echo (not $SHELL) — fast, no rc-file noise
    s = await reg.spawn("c1", "canvas_1", "s1", cwd="/tmp", shell="/bin/echo")
    await reg.attach(s, sink, lambda m: asyncio.sleep(0))
    # echo with no args prints a newline then exits → reader hits EOF
    for _ in range(200):
        if reg.get("c1", "canvas_1") is None:
            break
        await asyncio.sleep(0.01)
    assert reg.get("c1", "canvas_1") is None      # cleaned up on EOF
    assert s.exit_status == 0


def test_no_agent_side_imports():
    """terminals.py must not be reachable from tools/ or skills/ — the
    load-bearing 'agent cannot touch terminals' guarantee."""
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent / "src" / "decafclaw"
    offenders = []
    for sub in ("tools", "skills"):
        for py in (root / sub).rglob("*.py"):
            text = py.read_text(encoding="utf-8")
            if "decafclaw.terminals" in text or "from ..terminals" in text or "from .terminals" in text:
                offenders.append(str(py))
    assert not offenders, f"terminals.py imported by agent-side code: {offenders}"
```

- [ ] **Step 6: Run + check durations**

Run: `.venv/bin/pytest tests/test_terminals.py -v --durations=10`
Expected: PASS. The real-PTY test should be well under 1s; if it lands in the top durations, the poll loop is the cause — acceptable here since it exits on the `get() is None` signal, not a fixed sleep.

- [ ] **Step 7: Lint + commit**

```bash
make lint
git add src/decafclaw/terminals.py tests/test_terminals.py
git commit -m "feat(terminal): PTY session registry with ring buffer + resize (#442)"
```

---

## Task 3: `/ws/terminal/{conv_id}/{tab_id}` route

**Files:**
- Modify: `src/decafclaw/http_server.py` (route + shim + registry on app.state)
- Create: `tests/web/test_terminal_ws.py`

**Interfaces:**
- Consumes: `TerminalRegistry` (Task 2), `get_current_user` (`web/auth.py`).
- Produces: `async def websocket_terminal(websocket, config, registry)` handler; `app.state.terminal_registry`.

- [ ] **Step 1: Write failing WS tests**

In `tests/web/test_terminal_ws.py` (mirror the auth + TestClient pattern from `tests/web/test_websocket.py`):

```python
import pytest
from starlette.testclient import TestClient


def test_terminal_ws_rejects_unauthenticated(make_app_no_auth_cookie):
    app = make_app_no_auth_cookie()
    client = TestClient(app)
    with pytest.raises(Exception):
        with client.websocket_connect("/ws/terminal/c1/canvas_1"):
            pass  # server closes 4001 before accept


def test_terminal_ws_session_not_found_sends_ended(make_app_authed):
    app = make_app_authed()   # registry empty
    client = TestClient(app)
    with client.websocket_connect("/ws/terminal/c1/canvas_1") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "session_ended"


def test_terminal_ws_replays_buffer_then_done(make_app_authed_with_session):
    # fixture pre-seeds registry with a session whose buffer = b"hello\n"
    app = make_app_authed_with_session(buffer=b"hello\n")
    client = TestClient(app)
    with client.websocket_connect("/ws/terminal/c1/canvas_1") as ws:
        assert ws.receive_bytes() == b"hello\n"
        assert ws.receive_json()["type"] == "buffer_replay_done"
```

(Define the fixtures in the test file or `tests/web/conftest.py`, reusing the existing app-builder + auth-cookie helpers already used by `test_websocket.py`; seed `app.state.terminal_registry._sessions` directly for the found-session case and stub `pty.fork` so no real PTY spawns.)

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/web/test_terminal_ws.py -v`
Expected: FAIL — route `/ws/terminal/...` returns 404 / connection refused.

- [ ] **Step 3: Implement the handler + shim**

Add to `web/websocket.py` (near `websocket_chat`):

```python
async def websocket_terminal(websocket, config, registry):
    from .auth import get_current_user
    username = get_current_user(websocket, config)
    if not username:
        await websocket.close(code=4001, reason="Not authenticated")
        return
    if not config.terminal.enabled:
        await websocket.close(code=4003, reason="Terminals disabled")
        return
    conv_id = websocket.path_params["conv_id"]
    tab_id = websocket.path_params["tab_id"]
    await websocket.accept()
    session = registry.get(conv_id, tab_id)
    if session is None:
        await websocket.send_json({"type": "session_ended", "exit_status": None})
        await websocket.close()
        return

    async def send_bytes(chunk): await websocket.send_bytes(chunk)
    async def send_json(obj): await websocket.send_json(obj)

    await registry.attach(session, send_bytes, send_json)
    # Replay ring buffer, then signal done.
    if session.buffer:
        await websocket.send_bytes(bytes(session.buffer))
    await websocket.send_json({"type": "buffer_replay_done"})
    try:
        while True:
            msg = await websocket.receive()
            if msg["type"] == "websocket.disconnect":
                break
            if "text" in msg and msg["text"] is not None:
                import json
                ctrl = json.loads(msg["text"])
                if ctrl.get("type") == "input":
                    await registry.write_input(session, ctrl["data"].encode("utf-8"))
                elif ctrl.get("type") == "resize":
                    await registry.set_viewport(session, id(websocket), int(ctrl["cols"]), int(ctrl["rows"]))
                    await websocket.send_json({"type": "size_changed",
                                               "cols": registry._min_viewport(session)[0],
                                               "rows": registry._min_viewport(session)[1]})
                # ping ignored (liveness only)
    finally:
        await registry.detach(session, send_bytes)
        await registry.drop_viewport(session, id(websocket))
```

Add the shim + route in `http_server.py` (next to `ws_chat` and the `WebSocketRoute("/ws/chat", ...)` line):

```python
async def ws_terminal(websocket):
    from .web.websocket import websocket_terminal
    state = websocket.app.state
    await websocket_terminal(websocket, state.config, state.terminal_registry)
```

```python
    WebSocketRoute("/ws/terminal/{conv_id}/{tab_id}", ws_terminal),
```

In `create_app`, after building other `app.state` deps:

```python
    from .terminals import TerminalRegistry
    app.state.terminal_registry = TerminalRegistry(config)
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/pytest tests/web/test_terminal_ws.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Lint + commit**

```bash
make lint
git add src/decafclaw/http_server.py src/decafclaw/web/websocket.py tests/web/test_terminal_ws.py
git commit -m "feat(terminal): /ws/terminal route with auth + replay + input/resize (#442)"
```

---

## Task 4: Registry lifecycle — shutdown + conversation-delete hooks

**Files:**
- Modify: `src/decafclaw/http_server.py` (shutdown handler calls `shutdown_all`)
- Modify: `src/decafclaw/web/conversation_folders.py` (kill sessions on archive/delete)
- Test: `tests/test_terminals.py` (append)

**Interfaces:**
- Consumes: `TerminalRegistry.shutdown_all`, `kill`, `count_for_conv`, `_sessions`.

- [ ] **Step 1: Write failing tests**

Append to `tests/test_terminals.py`:

```python
@pytest.mark.asyncio
async def test_shutdown_all_kills_and_clears(monkeypatch):
    reg = TerminalRegistry(load_config())
    killed = []
    async def fake_kill(session, grace=1.0): killed.append(session.tab_id)
    monkeypatch.setattr(reg, "kill", fake_kill)
    reg._sessions[("c1", "canvas_1")] = _session()
    reg._sessions[("c1", "canvas_2")] = _session(tab_id="canvas_2")
    await reg.shutdown_all()
    assert sorted(killed) == ["canvas_1", "canvas_2"]
    assert reg._sessions == {}
```

Plus a test in `tests/web/` (or `tests/test_terminals.py`) that the conv-delete helper calls `kill` for every session of that conv and skips others. Name it `test_kill_sessions_for_conv`; assert it kills `c1`'s sessions and leaves `c2`'s.

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/test_terminals.py -k "shutdown_all or kill_sessions" -v`
Expected: FAIL — `shutdown_all` test passes only after Task 2 (it does); the conv-delete helper does not exist yet.

- [ ] **Step 3: Implement helper + hooks**

Add to `terminals.py`:

```python
    async def kill_sessions_for_conv(self, conv_id):
        for key, session in list(self._sessions.items()):
            if key[0] == conv_id:
                await self.kill(session, grace=0.2)
                self._sessions.pop(key, None)
                self._json_sinks.pop(id(session), None)
```

In `http_server.py`, find the app shutdown handler (Starlette `on_shutdown` / lifespan). Add:

```python
    reg = getattr(app.state, "terminal_registry", None)
    if reg is not None:
        await reg.shutdown_all()
```

In `conversation_folders.py`, in the archive/delete code path, after removing the conversation, reach the registry via the passed-in app/config context and call `await registry.kill_sessions_for_conv(conv_id)`. If that module has no registry reference, thread it from the caller (the delete route handler in `http_server.py` has `request.app.state.terminal_registry`) rather than importing a global.

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/pytest tests/test_terminals.py -k "shutdown_all or kill_sessions" -v`
Expected: PASS.

- [ ] **Step 5: Lint + commit**

```bash
make lint
git add src/decafclaw/terminals.py src/decafclaw/http_server.py src/decafclaw/web/conversation_folders.py tests/test_terminals.py
git commit -m "feat(terminal): kill sessions on server shutdown + conversation delete (#442)"
```

---

## Task 5: Vendor xterm.js

**Files:**
- Modify: `src/decafclaw/web/static/package.json`
- Modify: `src/decafclaw/web/static/build-vendor.mjs`
- Modify: `src/decafclaw/web/static/index.html` (importmap + CSS link)
- Modify: `src/decafclaw/web/static/canvas-page.html` (importmap + CSS link — standalone window)

**Verification:** JS/vendor task — verified by `make vendor` succeeding, the bundle files existing, and `make check-js`. No unit test (no JS test harness in-repo).

- [ ] **Step 1: Add dependencies**

In `package.json` `dependencies`, add (let `make vendor` resolve exact versions into `package-lock.json`):

```json
    "@xterm/xterm": "^5.5.0",
    "@xterm/addon-fit": "^0.10.0",
    "@xterm/addon-serialize": "^0.13.0",
    "@xterm/addon-web-links": "^0.11.0"
```

- [ ] **Step 2: Add bundle entries**

In `build-vendor.mjs`, add bundle targets. The addons import from `@xterm/xterm`, so mark it external on the addon builds (mirror the existing `external: [...]` usage, e.g. the `lit/directives` entry):

```js
  { entry: '@xterm/xterm', out: 'xterm' },
  { entry: '@xterm/addon-fit', out: 'xterm-addon-fit', external: ['@xterm/xterm'] },
  { entry: '@xterm/addon-serialize', out: 'xterm-addon-serialize', external: ['@xterm/xterm'] },
  { entry: '@xterm/addon-web-links', out: 'xterm-addon-web-links', external: ['@xterm/xterm'] },
```

(Match the exact object shape the script already uses for its targets.) Also copy the xterm CSS the way `pico.min.css` / `leaflet.css` are copied: add `@xterm/xterm/css/xterm.css` → `vendor/bundle/xterm.css` to the CSS-copy list.

- [ ] **Step 3: Add importmap + CSS entries**

In both `index.html` and `canvas-page.html` importmaps:

```json
      "@xterm/xterm": "/static/vendor/bundle/xterm.js",
      "@xterm/addon-fit": "/static/vendor/bundle/xterm-addon-fit.js",
      "@xterm/addon-serialize": "/static/vendor/bundle/xterm-addon-serialize.js",
      "@xterm/addon-web-links": "/static/vendor/bundle/xterm-addon-web-links.js"
```

And a stylesheet link near the other vendor CSS `<link>`s:

```html
    <link rel="stylesheet" href="/static/vendor/bundle/xterm.css">
```

- [ ] **Step 4: Build + verify**

```bash
make vendor
ls src/decafclaw/web/static/vendor/bundle/xterm*.js
make check-js
```
Expected: `xterm.js`, `xterm-addon-fit.js`, `xterm-addon-serialize.js`, `xterm-addon-web-links.js`, `xterm.css` present; `check-js` clean.

- [ ] **Step 5: Commit**

```bash
git add src/decafclaw/web/static/package.json src/decafclaw/web/static/package-lock.json \
        src/decafclaw/web/static/build-vendor.mjs src/decafclaw/web/static/index.html \
        src/decafclaw/web/static/canvas-page.html src/decafclaw/web/static/vendor/bundle/xterm*
git commit -m "build(terminal): vendor xterm.js + fit/serialize/web-links addons (#442)"
```

---

## Task 6: Terminal widget (`<dc-widget-terminal>`)

**Files:**
- Create: `src/decafclaw/web/static/widgets/terminal/widget.json`
- Create: `src/decafclaw/web/static/widgets/terminal/widget.js`

**Interfaces:**
- Consumes: xterm importmap names (Task 5); WS route `/ws/terminal/{conv}/{tab}` (Task 3); widget-host contract — component defined as `customElements.define('dc-widget-terminal', ...)`, receives `.data` (`{session_id, cwd, shell}`), `.mode`, and (added in Task 7) `.convId` + `.tabId`.
- Produces: a Lit element that opens the WS, streams xterm I/O, reconnects with backoff, replays, themes, and shows a session-ended banner.

**Verification:** `make check-js` + manual (Playwright) — no JS unit harness. The server-side pieces it depends on are already unit-tested.

- [ ] **Step 1: Write `widget.json`**

```json
{
  "name": "terminal",
  "description": "Live PTY shell session over WebSocket. Created by the /terminal command — the agent cannot spawn, attach to, or read terminals.",
  "modes": ["canvas"],
  "accepts_input": false,
  "data_schema": {
    "type": "object",
    "required": ["session_id"],
    "properties": {
      "session_id": {"type": "string", "minLength": 1},
      "cwd": {"type": "string"},
      "shell": {"type": "string"}
    },
    "additionalProperties": false
  }
}
```

- [ ] **Step 2: Write `widget.js`**

Full component. The host passes `convId`/`tabId` (wired in Task 7); until then it can read them from `this.data` fallback. Key logic — state machine, reconnect with capped backoff (1/2/4/…/30s, max 5), replay gating, theming from Pico CSS vars, ended banner:

```js
import { LitElement, html, css } from 'lit';
import { Terminal } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import { WebLinksAddon } from '@xterm/addon-web-links';

const BACKOFF = [1000, 2000, 4000, 8000, 16000, 30000];

class DcWidgetTerminal extends LitElement {
  static properties = {
    data: { attribute: false },
    convId: { attribute: false },
    tabId: { attribute: false },
    _state: { state: true },
    _ended: { state: true },
  };
  createRenderRoot() { return this; }  // light DOM, matches other widgets

  constructor() {
    super();
    this._state = 'mounting';
    this._ended = null;
    this._attempts = 0;
    this._ws = null;
    this._term = null;
    this._fit = null;
    this._ro = null;
    this._replaying = false;
  }

  connectedCallback() {
    super.connectedCallback();
    this._mountTerminal();
    this._connect();
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    this._teardown();
  }

  _mountTerminal() {
    const host = document.createElement('div');
    host.className = 'dc-terminal-surface';
    host.style.width = '100%';
    host.style.height = '100%';
    this.appendChild(host);
    this._surface = host;
    this._term = new Terminal({ convertEol: false, fontSize: 13, theme: this._theme() });
    this._fit = new FitAddon();
    this._term.loadAddon(this._fit);
    this._term.loadAddon(new WebLinksAddon());
    this._term.open(host);
    this._fit.fit();
    this._term.onData((d) => this._send({ type: 'input', data: d }));
    this._ro = new ResizeObserver(() => this._onResize());
    this._ro.observe(host);
    // live theme updates
    this._themeHandler = () => { if (this._term) this._term.options.theme = this._theme(); };
    window.addEventListener('dc-theme-change', this._themeHandler);
  }

  _theme() {
    const cs = getComputedStyle(document.documentElement);
    const v = (n, d) => (cs.getPropertyValue(n).trim() || d);
    return {
      background: v('--pico-card-background-color', '#11191f'),
      foreground: v('--pico-color', '#e8e8e8'),
      cursor: v('--pico-color', '#e8e8e8'),
    };
  }

  _wsUrl() {
    const conv = this.convId || (this.data && this.data.conv_id);
    const tab = this.tabId || (this.data && this.data.tab_id);
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    return `${proto}://${location.host}/ws/terminal/${conv}/${tab}`;
  }

  _connect() {
    this._state = 'connecting';
    const ws = new WebSocket(this._wsUrl());
    ws.binaryType = 'arraybuffer';
    this._ws = ws;
    ws.onopen = () => { this._attempts = 0; this._state = 'replaying'; this._replaying = true; };
    ws.onmessage = (e) => this._onMessage(e);
    ws.onclose = () => this._onClose();
    ws.onerror = () => { /* onclose will follow */ };
  }

  _onMessage(e) {
    if (typeof e.data !== 'string') {           // binary → terminal output
      this._term.write(new Uint8Array(e.data));
      return;
    }
    const msg = JSON.parse(e.data);
    if (msg.type === 'buffer_replay_done') {
      this._replaying = false;
      this._state = 'attached';
      this._onResize();                          // send initial size
    } else if (msg.type === 'session_ended') {
      this._ended = msg.exit_status;
      this._state = 'ended';
      this._teardownSocket();
    } else if (msg.type === 'size_changed') {
      // server letterbox hint — xterm already fit locally; no-op for now
    }
  }

  _onClose() {
    if (this._state === 'ended') return;         // clean end, no reconnect
    if (this._attempts >= BACKOFF.length) {
      this._state = 'error';
      return;
    }
    const delay = BACKOFF[this._attempts++];
    this._state = 'disconnected';
    this._reconnectTimer = setTimeout(() => this._connect(), delay);
  }

  _onResize() {
    if (!this._fit || !this._term) return;
    this._fit.fit();
    this._send({ type: 'resize', cols: this._term.cols, rows: this._term.rows });
  }

  _send(obj) {
    if (this._ws && this._ws.readyState === WebSocket.OPEN && !this._replaying) {
      this._ws.send(JSON.stringify(obj));
    }
  }

  _teardownSocket() {
    if (this._ws) { this._ws.onclose = null; this._ws.close(); this._ws = null; }
    clearTimeout(this._reconnectTimer);
  }

  _teardown() {
    this._teardownSocket();
    if (this._ro) { this._ro.disconnect(); this._ro = null; }
    window.removeEventListener('dc-theme-change', this._themeHandler);
    if (this._term) { this._term.dispose(); this._term = null; }
  }

  render() {
    if (this._state === 'ended') {
      return html`<div class="dc-terminal-banner">[session ended · exit ${this._ended ?? '?'}]</div>`;
    }
    if (this._state === 'error') {
      return html`<div class="dc-terminal-banner">[disconnected — reload to retry]</div>`;
    }
    return html``;  // xterm renders into the appended surface div
  }
}
customElements.define('dc-widget-terminal', DcWidgetTerminal);
```

Note the theme event name: check `theme-toggle.js` for the exact custom-event name it dispatches and use that in place of `dc-theme-change` if it differs. If it dispatches no event, drop the live-theme handler (theme applies on next mount) rather than inventing one.

- [ ] **Step 3: Verify catalog + render**

```bash
make check-js
```
Then manual: with `make dev` running (ask Les first — one bot rule), open the web UI, and confirm `/api/widgets` lists `terminal`. Full end-to-end render is exercised in Task 7.

- [ ] **Step 4: Commit**

```bash
git add src/decafclaw/web/static/widgets/terminal/
git commit -m "feat(terminal): <dc-widget-terminal> xterm client widget (#442)"
```

---

## Task 7: `/terminal` server-side command handler

**Files:**
- Modify: `src/decafclaw/web/websocket.py` (`_handle_send` interception; pass registry into `state`)
- Modify: `src/decafclaw/http_server.py` (ensure `state["terminal_registry"]` is in the dict handed to `_handle_send`)
- Create: `tests/web/test_terminal_command.py`

**Interfaces:**
- Consumes: `TerminalRegistry.spawn`/`count_for_conv` (Task 2), `canvas.new_tab` (existing), `config.terminal`.
- Produces: end-to-end `/terminal [cwd]` → PTY + canvas tab + `COMMAND_ACK`; no turn, no archive write.

- [ ] **Step 1: Write failing tests**

In `tests/web/test_terminal_command.py`:

```python
import pytest


@pytest.mark.asyncio
async def test_terminal_command_spawns_and_creates_tab(terminal_send_env):
    env = terminal_send_env()                     # fixture: stubs pty.fork, real registry, spy manager
    await env.handle_send({"conv_id": "c1", "text": "/terminal"})
    assert env.registry.count_for_conv("c1") == 1
    assert env.new_tab_calls and env.new_tab_calls[0]["widget_type"] == "terminal"
    assert any(m["type"] == "COMMAND_ACK" for m in env.sent)


@pytest.mark.asyncio
async def test_terminal_command_no_turn_no_archive(terminal_send_env):
    env = terminal_send_env()
    await env.handle_send({"conv_id": "c1", "text": "/terminal"})
    env.manager.enqueue_turn.assert_not_called()  # load-bearing invariant
    assert env.archive_appends == []              # no archive write


@pytest.mark.asyncio
async def test_terminal_command_disabled_returns_message(terminal_send_env):
    env = terminal_send_env(enabled=False)
    await env.handle_send({"conv_id": "c1", "text": "/terminal"})
    assert env.registry.count_for_conv("c1") == 0
    assert any("disabled" in (m.get("text") or "").lower() for m in env.sent)


@pytest.mark.asyncio
async def test_terminal_command_rejects_cwd_outside_roots(terminal_send_env):
    env = terminal_send_env(allowed_cwd_roots=["/tmp"])
    await env.handle_send({"conv_id": "c1", "text": "/terminal /etc"})
    assert env.registry.count_for_conv("c1") == 0
    assert any("not allowed" in (m.get("text") or "").lower() for m in env.sent)


@pytest.mark.asyncio
async def test_terminal_command_session_cap(terminal_send_env):
    env = terminal_send_env(max_sessions_per_conv=1)
    await env.handle_send({"conv_id": "c1", "text": "/terminal"})
    await env.handle_send({"conv_id": "c1", "text": "/terminal"})
    assert env.registry.count_for_conv("c1") == 1
    assert any("max session" in (m.get("text") or "").lower() for m in env.sent)
```

Build the `terminal_send_env` fixture to construct the `state` dict `_handle_send` expects (config with terminal overrides, `event_bus`, spy `manager` with `enqueue_turn` as a mock, a real `TerminalRegistry` with `pty.fork` monkeypatched to return `(4242, <os.pipe read fd>)`, a spy on `canvas.new_tab`, and a spy on `archive.append_message`). Seed the conversation index so the `conv.user_id == username` guard passes.

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/web/test_terminal_command.py -v`
Expected: FAIL — `/terminal` falls through to `dispatch_command` and is treated as unknown; no tab, no spawn.

- [ ] **Step 3: Implement the interception**

In `web/websocket.py`, immediately after the workflow-command block (after its `return`, ~line 331) and before the `# -- Command dispatch` block:

```python
    # -- /terminal side-effect command (#442): spawn a PTY + canvas tab with
    # NO agent turn and NO archive write. Human-only; the agent has no path here.
    if text.startswith("/terminal") and (text == "/terminal" or text[9:10].isspace()):
        await _handle_terminal_command(ws_send, conv_id, text, username, state)
        return
```

Add the handler in the same module:

```python
async def _handle_terminal_command(ws_send, conv_id, text, username, state):
    import uuid
    from pathlib import Path
    from .. import canvas
    config = state["config"]
    tcfg = config.terminal

    async def _msg(body):
        await ws_send({"type": WSMessageType.MESSAGE_COMPLETE, "conv_id": conv_id,
                       "role": "assistant", "text": body, "final": True})

    if not tcfg.enabled:
        await _msg("Terminals are disabled on this server.")
        return

    arg = text[len("/terminal"):].strip()
    default_cwd = tcfg.default_cwd or str(config.workspace_path)
    cwd = str(Path(arg).expanduser()) if arg else default_cwd

    roots = tcfg.allowed_cwd_roots or [str(config.workspace_path), str(Path.home())]
    resolved = Path(cwd).resolve()
    if not any(_is_within(resolved, Path(r).resolve()) for r in roots):
        await _msg(f"cwd not allowed: {cwd}")
        return

    registry = state["terminal_registry"]
    if registry.count_for_conv(conv_id) >= tcfg.max_sessions_per_conv:
        await _msg("Max terminal sessions reached for this conversation.")
        return

    shell = tcfg.shell_override or os.environ.get("SHELL") or "/bin/sh"
    session_id = uuid.uuid4().hex
    # Create the tab first so we know tab_id, then spawn keyed to it.
    result = await canvas.new_tab(
        config, conv_id, widget_type="terminal",
        data={"session_id": session_id, "cwd": str(resolved), "shell": shell},
        emit=_canvas_emit(state, conv_id),
    )
    if not result.ok:
        await _msg(f"Could not open terminal tab: {result.error}")
        return
    await registry.spawn(conv_id, result.tab_id, session_id, str(resolved), shell)
    await ws_send({"type": WSMessageType.COMMAND_ACK, "conv_id": conv_id,
                   "command": "/terminal", "skill": "terminal"})
```

Add `_is_within(child, parent)` (`return parent == child or parent in child.parents`) and `_canvas_emit(state, conv_id)` — reuse the exact emit wiring that `canvas_tools.py` / the `/api/canvas` handlers use to relay `canvas_update` to the web client (publish on `state["event_bus"]`; the per-conv forwarder already bridges it). Read `canvas_tools.py` for the concrete emit and copy it — do not invent a new event shape.

Confirm `import os` is at module top of `websocket.py` (add if missing). Ensure `websocket_chat` puts the registry into `state`: where it builds the `state` dict, add `"terminal_registry": app_ctx-or-app.state.terminal_registry`. In `http_server.py`'s `ws_chat` shim, pass it through (e.g. include `terminal_registry=state.terminal_registry` in the `websocket_chat(...)` call and have `websocket_chat` store it in its `state` dict).

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/pytest tests/web/test_terminal_command.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Manual end-to-end (ask Les — one-bot rule)**

With the web server on `HTTP_PORT=18900`: type `/terminal` in a conversation → a terminal tab opens, shell prompt appears, keystrokes echo, resize reflows. Type `/terminal /tmp` → opens in `/tmp`. Confirm `exit` shows the ended banner.

- [ ] **Step 6: Lint + commit**

```bash
make lint && make test
git add src/decafclaw/web/websocket.py src/decafclaw/http_server.py tests/web/test_terminal_command.py
git commit -m "feat(terminal): /terminal server-side command spawns PTY + canvas tab (#442)"
```

---

## Task 8: Canvas keep-alive host (survive tab switches; fix aliasing)

**Files:**
- Modify: `src/decafclaw/web/static/components/canvas-panel.js`
- Modify: `src/decafclaw/web/static/components/widgets/widget-host.js`

**Interfaces:**
- Produces: canvas renders one `<dc-widget-host>` per tab, keyed by `tab.id`, all mounted; inactive ones `hidden`. Host receives `.convId` and `.tabId` so widgets (terminal) can build their WS URL. Fixes: (a) same-type aliasing (two terminals no longer share one instance), (b) WS teardown on switch.

**Verification:** `make check-js` + manual (Playwright): two terminal tabs retain independent sessions; switching away and back keeps the socket live (no reconnect banner flash).

- [ ] **Step 1: Render one keyed host per tab, hide inactive**

In `canvas-panel.js` `render()`, replace the single-active-host block with a mapped list over all tabs, using Lit `repeat` keyed by `tab.id`, marking non-active hosts `hidden`:

```js
import { repeat } from 'lit/directives/repeat.js';
// ...
const tabs = this._snapshot.tabs || [];
const activeId = this._snapshot.activeTabId;
return html`
  ${/* existing tab strip */ this._renderTabStrip()}
  <div class="dc-canvas-body">
    ${repeat(tabs, (t) => t.id, (t) => html`
      <dc-widget-host
        class="dc-canvas-host"
        ?hidden=${t.id !== activeId}
        .widgetType=${t.widget_type}
        .data=${t.data}
        .convId=${this._snapshot.convId}
        .tabId=${t.id}
        .mode=${'canvas'}></dc-widget-host>
    `)}
  </div>`;
```

Add CSS so hidden hosts don't collapse layout / keep size for fit-addon: `.dc-canvas-host[hidden] { display: none; }` is fine — xterm re-fits on `ResizeObserver` when unhidden; verify reflow in manual test (if fit misbehaves while `display:none`, switch to `visibility:hidden; position:absolute` retaining box).

- [ ] **Step 2: Pass convId/tabId through the host to the widget**

In `widget-host.js`, add `convId` and `tabId` to `static properties`, and in the mount path (`_loadAndMount` and the same-type update branch) set them on the child element alongside `.data`:

```js
    child.convId = this.convId;
    child.tabId = this.tabId;
    child.data = this.data;
```

Because each tab now has its own keyed `<dc-widget-host>`, the previous same-type aliasing branch no longer merges two tabs — each host owns one child. Keep the existing per-host teardown in `disconnectedCallback` so closing a tab disposes its widget.

- [ ] **Step 3: Verify**

```bash
make check-js
```
Manual (ask Les): open two `/terminal` tabs; run `sleep 99` in tab 1, switch to tab 2, back to tab 1 — tab 1's `sleep` is still running, no ended/disconnect banner, scrollback intact. Open a non-terminal widget (data_table) in a third tab and confirm it still renders correctly when mounted-but-hidden.

- [ ] **Step 4: Commit**

```bash
git add src/decafclaw/web/static/components/canvas-panel.js src/decafclaw/web/static/components/widgets/widget-host.js
git commit -m "feat(canvas): keep widgets mounted across tab switches (per-tab host) (#442)"
```

---

## Task 9: Close-tab confirmation + PTY kill

**Files:**
- Modify: `src/decafclaw/web/static/lib/canvas-state.js` (client confirm for terminal tabs)
- Modify: `src/decafclaw/canvas.py` (`close_tab` kills the PTY for terminal tabs)
- Modify: `src/decafclaw/http_server.py` (close_tab handler passes registry into `canvas.close_tab`)
- Test: `tests/test_canvas.py` (append) or `tests/web/test_terminal_command.py`

**Interfaces:**
- Consumes: `TerminalRegistry.get`/`kill` (Task 2), `close_tab` (existing signature + new optional `registry` param).

- [ ] **Step 1: Write failing test (server kills PTY on close)**

Append a test asserting that `canvas.close_tab(config, conv_id, tab_id, emit=..., registry=reg)` calls `reg.kill` for a terminal tab and does not for a non-terminal tab:

```python
@pytest.mark.asyncio
async def test_close_tab_kills_terminal_pty(tmp_config, monkeypatch):
    from decafclaw import canvas
    # seed a terminal tab + a data_table tab in canvas.json via new_tab
    # seed the registry with a matching session; spy on kill
    killed = []
    class Reg:
        def get(self, c, t): return object() if t == term_tab else None
        async def kill(self, s, grace=1.0): killed.append(True)
    await canvas.close_tab(tmp_config, "c1", term_tab, registry=Reg())
    assert killed == [True]
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/test_canvas.py -k close_tab_kills -v`
Expected: FAIL — `close_tab` has no `registry` param / does not kill.

- [ ] **Step 3: Implement server-side kill**

In `canvas.py` `close_tab`, add an optional `registry=None` param. Before removing the tab, look up the tab; if `widget_type == "terminal"` and `registry` is not None, resolve the session and kill it:

```python
async def close_tab(config, conv_id, tab_id, emit=None, registry=None):
    tab = get_tab(config, conv_id, tab_id)
    if tab and tab.get("widget_type") == "terminal" and registry is not None:
        session = registry.get(conv_id, tab_id)
        if session is not None:
            await registry.kill(session)
    # ... existing removal + emit logic unchanged ...
```

In `http_server.py`'s close-tab route handler, pass `registry=request.app.state.terminal_registry` into `canvas.close_tab(...)`.

- [ ] **Step 4: Client confirm dialog for terminal tabs**

In `canvas-state.js` `closeTabFromUi(tabId)`, before POSTing the close, if the tab's `widget_type === 'terminal'`, gate on `window.confirm`:

```js
const tab = (this._state.tabs || []).find((t) => t.id === tabId);
if (tab && tab.widget_type === 'terminal') {
  if (!window.confirm('Close this terminal? The shell session will be terminated.')) return;
}
```

(Client-side dialog only — deliberately NOT the persistent confirmation infra, which would force archive writes and can't resurrect a PTY on reload. See spec revision #3.)

- [ ] **Step 5: Run + verify**

Run: `.venv/bin/pytest tests/test_canvas.py -k close_tab -v` → PASS.
`make check-js` clean. Manual (ask Les): closing a terminal tab prompts; confirming kills the shell (verify `ps` shows the child gone).

- [ ] **Step 6: Lint + commit**

```bash
make lint && make test
git add src/decafclaw/canvas.py src/decafclaw/http_server.py \
        src/decafclaw/web/static/lib/canvas-state.js tests/test_canvas.py
git commit -m "feat(terminal): confirm + kill PTY on terminal tab close (#442)"
```

---

## Task 10: Documentation

**Files:**
- Create: `docs/web-terminal.md`
- Modify: `docs/web-ui.md` (Terminals section under Features)
- Modify: `docs/index.md` (link the new doc)
- Modify: `CLAUDE.md` (key-files: add `terminals.py`; one line under web-ui conventions about the human-only terminal + `/terminal`)

**Verification:** prose review; links resolve.

- [ ] **Step 1: Write `docs/web-terminal.md`**

Cover: what it is (human-only PTY canvas tab), how to open (`/terminal [cwd]`), the security model (web-cookie gate, `enabled` boundary, allowed_cwd_roots, per-conv cap, agent has zero access + the import-boundary test), lifecycle (close-tab confirm + kill, server-shutdown kill, conv-delete kill, no disk persistence), multi-attach + smallest-viewport-wins, the two documented caveats (multi-attach input interleaving; scrollback not persisted across server restart), and config reference (`TerminalConfig` fields + `TERMINAL_*` env vars).

- [ ] **Step 2: Update `docs/web-ui.md` + `docs/index.md`**

Add a "Terminals" subsection to web-ui.md Features linking `web-terminal.md`; add the doc to `docs/index.md`.

- [ ] **Step 3: Update `CLAUDE.md`**

Add `terminals.py` to the key-files "Other" list with a one-line description. Add one sentence under the web-ui/Mattermost conventions noting `/terminal` is a server-side side-effect command (no LLM turn, no archive write) and that `terminals.py` must never be imported by `tools/` or `skills/`.

- [ ] **Step 4: Commit**

```bash
git add docs/web-terminal.md docs/web-ui.md docs/index.md CLAUDE.md
git commit -m "docs(terminal): web-terminal reference + web-ui/index/CLAUDE updates (#442)"
```

---

## Self-Review

**Spec coverage** (against spec goals + revisions):
- Open shell via `/terminal` — Task 7. ✓
- Real PTY in `$SHELL`/workspace or given CWD — Tasks 2, 7. ✓
- Reload restores (reconnect + replay) — Tasks 3, 6. ✓
- Standalone `/canvas/{conv}/{tab}` multi-attach — already exists (agent-verified); widget + WS support it via Tasks 3, 6; importmap added to `canvas-page.html` in Task 5. ✓
- Multiple terminals per conv without interference — Tasks 2 (keyed registry) + 8 (per-tab host, aliasing fix). ✓
- Agent zero access — Global Constraints + Task 2 import-boundary test. ✓
- Server-side command routing (revision #2) — Task 7. ✓
- Client-side close confirm (revision #3) — Task 9. ✓
- Keep-alive host (revision #4) — Task 8. ✓
- Canvas path correctness (revision #1) — Global Constraints; Tasks 7/9 use `canvas.py` helpers that already resolve the dir path. ✓
- `TerminalConfig` + env — Task 1. ✓
- Vendor xterm — Task 5. ✓
- Lifecycle kills (shutdown, conv-delete) — Task 4. ✓
- Docs — Task 10. ✓

**Deferred / non-goals** (unchanged from spec): read-only agent access, disk-persisted scrollback, detached/reattachable sessions, cross-conv terminals, mobile on-screen keys, recording — none in scope.

**Placeholder scan:** No "TBD"/"handle appropriately" steps. Two explicit read-and-copy instructions remain (Task 6 theme-event name from `theme-toggle.js`; Task 7 canvas emit wiring from `canvas_tools.py`) — these point at a concrete existing pattern to mirror rather than invent, which is correct given they must match live code exactly.

**Type consistency:** Registry method names (`spawn`/`get`/`attach`/`detach`/`write_input`/`set_viewport`/`drop_viewport`/`kill`/`shutdown_all`/`kill_sessions_for_conv`/`count_for_conv`/`_handle_output`/`_min_viewport`) are used consistently across Tasks 2–4, 7, 9. `TerminalSession` field names match the dataclass. WS message types (`input`/`resize`/`ping` ↔ `session_ended`/`size_changed`/`buffer_replay_done`) match between Task 3 (server) and Task 6 (client). Widget `data` schema (`session_id`/`cwd`/`shell`) matches Task 7's `new_tab(data=...)`.

**Known JS-testing gap:** Tasks 5, 6, 8, 9-client have no unit tests (no JS harness in-repo) — verified via `make check-js` + manual/Playwright, and their server-side counterparts are unit-tested. This is called out honestly, not papered over.
