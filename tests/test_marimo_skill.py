"""Tests for the native marimo pairing skill.

No marimo install and no real network: a fake ``marimo._code_mode`` module
executes the generated snippets in-process, and an ``httpx.MockTransport``
serves the session/execute endpoints.
"""

import contextlib
import io
import json
import sys
import textwrap
import types

import httpx
import pytest

from decafclaw.media import ToolResult
from decafclaw.skills.marimo import tools

# ---------------------------------------------------------------------------
# Fake marimo._code_mode
# ---------------------------------------------------------------------------


class _FakeCell:
    def __init__(self, notebook, cell_id, code, status="idle", name=None, errors=None):
        self._notebook = notebook
        self._id = cell_id
        self._code = code
        self._status = status
        self._name = name
        self._errors = errors or []

    @property
    def id(self):
        return self._id

    @property
    def code(self):
        self._notebook.reads.add(self._id)
        return self._code

    @property
    def name(self):
        return self._name

    @property
    def status(self):
        return self._status

    @property
    def errors(self):
        return list(self._errors)


class _FakeGraph:
    def __init__(self):
        self.cells = {}


class _FakeCellsView:
    def __init__(self, notebook):
        self._notebook = notebook

    def __getitem__(self, key):
        return self._notebook._cells[key]

    def __iter__(self):
        return iter(list(self._notebook._cells.values()))

    def __contains__(self, key):
        return key in self._notebook._cells

    def keys(self):
        return list(self._notebook._cells.keys())


class _FakeNotebook:
    """Minimal stand-in for marimo's ``AsyncCodeModeContext``."""

    def __init__(self):
        self._cells = {}
        self.reads = set()
        self.creates = []
        self.edits = []
        self.deletes = []
        self.runs = []
        self.graph = _FakeGraph()
        self.globals = {"df": object(), "_private": object()}
        self._cells_view = _FakeCellsView(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if exc_type is None:
            for target in self.runs:
                cell = self._cells.get(target)
                if cell is not None:
                    cell._status = "idle"
        return False

    @property
    def cells(self):
        return self._cells_view

    def create_cell(self, code, **kwargs):
        cell_id = f"cell_{len(self._cells) + 1}"
        cell = _FakeCell(self, cell_id, code)
        self._cells[cell_id] = cell
        self.graph.cells[cell_id] = types.SimpleNamespace(
            defs={"x"} if "x =" in code else set(),
            refs={"df"} if "df" in code else set(),
        )
        self.creates.append((code, kwargs))
        return cell_id

    def edit_cell(self, target, code):
        if target not in self.reads:
            raise AssertionError("edit_cell called before the cell was read")
        cell = self._cells[target]
        cell._code = code
        cell._status = "stale"
        self.edits.append((target, code))

    def delete_cell(self, target):
        self._cells.pop(target)
        self.graph.cells.pop(target, None)
        self.deletes.append(target)

    def run_cell(self, target):
        self.runs.append(target)


class _FakeCodeMode(types.ModuleType):
    def __init__(self, notebook):
        super().__init__("marimo._code_mode")
        self.notebook = notebook
        self.get_context = lambda: notebook


@pytest.fixture
def fake_marimo(monkeypatch):
    notebook = _FakeNotebook()
    marimo = types.ModuleType("marimo")
    marimo.__path__ = []
    code_mode = _FakeCodeMode(notebook)
    monkeypatch.setitem(sys.modules, "marimo", marimo)
    monkeypatch.setitem(sys.modules, "marimo._code_mode", code_mode)
    return notebook


async def _run_generated(code):
    """Execute a generated top-level-await snippet and return stdout."""
    namespace = {"__name__": "test_marimo_snippet"}
    wrapped = "async def _run():\n" + textwrap.indent(code, "    ")
    exec(wrapped, namespace)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        await namespace["_run"]()
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Fake marimo HTTP server
# ---------------------------------------------------------------------------


class _Transport(httpx.AsyncBaseTransport):
    def __init__(self, handler):
        self.handler = handler
        self.requests = []

    async def handle_async_request(self, request):
        body = await request.aread()
        self.requests.append((request, body))
        status, headers, content = self.handler(request, body)
        return httpx.Response(status, headers=headers, content=content, request=request)


@pytest.fixture
def install_client(monkeypatch):
    def _install(handler):
        transport = _Transport(handler)
        monkeypatch.setattr(
            tools,
            "_new_client",
            lambda timeout: httpx.AsyncClient(transport=transport),
        )
        return transport

    return _install


def _sse(*events):
    parts = []
    for event, payload in events:
        parts.append(f"event: {event}\ndata: {json.dumps(payload)}\n\n")
    return "".join(parts).encode()


def _marker(payload):
    return tools.RESULT_MARKER + json.dumps(payload)


def _session(path="/tmp/notebooks/smoke_test.py", session_id="s_test", url="http://marimo.test"):
    server = tools._Server(
        server_id="127.0.0.1:2718",
        url=url,
        host="127.0.0.1",
        port=2718,
        pid=4242,
        version="0.13.0",
        started_at="2026-11-19T00:00:00Z",
    )
    return tools._Session(
        server=server,
        session_id=session_id,
        path=path,
        filename=path.rsplit("/", 1)[-1],
        notebook_id="nb_1",
    )


@pytest.fixture
def session():
    tools.init(None, tools.SkillConfig())
    return _session()


@pytest.fixture
def resolve_to(monkeypatch, session):
    async def _resolve(file=None, url=None):
        return session

    monkeypatch.setattr(tools, "_resolve", _resolve)
    return session


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_discover_servers_reads_registry_and_probes_health(tmp_path, monkeypatch):
    servers_dir = tmp_path / "servers"
    servers_dir.mkdir()
    (servers_dir / "127.0.0.1_2718.json").write_text(
        json.dumps(
            {
                "server_id": "127.0.0.1:2718",
                "pid": 1,
                "host": "127.0.0.1",
                "port": 2718,
                "base_url": "",
                "started_at": "2026-11-19T00:00:00Z",
                "version": "0.13.0",
            }
        )
    )
    (servers_dir / "0.0.0.0_2719.json").write_text(
        json.dumps(
            {
                "server_id": "0.0.0.0:2719",
                "pid": 2,
                "host": "0.0.0.0",
                "port": 2719,
                "base_url": "/prefix",
                "version": "0.13.0",
            }
        )
    )
    (servers_dir / "dead.json").write_text(json.dumps({"server_id": "dead", "host": "127.0.0.1", "port": 2720}))

    tools.init(None, tools.SkillConfig(servers_dir=str(servers_dir)))

    async def fake_probe(server):
        return server.port != 2720

    monkeypatch.setattr(tools, "_probe_health", fake_probe)

    servers = await tools.discover_servers()
    by_port = {server.port: server for server in servers}
    assert set(by_port) == {2718, 2719}
    assert by_port[2718].url == "http://127.0.0.1:2718"
    assert by_port[2719].url == "http://127.0.0.1:2719/prefix"


@pytest.mark.asyncio
async def test_discover_servers_includes_configured_url(tmp_path, monkeypatch):
    tools.init(
        None,
        tools.SkillConfig(
            servers_dir=str(tmp_path / "empty"),
            default_url="http://decafclaw:2718/",
        ),
    )
    monkeypatch.setattr(tools, "_probe_health", lambda server: _true())

    async def _true():
        return True

    servers = await tools.discover_servers()
    assert [server.url for server in servers] == ["http://decafclaw:2718"]


# ---------------------------------------------------------------------------
# Session resolution
# ---------------------------------------------------------------------------


def test_resolve_session_accepts_session_id_path_and_filename():
    first = _session(path="/tmp/a/one.py", session_id="s_one")
    second = _session(path="/tmp/b/two.py", session_id="s_two")

    assert tools._resolve_session([first, second], "s_two") is second
    assert tools._resolve_session([first, second], "/tmp/a/one.py") is first
    assert tools._resolve_session([first, second], "one.py") is first


def test_resolve_session_file_url_and_single_default():
    only = _session(path="/tmp/a/one.py", session_id="s_one")
    assert tools._resolve_session([only], "file:///tmp/a/one.py") is only
    assert tools._resolve_session([only], None) is only


def test_resolve_session_ambiguous_filename_lists_candidates():
    first = _session(path="/tmp/a/smoke.py", session_id="s_one")
    second = _session(path="/tmp/b/smoke.py", session_id="s_two")
    with pytest.raises(tools.MarimoError) as excinfo:
        tools._resolve_session([first, second], "smoke.py")
    assert excinfo.value.data["ambiguous"] is True
    assert {row["session_id"] for row in excinfo.value.data["candidates"]} == {"s_one", "s_two"}


def test_resolve_session_unknown_file_lists_available():
    only = _session(path="/tmp/a/one.py", session_id="s_one")
    with pytest.raises(tools.MarimoError) as excinfo:
        tools._resolve_session([only], "missing.py")
    assert excinfo.value.data["candidates"][0]["session_id"] == "s_one"


# ---------------------------------------------------------------------------
# Generated snippets (behavior against fake marimo)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_cell_snippet_creates_and_runs(fake_marimo):
    stdout = await _run_generated(tools._add_cell_code("x = df.head()", True, False, None))
    result = tools._parse_marker(stdout)

    assert result["op"] == "add_cell"
    assert result["cell_id"] == "cell_1"
    assert result["status"] == "idle"
    assert fake_marimo.creates == [("x = df.head()", {"hide_code": False})]
    assert fake_marimo.runs == ["cell_1"]
    assert result["name"] is None


@pytest.mark.asyncio
async def test_add_cell_snippet_honors_after_and_hide_code(fake_marimo):
    first = fake_marimo.create_cell("x = 1")
    stdout = await _run_generated(tools._add_cell_code("y = x + 1", False, True, first))
    result = tools._parse_marker(stdout)

    assert result["run"] is False
    assert fake_marimo.creates[-1] == ("y = x + 1", {"hide_code": True, "after": first})
    assert fake_marimo.runs == []


@pytest.mark.asyncio
async def test_edit_cell_snippet_reads_before_writing(fake_marimo):
    cell_id = fake_marimo.create_cell("x = 1")
    stdout = await _run_generated(tools._edit_cell_code(cell_id, "x = 2", True))
    result = tools._parse_marker(stdout)

    assert result["op"] == "edit_cell"
    assert result["cell_id"] == cell_id
    assert result["run"] is True
    assert fake_marimo.edits == [(cell_id, "x = 2")]
    assert fake_marimo.runs == [cell_id]
    assert fake_marimo.cells[cell_id].status == "idle"


@pytest.mark.asyncio
async def test_delete_cell_snippet_deletes(fake_marimo):
    cell_id = fake_marimo.create_cell("x = 1")
    stdout = await _run_generated(tools._delete_cell_code(cell_id))
    result = tools._parse_marker(stdout)

    assert result["deleted"] is True
    assert fake_marimo.deletes == [cell_id]
    assert cell_id not in fake_marimo.cells


@pytest.mark.asyncio
async def test_inspect_snippet_reports_cells_graph_and_variables(fake_marimo):
    first = fake_marimo.create_cell("x = df.head()")
    second = fake_marimo.create_cell("y = x + 1")
    fake_marimo.cells[second]._status = "exception"
    fake_marimo.cells[second]._errors = [types.SimpleNamespace(msg="NameError: y")]

    stdout = await _run_generated(tools._inspect_code(False, 2))
    result = tools._parse_marker(stdout)

    assert result["cell_count"] == 2
    assert result["status"] == "error"
    assert [cell["cell_id"] for cell in result["cells"]] == [first, second]
    assert result["cells"][0]["code_preview"] == "x = df.head()"
    assert result["cells"][1]["errors"] == ["NameError: y"]
    assert result["graph"][first] == {"defs": ["x"], "refs": ["df"]}
    assert result["variables"] == ["df"]


@pytest.mark.asyncio
async def test_inspect_snippet_can_return_full_code(fake_marimo):
    fake_marimo.create_cell("x = 1\ny = 2")
    stdout = await _run_generated(tools._inspect_code(True, 1))
    result = tools._parse_marker(stdout)
    assert result["cells"][0]["code"] == "x = 1\ny = 2"


# ---------------------------------------------------------------------------
# Tool plumbing and SSE parsing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_cell_tool_posts_execute_and_parses_marker(install_client, resolve_to):
    seen = {}

    def handler(request, body):
        seen["path"] = request.url.path
        seen["session"] = request.headers.get("Marimo-Session-Id")
        seen["code"] = json.loads(body)["code"]
        return (
            200,
            {"content-type": "text/event-stream"},
            _sse(
                ("stdout", {"data": "created and ran cell 'cell_1'\n"}),
                (
                    "stdout",
                    {"data": _marker({"op": "add_cell", "cell_id": "cell_1", "run": True, "status": "idle"}) + "\n"},
                ),
                ("done", {"success": True, "output": {"data": ""}}),
            ),
        )

    install_client(handler)
    result = await tools.tool_marimo_add_cell(None, "x = 1", file="smoke_test.py")

    assert seen["path"] == "/api/kernel/execute"
    assert seen["session"] == "s_test"
    assert "create_cell('x = 1'" in seen["code"]
    assert result.data["cell_id"] == "cell_1"
    assert result.data["status"] == "idle"
    assert result.data["session_id"] == "s_test"
    assert "created and ran cell cell_1" in result.text


@pytest.mark.asyncio
async def test_execute_code_marks_child_error_unsuccessful(install_client, session):
    def handler(request, body):
        return (
            200,
            {"content-type": "text/event-stream"},
            _sse(
                ("stderr", {"data": "NameError: missing\n"}),
                ("done", {"success": False, "output": {"data": ""}}),
            ),
        )

    install_client(handler)
    execution = await tools.execute_code(session.server, session.session_id, "x = 1")

    assert execution.success is False
    assert execution.stderr == "NameError: missing\n"


@pytest.mark.asyncio
async def test_execute_code_rejects_http_error(install_client, session):
    def handler(request, body):
        return 401, {"content-type": "application/json"}, b'{"detail":"unauthorized"}'

    install_client(handler)
    with pytest.raises(tools.MarimoError) as excinfo:
        await tools.execute_code(session.server, session.session_id, "x = 1")
    assert "HTTP 401" in str(excinfo.value)


@pytest.mark.asyncio
async def test_ambiguous_tool_result_includes_candidates(monkeypatch):
    first = _session(path="/tmp/a/smoke.py", session_id="s_one")
    second = _session(path="/tmp/b/smoke.py", session_id="s_two")

    async def fake_discover():
        return [first, second], []

    monkeypatch.setattr(tools, "discover_sessions", fake_discover)
    result = await tools.tool_marimo_add_cell(None, "x = 1", file="smoke.py")

    assert result.text.startswith("[error: multiple marimo notebooks match")
    assert result.data["ambiguous"] is True
    assert {row["session_id"] for row in result.data["candidates"]} == {"s_one", "s_two"}


@pytest.mark.asyncio
async def test_list_sessions_without_details(install_client, monkeypatch):
    server = _session().server

    async def fake_servers():
        return [server]

    def handler(request, body):
        if request.url.path == "/api/sessions":
            return (
                200,
                {"content-type": "application/json"},
                json.dumps(
                    {
                        "s_test": {
                            "session_id": "s_test",
                            "notebook_id": "nb_1",
                            "filename": "smoke_test.py",
                            "path": "/tmp/notebooks/smoke_test.py",
                        }
                    }
                ).encode(),
            )
        raise AssertionError(f"unexpected request {request.url}")

    monkeypatch.setattr(tools, "discover_servers", fake_servers)
    install_client(handler)
    result = await tools.tool_marimo_list_sessions(None, include_details=False)

    assert result.data["sessions"][0]["name"] == "smoke_test.py"
    assert result.data["sessions"][0]["session_id"] == "s_test"
    assert "cell_count" not in result.data["sessions"][0]
    assert "1 active marimo session" in result.text


@pytest.mark.asyncio
async def test_list_sessions_with_details(install_client, monkeypatch):
    server = _session().server

    async def fake_servers():
        return [server]

    def handler(request, body):
        if request.url.path == "/api/sessions":
            return (
                200,
                {"content-type": "application/json"},
                json.dumps(
                    {
                        "s_test": {
                            "session_id": "s_test",
                            "filename": "smoke_test.py",
                            "path": "/tmp/notebooks/smoke_test.py",
                        }
                    }
                ).encode(),
            )
        if request.url.path == "/api/kernel/execute":
            return (
                200,
                {"content-type": "text/event-stream"},
                _sse(
                    ("stdout", {"data": _marker({"op": "inspect", "cell_count": 4, "status": "idle"}) + "\n"}),
                    ("done", {"success": True, "output": {"data": ""}}),
                ),
            )
        raise AssertionError(f"unexpected request {request.url}")

    monkeypatch.setattr(tools, "discover_servers", fake_servers)
    install_client(handler)
    result = await tools.tool_marimo_list_sessions(None)

    assert result.data["sessions"][0]["cell_count"] == 4
    assert result.data["sessions"][0]["status"] == "idle"
    assert "4 cells" in result.text


@pytest.mark.asyncio
async def test_list_sessions_auth_header(install_client, monkeypatch):
    tools.init(None, tools.SkillConfig(token="secret-token"))
    server = _session().server
    seen = {}

    async def fake_servers():
        return [server]

    def handler(request, body):
        seen["auth"] = request.headers.get("Authorization")
        return 200, {"content-type": "application/json"}, b"{}"

    monkeypatch.setattr(tools, "discover_servers", fake_servers)
    install_client(handler)
    await tools.tool_marimo_list_sessions(None, include_details=False)

    assert seen["auth"] == "Bearer secret-token"


@pytest.mark.asyncio
async def test_eval_tool_returns_stdout_stderr_and_result(install_client, resolve_to):
    def handler(request, body):
        return (
            200,
            {"content-type": "text/event-stream"},
            _sse(
                ("stdout", {"data": "hello\n"}),
                ("stderr", {"data": "warn\n"}),
                ("done", {"success": True, "output": {"data": "42"}}),
            ),
        )

    install_client(handler)
    result = await tools.tool_marimo_eval(None, "print('hello')")

    assert result.data["stdout"] == "hello\n"
    assert result.data["stderr"] == "warn\n"
    assert result.data["result"] == "42"
    assert "result: 42" in result.text


@pytest.mark.asyncio
async def test_open_in_canvas_builds_web_frame(monkeypatch, session):
    tools.init(None, tools.SkillConfig(canvas_url="http://decafclaw:2718"))
    calls = []

    async def fake_canvas(ctx, **kwargs):
        calls.append(kwargs)
        return ToolResult(text="ok", data={"tab_id": "canvas_1"})

    async def _resolve(file=None, url=None):
        return session

    monkeypatch.setattr(tools, "_resolve", _resolve)
    monkeypatch.setattr(tools, "tool_canvas_new_tab", fake_canvas)

    result = await tools.tool_marimo_open_in_canvas(None, "smoke_test.py", title="Smoke")

    assert calls[0]["widget_type"] == "web_frame"
    assert calls[0]["data"]["url"] == "http://decafclaw:2718/?file=/tmp/notebooks/smoke_test.py"
    assert calls[0]["data"]["title"] == "Smoke"
    assert calls[0]["label"] == "Smoke"
    assert result.data["tab_id"] == "canvas_1"
    assert result.data["url"].startswith("http://decafclaw:2718/?file=")


# ---------------------------------------------------------------------------
# Config and discovery helpers
# ---------------------------------------------------------------------------


def test_config_env_aliases(monkeypatch):
    from decafclaw.config import load_sub_config

    monkeypatch.setenv("MARIMO_URL", "http://marimo.test:2718")
    monkeypatch.setenv("MARIMO_TOKEN", "tok")
    cfg = load_sub_config(tools.SkillConfig, {}, "SKILLS_MARIMO")
    assert cfg.default_url == "http://marimo.test:2718"
    assert cfg.token == "tok"


def test_servers_dirs_uses_xdg_state(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    tools.init(None, tools.SkillConfig())
    dirs = tools._servers_dirs()
    assert tmp_path / "marimo" / "servers" in dirs


def test_server_url_normalizes_bind_all_and_ipv6():
    assert tools._normalize_server_url("0.0.0.0", 2718, "") == "http://127.0.0.1:2718"
    assert tools._normalize_server_url("::", 2718, "/base/") == "http://[::1]:2718/base"
    assert tools._normalize_server_url("", 2718, "") == "http://127.0.0.1:2718"
