"""Marimo pairing skill tools.

Talks to a running marimo server over HTTP. No marimo import is required:
the server does the Python work.

- Discovery reads marimo's server registry
  (`~/.local/state/marimo/servers/*.json`, or `%USERPROFILE%/.marimo/servers`
  on Windows) and probes each server's `/health`.
- Sessions come from `GET /api/sessions`, keyed by marimo's stable session ID.
- Code runs through `POST /api/kernel/execute` (SSE). Notebook mutations are
  generated Python that calls `marimo._code_mode` inside that live kernel.

`marimo._code_mode` is a private, unstable agent API. Keep the generated
snippets small and adapt to a changed signature rather than relying on a
separate marimo dependency.
"""

import asyncio
import json
import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import quote, unquote, urlsplit

import httpx

from decafclaw.media import ToolResult
from decafclaw.tools.canvas_tools import tool_canvas_new_tab

if TYPE_CHECKING:
    from decafclaw.context import Context

log = logging.getLogger(__name__)

RESULT_MARKER = "__DECAF_MARIMO_RESULT__"
_HEALTH_TIMEOUT = httpx.Timeout(2.0, connect=1.0)


@dataclass
class SkillConfig:
    """Configuration for the marimo skill."""

    token: str = field(default="", metadata={"secret": True, "env_alias": "MARIMO_TOKEN"})
    default_url: str = field(default="", metadata={"env_alias": "MARIMO_URL"})
    servers_dir: str = field(default="", metadata={"env_alias": "MARIMO_SERVERS_DIR"})
    canvas_url: str = field(default="", metadata={"env_alias": "MARIMO_CANVAS_URL"})
    request_timeout_sec: float = field(default=15.0, metadata={"env_alias": "MARIMO_REQUEST_TIMEOUT_SEC"})
    execute_timeout_sec: float = field(default=120.0, metadata={"env_alias": "MARIMO_EXECUTE_TIMEOUT_SEC"})


_skill_config = SkillConfig()


def init(config, skill_config: SkillConfig):
    """Store the resolved skill config. Called by the skill loader on activation."""
    global _skill_config
    _skill_config = skill_config
    log.info("[marimo] initialized (default_url=%r)", skill_config.default_url)


# ---------------------------------------------------------------------------
# Errors and data
# ---------------------------------------------------------------------------


class MarimoError(Exception):
    """A user-visible marimo failure, optionally with structured detail."""

    def __init__(self, message: str, data: dict | None = None):
        super().__init__(message)
        self.data = data or {}


@dataclass(frozen=True)
class _Server:
    server_id: str
    url: str
    host: str = ""
    port: int = 0
    pid: int = 0
    version: str = ""
    started_at: str = ""


@dataclass(frozen=True)
class _Session:
    server: _Server
    session_id: str
    path: str | None
    filename: str | None
    notebook_id: str | None = None

    @property
    def label(self) -> str:
        for value in (self.path, self.filename):
            if value:
                return Path(value).name
        return self.session_id

    def summary(self) -> dict:
        return {
            "session_id": self.session_id,
            "file": self.path,
            "name": self.label,
            "filename": self.filename,
            "notebook_id": self.notebook_id,
            "server_url": self.server.url,
            "server_id": self.server.server_id,
            "version": self.server.version,
        }


@dataclass
class _Execution:
    success: bool
    stdout: str = ""
    stderr: str = ""
    result: str = ""
    raw_error: str = ""


# ---------------------------------------------------------------------------
# HTTP and discovery
# ---------------------------------------------------------------------------


def _new_client(timeout) -> httpx.AsyncClient:
    """Create an HTTP client. Tests replace this with a mock transport."""
    return httpx.AsyncClient(timeout=timeout)


def _auth_headers() -> dict[str, str]:
    token = _skill_config.token.strip()
    if token:
        return {"Authorization": f"Bearer {token}"}
    return {}


def _normalize_server_url(host: str, port: int, base_url: str) -> str:
    host = (host or "").strip()
    if host in ("0.0.0.0", ""):
        host = "127.0.0.1"
    elif host == "::":
        host = "::1"
    netloc = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
    base = "/" + base_url.strip("/") if base_url and base_url.strip("/") else ""
    return f"http://{netloc}{base}"


def _servers_dirs() -> list[Path]:
    configured = _skill_config.servers_dir.strip()
    if configured:
        return [Path(configured).expanduser()]

    dirs: list[Path] = []
    if os.name == "posix":
        state_home = os.environ.get("XDG_STATE_HOME") or "~/.local/state"
        dirs.append(Path(state_home).expanduser() / "marimo" / "servers")
    dirs.append(Path("~/.marimo/servers").expanduser())

    unique: list[Path] = []
    for directory in dirs:
        if directory not in unique:
            unique.append(directory)
    return unique


def _read_registry() -> list[_Server]:
    servers: dict[str, _Server] = {}
    for directory in _servers_dirs():
        try:
            entries = sorted(directory.glob("*.json"))
        except OSError:
            continue
        for path in entries:
            try:
                data = json.loads(path.read_text())
            except (OSError, ValueError) as exc:
                log.debug("[marimo] skipping unreadable registry entry %s: %s", path, exc)
                continue
            if not isinstance(data, dict):
                continue
            host = str(data.get("host") or "")
            try:
                port = int(data.get("port") or 0)
            except (TypeError, ValueError):
                continue
            if port <= 0:
                continue
            base_url = str(data.get("base_url") or "")
            url = _normalize_server_url(host, port, base_url)
            server_id = str(data.get("server_id") or f"{host}:{port}")
            try:
                pid = int(data.get("pid") or 0)
            except (TypeError, ValueError):
                pid = 0
            servers[url] = _Server(
                server_id=server_id,
                url=url,
                host=host,
                port=port,
                pid=pid,
                version=str(data.get("version") or ""),
                started_at=str(data.get("started_at") or ""),
            )

    default_url = _skill_config.default_url.strip().rstrip("/")
    if default_url:
        servers.setdefault(
            default_url,
            _Server(server_id=default_url, url=default_url),
        )
    return list(servers.values())


async def _probe_health(server: _Server) -> bool:
    try:
        async with _new_client(_HEALTH_TIMEOUT) as client:
            resp = await client.get(f"{server.url}/health")
    except Exception as exc:  # noqa: BLE001 — unreachable servers are expected
        log.debug("[marimo] server %s did not answer /health: %s", server.url, exc)
        return False
    if resp.status_code != 200:
        return False
    try:
        payload = resp.json()
    except ValueError:
        return False
    return payload.get("status") == "healthy"


async def discover_servers() -> list[_Server]:
    """Return reachable marimo servers, registry entries plus a configured URL."""
    candidates = _read_registry()
    if not candidates:
        return []
    reachable = await asyncio.gather(*(_probe_health(server) for server in candidates))
    return [server for server, ok in zip(candidates, reachable, strict=True) if ok]


async def list_server_sessions(server: _Server) -> list[_Session]:
    """Fetch the browser sessions for one server."""
    try:
        async with _new_client(httpx.Timeout(_skill_config.request_timeout_sec, connect=5.0)) as client:
            resp = await client.get(f"{server.url}/api/sessions", headers=_auth_headers())
    except httpx.HTTPError as exc:
        raise MarimoError(f"cannot reach marimo server {server.url}: {exc}") from exc

    if resp.status_code in (401, 403):
        raise MarimoError(f"marimo server {server.url} requires a token; set skills.marimo.token or MARIMO_TOKEN")
    if resp.status_code != 200:
        raise MarimoError(f"marimo server {server.url} returned HTTP {resp.status_code}")
    try:
        payload = resp.json()
    except ValueError as exc:
        raise MarimoError(f"marimo server {server.url} returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise MarimoError(f"marimo server {server.url} returned an unexpected session list")

    sessions: list[_Session] = []
    for session_id, info in payload.items():
        if not isinstance(info, dict):
            continue
        sessions.append(
            _Session(
                server=server,
                session_id=str(session_id),
                path=info.get("path") or None,
                filename=info.get("filename") or None,
                notebook_id=info.get("notebook_id") or None,
            )
        )
    return sessions


async def discover_sessions() -> tuple[list[_Session], list[dict]]:
    """Return all sessions and per-server errors."""
    servers = await discover_servers()
    if not servers:
        return [], []
    results = await asyncio.gather(
        *(list_server_sessions(server) for server in servers),
        return_exceptions=True,
    )
    sessions: list[_Session] = []
    errors: list[dict] = []
    for server, result in zip(servers, results, strict=True):
        if isinstance(result, BaseException):
            errors.append({"server_url": server.url, "error": str(result)})
            continue
        sessions.extend(result)
    return sessions, errors


# ---------------------------------------------------------------------------
# Session resolution
# ---------------------------------------------------------------------------


def _file_key_candidates(session: _Session) -> set[str]:
    values: set[str] = set()
    for value in (session.path, session.filename):
        if not value:
            continue
        values.add(value)
        values.add(Path(value).name)
        try:
            values.add(str(Path(value).expanduser().resolve()))
        except OSError:
            pass
    return values


def _normalize_file_target(file: str) -> tuple[str, set[str]]:
    target = file.strip()
    if target.startswith("file://"):
        target = unquote(urlsplit(target).path)
    expanded = Path(target).expanduser()
    names = {target, str(expanded)}
    try:
        names.add(str(expanded.resolve()))
    except OSError:
        pass
    return target, names


def _ambiguity_error(target: str, sessions: list[_Session]) -> MarimoError:
    candidates = [session.summary() | {"reason": f"matches {target!r}"} for session in sessions]
    return MarimoError(
        f"multiple marimo notebooks match {target!r}; pass the file path or session ID",
        data={"ambiguous": True, "candidates": candidates},
    )


def _resolve_session(sessions: list[_Session], file: str | None, url: str | None = None) -> _Session:
    pool = sessions
    if url:
        wanted = url.rstrip("/")
        pool = [session for session in pool if session.server.url == wanted]
        if not pool:
            raise MarimoError(f"no marimo sessions on server {wanted}")

    if not file:
        if len(pool) == 1:
            return pool[0]
        if not pool:
            raise MarimoError("no active marimo sessions found; open the notebook in the marimo UI first")
        raise _ambiguity_error("<unspecified>", pool)

    target = file.strip()
    exact = [session for session in pool if session.session_id == target]
    if len(exact) == 1:
        return exact[0]

    _, names = _normalize_file_target(target)
    matches = [session for session in pool if _file_key_candidates(session) & names]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        candidates = [session.summary() for session in pool]
        raise MarimoError(
            f"no active marimo session matches {target!r}; available sessions are listed in the tool result data",
            data={"candidates": candidates},
        )
    raise _ambiguity_error(target, matches)


async def _resolve(file: str | None, url: str | None = None) -> _Session:
    sessions, errors = await discover_sessions()
    try:
        return _resolve_session(sessions, file, url)
    except MarimoError as exc:
        if errors:
            exc.data = {**exc.data, "server_errors": errors}
        raise


# ---------------------------------------------------------------------------
# Code execution
# ---------------------------------------------------------------------------


def _parse_sse_event(event: str | None, data_lines: list[str]) -> tuple[bool, bool, str, str, str, str]:
    """Handle one SSE record.

    Returns ``(done, success, stdout, stderr, result, raw)``. ``done`` is True
    when the terminal ``done`` event arrived; ``success`` is that event's bit,
    false when the scratch cell or a child cell errored.
    """
    payload_text = "\n".join(data_lines).strip()
    if not payload_text:
        return False, False, "", "", "", ""
    try:
        payload = json.loads(payload_text)
    except ValueError:
        return False, False, "", "", "", payload_text

    if event == "stdout":
        return False, False, str(payload.get("data", "")), "", "", ""
    if event == "stderr":
        return False, False, "", str(payload.get("data", "")), "", ""
    if event == "done":
        output = payload.get("output") or {}
        result = output.get("data", "") if isinstance(output, dict) else ""
        return True, bool(payload.get("success")), "", "", str(result or ""), ""
    return False, False, "", "", "", payload_text


async def execute_code(
    server: _Server,
    session_id: str,
    code: str,
    timeout_sec: float | None = None,
) -> _Execution:
    """Run Python in a marimo session's scratchpad and collect its SSE output."""
    headers = _auth_headers()
    headers["Marimo-Session-Id"] = session_id
    headers["Accept"] = "text/event-stream"
    effective_timeout = _skill_config.execute_timeout_sec if timeout_sec is None else timeout_sec
    timeout = httpx.Timeout(effective_timeout, connect=5.0)

    stdout_parts: list[str] = []
    stderr_parts: list[str] = []
    result = ""
    raw_error = ""
    done = False
    success = False
    event: str | None = None
    data_lines: list[str] = []

    try:
        async with _new_client(timeout) as client:
            async with client.stream(
                "POST",
                f"{server.url}/api/kernel/execute",
                headers=headers,
                json={"code": code},
            ) as resp:
                if resp.status_code != 200:
                    body = (await resp.aread()).decode("utf-8", "replace").strip()
                    raise MarimoError(f"marimo execute failed with HTTP {resp.status_code}: {body[:500]}")
                async for line in resp.aiter_lines():
                    if line == "":
                        finished, record_success, out, err, res, raw = _parse_sse_event(event, data_lines)
                        stdout_parts.append(out)
                        stderr_parts.append(err)
                        if res:
                            result = res
                        if raw:
                            raw_error += raw + "\n"
                        event = None
                        data_lines = []
                        if finished:
                            done = True
                            success = record_success
                            break
                        continue
                    if line.startswith("event:"):
                        event = line[len("event:") :].strip()
                    elif line.startswith("data:"):
                        data_lines.append(line[len("data:") :].lstrip())
                    else:
                        raw_error += line + "\n"
    except httpx.TimeoutException as exc:
        raise MarimoError(
            f"marimo did not finish within {effective_timeout:g}s; "
            "the server interrupts the kernel when the stream disconnects"
        ) from exc
    except httpx.HTTPError as exc:
        raise MarimoError(f"marimo execute request failed: {exc}") from exc

    if not done:
        detail = raw_error.strip()
        raise MarimoError("marimo ended the execute stream without a result" + (f": {detail[:500]}" if detail else ""))

    return _Execution(
        success=success,
        stdout="".join(stdout_parts),
        stderr="".join(stderr_parts),
        result=result,
        raw_error=raw_error.strip(),
    )


def _parse_marker(stdout: str) -> dict | None:
    for line in reversed(stdout.splitlines()):
        if line.startswith(RESULT_MARKER):
            try:
                payload = json.loads(line[len(RESULT_MARKER) :])
            except ValueError:
                return None
            return payload if isinstance(payload, dict) else None
    return None


def _without_marker(stdout: str) -> str:
    return "\n".join(line for line in stdout.splitlines() if not line.startswith(RESULT_MARKER)).strip()


async def _run_operation(
    server: _Server,
    session_id: str,
    code: str,
    timeout_sec: float | None = None,
) -> dict:
    execution = await execute_code(server, session_id, code, timeout_sec=timeout_sec)
    payload = _parse_marker(execution.stdout)
    if payload is None:
        detail = execution.stderr.strip() or execution.raw_error
        raise MarimoError(
            "marimo did not return a structured result" + (f": {detail[:800]}" if detail else ""),
            data={"stdout": execution.stdout, "stderr": execution.stderr},
        )
    payload["success"] = execution.success
    summary = _without_marker(execution.stdout)
    if summary:
        payload["summary"] = summary
    if execution.stderr.strip():
        payload["stderr"] = execution.stderr.strip()
    return payload


# ---------------------------------------------------------------------------
# Generated code-mode snippets
# ---------------------------------------------------------------------------


def _add_cell_code(code: str, run: bool, hide_code: bool, after_cell_id: str | None) -> str:
    return f"""
import json as _decaf_json
import marimo._code_mode as _decaf_cm

_decaf_result = {{"op": "add_cell", "run": {run!r}}}
async with _decaf_cm.get_context() as _decaf_ctx:
    _decaf_kwargs = {{"hide_code": {hide_code!r}}}
    if {after_cell_id!r}:
        _decaf_kwargs["after"] = {after_cell_id!r}
    _decaf_cell_id = _decaf_ctx.create_cell({code!r}, **_decaf_kwargs)
    _decaf_result["cell_id"] = str(_decaf_cell_id)
    if {run!r}:
        _decaf_ctx.run_cell(_decaf_cell_id)
try:
    _decaf_cell = _decaf_ctx.cells[_decaf_result["cell_id"]]
    _decaf_result["code"] = _decaf_cell.code
    _decaf_result["name"] = _decaf_cell.name or None
    _decaf_result["status"] = str(_decaf_cell.status) if _decaf_cell.status is not None else None
    _decaf_result["errors"] = [getattr(_decaf_error, "msg", str(_decaf_error)) for _decaf_error in _decaf_cell.errors]
except Exception as _decaf_exc:
    _decaf_result["detail_error"] = repr(_decaf_exc)
print({RESULT_MARKER!r} + _decaf_json.dumps(_decaf_result))
""".strip()


def _edit_cell_code(cell_id: str, code: str, run: bool) -> str:
    return f"""
import json as _decaf_json
import marimo._code_mode as _decaf_cm

_decaf_result = {{"op": "edit_cell", "cell_id": {cell_id!r}, "run": {run!r}}}
async with _decaf_cm.get_context() as _decaf_ctx:
    _decaf_ctx.cells[{cell_id!r}].code
    _decaf_ctx.edit_cell({cell_id!r}, {code!r})
    if {run!r}:
        _decaf_ctx.run_cell({cell_id!r})
try:
    _decaf_cell = _decaf_ctx.cells[{cell_id!r}]
    _decaf_result["code"] = _decaf_cell.code
    _decaf_result["name"] = _decaf_cell.name or None
    _decaf_result["status"] = str(_decaf_cell.status) if _decaf_cell.status is not None else None
    _decaf_result["errors"] = [getattr(_decaf_error, "msg", str(_decaf_error)) for _decaf_error in _decaf_cell.errors]
except Exception as _decaf_exc:
    _decaf_result["detail_error"] = repr(_decaf_exc)
print({RESULT_MARKER!r} + _decaf_json.dumps(_decaf_result))
""".strip()


def _delete_cell_code(cell_id: str) -> str:
    return f"""
import json as _decaf_json
import marimo._code_mode as _decaf_cm

_decaf_result = {{"op": "delete_cell", "cell_id": {cell_id!r}}}
async with _decaf_cm.get_context() as _decaf_ctx:
    _decaf_ctx.delete_cell({cell_id!r})
_decaf_result["deleted"] = True
print({RESULT_MARKER!r} + _decaf_json.dumps(_decaf_result))
""".strip()


def _inspect_code(include_code: bool, preview_lines: int) -> str:
    return f"""
import json as _decaf_json
import marimo._code_mode as _decaf_cm

_decaf_ctx = _decaf_cm.get_context()
_decaf_cells = []
for _decaf_cell in _decaf_ctx.cells:
    _decaf_source = _decaf_cell.code
    _decaf_lines = _decaf_source.splitlines()
    _decaf_entry = {{
        "cell_id": str(_decaf_cell.id),
        "name": _decaf_cell.name or None,
        "status": str(_decaf_cell.status) if _decaf_cell.status is not None else None,
        "errors": [getattr(_decaf_error, "msg", str(_decaf_error)) for _decaf_error in _decaf_cell.errors],
        "line_count": len(_decaf_lines),
    }}
    if {include_code!r}:
        _decaf_entry["code"] = _decaf_source
    else:
        _decaf_entry["code_preview"] = "\\n".join(_decaf_lines[:{int(preview_lines)}])
    _decaf_cells.append(_decaf_entry)

_decaf_graph = {{}}
for _decaf_cid, _decaf_impl in _decaf_ctx.graph.cells.items():
    _decaf_graph[str(_decaf_cid)] = {{
        "defs": sorted(getattr(_decaf_impl, "defs", set()) or set()),
        "refs": sorted(getattr(_decaf_impl, "refs", set()) or set()),
    }}

_decaf_variables = []
for _decaf_name in sorted(getattr(_decaf_ctx, "globals", {{}}) or {{}}):
    if _decaf_name.startswith("_"):
        continue
    _decaf_variables.append(_decaf_name)

_decaf_statuses = [_decaf_entry["status"] for _decaf_entry in _decaf_cells if _decaf_entry["status"]]
if any(_decaf_entry["errors"] for _decaf_entry in _decaf_cells):
    _decaf_status = "error"
elif "running" in _decaf_statuses:
    _decaf_status = "running"
elif "queued" in _decaf_statuses:
    _decaf_status = "queued"
elif "stale" in _decaf_statuses:
    _decaf_status = "stale"
elif _decaf_statuses:
    _decaf_status = "idle"
else:
    _decaf_status = "empty"

print({RESULT_MARKER!r} + _decaf_json.dumps({{
    "op": "inspect",
    "cell_count": len(_decaf_cells),
    "status": _decaf_status,
    "cells": _decaf_cells,
    "graph": _decaf_graph,
    "variables": _decaf_variables,
}}))
""".strip()


# ---------------------------------------------------------------------------
# ToolResult helpers
# ---------------------------------------------------------------------------


def _error_result(exc: MarimoError) -> ToolResult:
    data = exc.data or None
    return ToolResult(text=f"[error: {exc}]", data=data)


def _preview(text: str, limit: int = 800) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def _operation_text(payload: dict) -> str:
    op = payload.get("op")
    ran = bool(payload.get("run"))
    if op == "add_cell":
        action = "created and ran" if ran else "created"
        text = f"{action} cell {payload.get('cell_id')}"
    elif op == "edit_cell":
        action = "edited and ran" if ran else "edited"
        text = f"{action} cell {payload.get('cell_id')}"
    elif op == "delete_cell":
        text = f"deleted cell {payload.get('cell_id')}"
    elif op == "inspect":
        text = f"{payload.get('cell_count', 0)} cells, status {payload.get('status')}"
    else:
        text = "marimo operation completed"
    status = payload.get("status")
    if status:
        text += f" (status={status})"
    if payload.get("errors"):
        text += f" errors={json.dumps(payload['errors'])}"
    if payload.get("detail_error"):
        text += f" detail_error={payload['detail_error']}"
    return text


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


async def _session_details(session: _Session) -> dict:
    """Return live cell count and aggregate status for one session."""
    try:
        payload = await _run_operation(
            session.server,
            session.session_id,
            _inspect_code(False, 1),
            timeout_sec=min(_skill_config.request_timeout_sec, 15.0),
        )
    except MarimoError as exc:
        log.debug("[marimo] session detail failed for %s: %s", session.session_id, exc)
        return {"cell_count": None, "status": "unknown", "detail_error": str(exc)}
    return {
        "cell_count": payload.get("cell_count"),
        "status": payload.get("status"),
    }


async def tool_marimo_list_sessions(ctx: "Context", include_details: bool = True) -> ToolResult:
    """List running marimo notebooks and their sessions."""
    log.info("[tool:marimo_list_sessions] details=%s", include_details)
    servers = await discover_servers()
    if not servers:
        return ToolResult(
            text=(
                "No running marimo servers found. Start one with "
                "`marimo edit --no-token --headless=false <notebook.py>` and open "
                "the notebook in a browser, or configure skills.marimo.default_url."
            ),
            data={"servers": [], "sessions": []},
        )

    results = await asyncio.gather(
        *(list_server_sessions(server) for server in servers),
        return_exceptions=True,
    )
    sessions: list[_Session] = []
    server_errors: list[dict] = []
    for server, result in zip(servers, results, strict=True):
        if isinstance(result, BaseException):
            server_errors.append({"server_url": server.url, "error": str(result)})
        else:
            sessions.extend(result)

    rows = [session.summary() for session in sessions]
    if include_details and rows:
        details = await asyncio.gather(*(_session_details(session) for session in sessions))
        for row, detail in zip(rows, details, strict=True):
            row.update(detail)

    if not rows:
        text = "No active marimo notebook sessions. Open the notebook in the marimo UI first."
        if server_errors:
            text += " Server errors: " + json.dumps(server_errors)
        return ToolResult(
            text=text,
            data={
                "servers": [asdict(server) for server in servers],
                "sessions": [],
                "server_errors": server_errors,
            },
        )

    lines = []
    for row in rows:
        cells = row.get("cell_count")
        cell_part = f"{cells} cells" if cells is not None else "cell count unavailable"
        lines.append(
            f"- {row['name']} ({row['session_id']}) on {row['server_url']} — "
            f"{cell_part}, status={row.get('status', 'unknown')}"
        )
    text = f"{len(rows)} active marimo session(s):\n" + "\n".join(lines)
    if server_errors:
        text += "\nServer errors: " + json.dumps(server_errors)
    return ToolResult(
        text=text,
        data={
            "servers": [asdict(server) for server in servers],
            "sessions": rows,
            "server_errors": server_errors,
        },
    )


async def tool_marimo_inspect(
    ctx: "Context",
    file: str | None = None,
    include_code: bool = False,
    preview_lines: int = 3,
) -> ToolResult:
    """Inspect a live notebook's cells, status, graph, and variables."""
    log.info("[tool:marimo_inspect] file=%r", file)
    try:
        preview_lines = max(1, min(int(preview_lines), 50))
    except (TypeError, ValueError):
        return ToolResult(text="[error: preview_lines must be an integer]")
    try:
        session = await _resolve(file)
        payload = await _run_operation(
            session.server,
            session.session_id,
            _inspect_code(include_code, preview_lines),
        )
    except MarimoError as exc:
        return _error_result(exc)

    payload.update(session.summary())
    text = (
        f"{session.label}: {payload.get('cell_count', 0)} cells, "
        f"status={payload.get('status')}, variables={len(payload.get('variables', []))}"
    )
    return ToolResult(text=text, data=payload)


async def tool_marimo_add_cell(
    ctx: "Context",
    code: str,
    file: str | None = None,
    run: bool = True,
    hide_code: bool = False,
    after_cell_id: str | None = None,
) -> ToolResult:
    """Append or insert a cell in a live notebook and optionally run it."""
    log.info("[tool:marimo_add_cell] file=%r run=%s", file, run)
    try:
        session = await _resolve(file)
        payload = await _run_operation(
            session.server,
            session.session_id,
            _add_cell_code(code, run, hide_code, after_cell_id),
        )
    except MarimoError as exc:
        return _error_result(exc)

    payload.update({"file": session.path, "name": session.label, "session_id": session.session_id})
    text = f"{_operation_text(payload)} in {session.label}"
    if payload.get("stderr"):
        text += f"\nstderr:\n{_preview(payload['stderr'])}"
    return ToolResult(text=text, data=payload)


async def tool_marimo_edit_cell(
    ctx: "Context",
    cell_id: str,
    code: str,
    file: str | None = None,
    run: bool = True,
) -> ToolResult:
    """Replace a live notebook cell's code and optionally run it."""
    log.info("[tool:marimo_edit_cell] file=%r cell=%r run=%s", file, cell_id, run)
    try:
        session = await _resolve(file)
        payload = await _run_operation(
            session.server,
            session.session_id,
            _edit_cell_code(cell_id, code, run),
        )
    except MarimoError as exc:
        return _error_result(exc)

    payload.update({"file": session.path, "name": session.label, "session_id": session.session_id})
    text = f"{_operation_text(payload)} in {session.label}"
    if payload.get("stderr"):
        text += f"\nstderr:\n{_preview(payload['stderr'])}"
    return ToolResult(text=text, data=payload)


async def tool_marimo_delete_cell(
    ctx: "Context",
    cell_id: str,
    file: str | None = None,
) -> ToolResult:
    """Delete a cell from a live notebook."""
    log.info("[tool:marimo_delete_cell] file=%r cell=%r", file, cell_id)
    try:
        session = await _resolve(file)
        payload = await _run_operation(
            session.server,
            session.session_id,
            _delete_cell_code(cell_id),
        )
    except MarimoError as exc:
        return _error_result(exc)

    payload.update({"file": session.path, "name": session.label, "session_id": session.session_id})
    return ToolResult(text=f"{_operation_text(payload)} in {session.label}", data=payload)


async def tool_marimo_eval(ctx: "Context", code: str, file: str | None = None) -> ToolResult:
    """Evaluate exploratory Python in a live notebook's scratchpad."""
    log.info("[tool:marimo_eval] file=%r", file)
    try:
        session = await _resolve(file)
        execution = await execute_code(session.server, session.session_id, code)
    except MarimoError as exc:
        return _error_result(exc)

    data = {
        "success": execution.success,
        "stdout": execution.stdout,
        "stderr": execution.stderr,
        "result": execution.result,
        "file": session.path,
        "name": session.label,
        "session_id": session.session_id,
    }
    text = execution.stdout.strip()
    if execution.result.strip():
        text += ("\n" if text else "") + "result: " + execution.result.strip()
    if execution.stderr.strip():
        text += ("\n" if text else "") + "stderr: " + execution.stderr.strip()
    if not text:
        text = "(no output)"
    return ToolResult(text=text, data=data)


async def tool_marimo_open_in_canvas(
    ctx: "Context",
    file: str,
    title: str | None = None,
) -> ToolResult:
    """Open a live marimo notebook as a web_frame canvas tab."""
    log.info("[tool:marimo_open_in_canvas] file=%r", file)
    try:
        session = await _resolve(file)
    except MarimoError as exc:
        return _error_result(exc)

    path = session.path or session.filename
    if not path:
        return ToolResult(text=f"[error: notebook {session.label} has no file path]")

    base = (_skill_config.canvas_url or session.server.url).rstrip("/")
    frame_url = f"{base}/?file={quote(path, safe='/')}"
    label = title or session.label

    result = await tool_canvas_new_tab(
        ctx,
        widget_type="web_frame",
        data={"url": frame_url, "title": label},
        label=label,
    )
    if result.data and isinstance(result.data, dict) and result.data.get("tab_id"):
        payload = {
            "tab_id": result.data["tab_id"],
            "url": frame_url,
            "file": path,
            "name": session.label,
            "session_id": session.session_id,
        }
        return ToolResult(
            text=f"opened {session.label} in canvas tab {payload['tab_id']} — {frame_url}",
            data=payload,
        )
    return result


TOOLS: dict[str, Callable[..., Awaitable[ToolResult]]] = {
    "marimo_list_sessions": tool_marimo_list_sessions,
    "marimo_inspect": tool_marimo_inspect,
    "marimo_add_cell": tool_marimo_add_cell,
    "marimo_edit_cell": tool_marimo_edit_cell,
    "marimo_delete_cell": tool_marimo_delete_cell,
    "marimo_eval": tool_marimo_eval,
    "marimo_open_in_canvas": tool_marimo_open_in_canvas,
}


TOOL_DEFINITIONS = [
    {
        "type": "function",
        "priority": "normal",
        "writes": False,
        "function": {
            "name": "marimo_list_sessions",
            "description": (
                "List running marimo notebooks and their browser sessions. Use this first when "
                "pairing with marimo or when a tool reports an ambiguous or missing notebook. "
                "Each row has the file path, relative name, stable session ID, server URL, live "
                "cell count, and aggregate status. Resolve a bare filename like 'smoke_test.py' "
                "to the right session before calling other marimo tools. Set include_details=false "
                "to skip the live cell-count round-trip."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "include_details": {
                        "type": "boolean",
                        "description": (
                            "Fetch live cell count and status from each session. Default true. "
                            "Set false when you only need session IDs and paths."
                        ),
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "priority": "normal",
        "writes": False,
        "timeout": 120,
        "function": {
            "name": "marimo_inspect",
            "description": (
                "Inspect a live marimo notebook: cell IDs, names, code or previews, execution "
                "status, errors, dataflow defs/refs, and public variable names. Use after "
                "marimo_list_sessions to ground edits, and before deleting a cell to see its "
                "dependents. Set include_code=true when you need full cell bodies."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file": {
                        "type": "string",
                        "description": (
                            "Notebook selector: session ID, absolute or relative path, file:// "
                            "URL, or unique filename. Omit when exactly one notebook is open."
                        ),
                    },
                    "include_code": {
                        "type": "boolean",
                        "description": "Return full cell code instead of a short preview. Default false.",
                    },
                    "preview_lines": {
                        "type": "integer",
                        "description": "Lines of code per cell when include_code is false. Default 3, max 50.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "priority": "normal",
        "writes": True,
        "timeout": 240,
        "function": {
            "name": "marimo_add_cell",
            "description": (
                "Add a cell to a live marimo notebook and optionally run it. Appends at the end "
                "unless after_cell_id is given. Returns the new cell ID and its execution status. "
                "marimo validates the cell against the notebook DAG: no cycles, one public name "
                "owner, no wildcard imports. Creating a cell without running it leaves it stale."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "Cell source code. Submit cell contents, not @app.cell wrappers.",
                    },
                    "file": {
                        "type": "string",
                        "description": "Notebook selector; omit when exactly one notebook is open.",
                    },
                    "run": {
                        "type": "boolean",
                        "description": "Execute the new cell and its dependents. Default true.",
                    },
                    "hide_code": {
                        "type": "boolean",
                        "description": "Collapse the code editor for the new cell. Default false (visible).",
                    },
                    "after_cell_id": {
                        "type": "string",
                        "description": "Insert after this cell ID or name instead of appending.",
                    },
                },
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "priority": "normal",
        "writes": True,
        "timeout": 240,
        "function": {
            "name": "marimo_edit_cell",
            "description": (
                "Replace a live marimo cell's code and optionally run it. Reads the current cell "
                "first so marimo's read-before-write guard passes, then submits the full new "
                "body. Returns execution status and errors."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "cell_id": {
                        "type": "string",
                        "description": "Cell ID or cell name from marimo_inspect.",
                    },
                    "code": {
                        "type": "string",
                        "description": "Full replacement cell source code.",
                    },
                    "file": {
                        "type": "string",
                        "description": "Notebook selector; omit when exactly one notebook is open.",
                    },
                    "run": {
                        "type": "boolean",
                        "description": "Execute the edited cell and its dependents. Default true.",
                    },
                },
                "required": ["cell_id", "code"],
            },
        },
    },
    {
        "type": "function",
        "priority": "normal",
        "writes": True,
        "timeout": 120,
        "function": {
            "name": "marimo_delete_cell",
            "description": (
                "Delete a cell from a live marimo notebook. Deleting is destructive and removes "
                "the globals that cell owns. Inspect descendants first when the cell defines "
                "variables other cells use."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "cell_id": {
                        "type": "string",
                        "description": "Cell ID or cell name from marimo_inspect.",
                    },
                    "file": {
                        "type": "string",
                        "description": "Notebook selector; omit when exactly one notebook is open.",
                    },
                },
                "required": ["cell_id"],
            },
        },
    },
    {
        "type": "function",
        "priority": "normal",
        "writes": True,
        "timeout": 240,
        "function": {
            "name": "marimo_eval",
            "description": (
                "Evaluate exploratory Python in a live marimo notebook's scratchpad. Notebook "
                "globals are readable. Top-level assignments are discarded after the call, though "
                "in-place mutations to notebook objects can persist. Returns stdout, stderr, and "
                "the rendered result. Use the structured cell tools to persist notebook changes."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "Python source to run in the scratchpad. Top-level await is supported.",
                    },
                    "file": {
                        "type": "string",
                        "description": "Notebook selector; omit when exactly one notebook is open.",
                    },
                },
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "priority": "normal",
        "writes": True,
        "timeout": 60,
        "function": {
            "name": "marimo_open_in_canvas",
            "description": (
                "Open a running marimo notebook in the conversation's canvas as a web_frame tab. "
                "Resolves the notebook file to a marimo URL in one step. Use when the user asks "
                "to see, show, or pair with the notebook in the UI."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file": {
                        "type": "string",
                        "description": "Notebook selector: session ID, path, file:// URL, or unique filename.",
                    },
                    "title": {
                        "type": "string",
                        "description": "Optional canvas tab and frame title. Defaults to the notebook filename.",
                    },
                },
                "required": ["file"],
            },
        },
    },
]
