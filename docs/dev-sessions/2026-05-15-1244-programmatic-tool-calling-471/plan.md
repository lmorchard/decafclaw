# Programmatic tool calling — Implementation Plan

**Goal:** Add a bundled always-loaded `code_execution` skill that runs LLM-authored Python scripts in a subprocess with UDS-based RPC into a curated subset of decafclaw tools, so deterministic multi-step work runs as one tool call and keeps intermediate outputs off the conversation transcript.

**Approach:** New skill at `src/decafclaw/skills/code_execution/`. The tool spawns a Python subprocess (`sys.executable`) writing a generated `decafclaw_tools.py` proxy and the LLM's `script.py` to a temp dir. The parent runs an `asyncio.start_unix_server` that accepts one connection, reads line-delimited JSON requests, dispatches via `execute_tool` against a forked ctx (`request_confirmation=None`, `tools.allowed=ALLOWLIST`), and writes a JSON response per call. Hard caps: 300s wall-clock, 50 RPC calls, 50KB stdout / 10KB stderr (head 40% + tail 60% truncation), 512MB RLIMIT_AS. All caps configurable via `SkillConfig`.

**Tech stack:** Python stdlib only — `asyncio` (subprocess + UDS server), `tempfile`, `json`, `resource` (POSIX RLIMIT_AS), `dataclasses`. Reuses `decafclaw.tools.execute_tool` for actual tool invocation. No new dependencies.

**Allowlist constant (single source of truth — same list lives in stub generator AND server-side check):**

```
SANDBOX_ALLOWED_TOOLS = (
    "vault_read", "vault_search", "vault_journal_append", "vault_write",
    "workspace_read", "workspace_list",
    "notes_read", "notes_append",
    "tabstack_extract_markdown", "tabstack_extract_json", "tabstack_research",
)
```

11 tools. `http_request` is deliberately excluded (confirmation-gated; would deadlock the subprocess). Tabstack tools depend on the `tabstack` skill being activated in the conversation — when not active, `tools/__init__.py:execute_tool` will dispatch to `tool_tabstack_*` which raises in `_get_client()` (`skills/tabstack/tools.py:35-38`), and the RPC handler catches that and returns `.error` to the script.

---

## Phase 1: Skeleton bundled skill with stub tool

Establish the bundled-skill module so the tool is reachable from the LLM. No subprocess, no RPC — the tool just returns a fixed string. This locks in the SKILL.md frontmatter, `tools.py` shape, `SkillConfig` resolution, and the always-loaded activation contract before any subprocess complexity lands.

**Files:**
- Create: `src/decafclaw/skills/code_execution/__init__.py` — empty package marker (matches `skills/vault/__init__.py`).
- Create: `src/decafclaw/skills/code_execution/SKILL.md` — frontmatter `name: code_execution`, `description: ...`, `always-loaded: true`. Body explains the intended use (multi-step deterministic work; keep intermediate outputs off-context).
- Create: `src/decafclaw/skills/code_execution/tools.py` — `SkillConfig` dataclass (all caps as fields, defaults from the spec), `init(config, skill_config)` that stashes `skill_config` on a module-level `_settings`, `tool_code_execution(ctx, code: str) -> ToolResult` stub that returns `ToolResult(text="[stub: not yet implemented]")`, `TOOLS = {"code_execution": tool_code_execution}`, `TOOL_DEFINITIONS` with `priority: "low"` and `timeout: None` (matching the precedent at `tools/delegate.py:528` for `delegate_task`).
- Test: `tests/skills/test_code_execution_skill_loading.py` — verifies (a) `discover_skills` finds it under bundled, (b) it's classified `always-loaded`, (c) `init` runs and `_settings` matches `SkillConfig()` defaults when no overrides, (d) `code_execution` appears in `build_tool_list()` output as critical.

**Key changes:**

```python
# src/decafclaw/skills/code_execution/tools.py

from dataclasses import dataclass, field
from decafclaw.media import ToolResult

@dataclass
class SkillConfig:
    timeout_seconds: float = 300.0
    max_tool_calls: int = 50
    max_stdout_bytes: int = 50_000
    max_stderr_bytes: int = 10_000
    memory_cap_bytes: int = 512 * 1024 * 1024

_settings: SkillConfig = SkillConfig()

def init(config, skill_config: SkillConfig) -> None:
    global _settings
    _settings = skill_config

async def tool_code_execution(ctx, code: str) -> ToolResult:
    return ToolResult(text="[stub: not yet implemented]")

TOOLS = {"code_execution": tool_code_execution}

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "priority": "normal",  # always-loaded skill tools are force-promoted
                                # to critical regardless; "normal" reflects
                                # the declared default without misleading.
        "timeout": None,  # internal cap is authoritative
        "function": {
            "name": "code_execution",
            "description": (
                "Run a Python script that calls a curated set of decafclaw "
                "tools via the `dc.*` proxy. Use ONLY for deterministic "
                "multi-step work where intermediate per-tool outputs would "
                "be wasted context (e.g. read 5 pages, extract a field from "
                "each, return the earliest). Do NOT use for single lookups — "
                "call the underlying tool directly. See SKILL.md for the "
                "allowlist."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": (
                            "Python script. Import `from decafclaw_tools "
                            "import dc` to access tools. `print(...)` what "
                            "you want returned."
                        ),
                    },
                },
                "required": ["code"],
            },
        },
    },
]
```

**Verification — automated:**
- [x] `make lint` passes
- [x] `make typecheck` passes
- [x] `pytest tests/skills/test_code_execution_skill_loading.py -v` passes (3+ assertions)
- [x] `make test` passes (baseline 2507 → 2510+, no regressions)

**Verification — manual:**
- [ ] Run `make config` and confirm `skills.code_execution` section is present with default values
- [ ] Run `python -c "from decafclaw.skills import discover_skills; ..."` snippet to verify the new skill shows up classified as always-loaded

---

## Phase 2: Subprocess + UDS RPC plumbing with synthetic `dc.ping`

Spin up the actual subprocess and round-trip one synthetic RPC call. Still no real tools. This locks in the spawn / cleanup / framing / timeout / capture mechanics in isolation before tool dispatch lands.

**Files:**
- Create: `src/decafclaw/skills/code_execution/_sandbox.py` — async sandbox runner. Public surface: `async def run_script(ctx, code: str, settings: SkillConfig, handler: Callable[[str, dict], Awaitable[dict]]) -> SandboxResult` where `handler(tool_name, args) -> {text, data, error}` is the RPC dispatch hook (injected so Phase 3 can swap in real tool dispatch without touching this module).
- Create: `src/decafclaw/skills/code_execution/_stub.py` — `generate_stub_source(allowed_tools: tuple[str, ...]) -> str` returns the source for `decafclaw_tools.py`. Phase 2 calls it with `("ping",)`; Phase 3 calls it with the full allowlist.
- Modify: `src/decafclaw/skills/code_execution/tools.py` — replace stub body of `tool_code_execution`; inject a `_ping_handler` that returns `{"text": "pong", "data": None, "error": None}`; pass it to `_sandbox.run_script`. Convert the returned `SandboxResult` into a `ToolResult`.
- Test: `tests/skills/test_code_execution_sandbox.py`:
  - `test_ping_round_trip` — script `print(dc.ping().text)`; verify stdout == `"pong\n"`, exit 0, `tool_calls == [{"tool": "ping", "ok": True, ...}]`.
  - `test_timeout_kills_subprocess` — script `import time; time.sleep(10)`; settings.timeout_seconds=0.5; verify status == `"timeout"`, elapsed < 2.0, subprocess no longer running.
  - `test_script_crash_captures_traceback` — script `raise RuntimeError("boom")`; verify status == `"error"`, stderr contains `"boom"`, exit non-zero.
  - `test_stdout_capture` — script `print("hello")`; verify stdout == `"hello\n"`.

**Key changes:**

```python
# src/decafclaw/skills/code_execution/_sandbox.py

import asyncio
import json
import logging
import os
import resource
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from ._stub import generate_stub_source

log = logging.getLogger(__name__)

# Env vars that pass through to the subprocess.
_SAFE_PREFIX_ALLOW = (
    "PATH", "HOME", "USER", "LANG", "LC_", "TERM", "TMPDIR",
    "PYTHONPATH", "VIRTUAL_ENV", "DECAFCLAW_",
)
# Substrings that block any var name regardless of prefix match.
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


def _truncate(s: str, cap: int) -> str:
    """Head 40% + tail 60% truncation with explicit marker."""
    if len(s) <= cap:
        return s
    head = int(cap * 0.4)
    tail = cap - head - 64  # leave room for marker
    return (s[:head]
            + f"\n[... truncated {len(s) - head - tail} bytes ...]\n"
            + s[-tail:])


async def _serve_rpc(reader, writer, *, handler, max_calls, allowed,
                     calls_made: list[int], call_log: list[dict]):
    """One client connection, one line per request, one line per response."""
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

            calls_made[0] += 1
            if calls_made[0] > max_calls:
                resp = {"text": "", "data": None,
                        "error": f"tool call limit ({max_calls}) exceeded"}
            elif tool not in allowed:
                resp = {"text": "", "data": None,
                        "error": f"tool '{tool}' not in sandbox allowlist"}
            else:
                start = asyncio.get_event_loop().time()
                resp = await handler(tool, args)
                duration_ms = int((asyncio.get_event_loop().time() - start) * 1000)
                call_log.append({
                    "tool": tool, "args_keys": sorted(args.keys()),
                    "duration_ms": duration_ms,
                    "ok": resp.get("error") is None,
                })
            writer.write((json.dumps(resp) + "\n").encode())
            await writer.drain()
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception as exc:
            log.debug("RPC writer close failed: %s", exc)


def _preexec(mem_cap: int):
    """Set RLIMIT_AS before exec. POSIX only. macOS enforcement is best-effort."""
    resource.setrlimit(resource.RLIMIT_AS, (mem_cap, mem_cap))


async def run_script(ctx, code: str, settings, *,
                     handler, allowed: tuple[str, ...]) -> SandboxResult:
    tmp = Path(tempfile.mkdtemp(prefix="dc-codeexec-"))
    sock_path = tmp / "rpc.sock"
    try:
        (tmp / "decafclaw_tools.py").write_text(
            generate_stub_source(allowed, sock_path=str(sock_path))
        )
        (tmp / "script.py").write_text(code)

        calls_made = [0]
        call_log: list[dict] = []
        server = await asyncio.start_unix_server(
            lambda r, w: _serve_rpc(
                r, w, handler=handler,
                max_calls=settings.max_tool_calls,
                allowed=set(allowed),
                calls_made=calls_made,
                call_log=call_log,
            ),
            path=str(sock_path),
        )

        env = _scrub_env()
        env["DECAFCLAW_RPC_SOCKET"] = str(sock_path)

        loop_start = asyncio.get_event_loop().time()
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "script.py",
            cwd=str(tmp),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            preexec_fn=lambda: _preexec(settings.memory_cap_bytes),
            start_new_session=True,
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(), timeout=settings.timeout_seconds
            )
            elapsed = asyncio.get_event_loop().time() - loop_start
            stdout = stdout_b.decode("utf-8", errors="replace")
            stderr = stderr_b.decode("utf-8", errors="replace")
            if calls_made[0] > settings.max_tool_calls:
                status = "tool_call_limit"
            elif proc.returncode == 0:
                status = "success"
            else:
                status = "error"
            return SandboxResult(
                status=status, elapsed_seconds=elapsed,
                tool_calls=call_log,
                stdout=_truncate(stdout, settings.max_stdout_bytes),
                stderr=_truncate(stderr, settings.max_stderr_bytes),
                exit_code=proc.returncode,
            )
        except asyncio.TimeoutError:
            proc.kill()
            try:
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                log.warning("subprocess did not exit after SIGKILL")
            elapsed = asyncio.get_event_loop().time() - loop_start
            stdout_b = await proc.stdout.read() if proc.stdout else b""
            stderr_b = await proc.stderr.read() if proc.stderr else b""
            return SandboxResult(
                status="timeout", elapsed_seconds=elapsed,
                tool_calls=call_log,
                stdout=_truncate(stdout_b.decode("utf-8", errors="replace"),
                                 settings.max_stdout_bytes),
                stderr=_truncate(stderr_b.decode("utf-8", errors="replace"),
                                 settings.max_stderr_bytes),
                exit_code=None,
            )
        finally:
            server.close()
            await server.wait_closed()
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
```

```python
# src/decafclaw/skills/code_execution/_stub.py

_STUB_TEMPLATE = '''
"""Generated proxy module for the decafclaw code-execution sandbox."""

import json
import os
import socket
import threading
from dataclasses import dataclass

_SOCKET_PATH = os.environ["DECAFCLAW_RPC_SOCKET"]
_lock = threading.Lock()
_sock = None
_rfile = None


def _connect():
    global _sock, _rfile
    if _sock is None:
        _sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        _sock.connect(_SOCKET_PATH)
        _rfile = _sock.makefile("rb")


@dataclass
class ToolResultProxy:
    text: str = ""
    data: dict | None = None
    error: str | None = None


def _call(tool: str, args: dict) -> ToolResultProxy:
    with _lock:
        _connect()
        _sock.sendall((json.dumps({{"tool": tool, "args": args}}) + "\\n").encode())
        line = _rfile.readline()
        if not line:
            return ToolResultProxy(error="rpc connection closed")
        resp = json.loads(line)
        return ToolResultProxy(
            text=resp.get("text", "") or "",
            data=resp.get("data"),
            error=resp.get("error"),
        )


class _DCNamespace:
    """`dc.<tool_name>(**kwargs)` → ToolResultProxy."""
    {accessors}


dc = _DCNamespace()
'''


def generate_stub_source(allowed: tuple[str, ...], *, sock_path: str) -> str:
    """Generate the proxy module source. `sock_path` is informational only;
    the actual socket path comes from `DECAFCLAW_RPC_SOCKET` at runtime."""
    accessors = "\n    ".join(
        f"def {name}(self, **kwargs): return _call({name!r}, kwargs)"
        for name in allowed
    )
    return _STUB_TEMPLATE.format(accessors=accessors)
```

**Verification — automated:**
- [x] `make lint` passes
- [x] `make typecheck` passes
- [x] `pytest tests/skills/test_code_execution_sandbox.py -v` passes (4 tests)
- [x] `make test` passes (no regressions)
- [x] `pytest tests/skills/test_code_execution_sandbox.py --durations=10` — slowest test < 5s (timeout test is 0.57s wall)

**Verification — manual:**
- [x] No `/tmp/dc-codeexec-*` directories left behind after running tests (verified empty)
- [x] No zombie Python processes after timeout test (verified)

---

## Phase 3: Real tool dispatch via `execute_tool`

Replace the `ping` handler with a real dispatcher that forks ctx and calls `decafclaw.tools.execute_tool` against the allowlist. Stub generator emits proxy functions for every allowlisted name. End-to-end: an LLM script can do `dc.vault_read("Foo")` and get a real ToolResultProxy back.

**Files:**
- Modify: `src/decafclaw/skills/code_execution/tools.py`:
  - Add `SANDBOX_ALLOWED_TOOLS` constant (the 11-tuple above).
  - Replace `_ping_handler` with `_make_tool_handler(ctx)` that returns an async closure dispatching via `execute_tool` against a forked ctx.
  - Wire `run_script(..., handler=_make_tool_handler(ctx), allowed=SANDBOX_ALLOWED_TOOLS)`.
  - Convert `SandboxResult` → `ToolResult` for the LLM (structured text + machine-readable data).
- Modify: `src/decafclaw/skills/code_execution/_sandbox.py` — no changes (handler-injection already in place from Phase 2).
- Test: `tests/skills/test_code_execution_dispatch.py`:
  - `test_dc_notes_read_round_trip` — seed a notes entry; script `print(dc.notes_read().text)`; verify the entry is in stdout.
  - `test_dc_workspace_list_returns_data` — write a tmp file in workspace; script `print(dc.workspace_list().text)`; verify filename in stdout.
  - `test_non_allowlisted_tool_rejected` — script `print(dc.shell(command="ls").error)`; verify error mentions "not in sandbox allowlist" AND the agent's real `shell` tool was never invoked (mock or assert no shell-related events).
  - `test_vault_write_outside_agent_returns_non_interactive_error` — script `r = dc.vault_write(page="not-agent/foo", content="x"); print(r.error or r.text)`; verify text mentions "requires interactive confirmation".
  - `test_tabstack_inactive_surfaces_error` — without activating tabstack; script calls `dc.tabstack_extract_markdown(url="x")`; verify `.error` mentions "not initialized" (matches `_get_client()` raise).

**Key changes:**

```python
# src/decafclaw/skills/code_execution/tools.py — additions

import copy
import json
from typing import Awaitable, Callable
from decafclaw.tools import execute_tool

SANDBOX_ALLOWED_TOOLS: tuple[str, ...] = (
    "vault_read", "vault_search", "vault_journal_append", "vault_write",
    "workspace_read", "workspace_list",
    "notes_read", "notes_append",
    "tabstack_extract_markdown", "tabstack_extract_json", "tabstack_research",
)


def _make_tool_handler(parent_ctx) -> Callable[[str, dict], Awaitable[dict]]:
    async def handler(tool_name: str, args: dict) -> dict:
        # Defense in depth: the sandbox server already checks allowlist,
        # but a misbehaving stub could submit unknown names.
        if tool_name not in SANDBOX_ALLOWED_TOOLS:
            return {"text": "", "data": None,
                    "error": f"tool '{tool_name}' not in sandbox allowlist"}

        # Fork ctx so confirmation-gated tools fall through to their
        # NON_INTERACTIVE_ERROR paths instead of blocking the script.
        sandbox_ctx = copy.copy(parent_ctx)
        sandbox_ctx.request_confirmation = None
        # Note: ctx.tools.allowed is per-tool-restriction within a turn;
        # we use the explicit allowlist check above instead. Don't mutate
        # ctx.tools.allowed here — it would also affect concurrent tool
        # calls in the parent.

        try:
            result = await execute_tool(sandbox_ctx, tool_name, args)
        except Exception as exc:
            log.exception("sandbox tool '%s' raised", tool_name)
            return {"text": "", "data": None, "error": str(exc)}

        text = result.text or ""
        # decafclaw convention: error tools return text starting with "[error".
        error = None
        if text.startswith("[error"):
            error = text
            text = ""
        return {"text": text, "data": result.data, "error": error}

    return handler


async def tool_code_execution(ctx, code: str) -> ToolResult:
    handler = _make_tool_handler(ctx)
    sandbox = await _sandbox.run_script(
        ctx, code, _settings,
        handler=handler, allowed=SANDBOX_ALLOWED_TOOLS,
    )
    return _render_result(sandbox)


def _render_result(s: "_sandbox.SandboxResult") -> ToolResult:
    lines = [
        f"**status:** {s.status}",
        f"**elapsed:** {s.elapsed_seconds:.2f}s",
        f"**tool_calls:** {len(s.tool_calls)}",
    ]
    if s.tool_calls:
        for c in s.tool_calls:
            lines.append(
                f"  - {c['tool']}({', '.join(c['args_keys'])}) "
                f"{'ok' if c['ok'] else 'err'} {c['duration_ms']}ms"
            )
    if s.stdout:
        lines.append("**stdout:**\n```\n" + s.stdout + "\n```")
    if s.stderr:
        lines.append("**stderr:**\n```\n" + s.stderr + "\n```")
    return ToolResult(
        text="\n".join(lines),
        data={
            "status": s.status,
            "elapsed_seconds": s.elapsed_seconds,
            "tool_calls": s.tool_calls,
            "stdout": s.stdout,
            "stderr": s.stderr,
            "exit_code": s.exit_code,
        },
    )
```

The `ctx.tools.allowed` note in the comment is load-bearing: the rule "don't mutate ctx.tools fields on a forked ctx" follows the CLAUDE.md guidance that runtime state goes on the dataclass and shouldn't be repurposed for cross-cutting filtering. The allowlist check above is the trust boundary.

**Verification — automated:**
- [x] `make lint` passes
- [x] `make typecheck` passes
- [x] `pytest tests/skills/test_code_execution_dispatch.py -v` passes (5 tests)
- [x] `pytest tests/skills/test_code_execution_skill_loading.py tests/skills/test_code_execution_sandbox.py -v` still pass
- [x] `make test` passes (no regressions; baseline + new tests)
- [x] `make check` passes

**Verification — manual:** (deferred to Phase 6)
- [ ] In a real conversation, the LLM can call `code_execution` with a script that does `dc.vault_search(query="foo")` and the result is parseable.

---

## Phase 4: Resource caps + observability

Phase 2 already shipped most of the resource enforcement (timeout, tool-call cap, RLIMIT_AS, I/O truncation). This phase adds the missing piece — per-RPC progress events — and tightens the corner cases discovered in Phase 3 testing. Test cases here are *property* tests for the limits, separate from happy-path tests in earlier phases.

**Files:**
- Modify: `src/decafclaw/skills/code_execution/_sandbox.py` — in the RPC server, after dispatching a tool call, `await ctx.publish("tool_status", tool="code_execution", message=f"dc.{tool_name}", call_index=calls_made[0])`. This requires plumbing `parent_ctx` into `_serve_rpc` — add a `progress_publish: Callable[[str, dict], Awaitable[None]] | None` parameter so `_sandbox` stays decoupled from `ctx` directly.
- Modify: `src/decafclaw/skills/code_execution/tools.py` — pass `progress_publish=ctx.publish` into `run_script`.
- Test: `tests/skills/test_code_execution_limits.py`:
  - `test_tool_call_limit_enforced` — settings.max_tool_calls=3; script loops `for _ in range(10): dc.notes_read()`; verify status == `"tool_call_limit"` and `len(tool_calls) == 3` (server stops dispatching after limit).
  - `test_stdout_truncation_head_and_tail` — settings.max_stdout_bytes=200; script prints 1000 bytes; verify stdout contains `"truncated"` marker and is ≤ 200 bytes.
  - `test_stderr_truncation` — script raises with a long message; verify stderr is truncated similarly.
  - `test_memory_cap_on_linux` — `pytest.mark.skipif(sys.platform == "darwin", reason="RLIMIT_AS unreliable on macOS")`; settings.memory_cap_bytes=64 MB; script `x = bytearray(128 * 1024 * 1024)`; verify status == `"error"`, stderr contains `MemoryError` OR exit_code != 0.
  - `test_progress_events_published` — record `tool_status` events via a subscriber on `ctx.event_bus`; script does 3 `dc.notes_read()` calls; verify 3 progress events with monotonically increasing `call_index`.

**Key changes:**

```python
# _sandbox.py — _serve_rpc signature gains progress_publish

async def _serve_rpc(reader, writer, *, handler, max_calls, allowed,
                     calls_made: list[int], call_log: list[dict],
                     progress_publish):
    ...
    if tool not in allowed:
        resp = ...
    else:
        ...
        resp = await handler(tool, args)
        ...
        call_log.append({...})
        if progress_publish is not None:
            try:
                await progress_publish(
                    "tool_status", tool="code_execution",
                    message=f"dc.{tool}", call_index=calls_made[0],
                )
            except Exception as exc:
                log.debug("progress publish failed: %s", exc)
    ...

# run_script signature gains progress_publish; forwarded to _serve_rpc.
```

**Verification — automated:**
- [x] `make lint` passes
- [x] `make typecheck` passes
- [x] `pytest tests/skills/test_code_execution_limits.py -v` passes (5 tests; memory test skipped on macOS)
- [x] `make test` passes (2524 passed, 1 skipped)
- [x] `pytest tests/skills/test_code_execution_limits.py --durations=10` — slowest test 0.20s

**Verification — manual:** (deferred to Phase 6)
- [ ] In a real `make dev` session, run a code_execution script that does 3 dc calls and watch the web UI — three "tool_status" progress lines should appear inline before the final result.

---

## Phase 5: Tool description tightening, docs, eval

Polish the tool description (it's a control surface per CLAUDE.md), write user-facing docs, and add an eval test for disambiguation against existing tools (`vault_read`, `notes_read`) so the LLM doesn't reach for the heavy hammer on simple lookups.

**Files:**
- Modify: `src/decafclaw/skills/code_execution/tools.py` — refine `TOOL_DEFINITIONS[0]["function"]["description"]` based on eval results from this phase. Initial wording from Phase 1 may need adjustment.
- Modify: `src/decafclaw/skills/code_execution/SKILL.md` body — document the allowlist (11 tools), caps, the "deterministic multi-step" framing, and *non-uses* (single lookups → call the tool directly).
- Create: `docs/code-execution.md` — feature doc. Sections: Overview, Allowlist (with one-line descriptions of each tool's role), Caps & overrides (SkillConfig field-by-field), Security boundary (what's trusted, what isn't, the env scrub, why http_request is excluded), Adding tools to the allowlist (how to extend, what to verify).
- Modify: `docs/index.md` — add `code-execution.md` link under an appropriate section (likely "Tools" or "Skills"; check current grouping).
- Modify: `CLAUDE.md` — update the "Skills (bundled)" line to note `code_execution` is among the always-loaded set (`vault`, `background`, `mcp`, **`code_execution`**).
- Modify: `docs/context-composer.md` — per CLAUDE.md "update for any change to system prompt / tool definitions", add a brief mention that `code_execution` is an always-loaded tool with internal timeout (so it doesn't appear in deferred catalogs).
- Create: `src/decafclaw/eval/cases/code_execution_disambiguation.py` (or equivalent — check existing eval pattern via `make eval-tools` and `src/decafclaw/eval/` layout). The case asserts that for a single-page lookup, the LLM picks `vault_read` not `code_execution`; for a multi-page synthesis, it picks `code_execution`. If the eval framework doesn't make a per-case file pattern obvious, add the case to the existing tool-disambiguation eval fixture.

**Key changes:**

Final tool description draft (tune via `make eval-tools`):

```
Run a Python script that calls a curated set of decafclaw tools via the
`dc.*` proxy. Use ONLY when work is genuinely multi-step AND deterministic
AND intermediate per-tool outputs would be wasted context — for example:
read 5 vault pages, extract a field from each, return the one with the
earliest date.

DO NOT use for:
- single lookups (call the underlying tool directly)
- work that requires user confirmation (shell, send_email — call those
  directly outside the sandbox)
- exploratory work where you don't know what to compute yet

Allowlist: vault_read, vault_search, vault_journal_append, vault_write,
workspace_read, workspace_list, notes_read, notes_append,
tabstack_extract_markdown, tabstack_extract_json, tabstack_research.
Limits: 300s wall-clock, 50 tool calls per script. Script imports the
proxy as `from decafclaw_tools import dc`. Each `dc.<tool>(...)` returns
a ToolResultProxy with `.text`, `.data`, `.error`. `print(...)` what you
want returned to the conversation.
```

**Verification — automated:**
- [x] `make lint` passes
- [x] `make typecheck` passes
- [x] `make check` passes
- [x] `make eval-tools` — 3 of 4 new cases pass (all anti-overuse cases); positive case fails with LLM picking `vault_list` (defensible exploratory behavior, not a description bug; now in `near_miss` for the eval signal)
- [x] `make test` passes (2524, no regressions)
- [x] `grep -r "always-loaded" CLAUDE.md docs/` shows updated list

**Verification — manual:** (deferred to Phase 6)
- [ ] Read `docs/code-execution.md` end-to-end; a future contributor adding a tool to the allowlist should be able to follow it without asking
- [ ] Run `make config` and confirm `skills.code_execution` shows up with the documented field names

---

## Phase 6: Manual smoke against `make dev`

Live test in the web UI. This is opt-out of TDD (manual UI verification only).

**Files:** none.

**Verification — automated:** none (covered in Phases 1–5).

**Verification — manual:**
- [ ] Check with Les that `make dev` isn't running on the bot account before starting a local instance, OR run `make run` in interactive mode for this phase
- [ ] Single-page lookup prompt ("read agent/pages/DecafClaw and summarize") → LLM picks `vault_read`, not `code_execution`
- [ ] Multi-page synthesis prompt ("read 3 most recent journal entries and list the topics across them") → LLM picks `code_execution`, script does 3 dc calls
- [ ] Conversation transcript does NOT contain the 3 individual vault_read results — only the final code_execution summary
- [ ] Three `tool_status` progress lines appear inline during the code_execution call
- [ ] Force a timeout: prompt that asks the LLM to run a sandbox script with `while True: pass` (or `time.sleep(400)`) → result returns `status: timeout` within ~302s
- [ ] Force an allowlist rejection: prompt that has the LLM script call `dc.shell(...)` → result returns `.error` mentioning allowlist; no shell confirmation appears in the UI
- [ ] No `/tmp/dc-codeexec-*` directories left behind after a few sandbox runs
- [ ] Resume across page reload: start a sandbox script, reload the web UI mid-run, verify the result still arrives (or fails cleanly if a refresh kills the WS connection)

---

## Out-of-scope cleanup items (do NOT fix in this PR)

Captured here per CLAUDE.md "document worth-fixing items separately" — not load-bearing for this feature, candidates for future sessions:

- `_check_user_write_allowed`'s `NON_INTERACTIVE_ERROR` text mentions "agent folder" even when grants are in play (`vault/tools.py:319-323`). Minor wording.
- `http_request`'s allow-pattern mechanism could be reused if we ever add `http_request` to the sandbox; would need a non-interactive fail-closed path inside `request_confirmation` itself.

## Spec coverage check

| Spec requirement | Implementing phase |
|---|---|
| Bundled, always-loaded skill | Phase 1 |
| `SkillConfig` with all caps + env override | Phase 1 |
| `dc.<tool>(...) -> ToolResultProxy(text, data, error)` | Phase 2 (shape) + Phase 3 (real tools) |
| Subprocess via `sys.executable` + UDS, JSON-line framing | Phase 2 |
| Env scrubbing (safe-prefix + secret-substring block) | Phase 2 |
| Defense-in-depth allowlist (stub + server) | Phase 2 (server) + Phase 3 (stub generator with full list) |
| 300s wall-clock timeout with kill | Phase 2 |
| `threading.Lock` serialization in stub | Phase 2 |
| 50 tool calls per script cap | Phase 2 (mechanism) + Phase 4 (property test) |
| 50KB stdout / 10KB stderr head+tail truncation | Phase 2 (mechanism) + Phase 4 (property test) |
| 512MB RLIMIT_AS via `preexec_fn` | Phase 2 (mechanism) + Phase 4 (Linux-only test) |
| Forked ctx with `request_confirmation=None` so vault_write outside agent fails cleanly | Phase 3 |
| ToolResult shape returned to LLM (status / elapsed / tool_calls / stdout / stderr) | Phase 3 |
| Per-RPC progress events | Phase 4 |
| Tool description tuned for "multi-step deterministic only" | Phase 5 |
| `timeout: None` on TOOL_DEFINITIONS entry | Phase 1 |
| Docs + CLAUDE.md updates | Phase 5 |
| Eval disambiguation against single-lookup tools | Phase 5 |
| Manual smoke in dev | Phase 6 |
