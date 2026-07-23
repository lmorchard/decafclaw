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
    def __init__(self, config, *, loop=None, os_module=os, pty_module=pty):
        self._config = config
        self._loop = loop
        self._os = os_module
        self._pty = pty_module
        self._sessions: dict[tuple[str, str], TerminalSession] = {}
        self._lock = asyncio.Lock()
        # WS-json senders keyed by session, for control frames (size_changed,
        # session_ended). Parallel to session.attached (raw-byte senders).
        self._json_sinks: dict[int, dict] = {}
        self._tasks: set = set()

    # -- lookup --------------------------------------------------------------
    def get(self, conv_id, tab_id):
        return self._sessions.get((conv_id, tab_id))

    def count_for_conv(self, conv_id) -> int:
        return sum(1 for (c, _t) in self._sessions if c == conv_id)

    def _spawn_task(self, coro):
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    # -- spawn ---------------------------------------------------------------
    async def spawn(self, conv_id, tab_id, session_id, cwd, shell) -> TerminalSession:
        loop = self._loop or asyncio.get_running_loop()
        # pty.fork() is the portable way to get a child in its OWN session with
        # the PTY as its controlling terminal (login_tty: setsid + TIOCSCTTY) —
        # required for job control and for killpg() to tear down the shell tree
        # without touching the server. posix_spawn(setsid=True) is not available
        # on all platforms (NotImplementedError on Linux/CI), so we fork.
        # Forking a multi-threaded interpreter emits a DeprecationWarning; per
        # project decision we do NOT suppress it (it is ignored by Python's
        # default warning filter in normal runs). Build env in the parent so the
        # child only chdir + execvpe between fork and exec.
        env = dict(self._os.environ)
        env["TERM"] = "xterm-256color"
        pid, fd = self._pty.fork()
        if pid == 0:  # child
            try:
                self._os.chdir(cwd)
            except OSError:
                pass
            try:
                self._os.execvpe(shell, [shell], env)
            except OSError:
                self._os._exit(127)  # exec failed — do not fall back into async code
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
            loop = asyncio.get_running_loop()
            # Remove the reader synchronously, before scheduling _on_eof, so
            # the level-triggered selector can't dispatch a second EOF
            # callback while the coroutine is still pending.
            try:
                loop.remove_reader(session.fd)
            except (OSError, ValueError):
                pass
            self._spawn_task(self._on_eof(session))
            return
        self._handle_output(session, chunk)

    def _handle_output(self, session, chunk: bytes):
        session.buffer.extend(chunk)
        cap = self._config.terminal.buffer_bytes
        if len(session.buffer) > cap:
            del session.buffer[: len(session.buffer) - cap]
        for sink in list(session.attached):
            self._spawn_task(self._send(session, sink, chunk))

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
            if session.exit_status is None:
                session.exit_status = -1
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

    async def kill_sessions_for_conv(self, conv_id):
        for key, session in list(self._sessions.items()):
            if key[0] == conv_id:
                await self.kill(session, grace=0.2)
                self._sessions.pop(key, None)
                self._json_sinks.pop(id(session), None)
