"""Async sandbox runner for the code-execution skill.

Spawns a Python subprocess running an LLM-supplied script and serves a
Unix-domain-socket RPC server that the script's `dc.<tool>(...)` calls
connect to. Each request is one JSON object per line; each response is one
JSON object per line. The RPC dispatch is injected via a `handler` callable
so Phase 3 can swap in real tool dispatch without touching this module.

Resource enforcement layers:
  - env scrubbing (no secrets cross the fork boundary)
  - RLIMIT_AS via preexec_fn (Linux: hard cap; macOS: no enforcement —
    preexec is disabled because RLIMIT_AS can't be lowered below
    RLIM_INFINITY on Darwin)
  - wall-clock timeout via asyncio.wait_for + process-group SIGKILL
  - bounded stdout/stderr capture so a runaway script can't OOM the parent
  - per-script tool-call cap enforced at the RPC server
  - stdout/stderr truncation on the return path (byte-accurate)

Cleanup: the temp dir holding the generated proxy module, the script, and
the socket is removed unconditionally after the subprocess exits, even if
subprocess spawn itself raised.
"""

import asyncio
import json
import logging
import os
import resource
import shutil
import signal
import sys
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from decafclaw.skills.code_execution._stub import generate_stub_source

log = logging.getLogger(__name__)

# Env vars that pass through to the subprocess. Anything not matched is
# dropped — this is the trust boundary between agent process and script.
_SAFE_PREFIX_ALLOW = (
    "PATH", "HOME", "USER", "LANG", "LC_", "TERM", "TMPDIR", "TZ",
    "PYTHONPATH", "VIRTUAL_ENV", "DECAFCLAW_",
)
# Substrings that block a var name even if its prefix matched the allowlist —
# defense in depth against future allowlist drift.
_SECRET_SUBSTRING_BLOCK = (
    "KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL", "PASSWD", "AUTH",
)


def _scrub_env() -> dict[str, str]:
    out: dict[str, str] = {}
    for name, val in os.environ.items():
        upper = name.upper()
        if any(b in upper for b in _SECRET_SUBSTRING_BLOCK):
            continue
        if any(upper.startswith(p) or upper == p.rstrip("_")
               for p in _SAFE_PREFIX_ALLOW):
            out[name] = val
    return out


@dataclass
class SandboxResult:
    status: str  # "success" | "error" | "timeout" | "tool_call_limit"
    elapsed_seconds: float
    tool_calls: list[dict] = field(default_factory=list)
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None


def _truncate_bytes(data: bytes, cap: int) -> str:
    """Head 40% + tail truncation with an inline marker, measured in bytes.

    Caps are byte budgets — measuring code points after decode would let
    multi-byte UTF-8 sequences blow past the cap by 2-4x. We slice on the
    raw byte stream, then decode with `errors="replace"` at the end so any
    split surrogate / multi-byte boundary becomes a replacement character
    instead of a UnicodeDecodeError.
    """
    if len(data) <= cap:
        return data.decode("utf-8", errors="replace")
    head = int(cap * 0.4)
    # Reserve 64 bytes for the marker text itself.
    tail = cap - head - 64
    if tail <= 0:
        return data[:cap].decode("utf-8", errors="replace")
    dropped = len(data) - head - tail
    marker = f"\n[... truncated {dropped} bytes ...]\n".encode("utf-8")
    return (data[:head] + marker + data[-tail:]).decode(
        "utf-8", errors="replace"
    )


# Loose signature: `await progress_publish(event_type, **kwargs)` matches
# `Context.publish`, which takes a positional event_type plus arbitrary kwargs.
# A strict `Callable[[str, dict], ...]` would misrepresent the call site.
_ProgressPublisher = Callable[..., Awaitable[None]]


async def _serve_rpc(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    *,
    handler: Callable[[str, dict], Awaitable[dict]],
    max_calls: int,
    allowed: set[str],
    calls_made: list[int],
    call_log: list[dict],
    progress_publish: _ProgressPublisher | None,
    publish_tasks: list[asyncio.Task] | None = None,
) -> None:
    """One client connection, one line per request, one line per response.

    `calls_made` is a one-element list used as a mutable counter shared
    across connections — Python doesn't have `nonlocal` capture in a
    server callback, and a list is simpler than a wrapper class.

    `progress_publish`, when set, is invoked once per dispatched tool call
    with an event_type of ``"tool_status"`` so transports can render
    per-RPC progress. It is fired-and-forgotten (scheduled as a task and
    not awaited) — slow subscribers must not stretch the RPC roundtrip and
    push the script toward its wall-clock cap. Failures are swallowed at
    debug level by the wrapper task.
    """
    try:
        while True:
            line = await reader.readline()
            if not line:
                break
            try:
                req = json.loads(line)
                tool = req["tool"]
                args = req.get("args", {})
            except (json.JSONDecodeError, KeyError) as exc:
                resp = {"text": "", "data": None,
                        "error": f"malformed request: {exc}"}
                writer.write((json.dumps(resp) + "\n").encode())
                await writer.drain()
                continue

            if not isinstance(args, dict):
                resp = {
                    "text": "",
                    "data": None,
                    "error": (
                        f"args must be a dict, got "
                        f"{type(args).__name__}"
                    ),
                }
                writer.write((json.dumps(resp) + "\n").encode())
                await writer.drain()
                continue

            calls_made[0] += 1
            publish_after = False
            if calls_made[0] > max_calls:
                resp = {"text": "", "data": None,
                        "error": f"tool call limit ({max_calls}) exceeded"}
            elif tool not in allowed:
                resp = {"text": "", "data": None,
                        "error": f"tool '{tool}' not in sandbox allowlist"}
            else:
                start = asyncio.get_event_loop().time()
                resp = await handler(tool, args)
                duration_ms = int(
                    (asyncio.get_event_loop().time() - start) * 1000
                )
                call_log.append({
                    "tool": tool,
                    "args_keys": sorted(args.keys()),
                    "duration_ms": duration_ms,
                    "ok": resp.get("error") is None,
                })
                publish_after = progress_publish is not None
            # Write the RPC response BEFORE publishing progress so a slow
            # subscriber can't add latency to every dc.* call. Publish is
            # fire-and-forget for the same reason.
            writer.write((json.dumps(resp) + "\n").encode())
            await writer.drain()
            if publish_after:
                assert progress_publish is not None  # for type-checkers
                _spawn_publish(
                    progress_publish,
                    tool=tool,
                    call_index=calls_made[0],
                    publish_tasks=publish_tasks,
                )
    except (ConnectionResetError, BrokenPipeError) as exc:
        log.debug("RPC connection dropped: %s", exc)
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception as exc:
            log.debug("RPC writer close failed: %s", exc)


def _spawn_publish(
    progress_publish: _ProgressPublisher,
    *,
    tool: str,
    call_index: int,
    publish_tasks: list[asyncio.Task] | None = None,
) -> None:
    """Fire-and-forget a tool_status publish, swallowing any failure.

    When `publish_tasks` is provided, the created task is appended to it
    so the caller (typically `run_script`) can drain pending publishes
    before returning — keeps tests deterministic and avoids leaking
    pending tasks past the sandbox boundary.
    """

    async def _go() -> None:
        try:
            await progress_publish(
                "tool_status",
                tool="code_execution",
                message=f"dc.{tool}",
                call_index=call_index,
            )
        except Exception as exc:
            log.debug("progress publish failed: %s", exc)

    # asyncio.create_task requires a running loop; _serve_rpc only ever
    # runs under one, so the call is safe here.
    task = asyncio.create_task(_go())
    if publish_tasks is not None:
        publish_tasks.append(task)


# Whether the OS enforces RLIMIT_AS at all. macOS reports RLIM_INFINITY as
# the hard cap and rejects any attempt to lower the soft limit (setrlimit
# raises ValueError), which would propagate out of preexec_fn and abort the
# child fork with a generic SubprocessError. Linux accepts the cap. We gate
# on platform rather than probing because a behavioral probe would have to
# either lower the parent's own RLIMIT_AS (risking OOM in the agent) or
# spawn a throwaway subprocess at import time. Phase 4 will skip the
# memory-cap *test* on darwin for the same reason.
_RLIMIT_AS_OK = sys.platform.startswith("linux")


def _preexec(mem_cap: int) -> None:
    """Set RLIMIT_AS before exec. Linux only; on macOS preexec is disabled
    (RLIMIT_AS can't be lowered below RLIM_INFINITY), so this function is
    never installed there.

    Only `resource.setrlimit` is called — a single syscall, no Python locks
    acquired. This is safe to invoke in the post-fork pre-exec window even
    though the parent is multi-threaded.
    """
    resource.setrlimit(resource.RLIMIT_AS, (mem_cap, mem_cap))


async def _capture_pipe(stream: asyncio.StreamReader | None, cap: int) -> bytes:
    """Read up to `cap * 4` bytes from `stream`, drain the rest.

    We need enough overshoot that `_truncate_bytes` has both a head and a
    tail to work with even after the script blew past the cap, but we must
    not buffer arbitrarily large output in the parent — that's the OOM
    vector we're guarding against. 4x is generous enough to surface a
    "well past the cap" signal and bounded enough that 50 KB caps stay in
    200 KB territory.

    `StreamReader.read(n)` returns as soon as any data is available (up to
    `n` bytes), so we loop until we hit the cap or EOF. After the bounded
    read, anything left in the pipe is drained-and-discarded so the
    subprocess can finish writing without blocking on a full pipe buffer.
    """
    if stream is None:
        return b""
    bounded_max = cap * 4
    chunks: list[bytes] = []
    remaining = bounded_max
    while remaining > 0:
        chunk = await stream.read(remaining)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)
        remaining -= len(chunk)
    # Hit the bounded cap. Drain remainder so the writer side can close
    # cleanly, but discard the bytes — they wouldn't fit in the report.
    while True:
        try:
            chunk = await stream.read(8192)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.debug("pipe drain failed: %s", exc)
            break
        if not chunk:
            break
    return b"".join(chunks)


async def _kill_proc_group(proc: asyncio.subprocess.Process) -> None:
    """SIGKILL the subprocess's process group, falling back to the direct
    process if signalling the group fails.

    The subprocess is started with `start_new_session=True`, so its pgid
    equals its pid and any descendants share that group. Without the
    group kill, a script that spawned children leaves them orphaned after
    the sandbox returns.
    """
    pid = proc.pid
    try:
        pgid = os.getpgid(pid)
    except ProcessLookupError:
        # Process already exited — nothing to signal.
        return
    except PermissionError as exc:
        log.debug("could not getpgid(%d): %s — falling back to proc.kill", pid, exc)
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        return
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        # Group is already gone — fine, the process exited between calls.
        return
    except PermissionError as exc:
        log.debug("killpg(%d) denied: %s — falling back to proc.kill", pgid, exc)
        try:
            proc.kill()
        except ProcessLookupError:
            pass


async def run_script(
    ctx,
    code: str,
    settings,
    *,
    handler: Callable[[str, dict], Awaitable[dict]],
    allowed: tuple[str, ...],
    progress_publish: _ProgressPublisher | None = None,
) -> SandboxResult:
    """Spawn the sandbox subprocess, serve RPC, return a SandboxResult.

    `ctx` is unused here directly — the caller passes ``ctx.publish`` as
    ``progress_publish`` when per-RPC progress events are desired.
    Defaulting to ``None`` keeps unit tests that exercise the sandbox
    without a wired event bus working unchanged.
    """
    tmp = Path(tempfile.mkdtemp(prefix="dc-codeexec-"))
    sock_path = tmp / "rpc.sock"

    # State shared between the outer cleanup and the inner spawn/wait path.
    server: asyncio.base_events.Server | None = None
    serve_tasks: list[asyncio.Task] = []
    publish_tasks: list[asyncio.Task] = []

    async def _wrap_serve(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        task = asyncio.current_task()
        if task is not None:
            serve_tasks.append(task)
        try:
            await _serve_rpc(
                reader, writer,
                handler=handler,
                max_calls=settings.max_tool_calls,
                allowed=set(allowed),
                calls_made=calls_made,
                call_log=call_log,
                progress_publish=progress_publish,
                publish_tasks=publish_tasks,
            )
        finally:
            if task is not None and task in serve_tasks:
                serve_tasks.remove(task)

    try:
        (tmp / "decafclaw_tools.py").write_text(
            generate_stub_source(allowed, sock_path=str(sock_path))
        )
        (tmp / "script.py").write_text(code)

        calls_made = [0]
        call_log: list[dict] = []

        server = await asyncio.start_unix_server(
            _wrap_serve,
            path=str(sock_path),
        )

        env = _scrub_env()
        env["DECAFCLAW_RPC_SOCKET"] = str(sock_path)

        loop_start = asyncio.get_event_loop().time()
        preexec = (
            (lambda: _preexec(settings.memory_cap_bytes))
            if _RLIMIT_AS_OK else None
        )
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "script.py",
            cwd=str(tmp),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            preexec_fn=preexec,
            start_new_session=True,
        )

        # Bounded pipe capture + proc.wait — gathered under wait_for so the
        # whole thing races against the wall-clock cap. On timeout we kill
        # the process group, then await the cancelled tasks so we can pick
        # up whatever stdout/stderr was buffered before the kill.
        stdout_task = asyncio.create_task(
            _capture_pipe(proc.stdout, settings.max_stdout_bytes)
        )
        stderr_task = asyncio.create_task(
            _capture_pipe(proc.stderr, settings.max_stderr_bytes)
        )
        wait_task = asyncio.create_task(proc.wait())
        gather = asyncio.gather(
            stdout_task, stderr_task, wait_task, return_exceptions=False
        )

        try:
            try:
                await asyncio.wait_for(
                    asyncio.shield(gather),
                    timeout=settings.timeout_seconds,
                )
                stdout_b = stdout_task.result()
                stderr_b = stderr_task.result()
                elapsed = asyncio.get_event_loop().time() - loop_start
                if calls_made[0] > settings.max_tool_calls:
                    status = "tool_call_limit"
                elif proc.returncode == 0:
                    status = "success"
                else:
                    status = "error"
                return SandboxResult(
                    status=status,
                    elapsed_seconds=elapsed,
                    tool_calls=call_log,
                    stdout=_truncate_bytes(stdout_b, settings.max_stdout_bytes),
                    stderr=_truncate_bytes(stderr_b, settings.max_stderr_bytes),
                    exit_code=proc.returncode,
                )
            except asyncio.TimeoutError:
                await _kill_proc_group(proc)
                # Let the capture tasks drain whatever was already buffered
                # before the kill, then collect partial results. Bound the
                # wait so a stuck pipe (highly unlikely after SIGKILL) can't
                # extend the timeout.
                try:
                    await asyncio.wait_for(gather, timeout=2.0)
                except (asyncio.TimeoutError, Exception) as exc:
                    log.debug("post-timeout gather drain failed: %s", exc)
                elapsed = asyncio.get_event_loop().time() - loop_start
                stdout_b = (
                    stdout_task.result()
                    if stdout_task.done() and not stdout_task.cancelled()
                    else b""
                )
                stderr_b = (
                    stderr_task.result()
                    if stderr_task.done() and not stderr_task.cancelled()
                    else b""
                )
                return SandboxResult(
                    status="timeout",
                    elapsed_seconds=elapsed,
                    tool_calls=call_log,
                    stdout=_truncate_bytes(stdout_b, settings.max_stdout_bytes),
                    stderr=_truncate_bytes(stderr_b, settings.max_stderr_bytes),
                    exit_code=None,
                )
            except asyncio.CancelledError:
                # Outer tool task cancelled — kill the subprocess group, let
                # gather settle so we don't leak tasks, then re-raise.
                log.info("code_execution cancelled; killing subprocess group")
                await _kill_proc_group(proc)
                try:
                    await asyncio.wait_for(gather, timeout=2.0)
                except (asyncio.TimeoutError, Exception) as exc:
                    log.debug("post-cancel gather drain failed: %s", exc)
                raise
        finally:
            # Make sure no capture / wait task is left lingering even on
            # exception paths the explicit branches above didn't handle.
            for task in (stdout_task, stderr_task, wait_task):
                if not task.done():
                    task.cancel()
    finally:
        # Cancel any in-flight RPC connections so a slow handler can't
        # continue making tool calls after the sandbox returned. Closing
        # the server alone only stops accepting NEW connections.
        for task in list(serve_tasks):
            task.cancel()
        if server is not None:
            server.close()
            try:
                await server.wait_closed()
            except Exception as exc:
                log.debug("server wait_closed failed: %s", exc)
        # Drain pending progress publishes so a slow subscriber doesn't
        # leak a task past the sandbox boundary. Bounded wait — a stuck
        # subscriber must never block sandbox shutdown.
        if publish_tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*publish_tasks, return_exceptions=True),
                    timeout=2.0,
                )
            except (asyncio.TimeoutError, Exception) as exc:
                log.debug("publish task drain failed: %s", exc)
                for task in publish_tasks:
                    if not task.done():
                        task.cancel()
        shutil.rmtree(tmp, ignore_errors=True)
