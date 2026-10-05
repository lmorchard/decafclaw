"""Exercise current backend types and browser output in disposable source trees."""

import asyncio
import collections
import contextlib
import dataclasses
import fcntl
import json
import os
import pathlib
import re
import shutil
import socket
import subprocess
import threading

import pytest
import uvicorn
from playwright.sync_api import Browser, Page, sync_playwright
from starlette.staticfiles import StaticFiles

from decafclaw import notifications as notifs
from decafclaw.archive import append_message, archive_path
from decafclaw.config_types import ModelConfig
from decafclaw.context_composer import write_context_sidecar
from decafclaw.events import EventBus
from decafclaw.http_server import create_app
from decafclaw.sticky import write_sticky_state
from decafclaw.web.auth import create_token
from decafclaw.web.conversations import ConversationIndex
from decafclaw.widgets import init_widgets

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
STATIC_REL = pathlib.Path("src/decafclaw/web/static")
CLIENT_REL = STATIC_REL / "lib/api-client"


def copy_source_tree(root):
    shutil.copytree(REPO_ROOT, root, ignore=shutil.ignore_patterns(
        ".git", ".claude", ".codex", ".venv", ".env", "node_modules", "__pycache__", ".pytest_cache", ".ruff_cache", "data",
    ))
    # Dependencies are already installed by the normal check/test setup. Share
    # only those, never generated files or the backend source under mutation.
    (root / STATIC_REL / "node_modules").symlink_to(REPO_ROOT / STATIC_REL / "node_modules", target_is_directory=True)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "src/decafclaw/web/message_types.py",
                    "src/decafclaw/web/static/lib/message-types.js", "docs/websocket-messages.md",
                    "tui/src/types.generated.ts"], cwd=root, check=True)
    return root


@pytest.fixture
def source_tree(tmp_path):
    return copy_source_tree(tmp_path / "source")


def run_make(root, *targets):
    env = dict(os.environ, PYTHONPATH=str(root / "src"), UV_NO_SYNC="1",
               UV_PROJECT_ENVIRONMENT=str(REPO_ROOT / ".venv"))
    # Do not reinstall into the shared dependency directory. Every other
    # prerequisite (including generation) runs through the real Makefile.
    result = subprocess.run(["make", "-o", "install-js", *targets], cwd=root,
                            env=env, capture_output=True, text=True, timeout=180)
    return result, result.stdout + result.stderr


def test_missing_output_is_rebuilt_by_project_check(source_tree):
    shutil.rmtree(source_tree / CLIENT_REL)
    result, output = run_make(source_tree, "check")
    assert result.returncode == 0, output
    for relative in ("index.ts", "core/request.ts", "services/DefaultService.ts", "index.js"):
        assert (source_tree / CLIENT_REL / relative).is_file(), relative



def test_browser_asset_gate_rejects_missing_module(source_tree):
    (source_tree / STATIC_REL / "lib/auth-client.js").unlink()
    result, output = run_make(source_tree, "check-browser-assets")
    assert result.returncode != 0, output
    assert "auth-client.js" in output and "no such file" in output, output


def test_backend_response_drift_fails_at_unchanged_caller(source_tree):
    result, output = run_make(source_tree, "check-js")
    assert result.returncode == 0, output
    backend = source_tree / "src/decafclaw/http_server.py"
    original = backend.read_text()
    changed = original.replace("class UserResponse(BaseModel):\n    username:",
                               "class UserResponse(BaseModel):\n    renamed_username:")
    assert changed != original
    backend.write_text(changed)
    # Leave stale generated types and JS in place: check-js must regenerate.
    result, output = run_make(source_tree, "check-js")
    assert result.returncode != 0, output
    assert "auth-client.js" in output and "TS2339" in output, output
    assert "Property 'username' does not exist on type 'UserResponse'" in output, output
    assert "renamed_username" in (source_tree / CLIENT_REL / "models/UserResponse.ts").read_text()


@pytest.mark.parametrize("contract", ["identifier", "response"])
def test_sticky_contract_drift_fails_at_unchanged_caller(source_tree, contract):
    result, output = run_make(source_tree, "check-js")
    assert result.returncode == 0, output
    caller = source_tree / STATIC_REL / "lib/sticky-state.js"
    original_caller = caller.read_bytes()
    backend = source_tree / "src/decafclaw/http_server.py"
    original = backend.read_text()
    if contract == "identifier":
        changed = original.replace(
            "async def get_sticky_state(request: Request, conv_id: str)",
            "async def get_sticky_state(request: Request, conv_id: int)",
        )
        diagnostic = "Argument of type 'string' is not assignable to parameter of type 'number'"
        code = "TS2345"
    else:
        changed = original.replace(
            "class StickyResponse(BaseModel):\n    widget_type:",
            "class StickyResponse(BaseModel):\n    renamed_widget_type:",
        )
        diagnostic = "Property 'widget_type' does not exist on type 'StickyResponse'"
        code = "TS2339"
    assert changed != original
    backend.write_text(changed)
    result, output = run_make(source_tree, "check-js")
    assert result.returncode != 0, output
    # An install or generation failure, or an error elsewhere, is not evidence.
    diagnostics = [line for line in output.splitlines() if "error TS" in line]
    assert len(diagnostics) == 1, output
    assert "sticky-state.js(" in diagnostics[0] and code in diagnostics[0], output
    assert diagnostic in diagnostics[0], output
    assert caller.read_bytes() == original_caller


# ---------------------------------------------------------------------------
# Generated-client browser scenarios
#
# Each scenario starts its own app and server against its own temporary
# config, and uses its own browser context, so no scenario can observe
# cookies, storage, pages, or pending work from another. All scenarios share
# one clean generated-client build per test run (built_static) and one
# Chromium process per xdist worker (chromium_process).
#
# The fixtures enforce these setup limits. A change that adds a build, a
# Chromium launch, a shared or leaked context, or a second server in one
# scenario fails the tests.
# ---------------------------------------------------------------------------

# Per worker process: Chromium launches, and server starts per test node id.
_chromium_launches = 0
_server_starts = collections.Counter()


@pytest.fixture(scope="session")
def built_static(tmp_path_factory, worker_id):
    """Static assets from one clean generated-client build, shared read-only.

    Deletes the checked-in client output, then runs the real generation and
    browser-asset checks. xdist workers in one run share the build through a
    lock in the run's common temp directory; the first worker builds it.
    """
    shared = tmp_path_factory.getbasetemp()
    if worker_id != "master":
        shared = shared.parent
    build = shared / "built-client"
    with open(shared / "built-client.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not (build / "complete").exists():
            if build.exists():
                shutil.rmtree(build)  # An earlier worker's failed attempt.
            root = copy_source_tree(build / "source")
            shutil.rmtree(root / CLIENT_REL)
            result, output = run_make(root, "gen-api-client", "check-browser-assets")
            assert result.returncode == 0, output
            static = root / STATIC_REL
            assert (static / "lib/api-client/index.js").is_file()
            # Scenarios only serve and read this tree. Drop the link to the
            # shared dependency directory and make the rest read-only.
            (static / "node_modules").unlink()
            for directory, _dirnames, filenames in os.walk(static):
                for name in filenames:
                    os.chmod(os.path.join(directory, name), 0o444)
                os.chmod(directory, 0o555)
            (build / "complete").touch()
            with open(shared / "built-client.builds", "a") as builds:
                builds.write(f"{worker_id}\n")
    builders = (shared / "built-client.builds").read_text().split()
    assert len(builders) == 1, f"generated client built more than once in this run: {builders}"
    return build / "source" / STATIC_REL


@pytest.fixture(scope="session")
def chromium_process():
    """One Chromium process for every browser scenario in this worker.

    Playwright's sync API marks its own event loop as running in this thread
    after every call and does not clear the mark. While this session fixture
    stays open, every later async test in the worker would then fail with
    "Runner.run() cannot be called from a running event loop" (#941). So the
    mark is cleared here, and the ``chromium`` fixture sets it only for the
    duration of each browser test.
    """
    global _chromium_launches
    _chromium_launches += 1
    assert _chromium_launches == 1, "Chromium launched more than once in this worker"
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        playwright_loop = asyncio._get_running_loop()
        asyncio._set_running_loop(None)
        try:
            yield browser, playwright_loop
        finally:
            asyncio._set_running_loop(playwright_loop)
            browser.close()


@pytest.fixture
def chromium(chromium_process):
    """The worker's shared browser, with Playwright's loop marked running only during the test."""
    browser, playwright_loop = chromium_process
    asyncio._set_running_loop(playwright_loop)
    try:
        yield browser
    finally:
        asyncio._set_running_loop(None)


@dataclasses.dataclass
class RecordedRequest:
    method: str
    url_path: str  # request.url.path: truncated at a decoded "#" or "?".
    path: str  # The full decoded route path.
    raw_path: str
    query: list
    body: bytes
    content_type: str | None
    authenticated: bool

    def json(self):
        return json.loads(self.body) if self.body else None


@dataclasses.dataclass
class BrowserScenario:
    page: Page
    base: str
    static: pathlib.Path
    requests: list[RecordedRequest]
    errors: list[str]
    failed_requests: list[str]


@contextlib.contextmanager
def browser_scenario(static, browser: Browser, config):
    """Serve the real app from ``static`` and log a fresh browser context in.

    Yields a page in a new context of the shared ``browser``, on a same-origin
    host document that carries the app's own import map, holding an
    authentication cookie from the generated login. The context is closed,
    and the server stopped, before the next scenario can start.
    Every /api request reaching the server is recorded. On a normal exit,
    the scenario must have no page errors or failed requests left; scenarios
    assert and clear the failures that they expect.
    """
    test = os.environ["PYTEST_CURRENT_TEST"].rsplit(" (", 1)[0]
    _server_starts[test] += 1
    assert _server_starts[test] == 1, f"{test} started more than one application server"
    config.http.secret = "isolated-browser-test-secret"
    config.agent_path.mkdir(parents=True, exist_ok=True)
    token = create_token(config, "browser-user")
    init_widgets(config)
    app = create_app(config, EventBus())
    for route in app.routes:
        if getattr(route, "path", None) == "/static":
            route.app = StaticFiles(directory=static)
    requests = []

    @app.middleware("http")
    async def record_request(request, call_next):
        if request.url.path.startswith("/api/"):
            requests.append(RecordedRequest(
                method=request.method, url_path=request.url.path,
                path=request.scope["path"], raw_path=request.scope["raw_path"].decode(),
                query=list(request.query_params.multi_items()), body=await request.body(),
                content_type=request.headers.get("content-type"),
                authenticated=bool(request.cookies),
            ))
        return await call_next(request)

    # Bind before starting the server, avoiding a free-port check/use race.
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    ready = threading.Event()

    class Server(uvicorn.Server):
        async def startup(self, sockets=None):
            await super().startup(sockets=sockets)
            ready.set()

    server = Server(uvicorn.Config(app, log_level="error", ws="none"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        assert ready.wait(15), "test server failed to start"
        base = f"http://127.0.0.1:{sock.getsockname()[1]}"
        assert not browser.contexts, f"browser contexts left by an earlier scenario: {browser.contexts}"
        context = browser.new_context()
        try:
            page = context.new_page()
            errors = []
            failed_requests = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("requestfailed", lambda request: failed_requests.append(request.url))
            page.on("response", lambda response: failed_requests.append(response.url) if response.status >= 400 else None)
            # A same-origin document without the full app's unrelated services.
            page.goto(base + "/static/lib/auth-client.js")
            result = page.evaluate("""async (token) => {
                const { AuthClient } = await import('/static/lib/auth-client.js');
                const client = new AuthClient();
                await client.login(token);
                return { username: await client.checkSession(), currentUser: client.currentUser };
            }""", token)
            assert result == {"username": "browser-user", "currentUser": "browser-user"}
            index = (static / "index.html").read_text()
            import_map = re.search(r'<script type="importmap">(.*?)</script>', index, re.DOTALL)
            assert import_map, "index.html has no import map"
            page.add_script_tag(type="importmap", content=import_map.group(1))
            requests.clear()
            yield BrowserScenario(page, base, static, requests, errors, failed_requests)
            assert not errors, errors
            assert not failed_requests, failed_requests
            assert browser.contexts == [context], browser.contexts
            assert context.pages == [page], context.pages
        finally:
            context.close()
    finally:
        server.should_exit = True
        thread.join(timeout=15)
        sock.close()
        assert not thread.is_alive(), "test server did not stop"


VAULT_PAGE_NAME = "agent/pages/Browser & 日本語"
VAULT_PAGE_URL_PATH = "/vault/agent/pages/Browser%20%26%20%E6%97%A5%E6%9C%AC%E8%AA%9E"


def seed_conversation(config, title):
    config.agent_path.mkdir(parents=True, exist_ok=True)
    return ConversationIndex(config).create("browser-user", title=title).conv_id


def seed_vault_page(config):
    vault_page_path = config.vault_root / f"{VAULT_PAGE_NAME}.md"
    vault_page_path.parent.mkdir(parents=True, exist_ok=True)
    vault_page_path.write_text(
        "---\nsummary: Browser summary\ntags: [BrowserTag]\n"
        "nested:\n  rows: [1, true, null]\n---\n# Browser vault body\n",
    )
    return vault_page_path


def open_vault_page(scenario):
    """Load the real standalone vault page and wait for its guard and page load."""
    page = scenario.page
    page.evaluate("localStorage.setItem('wiki-edit-mode', 'false')")
    with page.expect_response("**/api/auth/me") as guard_response:
        page.goto(scenario.base + VAULT_PAGE_URL_PATH)
    assert guard_response.value.status == 200
    page.wait_for_function("document.querySelector('wiki-page')._loaded")


def expect_window_event(page, name, flag):
    """Arm a one-shot window listener; wait on ``window[flag] === true`` later."""
    page.evaluate("""([name, flag]) => {
        window[flag] = false;
        window.addEventListener(name, () => { window[flag] = true; }, { once: true });
    }""", [name, flag])


def seed_schedule_models(config):
    config.model_configs = {
        "browser-model": ModelConfig(provider="browser", model="browser-model"),
    }
    config.default_model = "browser-model"


@pytest.fixture
def fake_schedule_runs(monkeypatch):
    async def fake_run_schedule_task(*_args, **_kwargs):
        return {"is_ok": True}

    monkeypatch.setattr("decafclaw.http_server.run_schedule_task", fake_run_schedule_task)


def test_browser_workspace_reads(built_static, chromium, config):
    workspace_folder = "Browser files/日本語 #?"
    workspace_rel_path = f"{workspace_folder}/Browser note #?.md"
    workspace_path = config.workspace_path / workspace_rel_path
    workspace_path.parent.mkdir(parents=True, exist_ok=True)
    workspace_path.write_text("first browser content")
    config.vault_root.mkdir(parents=True, exist_ok=True)
    (config.vault_root / "Browser Vault.md").write_text("# Browser Vault")
    with browser_scenario(built_static, chromium, config) as scenario:
        page = scenario.page
        page.evaluate("""async ({ folder, path }) => {
            await Promise.all([
                import('/static/components/files-sidebar.js'),
                import('/static/components/file-page.js'),
                import('/static/components/chat-input.js'),
            ]);
            const sidebar = document.createElement('files-sidebar');
            sidebar.id = 'browser-files';
            sidebar._currentFolder = folder;
            document.body.append(sidebar);
            await sidebar.updateComplete;
            sidebar.active = true;

            const filePage = document.createElement('file-page');
            filePage.id = 'browser-file-page';
            filePage.kind = 'text';
            filePage.readonly = false;
            filePage.path = path;
            document.body.append(filePage);

            const chat = document.createElement('chat-input');
            chat.id = 'browser-chat-input';
            document.body.append(chat);
            await chat.updateComplete;
            const textarea = chat.querySelector('textarea');
            textarea.value = '@Browser';
            textarea.selectionStart = textarea.selectionEnd = textarea.value.length;
            textarea.dispatchEvent(new Event('input', { bubbles: true }));
        }""", {"folder": workspace_folder, "path": workspace_rel_path})
        page.wait_for_function("""() => {
            const sidebar = document.querySelector('#browser-files');
            const filePage = document.querySelector('#browser-file-page');
            const chat = document.querySelector('#browser-chat-input');
            return sidebar?._files?.length === 1 && !sidebar._loading
                && filePage?._content === 'first browser content' && !filePage._loading
                && chat?._mentionMatches?.some(item => item.type === 'vault'
                    && item.id === 'Browser Vault');
        }""")
        assert page.evaluate("document.querySelector('#browser-files')._files[0].path") == workspace_rel_path
        assert page.evaluate("document.querySelector('#browser-file-page')._modified > 0")
        assert page.evaluate("""document.querySelector('#browser-chat-input')
            ._mentionMatches.find(item => item.id === 'Browser Vault').label""") == "Browser Vault"
        workspace_path.write_text("second browser content")
        page.evaluate("document.querySelector('#browser-file-page').reload()")
        page.wait_for_function("""() => {
            const filePage = document.querySelector('#browser-file-page');
            return filePage._content === 'second browser content' && !filePage._loading;
        }""")
        assert [
            (request.path, request.query, request.authenticated)
            for request in scenario.requests
        ] == [
            ("/api/workspace", [("folder", workspace_folder)], True),
            (f"/api/workspace-file/{workspace_rel_path}", [], True),
            ("/api/autocomplete", [("q", "Browser")], True),
            (f"/api/workspace-file/{workspace_rel_path}", [], True),
        ]


def test_browser_workspace_file_writes(built_static, chromium, config):
    workspace_folder = "Browser files/日本語 #?"
    workspace_rel_path = f"{workspace_folder}/Browser note #?.md"
    renamed_workspace_rel_path = f"{workspace_folder}/Renamed 日本語 & #?.md"
    workspace_path = config.workspace_path / workspace_rel_path
    renamed_workspace_path = config.workspace_path / renamed_workspace_rel_path
    workspace_path.parent.mkdir(parents=True, exist_ok=True)
    workspace_path.write_text("first browser content")
    with browser_scenario(built_static, chromium, config) as scenario:
        page = scenario.page
        page.evaluate("""async ({ folder, path }) => {
            await Promise.all([
                import('/static/components/files-sidebar.js'),
                import('/static/components/file-page.js'),
            ]);
            const sidebar = document.createElement('files-sidebar');
            sidebar.id = 'browser-files';
            sidebar._currentFolder = folder;
            document.body.append(sidebar);
            await sidebar.updateComplete;
            sidebar.active = true;

            const filePage = document.createElement('file-page');
            filePage.id = 'browser-file-page';
            filePage.kind = 'text';
            filePage.readonly = false;
            filePage.path = path;
            document.body.append(filePage);
        }""", {"folder": workspace_folder, "path": workspace_rel_path})
        page.wait_for_function("""() => {
            const sidebar = document.querySelector('#browser-files');
            const filePage = document.querySelector('#browser-file-page');
            return sidebar?._files?.length === 1 && !sidebar._loading
                && filePage?._content === 'first browser content' && !filePage._loading;
        }""")

        page.locator('#browser-file-page .cm-content').fill('saved browser content')
        page.evaluate("document.querySelector('#browser-file-page file-editor').flushSave()")
        page.wait_for_function("document.querySelector('#browser-file-page')._saveStatus === 'saved'")
        assert workspace_path.read_text() == "saved browser content"

        stale_mtime = workspace_path.stat().st_mtime
        workspace_path.write_text("server conflict content")
        os.utime(workspace_path, (stale_mtime + 100, stale_mtime + 100))
        page.locator('#browser-file-page .cm-content').fill('stale browser content')
        page.evaluate("document.querySelector('#browser-file-page file-editor').flushSave()")
        page.wait_for_function("document.querySelector('#browser-file-page')._conflict === true")
        assert workspace_path.read_text() == "server conflict content"
        assert len(scenario.failed_requests) == 1
        assert scenario.failed_requests[0].endswith(
            "/api/workspace/Browser%20files/%E6%97%A5%E6%9C%AC%E8%AA%9E%20%23%3F/Browser%20note%20%23%3F.md")
        scenario.failed_requests.clear()
        page.locator('#browser-file-page .file-editor-conflict button').click()
        page.wait_for_function("""() => {
            const filePage = document.querySelector('#browser-file-page');
            return filePage._content === 'server conflict content' && !filePage._loading;
        }""")

        page.evaluate("""() => {
            window.workspaceFileOpen = null;
            document.querySelector('#browser-file-page').addEventListener(
                'file-open', event => window.workspaceFileOpen = event.detail.path,
                {once: true});
        }""")
        page.locator('#browser-file-page .file-rename-btn').click()
        page.locator('#browser-file-page .file-rename-input').fill(renamed_workspace_rel_path)
        page.locator('#browser-file-page .file-rename-ok').click()
        page.wait_for_function("window.workspaceFileOpen !== null")
        assert page.evaluate("window.workspaceFileOpen") == renamed_workspace_rel_path
        assert renamed_workspace_path.read_text() == "server conflict content"
        assert not workspace_path.exists()

        page.evaluate("""path => {
            const filePage = document.querySelector('#browser-file-page');
            filePage.kind = 'binary';
            filePage.path = path;
        }""", renamed_workspace_rel_path)
        page.wait_for_function(
            "document.querySelector('#browser-file-page .file-delete-btn') !== null")
        expect_window_event(page, 'workspace-file-deleted', 'workspaceFileDeleted')
        page.once("dialog", lambda dialog: dialog.accept())
        page.locator('#browser-file-page .file-delete-btn').click()
        page.wait_for_function("window.workspaceFileDeleted === true")
        # The standalone test page has no app-level close handler; route
        # completion and the sidebar's refresh are the observable results.
        # Deleting the folder's last file prunes the folder, so the refresh
        # gets a 404 and the sidebar falls back to the workspace root.
        page.wait_for_function("""() => {
            const sidebar = document.querySelector('#browser-files');
            return sidebar._currentFolder === '' && sidebar._files.length === 0
                && !sidebar._loading;
        }""")
        assert not renamed_workspace_path.exists()
        assert not renamed_workspace_path.parent.exists()
        assert len(scenario.failed_requests) == 1
        assert scenario.failed_requests[0].endswith(
            "/api/workspace?folder=Browser%20files%2F%E6%97%A5%E6%9C%AC%E8%AA%9E%20%23%3F")
        scenario.failed_requests.clear()
        assert [
            (request.path, request.query, request.authenticated)
            for request in scenario.requests if request.method == "GET"
        ] == [
            ("/api/workspace", [("folder", workspace_folder)], True),
            (f"/api/workspace-file/{workspace_rel_path}", [], True),
            (f"/api/workspace-file/{workspace_rel_path}", [], True),
            ("/api/workspace", [("folder", workspace_folder)], True),
        ]
        workspace_mutation_requests = [
            request for request in scenario.requests if request.method in {"PUT", "DELETE"}
        ]
        assert [
            (request.method, request.path, request.query, request.content_type, request.authenticated)
            for request in workspace_mutation_requests
        ] == [
            ("PUT", f"/api/workspace/{workspace_rel_path}", [], "application/json", True),
            ("PUT", f"/api/workspace/{workspace_rel_path}", [], "application/json", True),
            ("PUT", f"/api/workspace/{workspace_rel_path}",
             [("rename_to", renamed_workspace_rel_path)], None, True),
            ("DELETE", f"/api/workspace/{renamed_workspace_rel_path}", [], None, True),
        ]
        first_save = workspace_mutation_requests[0].json()
        conflict_save = workspace_mutation_requests[1].json()
        assert first_save["content"] == "saved browser content"
        assert isinstance(first_save["modified"], float)
        assert conflict_save["content"] == "stale browser content"
        assert isinstance(conflict_save["modified"], float)
        assert workspace_mutation_requests[2].body == b""
        assert workspace_mutation_requests[3].body == b""


def test_browser_notification_inbox(built_static, chromium, config):
    conv_id = seed_conversation(config, "Notification test")
    notification_id = "record ?#%é"
    notification = notifs.NotificationRecord(
        id=notification_id, timestamp="2026-10-01T00:00:00Z", category="background",
        title="Browser notification", body="Finished work", conv_id=conv_id,
    )
    inbox = config.workspace_path / "notifications" / "inbox.jsonl"
    inbox.parent.mkdir(parents=True, exist_ok=True)
    inbox.write_text(json.dumps(notification.to_dict()) + "\n")
    with browser_scenario(built_static, chromium, config) as scenario:
        page = scenario.page
        page.evaluate("""async () => {
            await import('/static/components/notification-inbox.js');
            document.body.append(document.createElement('notification-inbox'));
        }""")
        page.wait_for_function("document.querySelector('notification-inbox')._count === 1")
        page.locator('.notification-bell').click()
        page.locator('.notification-row').wait_for()
        assert "Browser notification" in page.locator('.notification-panel').inner_text()
        page.evaluate("""() => {
            window.notificationNavigation = null;
            document.querySelector('notification-inbox').addEventListener('navigate-conversation',
                e => window.notificationNavigation = e.detail.convId);
        }""")
        # The row awaits its mark-read request before it navigates.
        page.locator('.notification-row').click()
        page.wait_for_function("window.notificationNavigation !== null")
        assert page.evaluate("window.notificationNavigation") == conv_id
        assert notification_id in notifs.get_read_ids(config)
        # A pushed count enables mark-all; its caller still reaches the real route.
        page.evaluate("window.dispatchEvent(new CustomEvent('notification-created', {detail: {unread_count: 1}}))")
        page.locator('.notification-bell').click()
        page.locator('.notification-row').wait_for()
        page.locator('.notification-mark-all').click()
        page.wait_for_function("document.querySelector('notification-inbox')._count === 0")
        assert [
            (request.method, request.path, request.query, request.body, request.authenticated)
            for request in scenario.requests
        ] == [
            ("GET", "/api/notifications/unread-count", [], b"", True),
            ("GET", "/api/notifications", [("limit", "20")], b"", True),
            ("POST", f"/api/notifications/{notification_id}/read", [], b"", True),
            ("GET", "/api/notifications", [("limit", "20")], b"", True),
            ("POST", "/api/notifications/read-all", [], b"", True),
        ]


def test_browser_context_inspector(built_static, chromium, config):
    conv_id = seed_conversation(config, "Context test")
    diagnostics_payload = {
        "total_tokens_estimated": 123, "context_window_size": 1000,
        "sources": [{"source": "memory", "tokens_estimated": 123, "items_included": 1,
                     "details": {"top_score": 0.9, "budget_source": "dynamic"}}],
    }
    write_context_sidecar(config, conv_id, diagnostics_payload)
    with browser_scenario(built_static, chromium, config) as scenario:
        page = scenario.page
        page.evaluate("""async (convId) => {
            await import('/static/components/context-inspector.js');
            const el = document.createElement('context-inspector');
            el.convId = convId; el.open = true; document.body.append(el);
        }""", conv_id)
        page.wait_for_function("document.querySelector('context-inspector')._data !== null")
        assert page.locator("context-inspector").inner_text().find("dynamic budget") >= 0
        assert page.evaluate("document.querySelector('context-inspector')._data") == diagnostics_payload
        assert [
            (request.method, request.path, request.query, request.body, request.authenticated)
            for request in scenario.requests
        ] == [
            ("GET", f"/api/conversations/{conv_id}/context", [], b"", True),
        ]


def test_browser_conversation_export(built_static, chromium, config):
    conv_id = seed_conversation(config, "Export test")
    append_message(config, conv_id, {"role": "user", "content": "Export 日本語"})
    append_message(config, conv_id, {"role": "assistant", "content": "Second line"})
    with browser_scenario(built_static, chromium, config) as scenario:
        copied = scenario.page.evaluate("""async (convId) => {
            await import('/static/components/copy-conversation-menu.js');
            const copies = [];
            Object.defineProperty(navigator, 'clipboard', {
                configurable: true, value: { writeText: async text => copies.push(text) },
            });
            const el = document.createElement('copy-conversation-menu');
            el.convId = convId;
            await el._copy('jsonl');
            await el._copy('markdown');
            return copies;
        }""", conv_id)
        assert copied[0] == archive_path(config, conv_id).read_text()
        assert "## User\n\nExport 日本語" in copied[1]
        assert "## Assistant\n\nSecond line" in copied[1]
        assert [
            (request.method, request.path, request.query, request.body, request.authenticated)
            for request in scenario.requests
        ] == [
            ("GET", f"/api/conversations/{conv_id}/export", [("format", "jsonl")], b"", True),
            ("GET", f"/api/conversations/{conv_id}/export", [("format", "markdown")], b"", True),
        ]


def test_browser_sticky_state(built_static, chromium, config):
    conv_id = seed_conversation(config, "Sticky test")
    payload = {"content": "# Doc", "extra": {"rows": [1, True, None, {"label": "nested"}]}}
    assert write_sticky_state(config, conv_id, {
        "schema_version": 1, "widget_type": "markdown_document", "data": payload,
    })
    with browser_scenario(built_static, chromium, config) as scenario:
        snapshot = scenario.page.evaluate("""async (convId) => {
            const sticky = await import('/static/lib/sticky-state.js');
            await sticky.setActiveConv(convId);
            return sticky.currentSnapshot();
        }""", conv_id)
        assert snapshot == {
            "widgetType": "markdown_document", "data": payload,
            "collapsed": False, "visible": True,
        }
        assert [
            (request.method, request.path, request.authenticated) for request in scenario.requests
        ] == [("GET", f"/api/sticky/{conv_id}", True)]


NESTED_CONV_FOLDER = "Work space/日本語 & plus+ #hash"


def test_browser_conversation_listings(built_static, chromium, config):
    nested = NESTED_CONV_FOLDER
    with browser_scenario(built_static, chromium, config) as scenario:
        page, base = scenario.page, scenario.base
        assert page.request.post(base + "/api/conversations/folders", data={"path": nested}).status == 200
        active = page.request.post(base + "/api/conversations", data={"title": "Nested", "folder": nested}).json()
        archived = page.request.post(base + "/api/conversations", data={"title": "Archived", "folder": nested}).json()
        assert page.request.post(base + f"/api/conversations/{archived['conv_id']}/archive").status == 200
        scenario.requests.clear()
        listings = page.evaluate("""async (folder) => {
            const { DefaultService } = await import('/static/lib/api-client/index.js');
            const result = [];
            for (const method of ['listConversationsApiConversationsGet',
                'listArchivedConversationsApiConversationsArchivedGet']) {
                for (const arg of [undefined, '', folder]) result.push(await DefaultService[method](arg));
            }
            for (const arg of [undefined, '', 'heartbeat', 'schedule', 'delegated']) {
                result.push(await DefaultService.listSystemConversationsApiConversationsSystemGet(arg));
            }
            return result;
        }""", nested)
        assert listings[0] == listings[1]
        assert listings[3] == listings[4]
        assert listings[2]["conversations"][0]["conv_id"] == active["conv_id"]
        assert listings[5]["conversations"][0]["conv_id"] == archived["conv_id"]
        assert listings[2]["folder"] == listings[5]["folder"] == nested
        assert listings[6] == listings[7]
        expected_requests = []
        for route_path in ["/api/conversations", "/api/conversations/archived"]:
            expected_requests.extend([(route_path, [], True), (route_path, [], True),
                                      (route_path, [("folder", nested)], True)])
        for folder in [None, "", "heartbeat", "schedule", "delegated"]:
            expected_requests.append(("/api/conversations/system", [] if not folder else [("folder", folder)], True))
        assert [
            (request.url_path, request.query, request.authenticated)
            for request in scenario.requests if request.method == "GET"
        ] == expected_requests


def test_browser_conversation_patch(built_static, chromium, config):
    nested = NESTED_CONV_FOLDER
    conv_id = seed_conversation(config, "Patch test")
    with browser_scenario(built_static, chromium, config) as scenario:
        page, base = scenario.page, scenario.base
        assert page.request.post(base + "/api/conversations/folders", data={"path": nested}).status == 200
        scenario.requests.clear()
        patch_title = "Title 日本語 & plus+ #hash"
        patched = page.evaluate("""async ({ id, title, folder }) => {
            const { DefaultService } = await import('/static/lib/api-client/index.js');
            const renamed = await DefaultService.renameConversationApiConversationsIdPatch(id, { title });
            const moved = await DefaultService.renameConversationApiConversationsIdPatch(id, { folder }, true);
            return { renamed, ignored: moved === undefined };
        }""", {"id": conv_id, "title": patch_title, "folder": "  " + nested + "  "})
        assert patched["renamed"]["conv_id"] == conv_id
        assert patched["renamed"]["title"] == patch_title
        assert set(patched["renamed"]) == {"conv_id", "title", "created_at", "updated_at"}
        assert patched["ignored"] is True
        assert [
            (request.method, request.url_path, request.json(), request.content_type, request.authenticated)
            for request in scenario.requests
        ] == [
            ("PATCH", f"/api/conversations/{conv_id}", {"title": patch_title}, "application/json", True),
            ("PATCH", f"/api/conversations/{conv_id}", {"folder": "  " + nested + "  "}, "application/json", True),
        ]
        saved = page.request.get(base + "/api/conversations", params={"folder": nested}).json()
        assert any(c["conv_id"] == conv_id and c["title"] == patch_title for c in saved["conversations"])


def test_browser_conversation_folders(built_static, chromium, config):
    folder_path = "Folder space/日本語 & plus+ #hash%?"
    renamed_path = "Folder space/new + %?"
    with browser_scenario(built_static, chromium, config) as scenario:
        folders = scenario.page.evaluate("""async ({ path, renamed }) => {
            const { DefaultService } = await import('/static/lib/api-client/index.js');
            return [
                await DefaultService.createConvFolderApiConversationsFoldersPost({ path }),
                await DefaultService.renameConvFolderApiConversationsFoldersPathPut(path, { path: renamed }),
                await DefaultService.deleteConvFolderApiConversationsFoldersPathDelete(renamed),
            ];
        }""", {"path": folder_path, "renamed": renamed_path})
        assert folders == [{"ok": True, "path": folder_path}, {"ok": True}, {"ok": True}]
        assert [
            (request.method, request.path, request.json(), request.authenticated)
            for request in scenario.requests
        ] == [
            ("POST", "/api/conversations/folders", {"path": folder_path}, True),
            ("PUT", "/api/conversations/folders/" + folder_path, {"path": renamed_path}, True),
            ("DELETE", "/api/conversations/folders/" + renamed_path, None, True),
        ]


def test_browser_conversation_lifecycle(built_static, chromium, config):
    nested = NESTED_CONV_FOLDER
    with browser_scenario(built_static, chromium, config) as scenario:
        page, base = scenario.page, scenario.base
        assert page.request.post(base + "/api/conversations/folders", data={"path": nested}).status == 200
        scenario.requests.clear()
        lifecycle = page.evaluate("""async (folder) => {
            const { ConversationStore } = await import('/static/lib/conversation-store.js');
            const ws = new EventTarget();
            ws.send = () => {};
            const store = new ConversationStore(ws);
            await store.createConversation('Lifecycle 日本語 & + #', '', folder);
            const created = store.conversations[0];
            await store.archiveConversation(created.conv_id);
            const deselected = store.currentConvId === null;
            await store.unarchiveConversation(created.conv_id);
            await store.deleteConversation(created.conv_id);
            return { created, deselected };
        }""", nested)
        created = lifecycle["created"]
        assert created["title"] == "Lifecycle 日本語 & + #"
        assert created["folder"] == nested
        assert lifecycle["deselected"] is True
        assert [
            (request.method, request.path,
             request.json() if request.url_path == "/api/conversations" else None,
             request.authenticated)
            for request in scenario.requests if request.method in {"POST", "DELETE"}
        ] == [
            ("POST", "/api/conversations", {"title": created["title"], "folder": nested}, True),
            ("POST", f"/api/conversations/{created['conv_id']}/archive", None, True),
            ("POST", f"/api/conversations/{created['conv_id']}/unarchive", None, True),
            ("DELETE", f"/api/conversations/{created['conv_id']}", None, True),
        ]
        assert ConversationIndex(config).get(created["conv_id"]) is None


def test_browser_canvas_tabs_and_standalone_page(built_static, chromium, config):
    conv_id = seed_conversation(config, "Canvas test")
    with browser_scenario(built_static, chromium, config) as scenario:
        page = scenario.page
        canvas_result = page.evaluate("""async (convId) => {
            const canvas = await import('/static/lib/canvas-state.js');
            const catalog = await import('/static/lib/widget-catalog.js');
            await canvas.setActiveConv(convId);
            const descriptors = await catalog.getCatalog();
            const modules = [
                ['/static/widgets/code_block/widget.js', 'dc-widget-code-block',
                 {code: 'print(1)', language: 'python', filename: 'demo.py'}],
                ['/static/widgets/diff_view/widget.js', 'dc-widget-diff-view',
                 {before: 'a', after: 'b', filename: 'demo.txt'}],
                ['/static/widgets/json_view/widget.js', 'dc-widget-json-view',
                 {value: {nested: [1, true, null]}}],
                ['/static/widgets/markdown_document/widget.js', 'dc-widget-markdown-document',
                 {content: '# Browser doc\\n\\nBody'}],
            ];
            for (const [url, tag, data] of modules) {
                await import(url);
                const widget = document.createElement(tag);
                widget.data = data;
                await widget._openInCanvas();
            }
            await canvas.switchToTab('canvas_1');
            await canvas.closeTabById(convId, 'canvas_4');
            const descriptor = descriptors.get('code_block');
            return {
                snapshot: canvas.currentSnapshot(),
                descriptor: {name: descriptor.name, js_url: descriptor.js_url,
                             schemaType: descriptor.data_schema.type},
            };
        }""", conv_id)
        assert canvas_result["descriptor"]["name"] == "code_block"
        assert canvas_result["descriptor"]["schemaType"] == "object"
        assert canvas_result["descriptor"]["js_url"].startswith(
            "/widgets/bundled/code_block/widget.js?v=")
        assert canvas_result["snapshot"]["activeTabId"] == "canvas_1"
        assert canvas_result["snapshot"]["tabs"] == []  # REST changes arrive over WS.

        def widget_catalog_requests():
            return [request.authenticated for request in scenario.requests
                    if request.url_path == "/api/widgets"]

        def canvas_requests():
            return [
                (request.method, request.path,
                 request.json() if request.method == "POST" else None,
                 request.content_type, request.authenticated)
                for request in scenario.requests
                if request.url_path.startswith(f"/api/canvas/{conv_id}")
            ]

        assert widget_catalog_requests() == [True]
        assert canvas_requests() == [
            ("GET", f"/api/canvas/{conv_id}", None, None, True),
            ("POST", f"/api/canvas/{conv_id}/new_tab", {
                "widget_type": "code_block",
                "data": {"code": "print(1)", "language": "python", "filename": "demo.py"},
                "label": "demo.py",
            }, "application/json", True),
            ("POST", f"/api/canvas/{conv_id}/new_tab", {
                "widget_type": "diff_view",
                "data": {"before": "a", "after": "b", "filename": "demo.txt", "view": "unified"},
                "label": "demo.txt",
            }, "application/json", True),
            ("POST", f"/api/canvas/{conv_id}/new_tab", {
                "widget_type": "json_view", "data": {"value": {"nested": [1, True, None]}},
                "label": "JSON View",
            }, "application/json", True),
            ("POST", f"/api/canvas/{conv_id}/new_tab", {
                "widget_type": "markdown_document",
                "data": {"content": "# Browser doc\n\nBody"}, "label": "Browser doc",
            }, "application/json", True),
            ("POST", f"/api/canvas/{conv_id}/active_tab", {"tab_id": "canvas_1"},
             "application/json", True),
            ("POST", f"/api/canvas/{conv_id}/close_tab", {"tab_id": "canvas_4"},
             "application/json", True),
        ]
        page.goto(scenario.base + f"/canvas/{conv_id}/canvas_1")
        page.wait_for_function("document.querySelector('dc-widget-code-block') !== null")
        assert page.locator("dc-widget-code-block").inner_text().find("print(1)") >= 0
        assert page.title() == "Canvas — demo.py"
        assert canvas_requests()[-1] == (
            "GET", f"/api/canvas/{conv_id}", None, None, True,
        )
        assert widget_catalog_requests() == [True, True]


def test_browser_vault_standalone_page(built_static, chromium, config):
    seed_vault_page(config)
    with browser_scenario(built_static, chromium, config) as scenario:
        page = scenario.page
        # Load the actual standalone page and its module graph, including
        # decoding a page name and observing the loaded title.
        open_vault_page(scenario)
        assert page.locator("wiki-page").evaluate("node => node._body") == "# Browser vault body\n"
        assert page.locator("wiki-page").evaluate(
            "node => node._frontmatter.nested.rows") == [1, True, None]
        # The component currently renders no .wiki-page-title element.
        # Preserve the page's existing observer reaction without fixing
        # that independent mismatch in this auth migration.
        assert page.title() == "Vault — DecafClaw"
        page.locator("wiki-page").evaluate("""node => {
            const title = document.createElement('span');
            title.className = 'wiki-page-title';
            title.textContent = 'Browser & 日本語';
            node.append(title);
        }""")
        page.wait_for_function("document.title === 'Browser & 日本語 — DecafClaw Vault'")
        assert page.locator("wiki-page").evaluate("node => node.page") == VAULT_PAGE_NAME
        assert page.url == scenario.base + VAULT_PAGE_URL_PATH
        assert [
            (request.path, request.query, request.authenticated)
            for request in scenario.requests if request.url_path.startswith("/api/vault")
        ] == [(f"/api/vault/{VAULT_PAGE_NAME}", [], True)]


def test_browser_vault_sidebars(built_static, chromium, config):
    seed_vault_page(config)
    with browser_scenario(built_static, chromium, config) as scenario:
        page = scenario.page
        page.evaluate("""async () => {
            await Promise.all([
                import('/static/components/vault-sidebar.js'),
                import('/static/components/tags-sidebar.js'),
            ]);
            const sidebar = document.createElement('vault-sidebar');
            sidebar.id = 'browser-vault-sidebar';
            sidebar._vaultFolder = 'agent/pages';
            document.body.append(sidebar);
            await sidebar.updateComplete;
            sidebar.active = true;

            const tags = document.createElement('tags-sidebar');
            tags.id = 'browser-tags-sidebar';
            document.body.append(tags);
            await tags.updateComplete;
            tags.active = true;
        }""")
        page.wait_for_function("""() =>
            document.querySelector('#browser-vault-sidebar')._wikiPages.length === 1
            && !document.querySelector('#browser-vault-sidebar')._loading
            && document.querySelector('#browser-tags-sidebar')._tags.length === 1
            && !document.querySelector('#browser-tags-sidebar')._loading
        """)
        assert page.locator('#browser-vault-sidebar').evaluate(
            "node => node._wikiPages[0].path") == VAULT_PAGE_NAME
        assert page.locator('#browser-tags-sidebar').evaluate(
            "node => node._tags[0].pages") == [f"{VAULT_PAGE_NAME}.md"]
        page.locator('#browser-vault-sidebar button', has_text='Recent').click()
        page.wait_for_function("""() => {
            const sidebar = document.querySelector('#browser-vault-sidebar');
            return sidebar._recentPages.length === 1 && !sidebar._loading;
        }""")
        assert [
            (request.path, request.query, request.authenticated)
            for request in scenario.requests if request.method == "GET"
        ] == [
            ("/api/vault", [("folder", "agent/pages")], True),
            ("/api/vault/tags", [], True),
            ("/api/vault/recent", [], True),
        ]

        page.locator('#browser-vault-sidebar button', has_text='Browse').click()
        page.wait_for_function("""() => {
            const sidebar = document.querySelector('#browser-vault-sidebar');
            return sidebar.querySelector('.wiki-new-page-btn') !== null && !sidebar._loading;
        }""")
        page.once("dialog", lambda dialog: dialog.accept("Created #1"))
        page.locator('#browser-vault-sidebar .wiki-new-page-btn').click()
        created_vault_page = config.vault_root / "agent/pages/Created #1.md"
        page.wait_for_function("""() => {
            const sidebar = document.querySelector('#browser-vault-sidebar');
            return sidebar._wikiPages.some(item => item.path === 'agent/pages/Created #1')
                && !sidebar._loading;
        }""")
        assert created_vault_page.exists()

        page.once("dialog", lambda dialog: dialog.accept("Folder #1"))
        page.locator('#browser-vault-sidebar .wiki-new-folder-btn').click()
        page.wait_for_function("""() => {
            const sidebar = document.querySelector('#browser-vault-sidebar');
            return sidebar._vaultFolder === 'agent/pages/Folder #1' && !sidebar._loading;
        }""")
        assert (config.vault_root / "agent/pages/Folder #1").is_dir()
        assert [
            (request.method, request.path, request.json(), request.content_type, request.authenticated)
            for request in scenario.requests if request.method in {"POST", "PUT", "DELETE"}
        ] == [
            ("POST", "/api/vault", {"name": "agent/pages/Created #1"}, "application/json", True),
            ("POST", "/api/vault/folders", {"folder": "agent/pages/Folder #1"},
             "application/json", True),
        ]


def test_browser_vault_editor_conflicts(built_static, chromium, config):
    vault_page_path = seed_vault_page(config)
    with browser_scenario(built_static, chromium, config) as scenario:
        page = scenario.page
        page.evaluate("""async (pageName) => {
            await import('/static/components/wiki-editor.js');
            const editor = document.createElement('wiki-editor');
            editor.id = 'browser-vault-editor';
            editor.page = pageName;
            editor.content = 'stale editor body';
            editor.modified = 0;
            document.body.append(editor);
            await editor.updateComplete;
            editor._status = 'conflict';
            await editor.updateComplete;
        }""", VAULT_PAGE_NAME)
        page.locator('#browser-vault-editor .wiki-editor-conflict button',
                     has_text='Reload').click()
        page.wait_for_function("""() => {
            const editor = document.querySelector('#browser-vault-editor');
            return editor.content === '# Browser vault body\\n' && editor._status === 'saved';
        }""")
        assert [
            (request.path, request.query, request.authenticated)
            for request in scenario.requests if request.method == "GET"
        ] == [(f"/api/vault/{VAULT_PAGE_NAME}", [], True)]

        vault_editor = page.locator('#browser-vault-editor .milkdown .ProseMirror')
        initial_vault_mtime = page.evaluate(
            "document.querySelector('#browser-vault-editor').modified")
        vault_editor.fill("Saved through generated client")
        page.wait_for_function(
            "document.querySelector('#browser-vault-editor')._status === 'editing'")
        page.evaluate("document.querySelector('#browser-vault-editor').flushSave()")
        page.wait_for_function(
            "document.querySelector('#browser-vault-editor')._status === 'saved'")
        assert vault_page_path.read_text().endswith("# Saved through generated client\n")

        stale_vault_mtime = vault_page_path.stat().st_mtime
        vault_page_path.write_text("# Server conflict")
        os.utime(vault_page_path, (stale_vault_mtime + 100, stale_vault_mtime + 100))
        vault_editor.fill("Forced through generated client")
        page.wait_for_function(
            "document.querySelector('#browser-vault-editor')._status === 'editing'")
        page.evaluate("document.querySelector('#browser-vault-editor').flushSave()")
        page.wait_for_function(
            "document.querySelector('#browser-vault-editor')._status === 'conflict'")
        page.locator('#browser-vault-editor .wiki-editor-conflict button',
                     has_text='Overwrite').click()
        page.wait_for_function(
            "document.querySelector('#browser-vault-editor')._status === 'saved'")
        assert vault_page_path.read_text().endswith("# Forced through generated client\n")
        assert [
            (request.method, request.path, request.json(), request.content_type, request.authenticated)
            for request in scenario.requests if request.method in {"POST", "PUT", "DELETE"}
        ] == [
            ("PUT", f"/api/vault/{VAULT_PAGE_NAME}", {
                "content": "# Saved through generated client\n",
                "modified": initial_vault_mtime,
            }, "application/json", True),
            ("PUT", f"/api/vault/{VAULT_PAGE_NAME}", {
                "content": "# Forced through generated client\n",
                "modified": stale_vault_mtime,
            }, "application/json", True),
            ("PUT", f"/api/vault/{VAULT_PAGE_NAME}", {
                "content": "# Forced through generated client\n",
            }, "application/json", True),
        ]
        assert len(scenario.failed_requests) == 1
        assert scenario.failed_requests[0].endswith(
            "/api/vault/agent/pages/Browser%20%26%20%E6%97%A5%E6%9C%AC%E8%AA%9E")
        scenario.failed_requests.clear()


def test_browser_vault_page_metadata_rename_delete(built_static, chromium, config):
    vault_page_path = seed_vault_page(config)
    with browser_scenario(built_static, chromium, config) as scenario:
        page = scenario.page
        open_vault_page(scenario)
        vault_page_path.write_text("---\nsummary: External summary\n---\n# Externally changed body\n")
        page.evaluate("document.querySelector('wiki-page')._onMetadataReload()")
        page.wait_for_function("""() => {
            const wikiPage = document.querySelector('wiki-page');
            return wikiPage._loaded && wikiPage._body === '# Externally changed body\\n';
        }""")
        metadata_modified = page.evaluate(
            "document.querySelector('wiki-page')._modified")
        page.evaluate("""() => document.querySelector('wiki-page')._onMetadataRawSave(
            new CustomEvent('metadata-raw-save', {
                detail: {raw: 'summary: Generated write\\nnested: [1, true, null]'},
            }))""")
        page.wait_for_function("""() => document.querySelector('wiki-page')
            ._frontmatter.summary === 'Generated write'""")
        assert "nested: [1, true, null]" in vault_page_path.read_text()

        renamed_vault_name = "agent/archive/Renamed 日本語 #1"
        renamed_vault_path = config.vault_root / f"{renamed_vault_name}.md"
        page.locator('wiki-page .wiki-rename-btn').click()
        page.locator('wiki-page .wiki-rename-input').fill(renamed_vault_name)
        expect_window_event(page, 'wiki-open', 'vaultPageRenamed')
        with page.expect_response("**/api/vault/agent/pages/Browser**") as rename_response:
            page.locator('wiki-page .wiki-rename-ok').click()
        assert rename_response.value.status == 200
        page.wait_for_function("window.vaultPageRenamed === true")
        assert renamed_vault_path.exists()
        assert not vault_page_path.exists()

        page.evaluate("""name => { document.querySelector('wiki-page').page = name; }""",
                      renamed_vault_name)
        page.wait_for_function("""() => document.querySelector('wiki-page')._loaded
            && document.querySelector('wiki-page').page
                === 'agent/archive/Renamed 日本語 #1'""")
        page.once("dialog", lambda dialog: dialog.accept())
        expect_window_event(page, 'wiki-close', 'vaultPageClosed')
        with page.expect_response("**/api/vault/agent/archive/Renamed**") as delete_response:
            page.locator('wiki-page .wiki-delete-btn').click()
        assert delete_response.value.status == 200
        page.wait_for_function("window.vaultPageClosed === true")
        assert not renamed_vault_path.exists()

        assert [
            (request.method, request.path, request.json(), request.content_type, request.authenticated)
            for request in scenario.requests if request.method in {"POST", "PUT", "DELETE"}
        ] == [
            ("PUT", f"/api/vault/{VAULT_PAGE_NAME}", {
                "frontmatter_raw": "summary: Generated write\nnested: [1, true, null]",
                "modified": metadata_modified,
            }, "application/json", True),
            ("PUT", f"/api/vault/{VAULT_PAGE_NAME}", {
                "rename_to": renamed_vault_name,
            }, "application/json", True),
            ("DELETE", f"/api/vault/{renamed_vault_name}", None, None, True),
        ]


def test_browser_config_files(built_static, chromium, config):
    config_file_path = config.workspace_path / "schedules" / "Config Browser #1.md"
    config_file_path.parent.mkdir(parents=True, exist_ok=True)
    config_file_path.write_text("# Initial config\n")
    with browser_scenario(built_static, chromium, config) as scenario:
        page = scenario.page
        page.evaluate("""async () => {
            await import('/static/components/config-panel.js');
            const panel = document.createElement('config-panel');
            panel.id = 'browser-config-panel';
            document.body.append(panel);
        }""")
        page.wait_for_function("""() => {
            const panel = document.querySelector('#browser-config-panel');
            return panel._files.some(file => file.path === 'workspace/schedules/Config Browser #1.md')
                && !panel._loading;
        }""")

        # Read an absent local file through its bundled fallback. The
        # null wire mtime remains observable as the editor's existing
        # zero sentinel rather than preventing selection.
        page.locator(
            '#browser-config-panel .config-file-item', has_text='AGENT.md',
        ).click()
        page.wait_for_function("""() => document.querySelector('#browser-config-panel')
            ._selectedFile?.path === 'AGENT.md'""")
        assert page.evaluate("""document.querySelector(
            '#browser-config-panel wiki-editor').content.length > 0""")
        assert page.evaluate("""document.querySelector(
            '#browser-config-panel wiki-editor').modified""") == 0

        # Going back refetches the list; wait for it before selecting from it.
        page.locator('#browser-config-panel .config-back-btn').click()
        page.wait_for_function("""() => {
            const panel = document.querySelector('#browser-config-panel');
            return panel._selectedFile === null && !panel._loading;
        }""")
        page.locator(
            '#browser-config-panel .config-file-item', has_text='Config Browser #1.md',
        ).click()
        page.wait_for_function("""() => document.querySelector('#browser-config-panel')
            ._selectedFile?.path === 'workspace/schedules/Config Browser #1.md'""")
        config_editor = page.locator(
            '#browser-config-panel wiki-editor .milkdown .ProseMirror')
        initial_config_mtime = page.evaluate("""document.querySelector(
            '#browser-config-panel wiki-editor').modified""")
        config_editor.fill("Saved through generated config")
        page.wait_for_function("""document.querySelector(
            '#browser-config-panel wiki-editor')._status === 'editing'""")
        page.evaluate("""document.querySelector(
            '#browser-config-panel wiki-editor').flushSave()""")
        page.wait_for_function("""document.querySelector(
            '#browser-config-panel wiki-editor')._status === 'saved'""")
        assert config_file_path.read_text() == "# Saved through generated config\n"

        saved_config_mtime = config_file_path.stat().st_mtime
        config_file_path.write_text("# First external config change\n")
        os.utime(config_file_path,
                 (saved_config_mtime + 100, saved_config_mtime + 100))
        config_editor.fill("First stale config edit")
        page.wait_for_function("""document.querySelector(
            '#browser-config-panel wiki-editor')._status === 'editing'""")
        page.evaluate("""document.querySelector(
            '#browser-config-panel wiki-editor').flushSave()""")
        page.wait_for_function("""document.querySelector(
            '#browser-config-panel wiki-editor')._status === 'conflict'""")
        page.locator(
            '#browser-config-panel .wiki-editor-conflict button',
            has_text='Reload',
        ).click()
        page.wait_for_function("""() => {
            const editor = document.querySelector('#browser-config-panel wiki-editor');
            return editor.content === '# First external config change\\n'
                && editor._status === 'saved';
        }""")

        reloaded_config_mtime = config_file_path.stat().st_mtime
        config_file_path.write_text("# Second external config change\n")
        os.utime(config_file_path,
                 (reloaded_config_mtime + 100, reloaded_config_mtime + 100))
        config_editor.fill("Forced through generated config")
        page.wait_for_function("""document.querySelector(
            '#browser-config-panel wiki-editor')._status === 'editing'""")
        page.evaluate("""document.querySelector(
            '#browser-config-panel wiki-editor').flushSave()""")
        page.wait_for_function("""document.querySelector(
            '#browser-config-panel wiki-editor')._status === 'conflict'""")
        page.locator(
            '#browser-config-panel .wiki-editor-conflict button',
            has_text='Overwrite',
        ).click()
        page.wait_for_function("""document.querySelector(
            '#browser-config-panel wiki-editor')._status === 'saved'""")
        assert config_file_path.read_text() == "# Forced through generated config\n"

        config_path = "/api/config/files/workspace/schedules/Config Browser #1.md"
        assert [
            (request.method, request.path,
             request.json() if request.method == "PUT" else None,
             request.content_type, request.authenticated)
            for request in scenario.requests
        ] == [
            ("GET", "/api/config/files", None, None, True),
            ("GET", "/api/config/files/AGENT.md", None, None, True),
            ("GET", "/api/config/files", None, None, True),
            ("GET", config_path, None, None, True),
            ("PUT", config_path, {
                "content": "# Saved through generated config\n",
                "modified": initial_config_mtime,
            }, "application/json", True),
            ("PUT", config_path, {
                "content": "# First stale config edit\n",
                "modified": saved_config_mtime,
            }, "application/json", True),
            ("GET", config_path, None, None, True),
            ("PUT", config_path, {
                "content": "# Forced through generated config\n",
                "modified": reloaded_config_mtime,
            }, "application/json", True),
            ("PUT", config_path, {
                "content": "# Forced through generated config\n",
            }, "application/json", True),
        ]
        assert len(scenario.failed_requests) == 2
        assert all(request.endswith(
            "/api/config/files/workspace/schedules/Config%20Browser%20%231.md",
        ) for request in scenario.failed_requests)
        scenario.failed_requests.clear()


SCHEDULE_NAME = "Browser_Schedule-1"
ENCODED_SCHEDULE = "/api/schedules/Browser_Schedule-1"


def seed_schedule(config):
    schedule_path = config.agent_path / "schedules" / f"{SCHEDULE_NAME}.md"
    schedule_path.parent.mkdir(parents=True, exist_ok=True)
    schedule_path.write_text(
        "---\nschedule: '0 3 * * *'\nenabled: true\nmodel: browser-model\n---\n"
        "# Initial schedule\n",
    )
    return schedule_path


def schedule_requests(scenario):
    return [
        (request.method, request.raw_path, request.json(), request.content_type, request.authenticated)
        for request in scenario.requests
        if request.url_path in {"/api/models", "/api/schedules"}
        or request.url_path.startswith("/api/schedules/")
    ]


def test_browser_schedule_sidebar(built_static, chromium, config, fake_schedule_runs):
    seed_schedule_models(config)
    schedule_path = seed_schedule(config)
    with browser_scenario(built_static, chromium, config) as scenario:
        page = scenario.page
        page.evaluate("""async () => {
            await import('/static/components/schedules-sidebar.js');
            const sidebar = document.createElement('schedules-sidebar');
            sidebar.id = 'browser-schedules-sidebar';
            document.body.append(sidebar);
            await sidebar.updateComplete;
            sidebar.active = true;
        }""")
        page.wait_for_function("""name => {
            const sidebar = document.querySelector('#browser-schedules-sidebar');
            return sidebar?._schedules?.some(item => item.name === name) && !sidebar._loading;
        }""", arg=SCHEDULE_NAME)

        schedule_row = page.locator(
            "#browser-schedules-sidebar .schedule-row", has_text=SCHEDULE_NAME,
        )
        with page.expect_response("**/api/schedules/Browser_Schedule-1") as toggle_response:
            schedule_row.locator(".schedule-enabled-toggle").uncheck()
        assert toggle_response.value.status == 200
        # The toggle refetches the list; wait for that before the next click.
        page.wait_for_function("""name => {
            const sidebar = document.querySelector('#browser-schedules-sidebar');
            return sidebar._schedules.find(item => item.name === name)?.enabled === false
                && !sidebar._loading;
        }""", arg=SCHEDULE_NAME)
        assert "enabled: false" in schedule_path.read_text()

        with page.expect_response("**/api/schedules/*/run") as sidebar_run_response:
            schedule_row.locator(".schedule-row-run").click()
        assert sidebar_run_response.value.status == 202
        page.wait_for_function("""name => document.querySelector(
            '#browser-schedules-sidebar')._runStatus[name] === 'started'""", arg=SCHEDULE_NAME)
        assert schedule_requests(scenario) == [
            ("GET", "/api/schedules", None, None, True),
            ("PUT", ENCODED_SCHEDULE, {"enabled": False}, "application/json", True),
            ("GET", "/api/schedules", None, None, True),
            ("POST", ENCODED_SCHEDULE + "/run", None, None, True),
        ]


def test_browser_schedule_page_edits(built_static, chromium, config, fake_schedule_runs):
    seed_schedule_models(config)
    schedule_path = seed_schedule(config)
    with browser_scenario(built_static, chromium, config) as scenario:
        page = scenario.page
        page.evaluate("""async (name) => {
            await import('/static/components/schedule-page.js');
            const schedulePage = document.createElement('schedule-page');
            schedulePage.id = 'browser-schedule-page';
            schedulePage.name = name;
            document.body.append(schedulePage);
        }""", SCHEDULE_NAME)
        page.wait_for_function("""name => {
            const schedulePage = document.querySelector('#browser-schedule-page');
            return schedulePage?._data?.name === name && !schedulePage._loading
                && schedulePage._modelsLoaded
                && schedulePage._models.includes('browser-model');
        }""", arg=SCHEDULE_NAME)
        assert page.evaluate("""document.querySelector(
            '#browser-schedule-page schedule-metadata').models""") == ["browser-model"]

        with page.expect_response("**/api/schedules/*/run") as page_run_response:
            page.locator("#browser-schedule-page .schedule-run-btn").click()
        assert page_run_response.value.status == 202
        page.wait_for_function("""() => document.querySelector(
            '#browser-schedule-page')._runStatus === 'started'""")

        expect_window_event(page, 'schedule-saved', 'scheduleSaved')
        channel = page.locator("#browser-schedule-page .sched-md-channel")
        channel.fill("browser-channel")
        channel.dispatch_event("change")
        page.wait_for_function("""() => window.scheduleSaved === true && document.querySelector(
            '#browser-schedule-page')._data.channel === 'browser-channel'""")
        assert "channel: browser-channel" in schedule_path.read_text()

        schedule_editor = page.locator(
            "#browser-schedule-page wiki-editor .milkdown .ProseMirror")
        schedule_editor.fill("Saved through generated schedule")
        page.wait_for_function("""document.querySelector(
            '#browser-schedule-page wiki-editor')._status === 'editing'""")
        # A save is complete once the page's post-save refresh announces it.
        expect_window_event(page, 'schedule-saved', 'scheduleSaved')
        page.evaluate("""document.querySelector(
            '#browser-schedule-page wiki-editor').flushSave()""")
        page.wait_for_function("""() => window.scheduleSaved === true && document.querySelector(
            '#browser-schedule-page wiki-editor')._status === 'saved'""")
        assert "# Saved through generated schedule\n" in schedule_path.read_text()

        # Exercise the editor's schedule reload against the real detail
        # envelope. The schedule server intentionally ignores modified,
        # so conflict UI is entered directly rather than inventing new
        # conflict enforcement for this migration.
        schedule_path.write_text(schedule_path.read_text().replace(
            "# Saved through generated schedule\n\n", "# Server schedule\n"))
        page.evaluate("""node => { node._status = 'conflict'; }""",
                      page.locator("#browser-schedule-page wiki-editor").element_handle())
        page.locator(
            "#browser-schedule-page .wiki-editor-conflict button", has_text="Reload",
        ).click()
        page.wait_for_function("""() => {
            const editor = document.querySelector('#browser-schedule-page wiki-editor');
            return editor.content.includes('Server schedule') && editor._status === 'saved';
        }""")

        schedule_editor.fill("Forced through generated schedule")
        page.wait_for_function("""document.querySelector(
            '#browser-schedule-page wiki-editor')._status === 'editing'""")
        page.evaluate("""node => { node._status = 'conflict'; }""",
                      page.locator("#browser-schedule-page wiki-editor").element_handle())
        expect_window_event(page, 'schedule-saved', 'scheduleSaved')
        page.locator(
            "#browser-schedule-page .wiki-editor-conflict button", has_text="Overwrite",
        ).click()
        page.wait_for_function("""() => window.scheduleSaved === true && document.querySelector(
            '#browser-schedule-page wiki-editor')._status === 'saved'""")
        assert "# Forced through generated schedule\n" in schedule_path.read_text()

        requests = schedule_requests(scenario)
        assert all(request[4] for request in requests)
        assert ("GET", "/api/models", None, None, True) in requests
        assert ("GET", ENCODED_SCHEDULE, None, None, True) in requests
        assert ("PUT", ENCODED_SCHEDULE, {"channel": "browser-channel"},
                "application/json", True) in requests
        saved_schedule_requests = [request for request in requests
                                   if request[0] == "PUT"
                                   and request[1] == ENCODED_SCHEDULE
                                   and request[2].get("content")
                                   == "# Saved through generated schedule\n"]
        assert len(saved_schedule_requests) == 1
        assert isinstance(saved_schedule_requests[0][2].get("modified"), float)
        assert saved_schedule_requests[0][3:] == ("application/json", True)
        assert ("PUT", ENCODED_SCHEDULE, {
            "content": "# Forced through generated schedule\n",
        }, "application/json", True) in requests
        assert sum(
            request[0] == "POST" and request[1] == ENCODED_SCHEDULE + "/run"
            for request in requests
        ) == 1


def test_browser_schedule_overlay_reset(built_static, chromium, config):
    seed_schedule_models(config)
    dream_overlay = config.agent_path / "schedules" / "dream.md"
    dream_overlay.parent.mkdir(parents=True, exist_ok=True)
    dream_overlay.write_text(
        "---\nschedule: '0 4 * * *'\nenabled: true\n---\n# Browser dream overlay\n",
    )
    with browser_scenario(built_static, chromium, config) as scenario:
        page = scenario.page
        page.evaluate("""async () => {
            await import('/static/components/schedule-page.js');
            const schedulePage = document.createElement('schedule-page');
            schedulePage.id = 'browser-schedule-page';
            schedulePage.name = 'dream';
            document.body.append(schedulePage);
        }""")
        page.wait_for_function("""() => {
            const schedulePage = document.querySelector('#browser-schedule-page');
            return schedulePage?._data?.name === 'dream' && schedulePage._data.has_overlay
                && !schedulePage._loading && schedulePage._modelsLoaded;
        }""")
        page.evaluate("""() => {
            document.querySelector('#browser-schedule-page wiki-editor')
                .dataset.beforeReset = 'true';
        }""")
        page.once("dialog", lambda dialog: dialog.accept())
        page.locator("#browser-schedule-page .schedule-reset-btn").click()
        # The reset refetches the schedule, remounting the editor with the
        # bundled body.
        page.wait_for_function("""() => {
            const schedulePage = document.querySelector('#browser-schedule-page');
            const editor = schedulePage.querySelector('wiki-editor');
            return schedulePage._data?.name === 'dream'
                && !schedulePage._data?.has_overlay && !schedulePage._loading
                && editor?.dataset.beforeReset !== 'true';
        }""")
        assert not dream_overlay.exists()
        requests = schedule_requests(scenario)
        assert all(request[4] for request in requests)
        assert ("DELETE", "/api/schedules/dream/overlay", None, None, True) in requests


def test_browser_auth_me_without_body_decoding(built_static, chromium, config):
    seed_vault_page(config)
    with browser_scenario(built_static, chromium, config) as scenario:
        page = scenario.page
        vault_url = scenario.base + VAULT_PAGE_URL_PATH
        # The no-body /me overload must not decode a successful response.
        page.route("**/api/auth/me", lambda route: route.fulfill(status=200, body="not JSON"))
        with page.expect_response("**/api/auth/me"):
            page.goto(vault_url)
        page.wait_for_function("document.querySelector('wiki-page')._loaded")
        assert page.url == vault_url


def test_browser_auth_logout_redirects_guard(built_static, chromium, config):
    seed_vault_page(config)
    with browser_scenario(built_static, chromium, config) as scenario:
        page, base = scenario.page, scenario.base
        vault_url = base + VAULT_PAGE_URL_PATH
        # Real generated logout deletes the browser cookie. The guard's
        # unauthenticated HTTP response redirects the standalone page.
        page.evaluate("""async () => {
            const { AuthClient } = await import('/static/lib/auth-client.js');
            await new AuthClient().logout();
        }""")
        assert not any(cookie["name"] == "decafclaw_session" for cookie in page.context.cookies())
        assert [(request.method, request.path) for request in scenario.requests] == [
            ("POST", "/api/auth/logout"),
        ]
        page.route(base + "/", lambda route: route.fulfill(body="Login destination"))
        # /vault itself requires auth. Serve the real shell as though
        # it loaded before the session expired, then exercise its guard
        # against the real unauthenticated backend response.
        page.route(vault_url, lambda route: route.fulfill(
            content_type="text/html", body=(scenario.static / "vault.html").read_text(),
        ))
        with page.expect_response("**/api/auth/me") as guard_response:
            page.goto(vault_url)
        assert guard_response.value.status == 401
        page.wait_for_url(base + "/")
        assert base + "/api/auth/me" in scenario.failed_requests
        # The page's own load can race the redirect; the backend refuses it too.
        assert set(scenario.failed_requests) <= {
            base + "/api/auth/me",
            base + "/api/vault/agent/pages/Browser%20%26%20%E6%97%A5%E6%9C%AC%E8%AA%9E",
        }, scenario.failed_requests
        scenario.failed_requests.clear()


@pytest.mark.parametrize("operation,model", [
    ("list_conversations", "ConversationListingResponse"),
    ("list_archived_conversations", "ConversationListingResponse"),
    ("list_system_conversations", "SystemConversationListingResponse"),
])
@pytest.mark.parametrize("contract", ["query", "response"])
def test_listing_contract_drift_fails_at_unchanged_caller(source_tree, operation, model, contract):
    caller = source_tree / STATIC_REL / "lib/conversation-store.js"
    original_caller = caller.read_bytes()
    result, output = run_make(source_tree, "check-js")
    assert result.returncode == 0, output
    backend = source_tree / "src/decafclaw/http_server.py"
    original = backend.read_text()
    if contract == "query":
        changed = original.replace(
            f'async def {operation}(request: Request, folder: str = "")',
            f'async def {operation}(request: Request, folder: int = 0)',
        )
        diagnostic = "Argument of type 'string' is not assignable to parameter of type 'number'"
        code = "TS2345"
    else:
        # Give only the selected operation a changed envelope, including when
        # active and archived share the regular response model.
        start = original.index(f"class {model}(BaseModel):")
        end = original.index("\n\n\n", start)
        isolated = original[start:end].replace(model, "ChangedListingResponse").replace(
            "    conversations:", "    renamed_conversations:")
        changed = original[:start] + isolated + "\n\n\n" + original[start:]
        route = changed.index(f', {operation}, methods=["GET"],')
        prefix, suffix = changed[:route], changed[route:]
        changed = prefix + suffix.replace(f"response_model={model}", "response_model=ChangedListingResponse", 1)
        diagnostic = "Property 'conversations' does not exist on type 'ChangedListingResponse'"
        code = "TS2339"
    assert changed != original
    backend.write_text(changed)
    result, output = run_make(source_tree, "check-js")
    assert result.returncode != 0, output
    diagnostics = [line for line in output.splitlines() if "error TS" in line]
    assert len(diagnostics) == 1, output
    assert "conversation-store.js(" in diagnostics[0] and code in diagnostics[0], output
    assert diagnostic in diagnostics[0], output
    method = {"list_conversations": "listConversations", "list_archived_conversations": "listArchivedConversations",
              "list_system_conversations": "listSystemConversations"}[operation]
    lines = original_caller.decode().splitlines()
    start = next(i for i, line in enumerate(lines, 1) if f"async {method}(" in line)
    target = next(i for i, line in enumerate(lines[start:], start + 1)
                  if ("await DefaultService." if contract == "query" else "= data.conversations") in line)
    assert f"conversation-store.js({target}," in diagnostics[0], output
    assert caller.read_bytes() == original_caller


@pytest.mark.parametrize("contract", ["identifier", "title", "folder", "response"])
def test_patch_contract_drift_fails_at_unchanged_caller(source_tree, contract):
    caller = source_tree / STATIC_REL / "lib/conversation-store.js"
    original_caller = caller.read_bytes()
    result, output = run_make(source_tree, "check-js")
    assert result.returncode == 0, output
    backend = source_tree / "src/decafclaw/http_server.py"
    original = backend.read_text()
    if contract == "identifier":
        changed = original.replace("async def rename_conversation(request: Request, id: str)",
                                   "async def rename_conversation(request: Request, id: int)")
    elif contract in {"title", "folder"}:
        start = original.index("class ConversationPatchRequest(BaseModel):")
        end = original.index("\n\n\n", start)
        changed = (original[:start] + original[start:end].replace(
            f"    {contract}: str | None", f"    {contract}: int | None") + original[end:])
    else:
        start = original.index("class ConversationPatchResponse(TypedDict):")
        end = original.index("\n\n\n", start)
        changed = original[:start] + original[start:end].replace("    title: str", "    title: int") + original[end:]
    assert changed != original
    backend.write_text(changed)
    result, output = run_make(source_tree, "check-js")
    assert result.returncode != 0, output
    diagnostics = [line for line in output.splitlines() if "error TS" in line]
    lines = original_caller.decode().splitlines()
    rename = next(i for i, line in enumerate(lines, 1) if "const updated = await DefaultService.renameConversation" in line)
    move = next(i for i, line in enumerate(lines, 1) if "{ folder }, true" in line)
    merge = next(i for i, line in enumerate(lines, 1) if "this.#conversations = this.#conversations.map" in line)
    # A rejected rename overload also makes its result non-spreadable. Require
    # the direct argument error as well as that known dependent diagnostic.
    expected = {
        "identifier": [(rename, "TS2345"), (merge + 1, "TS2698"), (move, "TS2345")],
        "title": [(rename, "TS2322"), (merge + 1, "TS2698")],
        "folder": [(move, "TS2322")],
        "response": [(merge, "TS2322")],
    }[contract]
    assert len(diagnostics) == len(expected), output
    for diagnostic, (target, code) in zip(diagnostics, expected, strict=True):
        assert f"conversation-store.js({target}," in diagnostic and code in diagnostic, output
    if contract == "response":
        assert "Types of property 'title' are incompatible" in output, output
        assert "Type 'number' is not assignable to type 'string'" in output, output
    else:
        assert "'string' is not assignable to" in diagnostics[0] and "'number'" in diagnostics[0], output
    assert caller.read_bytes() == original_caller


@pytest.mark.parametrize("contract", ["body", "rename_path", "delete_path"])
def test_folder_contract_drift_fails_at_unchanged_callers(source_tree, contract):
    caller = source_tree / STATIC_REL / "lib/conversation-store.js"
    original_caller = caller.read_bytes()
    result, output = run_make(source_tree, "check-js")
    assert result.returncode == 0, output
    backend = source_tree / "src/decafclaw/http_server.py"
    original = backend.read_text()
    if contract == "body":
        changed = original.replace("class ConversationFolderRequest(BaseModel):\n    path: str",
                                   "class ConversationFolderRequest(BaseModel):\n    path: int")
        methods = ["createConvFolder", "renameConvFolder"]
    else:
        operation = "rename" if contract == "rename_path" else "delete"
        changed = original.replace(f"async def {operation}_conv_folder(request: Request, path: str)",
                                   f"async def {operation}_conv_folder(request: Request, path: int)")
        methods = [operation + "ConvFolder"]
    assert changed != original
    backend.write_text(changed)
    result, output = run_make(source_tree, "check-js")
    assert result.returncode != 0, output
    diagnostics = [line for line in output.splitlines() if "error TS" in line]
    lines = original_caller.decode().splitlines()
    targets = [next(i for i, line in enumerate(lines, 1) if "await DefaultService." + method in line)
               for method in methods]
    assert len(diagnostics) == len(targets), output
    for diagnostic, target in zip(diagnostics, targets, strict=True):
        assert f"conversation-store.js({target}," in diagnostic, output
        assert ("TS2322" if contract == "body" else "TS2345") in diagnostic, output
        assert "'string' is not assignable to" in diagnostic and "'number'" in diagnostic, output
    assert caller.read_bytes() == original_caller


@pytest.mark.parametrize("contract", ["title", "model", "folder", "response", "archive", "unarchive", "delete"])
def test_lifecycle_contract_drift_fails_at_unchanged_caller(source_tree, contract):
    caller = source_tree / STATIC_REL / "lib/conversation-store.js"
    original_caller = caller.read_bytes()
    result, output = run_make(source_tree, "check-js")
    assert result.returncode == 0, output
    backend = source_tree / "src/decafclaw/http_server.py"
    original = backend.read_text()
    if contract in {"archive", "unarchive", "delete"}:
        changed = original.replace(f"async def {contract}_conversation(request: Request, id: str)",
                                   f"async def {contract}_conversation(request: Request, id: int)")
        target_text = f"await DefaultService.{contract}Conversation"
        code = "TS2345"
    else:
        model = "ConversationCreateResponse(TypedDict)" if contract == "response" else "ConversationCreateRequest(BaseModel)"
        start = original.index(f"class {model}:")
        end = original.index("\n\n\n", start)
        field = "title" if contract == "response" else contract
        changed = original[:start] + original[start:end].replace(f"    {field}: str", f"    {field}: int") + original[end:]
        target_text = "this.#conversations.unshift(conv)" if contract == "response" else (
            "title, ...(model" if contract == "title" else "const conv = await DefaultService.createConversation")
        code = "TS2322" if contract == "title" else "TS2345"
    assert changed != original
    backend.write_text(changed)
    result, output = run_make(source_tree, "check-js")
    assert result.returncode != 0, output
    diagnostics = [line for line in output.splitlines() if "error TS" in line]
    target = next(i for i, line in enumerate(original_caller.decode().splitlines(), 1) if target_text in line)
    assert len(diagnostics) == 1, output
    assert f"conversation-store.js({target}," in diagnostics[0] and code in diagnostics[0], output
    if contract == "response":
        assert "Types of property 'title' are incompatible" in output, output
        assert "Type 'number' is not assignable to type 'string'" in output, output
    else:
        assert "'string' is not assignable to" in output and "'number'" in output, output
    assert caller.read_bytes() == original_caller


@pytest.mark.parametrize("contract", ["request", "response"])
def test_login_contract_drift_fails_at_unchanged_caller(source_tree, contract):
    result, output = run_make(source_tree, "check-js")
    assert result.returncode == 0, output
    caller = source_tree / STATIC_REL / "lib/auth-client.js"
    original_caller = caller.read_bytes()
    backend = source_tree / "src/decafclaw/http_server.py"
    original = backend.read_text()
    if contract == "request":
        changed = original.replace("class LoginRequest(BaseModel):\n    token: str",
                                   "class LoginRequest(BaseModel):\n    token: int")
        diagnostic = "Type 'string' is not assignable to type 'number'"
        code = "TS2322"
    else:
        changed = original.replace("class LoginResponse(BaseModel):\n    username:",
                                   "class LoginResponse(BaseModel):\n    renamed_username:")
        diagnostic = "Property 'username' does not exist on type 'LoginResponse'"
        code = "TS2339"
    assert changed != original
    backend.write_text(changed)
    result, output = run_make(source_tree, "check-js")
    assert result.returncode != 0, output
    diagnostics = [line for line in output.splitlines() if "error TS" in line]
    assert len(diagnostics) == (1 if contract == "request" else 3), output
    assert all("auth-client.js(" in line and code in line and diagnostic in line for line in diagnostics), output
    assert caller.read_bytes() == original_caller


def test_vault_guard_is_typechecked(source_tree):
    caller = source_tree / STATIC_REL / "lib/vault-auth.js"
    original = caller.read_text()
    changed = original.replace("authMeApiAuthMeGet(true)", "authMeApiAuthMeGet('invalid')")
    assert changed != original
    caller.write_text(changed)
    result, output = run_make(source_tree, "check-js")
    assert result.returncode != 0, output
    diagnostics = [line for line in output.splitlines() if "error TS" in line]
    assert len(diagnostics) == 1, output
    assert "vault-auth.js(" in diagnostics[0] and "TS2345" in diagnostics[0], output


def test_auth_generated_contracts(source_tree):
    result, output = run_make(source_tree, "gen-api-client")
    assert result.returncode == 0, output
    service = (source_tree / CLIENT_REL / "services/DefaultService.ts").read_text()
    assert "token: string" in service
    assert "): CancelablePromise<LoginResponse>" in service
    assert "): CancelablePromise<LogoutResponse>" in service
    assert "): CancelablePromise<UserResponse>" in service
    for model, field in [("LoginResponse", "username: string"), ("LogoutResponse", "ok: boolean")]:
        assert field in (source_tree / CLIENT_REL / f"models/{model}.ts").read_text()


@pytest.mark.parametrize("contract", ["context_id", "export_id", "format", "source", "details", "candidate", "cache", "window"])
def test_context_export_contract_drift_fails_at_unchanged_caller(source_tree, contract):
    result, output = run_make(source_tree, "check-js")
    assert result.returncode == 0, output
    backend = source_tree / "src/decafclaw/http_server.py"
    original = backend.read_text()
    component = "copy-conversation-menu.js" if contract in {"export_id", "format"} else "context-inspector.js"
    caller = source_tree / STATIC_REL / "components" / component
    original_caller = caller.read_bytes()
    if contract in {"context_id", "export_id"}:
        operation = "get_context_diagnostics" if contract == "context_id" else "export_conversation"
        changed = original.replace(f"async def {operation}(request: Request, id: str)",
                                   f"async def {operation}(request: Request, id: int)")
        diagnostic = "Argument of type 'string' is not assignable to parameter of type 'number'"
        code = "TS2345"
    elif contract == "format":
        changed = original.replace('"enum": ["jsonl", "markdown"]', '"enum": ["html"]')
        diagnostic = "is not assignable to parameter of type"
        code = "TS2345"
    else:
        model, field = {
            "source": ("ContextSource", "tokens_estimated"),
            "details": ("ContextSourceDetails", "matches"),
            "candidate": ("ContextCandidate", "file_path"),
            "cache": ("ContextDiagnosticsResponse", "cached_prompt_tokens"),
            "window": ("ContextDiagnosticsResponse", "context_window_size"),
        }[contract]
        start = original.index(f"class {model}(BaseModel):")
        end = original.index("\n\n", start)
        changed = original[:start] + original[start:end].replace(f"    {field}:", f"    renamed_{field}:") + original[end:]
        diagnostic = f"Property '{field}' does not exist on type '{model}'"
        code = "TS2339"
    assert changed != original
    backend.write_text(changed)
    result, output = run_make(source_tree, "check-js")
    assert result.returncode != 0, output
    diagnostics = [line for line in output.splitlines() if "error TS" in line]
    assert any(component in line and code in line and diagnostic in line for line in diagnostics), output
    assert all(component in line for line in diagnostics), output
    assert caller.read_bytes() == original_caller


@pytest.mark.parametrize("contract", ["limit", "id", "records", "count", "title", "link"])
def test_notification_contract_drift_fails_at_unchanged_caller(source_tree, contract):
    result, output = run_make(source_tree, "check-js")
    assert result.returncode == 0, output
    backend = source_tree / "src/decafclaw/http_server.py"
    original = backend.read_text()
    caller = source_tree / STATIC_REL / "components/notification-inbox.js"
    original_caller = caller.read_bytes()
    if contract == "limit":
        changed = original.replace('"type": "integer", "default": 20, "minimum": 1, "maximum": 200',
                                   '"type": "string", "default": "20"')
        diagnostic, code = "not assignable to parameter of type 'string'", "TS2345"
    elif contract == "id":
        changed = original.replace('async def notifications_mark_read(request: Request, id: str)',
                                   'async def notifications_mark_read(request: Request, id: int)')
        diagnostic, code = "not assignable to parameter of type 'number'", "TS2345"
    else:
        model = {"records": "NotificationListResponse", "count": "NotificationCountResponse"}.get(contract, "NotificationResponse")
        start = original.index(f"class {model}(BaseModel):")
        end = original.index("\n\n", start)
        changed = original[:start] + original[start:end].replace(f"    {contract}:", f"    renamed_{contract}:") + original[end:]
        diagnostic, code = f"Property '{contract}' does not exist", "TS2339"
    assert changed != original
    backend.write_text(changed)
    result, output = run_make(source_tree, "check-js")
    assert result.returncode != 0, output
    diagnostics = [line for line in output.splitlines() if "error TS" in line]
    assert any("notification-inbox.js(" in line and code in line and diagnostic in line for line in diagnostics), output
    assert all("notification-inbox.js(" in line for line in diagnostics), output
    assert caller.read_bytes() == original_caller


def _mutate_canvas_contract(source_tree, replacements):
    result, output = run_make(source_tree, "check-js")
    assert result.returncode == 0, output
    backend = source_tree / "src/decafclaw/http_server.py"
    original = backend.read_text()
    changed = original
    for before, after in replacements:
        assert before in changed
        changed = changed.replace(before, after)
    backend.write_text(changed)
    result, output = run_make(source_tree, "check-js")
    assert result.returncode != 0, output
    assert "error TS" in output, output
    return output


def _canvas_caller_diagnostic_prefix(source_tree, caller, operation, argument):
    caller_path = source_tree / "src/decafclaw/web/static" / caller
    lines = caller_path.read_text().splitlines()
    operation_line = next(i for i, line in enumerate(lines) if operation in line)
    argument_line = next(
        i for i, line in enumerate(lines[operation_line:operation_line + 15], operation_line)
        if argument in line
    )
    return f"{caller}({argument_line + 1},"


@pytest.mark.parametrize("contract", [
    "state_path", "new_tab_path", "active_tab_path", "close_tab_path",
    "widget_type", "data", "label", "tab_id",
])
def test_canvas_used_input_contract_drift_fails_at_unchanged_callers(source_tree, contract):
    new_tab_callers = [
        ("widgets/code_block/widget.js", "widget_type: 'code_block'"),
        ("widgets/diff_view/widget.js", "widget_type: 'diff_view'"),
        ("widgets/json_view/widget.js", "widget_type: 'json_view'"),
        ("widgets/markdown_document/widget.js", "widget_type: 'markdown_document'"),
    ]
    if contract.endswith("_path"):
        operation = {
            "state_path": "get_canvas_state",
            "new_tab_path": "post_canvas_new_tab",
            "active_tab_path": "post_canvas_active_tab",
            "close_tab_path": "post_canvas_close_tab",
        }[contract]
        replacements = [(f"async def {operation}(request: Request, conv_id: str)",
                         f"async def {operation}(request: Request, conv_id: int)")]
        expectations = {
            "state_path": [
                ("canvas-page.js", "getCanvasStateApiCanvasConvIdGet", "getCanvasStateApiCanvasConvIdGet"),
                ("lib/canvas-state.js", "getCanvasStateApiCanvasConvIdGet", "getCanvasStateApiCanvasConvIdGet"),
            ],
            "new_tab_path": [
                (caller, "postCanvasNewTabApiCanvasConvIdNewTabPost", "convId")
                for caller, _ in new_tab_callers
            ],
            "active_tab_path": [
                ("lib/canvas-state.js", "postCanvasActiveTabApiCanvasConvIdActiveTabPost",
                 "convId"),
            ],
            "close_tab_path": [
                ("lib/canvas-state.js", "postCanvasCloseTabApiCanvasConvIdCloseTabPost",
                 "convId"),
            ],
        }[contract]
        diagnostic, code = "not assignable to parameter of type 'number'", "TS2345"
    elif contract == "tab_id":
        replacements = [("class CanvasTabRequest(BaseModel):\n    tab_id: str",
                         "class CanvasTabRequest(BaseModel):\n    tab_id: int")]
        expectations = [
            ("lib/canvas-state.js", "postCanvasActiveTabApiCanvasConvIdActiveTabPost", "tab_id: tabId"),
            ("lib/canvas-state.js", "postCanvasCloseTabApiCanvasConvIdCloseTabPost", "tab_id: tabId"),
        ]
        diagnostic, code = "Type 'string' is not assignable to type 'number'", "TS2322"
    else:
        replacement = {
            "widget_type": ("    widget_type: str", "    widget_type: int"),
            "data": ("    data: dict[str, object]", "    renamed_data: dict[str, object]"),
            "label": ("    label: str | None = None", "    label: int | None = None"),
        }[contract]
        replacements = [replacement]
        argument = {
            "widget_type": lambda marker: marker,
            "data": lambda marker: "data:",
            "label": lambda marker: "label",
        }[contract]
        expectations = [
            (caller, "postCanvasNewTabApiCanvasConvIdNewTabPost", argument(marker))
            for caller, marker in new_tab_callers
        ]
        diagnostic, code = {
            "widget_type": ("Type 'string' is not assignable to type 'number'", "TS2322"),
            "data": ("'data' does not exist", "TS2353"),
            "label": ("Type 'string' is not assignable to type 'number'", "TS2322"),
        }[contract]
    output = _mutate_canvas_contract(source_tree, replacements)
    diagnostics = [line for line in output.splitlines() if "error TS" in line]
    for caller, operation, argument in expectations:
        prefix = _canvas_caller_diagnostic_prefix(source_tree, caller, operation, argument)
        assert any(line.startswith(prefix) and code in line and diagnostic in line
                   for line in diagnostics), output


def test_canvas_state_field_drift_fails_at_unchanged_callers(source_tree):
    output = _mutate_canvas_contract(source_tree, [
        ("    active_tab: str | None\n    next_tab_id: int\n    tabs:",
         "    renamed_active_tab: str | None\n    next_tab_id: int\n    renamed_tabs:"),
    ])
    assert "canvas-state.js" in output and "canvas-page.js" in output, output
    assert "Property 'tabs' does not exist on type 'CanvasStateResponse'" in output, output
    assert "Property 'active_tab' does not exist on type 'CanvasStateResponse'" in output, output


@pytest.mark.parametrize("field", ["id", "label", "widget_type", "data"])
def test_canvas_tab_field_drift_fails_at_unchanged_callers(source_tree, field):
    before = {
        "id": "class CanvasTabResponse(BaseModel):\n    id:",
        "label": "    id: str\n    label:",
        "widget_type": "    label: str\n    widget_type:",
        "data": "    label: str\n    widget_type: str\n    data:",
    }[field]
    output = _mutate_canvas_contract(source_tree, [
        (before, before.replace(f"    {field}:", f"    renamed_{field}:"))
    ])
    assert "canvas-page.js" in output or "canvas-state.js" in output, output
    assert f"Property '{field}' does not exist on type 'CanvasTabResponse'" in output, output


def test_widget_catalog_envelope_drift_fails_at_unchanged_caller(source_tree):
    output = _mutate_canvas_contract(source_tree, [
        ("class WidgetCatalogResponse(BaseModel):\n    widgets:",
         "class WidgetCatalogResponse(BaseModel):\n    renamed_widgets:"),
    ])
    assert "widget-catalog.js" in output and "TS2339" in output, output
    assert "Property 'widgets' does not exist on type 'WidgetCatalogResponse'" in output, output


@pytest.mark.parametrize("field", ["name", "js_url"])
def test_widget_descriptor_field_drift_fails_at_unchanged_callers(source_tree, field):
    before = {
        "name": "class WidgetDescriptorResponse(BaseModel):\n    name:",
        "js_url": "    data_schema: dict[str, JsonValue]\n    js_url:",
    }[field]
    output = _mutate_canvas_contract(source_tree, [
        (before, before.replace(f"    {field}:", f"    renamed_{field}:"))
    ])
    caller = "widget-catalog.js" if field == "name" else "widget-host.js"
    assert caller in output and "TS2339" in output, output
    assert f"Property '{field}' does not exist on type 'WidgetDescriptorResponse'" in output, output


def _mutate_workspace_contract(source_tree, mutate):
    result, output = run_make(source_tree, "check-js")
    assert result.returncode == 0, output
    backend = source_tree / "src/decafclaw/http_server.py"
    original = backend.read_text()
    changed = mutate(original)
    assert changed != original
    backend.write_text(changed)
    result, output = run_make(source_tree, "check-js")
    assert result.returncode != 0, output
    assert "error TS" in output, output
    return output


@pytest.mark.parametrize("contract", ["folder", "path", "query"])
def test_workspace_read_input_drift_fails_at_every_unchanged_call(source_tree, contract):
    callers = {
        "folder": ("components/files-sidebar.js", "wrapperApiWorkspaceGet"),
        "path": ("components/file-page.js", "wrapperApiWorkspaceFilePathGet"),
        "query": ("components/chat-input.js", "wrapperApiAutocompleteGet"),
    }
    caller, operation = callers[contract]
    caller_path = source_tree / STATIC_REL / caller
    original_caller = caller_path.read_bytes()
    expected_lines = [
        line_no for line_no, line in enumerate(original_caller.decode().splitlines(), 1)
        if f"DefaultService.{operation}(" in line
    ]

    def mutate(original):
        parameter = "q" if contract == "query" else contract
        before = (f'"name": "{parameter}", "in": "'
                  + ("path" if contract == "path" else "query")
                  + '", "required": ' + ("True" if contract != "folder" else "False")
                  + ',\n                     "schema": {"type": "string"},')
        after = before.replace('"type": "string"', '"type": "integer"')
        if contract == "path":
            start = original.index(
                'APIRoute("/api/workspace-file/{path:path}", workspace_read_json')
            end = original.index(
                'APIRoute("/api/workspace/{path:path}", serve_workspace_file', start)
            block = original[start:end]
            assert block.count(before) == 1
            return original[:start] + block.replace(before, after) + original[end:]
        if contract == "folder":
            start = original.index('APIRoute("/api/workspace", workspace_list')
            end = original.index('APIRoute("/api/workspace", workspace_create', start)
            block = original[start:end]
            assert block.count(before) == 1
            return original[:start] + block.replace(before, after) + original[end:]
        assert original.count(before) == 1
        return original.replace(before, after)

    output = _mutate_workspace_contract(source_tree, mutate)
    diagnostics = [line for line in output.splitlines() if "error TS" in line]
    assert len(diagnostics) == len(expected_lines), output
    for line_no in expected_lines:
        assert any(
            line.startswith(f"{caller}({line_no},") and "TS2345" in line
            and "not assignable to parameter of type 'number'" in line
            for line in diagnostics
        ), output
    assert caller_path.read_bytes() == original_caller


@pytest.mark.parametrize(("model", "field", "caller"), [
    ("WorkspaceListingResponse", "folders", "components/files-sidebar.js"),
    ("WorkspaceListingResponse", "files", "components/files-sidebar.js"),
    ("WorkspaceRecentResponse", "files", "components/files-sidebar.js"),
    ("WorkspaceFolderEntry", "name", "components/files-sidebar.js"),
    ("WorkspaceFolderEntry", "path", "components/files-sidebar.js"),
    *[("WorkspaceFileEntry", field, "components/files-sidebar.js") for field in
      ("name", "path", "size", "modified", "kind", "readonly", "secret")],
    *[("WorkspaceTextResponse", field, "components/file-page.js") for field in
      ("content", "modified", "readonly")],
    ("AutocompleteResponse", "results", "components/chat-input.js"),
    *[(model, field, "components/chat-input.js")
      for model in ("VaultCompletion", "McpCompletion", "FileCompletion")
      for field in ("type", "id", "label", "description")],
])
def test_workspace_read_output_drift_fails_at_unchanged_caller(
    source_tree, model, field, caller,
):
    caller_path = source_tree / STATIC_REL / caller
    original_caller = caller_path.read_bytes()

    def mutate(original):
        start = original.index(f"class {model}(BaseModel):")
        end = original.index("\n\n\n", start)
        block = original[start:end]
        before = f"    {field}:"
        assert block.count(before) == 1
        changed_block = block.replace(before, f"    renamed_{field}:")
        return original[:start] + changed_block + original[end:]

    output = _mutate_workspace_contract(source_tree, mutate)
    diagnostics = [line for line in output.splitlines() if "error TS" in line]
    expected = f"Property '{field}' does not exist"
    assert any(caller in line and ("TS2339" in line or "TS2551" in line)
               and expected in line
               for line in diagnostics), output
    assert all(caller in line for line in diagnostics), output
    assert caller_path.read_bytes() == original_caller


@pytest.mark.parametrize("drift", ["rename", "add"])
def test_completion_variant_drift_fails_at_unchanged_routing(source_tree, drift):
    caller = source_tree / STATIC_REL / "components/chat-input.js"
    original_caller = caller.read_bytes()

    def mutate(original):
        if drift == "rename":
            before = 'class McpCompletion(BaseModel):\n    type: Literal["mcp"]'
            assert original.count(before) == 1
            return original.replace(before, before.replace('"mcp"', '"resource"'))

        file_completion = '''class FileCompletion(BaseModel):
    type: Literal["file"]
    id: str
    label: str
    description: str'''
        added_completion = '''class AddedCompletion(BaseModel):
    type: Literal["added"]
    id: str
    label: str
    description: str'''
        assert original.count(file_completion) == 1
        union = "list[VaultCompletion | McpCompletion | FileCompletion]"
        assert original.count(union) == 1
        return original.replace(
            file_completion,
            file_completion + "\n\n\n" + added_completion,
        ).replace(union, union[:-1] + " | AddedCompletion]")

    output = _mutate_workspace_contract(source_tree, mutate)
    diagnostics = [line for line in output.splitlines() if "error TS" in line]
    assert diagnostics and all("components/chat-input.js(" in line for line in diagnostics), output
    if drift == "rename":
        assert any("TS2367" in line and "no overlap" in line for line in diagnostics), output
    else:
        assert len(diagnostics) == 1, output
        assert "TS2345" in diagnostics[0] and "parameter of type 'never'" in diagnostics[0], output
    assert caller.read_bytes() == original_caller


def test_workspace_read_generated_contracts(source_tree):
    result, output = run_make(source_tree, "gen-api-client")
    assert result.returncode == 0, output
    service = (source_tree / CLIENT_REL / "services/DefaultService.ts").read_text()
    for signature in (
        "folder?: string",
        "q: string",
        "path: string",
        "): CancelablePromise<WorkspaceListingResponse>",
        "): CancelablePromise<WorkspaceRecentResponse>",
        "): CancelablePromise<WorkspaceTextResponse>",
        "): CancelablePromise<AutocompleteResponse>",
    ):
        assert signature in service
    for model in (
        "WorkspaceListingResponse", "WorkspaceRecentResponse", "WorkspaceTextResponse",
        "WorkspaceFolderEntry", "WorkspaceFileEntry", "AutocompleteResponse",
        "VaultCompletion", "McpCompletion", "FileCompletion",
    ):
        generated = (source_tree / CLIENT_REL / f"models/{model}.ts").read_text()
        assert "any" not in generated
    for model, literal in (
        ("VaultCompletion", "vault"),
        ("McpCompletion", "mcp"),
        ("FileCompletion", "file"),
    ):
        generated = (source_tree / CLIENT_REL / f"models/{model}.ts").read_text()
        assert f"type: {model}.type;" in generated
        assert f"{literal.upper()} = '{literal}'" in generated


def test_upload_and_native_workspace_generated_contracts(source_tree):
    result, output = run_make(source_tree, "gen-api-client")
    assert result.returncode == 0, output
    service = (source_tree / CLIENT_REL / "services/DefaultService.ts").read_text()
    upload_start = service.index("public static wrapperApiUploadConvIdPost(")
    upload_end = service.index("    /**", upload_start)
    upload = service[upload_start:upload_end]
    for signature in (
        "convId: string",
        "file: Blob",
        "CancelablePromise<AttachmentResponse>",
        "formData: formData",
    ):
        assert signature in upload
    attachment = (source_tree / CLIENT_REL / "models/AttachmentResponse.ts").read_text()
    for field in ("filename: string", "path: string", "mime_type: string"):
        assert field in attachment
    assert "any" not in attachment

    native_start = service.index("public static wrapperApiWorkspacePathGet(")
    native_end = service.index("    /**", native_start)
    native = service[native_start:native_end]
    assert "path: string" in native
    assert "CancelablePromise<Blob>" in native
    assert "CancelablePromise<any>" not in native
    assert "url: '/api/workspace/{path}'" in native
    index = (source_tree / CLIENT_REL / "index.ts").read_text()
    assert "Parameters<" in index
    assert "typeof __DefaultService.wrapperApiWorkspacePathGet" in index
    assert "buildNativeWorkspaceUrl" in index

    schema = json.loads((source_tree / "openapi.json").read_text())
    upload_operation = schema["paths"]["/api/upload/{conv_id}"]["post"]
    assert upload_operation["responses"]["201"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/AttachmentResponse",
    }
    file_schema = upload_operation["requestBody"]["content"]["multipart/form-data"]["schema"]
    assert file_schema["required"] == ["file"]
    assert file_schema["properties"]["file"] == {"type": "string", "format": "binary"}
    native_response = schema["paths"]["/api/workspace/{path}"]["get"]["responses"]["200"]
    assert native_response["content"]["application/octet-stream"]["schema"] == {
        "type": "string", "format": "binary",
    }
    assert set(native_response["headers"]) == {
        "X-Content-Type-Options", "Content-Disposition",
    }


@pytest.mark.parametrize(
    "contract",
    ["upload_conv_id", "upload_file", "filename", "path", "mime_type", "native_path"],
)
def test_upload_and_native_workspace_contract_drift_fails_at_unchanged_callers(
    source_tree, contract,
):
    callers = {
        "upload-client.js": source_tree / STATIC_REL / "lib/upload-client.js",
        "conversation-store.js": source_tree / STATIC_REL / "lib/conversation-store.js",
        "chat-input.js": source_tree / STATIC_REL / "components/chat-input.js",
        "user-message.js": source_tree / STATIC_REL / "components/messages/user-message.js",
        "file-page.js": source_tree / STATIC_REL / "components/file-page.js",
        "markdown.js": source_tree / STATIC_REL / "lib/markdown.js",
    }
    originals = {name: path.read_bytes() for name, path in callers.items()}

    def mutate(original):
        if contract in {"filename", "path", "mime_type"}:
            before = "class AttachmentResponse(BaseModel):\n"
            start = original.index(before)
            end = original.index("\n\n\n", start)
            block = original[start:end]
            marker = f"    {contract}:"
            assert block.count(marker) == 1
            return original[:start] + block.replace(
                marker, f"    renamed_{contract}:",
            ) + original[end:]

        route = "handle_upload" if contract.startswith("upload_") else "serve_workspace_file"
        start = original.index('APIRoute("/api/' + (
            'upload/{conv_id}", handle_upload' if route == "handle_upload"
            else 'workspace/{path:path}", serve_workspace_file'
        ))
        end = original.index("        APIRoute(", start + 10)
        block = original[start:end]
        if contract == "upload_file":
            before = '"properties": {"file": {"type": "string", "format": "binary"}}'
            assert block.count(before) == 1
            changed = block.replace(before, '"properties": {"file": {"type": "integer"}}')
        else:
            parameter = "conv_id" if contract == "upload_conv_id" else "path"
            marker = f'"name": "{parameter}", "in": "path"'
            parameter_start = block.index(marker)
            schema_start = block.index('"schema": {"type": "string"}', parameter_start)
            changed = (block[:schema_start] + '"schema": {"type": "integer"}'
                       + block[schema_start + len('"schema": {"type": "string"}'):])
        return original[:start] + changed + original[end:]

    output = _mutate_workspace_contract(source_tree, mutate)
    diagnostics = [line for line in output.splitlines() if "error TS" in line]
    expected_callers = {
        "upload_conv_id": {"lib/conversation-store.js", "components/chat-input.js"},
        "upload_file": {"lib/conversation-store.js", "components/chat-input.js"},
        "filename": {"components/messages/user-message.js"},
        "path": {"components/messages/user-message.js"},
        "mime_type": {"components/messages/user-message.js"},
        "native_path": {
            "components/messages/user-message.js", "components/file-page.js", "lib/markdown.js",
        },
    }[contract]
    for caller in expected_callers:
        assert any(line.startswith(caller + "(") for line in diagnostics), output
    assert all("TS23" in line for line in diagnostics), output
    for name, path in callers.items():
        assert path.read_bytes() == originals[name]


def test_vault_read_generated_contracts(source_tree):
    result, output = run_make(source_tree, "gen-api-client")
    assert result.returncode == 0, output
    service = (source_tree / CLIENT_REL / "services/DefaultService.ts").read_text()
    for signature in (
        "folder?: string",
        "page: string",
        "): CancelablePromise<VaultListingResponse>",
        "): CancelablePromise<VaultRecentResponse>",
        "): CancelablePromise<VaultTagsResponse>",
        "): CancelablePromise<VaultPageResponse>",
    ):
        assert signature in service
    models = (
        "VaultFolderEntry", "VaultPageListEntry", "VaultListingResponse",
        "VaultRecentResponse", "VaultTagEntry", "VaultTagsResponse",
        "VaultPageResponse",
    )
    for model in models:
        generated = (source_tree / CLIENT_REL / f"models/{model}.ts").read_text()
        assert "any" not in generated
    page = (source_tree / CLIENT_REL / "models/VaultPageResponse.ts").read_text()
    assert "frontmatter: Record<string, JsonValue>" in page
    arbitrary = (source_tree / CLIENT_REL / "models/JsonValue.ts").read_text()
    assert "export type JsonValue = unknown" in arbitrary


@pytest.mark.parametrize("contract", ["folder", "page"])
def test_vault_read_input_drift_fails_at_every_unchanged_call(source_tree, contract):
    callers = {
        "folder": [("components/vault-sidebar.js", "wrapperApiVaultGet")],
        "page": [
            ("components/wiki-page.js", "wrapperApiVaultPageGet"),
            ("components/wiki-editor.js", "wrapperApiVaultPageGet"),
        ],
    }[contract]
    originals = {
        caller: (source_tree / STATIC_REL / caller).read_bytes()
        for caller, _operation in callers
    }
    diagnostic_markers = {
        "components/vault-sidebar.js": "this._vaultFolder || undefined",
        "components/wiki-page.js": "wrapperApiVaultPageGet(this.page)",
        "components/wiki-editor.js": "wrapperApiVaultPageGet(this.page)",
    }

    def mutate(original):
        route = 'APIRoute("/api/vault", vault_list' if contract == "folder" \
            else 'APIRoute("/api/vault/{page:path}", vault_read'
        start = original.index(route)
        end = original.index("),\n", start) + len("),\n")
        block = original[start:end]
        before = '"schema": {"type": "string"}'
        assert block.count(before) == 1
        return original[:start] + block.replace(
            before, '"schema": {"type": "integer"}',
        ) + original[end:]

    output = _mutate_workspace_contract(source_tree, mutate)
    diagnostics = [line for line in output.splitlines() if "error TS" in line]
    for caller, _operation in callers:
        expected_lines = [
            line_no for line_no, line in enumerate(originals[caller].decode().splitlines(), 1)
            if diagnostic_markers[caller] in line
        ]
        assert expected_lines
        for line_no in expected_lines:
            assert any(
                diagnostic.startswith(f"{caller}({line_no},")
                and "TS2345" in diagnostic
                and "not assignable to parameter of type 'number'" in diagnostic
                for diagnostic in diagnostics
            ), output
        assert (source_tree / STATIC_REL / caller).read_bytes() == originals[caller]


@pytest.mark.parametrize(("model", "field", "callers"), [
    ("VaultListingResponse", "folders", ["components/vault-sidebar.js"]),
    ("VaultListingResponse", "pages", ["components/vault-sidebar.js"]),
    ("VaultRecentResponse", "pages", ["components/vault-sidebar.js"]),
    ("VaultTagsResponse", "tags", ["components/tags-sidebar.js"]),
    *[("VaultFolderEntry", field, ["components/vault-sidebar.js"])
      for field in ("name", "path")],
    *[("VaultPageListEntry", field, ["components/vault-sidebar.js"])
      for field in ("title", "path", "folder", "modified", "summary")],
    *[("VaultTagEntry", field, ["components/tags-sidebar.js"])
      for field in ("tag", "count", "pages")],
    ("VaultPageResponse", "title", ["components/wiki-page.js"]),
    ("VaultPageResponse", "body", ["components/wiki-page.js", "components/wiki-editor.js"]),
    ("VaultPageResponse", "modified", ["components/wiki-page.js", "components/wiki-editor.js"]),
    *[("VaultPageResponse", field, ["components/wiki-page.js"])
      for field in ("frontmatter", "frontmatter_raw", "frontmatter_error")],
])
def test_vault_read_output_drift_fails_at_unchanged_callers(
    source_tree, model, field, callers,
):
    originals = {
        caller: (source_tree / STATIC_REL / caller).read_bytes()
        for caller in callers
    }

    def mutate(original):
        start = original.index(f"class {model}(BaseModel):")
        end = original.index("\n\n\n", start)
        block = original[start:end]
        before = f"    {field}:"
        assert block.count(before) == 1
        return original[:start] + block.replace(
            before, f"    renamed_{field}:",
        ) + original[end:]

    output = _mutate_workspace_contract(source_tree, mutate)
    diagnostics = [line for line in output.splitlines() if "error TS" in line]
    for caller in callers:
        assert any(
            caller in diagnostic
            and ("TS2339" in diagnostic or "TS2551" in diagnostic)
            and f"Property '{field}' does not exist" in diagnostic
            for diagnostic in diagnostics
        ), output
        assert (source_tree / STATIC_REL / caller).read_bytes() == originals[caller]


@pytest.mark.parametrize(("field", "original_type", "changed_type"), [
    ("body", "str", "int"),
    ("modified", "float", "str"),
])
def test_vault_page_incompatible_types_fail_at_unchanged_editor_assignment(
    source_tree, field, original_type, changed_type,
):
    caller = source_tree / STATIC_REL / "components/wiki-editor.js"
    original_caller = caller.read_bytes()
    assignment = f"new{'Content' if field == 'body' else 'Modified'} = data.{field};"
    target = next(
        line_no for line_no, line in enumerate(original_caller.decode().splitlines(), 1)
        if line.strip() == assignment
    )

    def mutate(original):
        start = original.index("class VaultPageResponse(BaseModel):")
        end = original.index("\n\n\n", start)
        block = original[start:end]
        before = f"    {field}: {original_type}"
        assert block.count(before) == 1
        return original[:start] + block.replace(
            before, f"    {field}: {changed_type}",
        ) + original[end:]

    output = _mutate_workspace_contract(source_tree, mutate)
    diagnostics = [line for line in output.splitlines() if "error TS" in line]
    assert any(
        diagnostic.startswith(f"components/wiki-editor.js({target},")
        and "TS2322" in diagnostic
        and "is not assignable to type" in diagnostic
        for diagnostic in diagnostics
    ), output
    assert caller.read_bytes() == original_caller


def test_vault_recent_modified_type_fails_at_unchanged_formatter_call(source_tree):
    caller = source_tree / STATIC_REL / "components/vault-sidebar.js"
    original_caller = caller.read_bytes()
    call = "this.#formatRelativeTime(p.modified)"
    target = next(
        line_no for line_no, line in enumerate(original_caller.decode().splitlines(), 1)
        if call in line
    )

    def mutate(original):
        start = original.index("class VaultPageListEntry(BaseModel):")
        end = original.index("\n\n\n", start)
        block = original[start:end]
        before = "    modified: float"
        assert block.count(before) == 1
        return original[:start] + block.replace(
            before, "    modified: str",
        ) + original[end:]

    output = _mutate_workspace_contract(source_tree, mutate)
    diagnostics = [line for line in output.splitlines() if "error TS" in line]
    assert any(
        diagnostic.startswith(f"components/vault-sidebar.js({target},")
        and "TS2345" in diagnostic
        and "not assignable to parameter of type 'number'" in diagnostic
        for diagnostic in diagnostics
    ), output
    assert caller.read_bytes() == original_caller


def test_vault_write_generated_contracts(source_tree):
    result, output = run_make(source_tree, "gen-api-client")
    assert result.returncode == 0, output
    service = (source_tree / CLIENT_REL / "services/DefaultService.ts").read_text()
    for method, signatures in {
        "wrapperApiVaultPost": ("name: string", "VaultCreateResponse"),
        "wrapperApiVaultFoldersPost": ("folder: string", "VaultFolderCreateResponse"),
        "wrapperApiVaultPagePut": (
            "page: string", "content?: (string | null)", "body?: (string | null)",
            "modified?: (number | null)",
            "frontmatter?: (Record<string, JsonValue> | null)",
            "frontmatter_raw?: (string | null)", "rename_to?: (string | null)",
            "VaultWriteResponse",
        ),
        "wrapperApiVaultPageDelete": ("page: string", "VaultDeleteResponse"),
    }.items():
        start = service.index(f"public static {method}(")
        end = service.index("    /**", start)
        block = service[start:end]
        for signature in signatures:
            assert signature in block
        assert "any" not in block
    for model in (
        "VaultCreateResponse", "VaultFolderCreateResponse",
        "VaultWriteResponse", "VaultDeleteResponse",
    ):
        assert "any" not in (source_tree / CLIENT_REL / f"models/{model}.ts").read_text()
    assert "frontmatter?: (Record<string, JsonValue> | null)" in service
    assert "export type JsonValue = unknown" in (
        source_tree / CLIENT_REL / "models/JsonValue.ts"
    ).read_text()


@pytest.mark.parametrize(("model", "field", "old_type", "new_type", "caller"), [
    ("VaultCreateRequest", "name", "str", "int", "components/vault-sidebar.js"),
    ("VaultFolderCreateRequest", "folder", "str", "int", "components/vault-sidebar.js"),
    ("VaultWriteRequest", "content", "str", "int", "components/wiki-editor.js"),
    ("VaultWriteRequest", "modified", "float", "str", "components/wiki-editor.js"),
    ("VaultWriteRequest", "frontmatter", "dict[str, JsonValue]", "str",
     "lib/wiki-page-write-mutex.js"),
    ("VaultWriteRequest", "frontmatter_raw", "str", "int",
     "lib/wiki-page-write-mutex.js"),
    ("VaultWriteRequest", "rename_to", "str", "int", "components/wiki-page.js"),
])
def test_vault_write_input_type_drift_fails_at_unchanged_caller(
    source_tree, model, field, old_type, new_type, caller,
):
    caller_path = source_tree / STATIC_REL / caller
    original_caller = caller_path.read_bytes()

    def mutate(original):
        start = original.index(f"class {model}(BaseModel):")
        end = original.index("\n\n\n", start)
        block = original[start:end]
        before = f"    {field}: {old_type}"
        assert block.count(before) == 1
        return original[:start] + block.replace(
            before, f"    {field}: {new_type}",
        ) + original[end:]

    output = _mutate_workspace_contract(source_tree, mutate)
    diagnostics = [line for line in output.splitlines() if "error TS" in line]
    assert any(caller in diagnostic and "assignable" in diagnostic for diagnostic in diagnostics), output
    assert caller_path.read_bytes() == original_caller


def test_vault_write_page_type_drift_fails_at_every_unchanged_caller(source_tree):
    callers = [
        "components/wiki-page.js", "components/wiki-editor.js",
    ]
    originals = {
        caller: (source_tree / STATIC_REL / caller).read_bytes() for caller in callers
    }

    def mutate(original):
        route = 'APIRoute("/api/vault/{page:path}", vault_write'
        start = original.index(route)
        end = original.index("),\n", start) + len("),\n")
        block = original[start:end]
        before = '"schema": {"type": "string"}'
        assert block.count(before) == 1
        return original[:start] + block.replace(
            before, '"schema": {"type": "integer"}',
        ) + original[end:]

    output = _mutate_workspace_contract(source_tree, mutate)
    diagnostics = [line for line in output.splitlines() if "error TS" in line]
    for caller in callers:
        assert any(
            caller in diagnostic and "TS2345" in diagnostic
            and "not assignable to parameter of type 'number'" in diagnostic
            for diagnostic in diagnostics
        ), output
        assert (source_tree / STATIC_REL / caller).read_bytes() == originals[caller]


def test_vault_delete_page_type_drift_fails_at_unchanged_caller(source_tree):
    caller = source_tree / STATIC_REL / "components/wiki-page.js"
    original_caller = caller.read_bytes()
    call = "wrapperApiVaultPageDelete(this.page, true)"
    target = next(
        line_no for line_no, line in enumerate(original_caller.decode().splitlines(), 1)
        if call in line
    )

    def mutate(original):
        route = 'APIRoute("/api/vault/{page:path}", vault_delete'
        start = original.index(route)
        end = original.index("),\n", start) + len("),\n")
        block = original[start:end]
        before = '"schema": {"type": "string"}'
        assert block.count(before) == 1
        return original[:start] + block.replace(
            before, '"schema": {"type": "integer"}',
        ) + original[end:]

    output = _mutate_workspace_contract(source_tree, mutate)
    diagnostics = [line for line in output.splitlines() if "error TS" in line]
    assert any(
        diagnostic.startswith(f"components/wiki-page.js({target},")
        and "TS2345" in diagnostic
        and "not assignable to parameter of type 'number'" in diagnostic
        for diagnostic in diagnostics
    ), output
    assert caller.read_bytes() == original_caller


@pytest.mark.parametrize(("field", "old_type", "new_type", "caller"), [
    ("modified", "float", "str", "components/wiki-editor.js"),
    ("frontmatter", "dict[str, JsonValue]", "str", "components/wiki-page.js"),
    ("frontmatter_raw", "str", "int", "components/wiki-page.js"),
    ("frontmatter_error", "str", "int", "components/wiki-page.js"),
])
def test_vault_write_output_type_drift_fails_at_unchanged_consumer(
    source_tree, field, old_type, new_type, caller,
):
    caller_path = source_tree / STATIC_REL / caller
    original_caller = caller_path.read_bytes()

    def mutate(original):
        start = original.index("class VaultWriteResponse(BaseModel):")
        end = original.index("\n\n\n", start)
        block = original[start:end]
        before = f"    {field}: {old_type}"
        assert block.count(before) == 1
        return original[:start] + block.replace(
            before, f"    {field}: {new_type}",
        ) + original[end:]

    output = _mutate_workspace_contract(source_tree, mutate)
    diagnostics = [line for line in output.splitlines() if "error TS" in line]
    assert any(caller in diagnostic and "assignable" in diagnostic for diagnostic in diagnostics), output
    assert caller_path.read_bytes() == original_caller


def test_config_file_generated_contracts(source_tree):
    result, output = run_make(source_tree, "gen-api-client")
    assert result.returncode == 0, output
    service = (source_tree / CLIENT_REL / "services/DefaultService.ts").read_text()
    for method, signatures in {
        "wrapperApiConfigFilesGet": (
            "CancelablePromise<Array<ConfigFileEntry>>",
        ),
        "wrapperApiConfigFilesPathGet": (
            "path: string", "CancelablePromise<ConfigFileResponse>",
        ),
        "wrapperApiConfigFilesPathPut": (
            "path: string", "content: string", "modified?: (number | null)",
            "CancelablePromise<ConfigWriteResponse>",
        ),
    }.items():
        start = service.index(f"public static {method}(")
        end = service.index("    /**", start)
        block = service[start:end]
        for signature in signatures:
            assert signature in block
        assert "any" not in block

    entry = (source_tree / CLIENT_REL / "models/ConfigFileEntry.ts").read_text()
    for field in (
        "name: string", "path: string", "description: string",
        "modified: (number | null)", "exists: boolean",
    ):
        assert field in entry
    assert "ADMIN = 'admin'" in entry and "WORKSPACE = 'workspace'" in entry

    read = (source_tree / CLIENT_REL / "models/ConfigFileResponse.ts").read_text()
    for field in (
        "content: string", "modified: (number | null)",
        "name: string", "default: boolean",
    ):
        assert field in read
    written = (source_tree / CLIENT_REL / "models/ConfigWriteResponse.ts").read_text()
    assert "ok: boolean" in written and "modified: number" in written
    assert all("any" not in model for model in (entry, read, written))


@pytest.mark.parametrize(("operation", "callers"), [
    ("read", ["components/config-panel.js", "components/wiki-editor.js"]),
    ("write", ["components/wiki-editor.js"]),
])
def test_config_path_type_drift_fails_at_unchanged_callers(
    source_tree, operation, callers,
):
    originals = {
        caller: (source_tree / STATIC_REL / caller).read_bytes()
        for caller in callers
    }

    def mutate(original):
        handler = "config_read_file" if operation == "read" else "config_write_file"
        start = original.index(
            f'APIRoute("/api/config/files/{{path:path}}", {handler}',
        )
        next_route = (
            'APIRoute("/api/config/files/{path:path}", config_write_file'
            if operation == "read" else 'APIRoute("/api/models"'
        )
        end = original.index(next_route, start + 1)
        block = original[start:end]
        before = '"schema": {"type": "string"}'
        assert block.count(before) == 1
        return original[:start] + block.replace(
            before, '"schema": {"type": "integer"}',
        ) + original[end:]

    output = _mutate_workspace_contract(source_tree, mutate)
    diagnostics = [line for line in output.splitlines() if "error TS" in line]
    for caller in callers:
        assert any(
            caller in diagnostic and "TS2345" in diagnostic
            and "not assignable to parameter of type 'number'" in diagnostic
            for diagnostic in diagnostics
        ), output
        assert (source_tree / STATIC_REL / caller).read_bytes() == originals[caller]


@pytest.mark.parametrize(("field", "old_type", "new_type"), [
    ("content", "str", "int"),
    ("modified", "float", "str"),
])
def test_config_save_input_type_drift_fails_at_unchanged_editor(
    source_tree, field, old_type, new_type,
):
    caller = source_tree / STATIC_REL / "components/wiki-editor.js"
    original_caller = caller.read_bytes()

    def mutate(original):
        start = original.index("class ConfigSaveRequest(BaseModel):")
        end = original.index("\n\n\n", start)
        block = original[start:end]
        before = f"    {field}: {old_type}"
        assert block.count(before) == 1
        return original[:start] + block.replace(
            before, f"    {field}: {new_type}",
        ) + original[end:]

    output = _mutate_workspace_contract(source_tree, mutate)
    diagnostics = [line for line in output.splitlines() if "error TS" in line]
    assert any(
        "components/wiki-editor.js" in diagnostic
        and ("TS2322" in diagnostic or "TS2345" in diagnostic)
        and "assignable" in diagnostic
        for diagnostic in diagnostics
    ), output
    assert caller.read_bytes() == original_caller


@pytest.mark.parametrize(("model", "field", "callers"), [
    *[("ConfigFileEntry", field, ["components/config-panel.js"])
      for field in ("name", "path", "description", "scope", "exists")],
    ("ConfigFileResponse", "content",
     ["components/config-panel.js", "components/wiki-editor.js"]),
    ("ConfigFileResponse", "modified",
     ["components/config-panel.js", "components/wiki-editor.js"]),
    ("ConfigWriteResponse", "modified", ["components/wiki-editor.js"]),
])
def test_config_consumed_output_drift_fails_at_unchanged_callers(
    source_tree, model, field, callers,
):
    originals = {
        caller: (source_tree / STATIC_REL / caller).read_bytes()
        for caller in callers
    }

    def mutate(original):
        start = original.index(f"class {model}(BaseModel):")
        end = original.index("\n\n\n", start)
        block = original[start:end]
        before = f"    {field}:"
        assert block.count(before) == 1
        return original[:start] + block.replace(
            before, f"    renamed_{field}:",
        ) + original[end:]

    output = _mutate_workspace_contract(source_tree, mutate)
    diagnostics = [line for line in output.splitlines() if "error TS" in line]
    for caller in callers:
        assert any(
            caller in diagnostic
            and ("TS2339" in diagnostic or "TS2551" in diagnostic)
            and f"Property '{field}' does not exist" in diagnostic
            for diagnostic in diagnostics
        ), output
        assert (source_tree / STATIC_REL / caller).read_bytes() == originals[caller]


@pytest.mark.parametrize(("model", "field", "old_type", "new_type", "callers"), [
    ("ConfigFileResponse", "content", "str", "int",
     ["components/config-panel.js", "components/wiki-editor.js"]),
    ("ConfigFileResponse", "modified", "float", "str",
     ["components/config-panel.js", "components/wiki-editor.js"]),
    ("ConfigWriteResponse", "modified", "float", "str",
     ["components/wiki-editor.js"]),
])
def test_config_consumed_output_type_drift_fails_at_unchanged_callers(
    source_tree, model, field, old_type, new_type, callers,
):
    originals = {
        caller: (source_tree / STATIC_REL / caller).read_bytes()
        for caller in callers
    }

    def mutate(original):
        start = original.index(f"class {model}(BaseModel):")
        end = original.index("\n\n\n", start)
        block = original[start:end]
        before = f"    {field}: {old_type}"
        assert block.count(before) == 1
        return original[:start] + block.replace(
            before, f"    {field}: {new_type}",
        ) + original[end:]

    output = _mutate_workspace_contract(source_tree, mutate)
    diagnostics = [line for line in output.splitlines() if "error TS" in line]
    for caller in callers:
        assert any(
            caller in diagnostic and "TS2322" in diagnostic
            and "is not assignable" in diagnostic
            for diagnostic in diagnostics
        ), output
        assert (source_tree / STATIC_REL / caller).read_bytes() == originals[caller]


def test_schedule_generated_contracts(source_tree):
    result, output = run_make(source_tree, "gen-api-client")
    assert result.returncode == 0, output
    schema = json.loads((source_tree / "openapi.json").read_text())
    run_responses = schema["paths"]["/api/schedules/{name}/run"]["post"]["responses"]
    assert "202" in run_responses
    assert "200" not in run_responses
    update_request = schema["paths"]["/api/schedules/{name}"]["put"][
        "requestBody"
    ]["content"]["application/json"]["schema"]
    assert update_request["additionalProperties"] is False
    service = (source_tree / CLIENT_REL / "services/DefaultService.ts").read_text()
    for method, signatures in {
        "wrapperApiModelsGet": ("CancelablePromise<ModelListResponse>",),
        "wrapperApiSchedulesGet": ("CancelablePromise<ScheduleListResponse>",),
        "wrapperApiSchedulesNameGet": (
            "name: string", "CancelablePromise<ScheduleDetailResponse>",
        ),
        "wrapperApiSchedulesNamePut": (
            "name: string", "content?: (string | null)", "body?: (string | null)",
            "modified?: (number | null)", "enabled?: (boolean | null)",
            "schedule?: (string | null)", "channel?: (string | null)",
            "model?: (string | null)", "allowed_tools?: (Array<string> | null)",
            "disallowed_tools?: (Array<string> | null)",
            "required_skills?: (Array<string> | null)",
            "shell_patterns?: (Array<string> | null)",
            "email_recipients?: (Array<string> | null)",
            "pre_script?: (string | null)",
            "CancelablePromise<ScheduleUpdateResponse>", "discardResponse: true",
        ),
        "wrapperApiSchedulesNameRunPost": (
            "name: string", "CancelablePromise<ScheduleRunResponse>",
            "discardResponse: true",
        ),
        "wrapperApiSchedulesNameOverlayDelete": (
            "name: string", "CancelablePromise<ScheduleResetResponse>",
            "discardResponse: true",
        ),
    }.items():
        start = service.index(f"public static {method}(")
        end = service.index("    /**", start)
        block = service[start:end]
        for signature in signatures:
            assert signature in block
        assert "any" not in block

    for model in (
        "ModelListResponse", "ScheduleResponse", "ScheduleListResponse",
        "ScheduleDetailResponse", "ScheduleUpdateResponse", "ScheduleResetResponse",
        "ScheduleRunResponse",
    ):
        assert "any" not in (
            source_tree / CLIENT_REL / f"models/{model}.ts"
        ).read_text()


@pytest.mark.parametrize(("handler", "next_handler", "callers"), [
    ("schedules_run", "schedules_reset",
     ["components/schedules-sidebar.js", "components/schedule-page.js"]),
    ("schedules_reset", "schedules_get", ["components/schedule-page.js"]),
    ("schedules_get", "schedules_update",
     ["components/schedule-page.js", "components/wiki-editor.js"]),
    ("schedules_update", "vault_create", [
        "components/schedules-sidebar.js", "components/schedule-page.js",
        "components/wiki-editor.js",
    ]),
])
def test_schedule_path_type_drift_fails_at_unchanged_callers(
    source_tree, handler, next_handler, callers,
):
    originals = {
        caller: (source_tree / STATIC_REL / caller).read_bytes()
        for caller in callers
    }

    def mutate(original):
        handler_pos = original.index(f", {handler},")
        start = original.rfind("APIRoute(", 0, handler_pos)
        next_handler_pos = original.index(f", {next_handler},", handler_pos)
        end = original.rfind("APIRoute(", 0, next_handler_pos)
        block = original[start:end]
        before = '"schema": {"type": "string"}'
        assert block.count(before) == 1
        return original[:start] + block.replace(
            before, '"schema": {"type": "integer"}',
        ) + original[end:]

    output = _mutate_workspace_contract(source_tree, mutate)
    diagnostics = [line for line in output.splitlines() if "error TS" in line]
    for caller in callers:
        assert any(
            caller in diagnostic and "TS2345" in diagnostic
            and "not assignable to parameter of type 'number'" in diagnostic
            for diagnostic in diagnostics
        ), output
        assert (source_tree / STATIC_REL / caller).read_bytes() == originals[caller]
    assert all(any(caller in diagnostic for caller in callers)
               for diagnostic in diagnostics), output


@pytest.mark.parametrize(("field", "old_type", "new_type", "callers"), [
    ("content", "str", "int", ["components/wiki-editor.js"]),
    ("modified", "float", "str", ["components/wiki-editor.js"]),
    ("enabled", "bool", "str", [
        "components/schedules-sidebar.js", "components/schedule-metadata.js",
    ]),
    ("schedule", "str", "int", ["components/schedule-metadata.js"]),
    ("channel", "str", "int", ["components/schedule-metadata.js"]),
    ("model", "str", "int", ["components/schedule-metadata.js"]),
    ("allowed_tools", "list[str]", "str", ["components/schedule-metadata.js"]),
    ("required_skills", "list[str]", "str", ["components/schedule-metadata.js"]),
    ("shell_patterns", "list[str]", "str", ["components/schedule-metadata.js"]),
    ("email_recipients", "list[str]", "str", ["components/schedule-metadata.js"]),
    ("pre_script", "str", "int", ["components/schedule-metadata.js"]),
])
def test_schedule_update_input_type_drift_fails_at_unchanged_callers(
    source_tree, field, old_type, new_type, callers,
):
    originals = {
        caller: (source_tree / STATIC_REL / caller).read_bytes()
        for caller in callers
    }

    def mutate(original):
        start = original.index("class ScheduleUpdateRequest(BaseModel):")
        end = original.index("\n\n\n", start)
        block = original[start:end]
        before = f"    {field}: {old_type} | None"
        assert block.count(before) == 1
        return original[:start] + block.replace(
            before, f"    {field}: {new_type} | None",
        ) + original[end:]

    output = _mutate_workspace_contract(source_tree, mutate)
    diagnostics = [line for line in output.splitlines() if "error TS" in line]
    for caller in callers:
        assert any(
            caller in diagnostic
            and ("TS2322" in diagnostic or "TS2345" in diagnostic)
            and "assignable" in diagnostic
            for diagnostic in diagnostics
        ), output
        assert (source_tree / STATIC_REL / caller).read_bytes() == originals[caller]
    assert all(any(caller in diagnostic for caller in callers)
               for diagnostic in diagnostics), output


@pytest.mark.parametrize(("model", "field", "callers"), [
    ("ModelListResponse", "models", [
        "components/schedule-page.js", "components/schedule-metadata.js",
    ]),
    ("ScheduleListResponse", "schedules", ["components/schedules-sidebar.js"]),
    ("ScheduleDetailResponse", "schedule", ["components/schedule-page.js"]),
    ("ScheduleDetailResponse", "body", ["components/wiki-editor.js"]),
    ("ScheduleDetailResponse", "modified", ["components/wiki-editor.js"]),
    ("ScheduleUpdateResponse", "schedule", ["components/schedule-page.js"]),
    ("ScheduleUpdateResponse", "modified", ["components/wiki-editor.js"]),
    *[("ScheduleResponse", field, callers) for field, callers in (
        ("name", ["components/schedules-sidebar.js", "components/schedule-page.js"]),
        ("source_tier", [
            "components/schedules-sidebar.js", "components/schedule-page.js",
            "components/schedule-metadata.js",
        ]),
        ("has_overlay", [
            "components/schedules-sidebar.js", "components/schedule-page.js",
        ]),
        ("enabled", [
            "components/schedules-sidebar.js", "components/schedule-metadata.js",
        ]),
        ("schedule", [
            "components/schedules-sidebar.js", "components/schedule-metadata.js",
        ]),
        ("channel", ["components/schedule-metadata.js"]),
        ("model", ["components/schedule-metadata.js"]),
        ("allowed_tools", ["components/schedule-metadata.js"]),
        ("required_skills", ["components/schedule-metadata.js"]),
        ("shell_patterns", ["components/schedule-metadata.js"]),
        ("email_recipients", ["components/schedule-metadata.js"]),
        ("pre_script", ["components/schedule-metadata.js"]),
        ("unknown_keys", ["components/schedule-metadata.js"]),
        ("frontmatter_raw", ["components/schedule-metadata.js"]),
        ("body", ["components/schedule-page.js"]),
        ("modified", ["components/schedule-page.js"]),
        ("next_run_iso", ["components/schedules-sidebar.js"]),
    )],
])
def test_schedule_consumed_output_drift_fails_at_unchanged_callers(
    source_tree, model, field, callers,
):
    originals = {
        caller: (source_tree / STATIC_REL / caller).read_bytes()
        for caller in callers
    }

    def mutate(original):
        start = original.index(f"class {model}(BaseModel):")
        end = original.index("\n\n\n", start)
        block = original[start:end]
        before = f"    {field}:"
        assert block.count(before) == 1
        return original[:start] + block.replace(
            before, f"    renamed_{field}:",
        ) + original[end:]

    output = _mutate_workspace_contract(source_tree, mutate)
    diagnostics = [line for line in output.splitlines() if "error TS" in line]
    for caller in callers:
        assert any(
            caller in diagnostic
            and ("TS2339" in diagnostic or "TS2551" in diagnostic)
            and f"Property '{field}' does not exist" in diagnostic
            for diagnostic in diagnostics
        ), output
        assert (source_tree / STATIC_REL / caller).read_bytes() == originals[caller]
    assert all(any(caller in diagnostic for caller in callers)
               for diagnostic in diagnostics), output


@pytest.mark.parametrize(("model", "field", "old_type", "new_type", "callers"), [
    ("ModelListResponse", "models", "list[str]", "str", [
        "components/schedule-page.js", "components/schedule-metadata.js",
    ]),
    ("ScheduleDetailResponse", "body", "str", "int", ["components/wiki-editor.js"]),
    ("ScheduleDetailResponse", "modified", "float", "str", ["components/wiki-editor.js"]),
    ("ScheduleUpdateResponse", "modified", "float", "str", ["components/wiki-editor.js"]),
    ("ScheduleResponse", "next_run_iso", "str | None", "int | None",
     ["components/schedules-sidebar.js"]),
])
def test_schedule_consumed_output_type_drift_fails_at_unchanged_caller(
    source_tree, model, field, old_type, new_type, callers,
):
    caller_paths = {caller: source_tree / STATIC_REL / caller for caller in callers}
    original_callers = {caller: path.read_bytes() for caller, path in caller_paths.items()}

    def mutate(original):
        start = original.index(f"class {model}(BaseModel):")
        end = original.index("\n\n\n", start)
        block = original[start:end]
        before = f"    {field}: {old_type}"
        assert block.count(before) == 1
        return original[:start] + block.replace(
            before, f"    {field}: {new_type}",
        ) + original[end:]

    output = _mutate_workspace_contract(source_tree, mutate)
    diagnostics = [line for line in output.splitlines() if "error TS" in line]
    for caller in callers:
        assert any(
            caller in diagnostic
            and ("TS2322" in diagnostic or "TS2339" in diagnostic
                 or "TS2345" in diagnostic)
            and ("assignable" in diagnostic or "does not exist" in diagnostic)
            for diagnostic in diagnostics
        ), output
        assert caller_paths[caller].read_bytes() == original_callers[caller]
    assert all(any(caller in diagnostic for caller in callers)
               for diagnostic in diagnostics), output


def test_workspace_mutation_generated_contracts(source_tree):
    result, output = run_make(source_tree, "gen-api-client")
    assert result.returncode == 0, output
    service = (source_tree / CLIENT_REL / "services/DefaultService.ts").read_text()
    put_start = service.index("public static wrapperApiWorkspacePathPut(")
    put_end = service.index("    /**", put_start)
    put_method = service[put_start:put_end]
    for signature in (
        "path: string",
        "renameTo?: string",
        "requestBody?: {",
        "content: string",
        "modified?: (number | null)",
        "CancelablePromise<WorkspaceWriteResponse>",
    ):
        assert signature in put_method
    assert "CancelablePromise<any>" not in put_method

    delete_start = service.index("public static wrapperApiWorkspacePathDelete(")
    delete_end = service.index("    /**", delete_start)
    delete_method = service[delete_start:delete_end]
    for signature in (
        "path: string",
        "CancelablePromise<WorkspaceDeleteResponse>",
        "discardResponse: true",
    ):
        assert signature in delete_method
    assert "CancelablePromise<any>" not in delete_method

    for model in ("WorkspaceWriteResponse", "WorkspaceDeleteResponse"):
        generated = (source_tree / CLIENT_REL / f"models/{model}.ts").read_text()
        assert "any" not in generated

    write_response = (source_tree / CLIENT_REL / "models/WorkspaceWriteResponse.ts").read_text()
    assert "modified: number" in write_response
    assert "path?: (string | null)" in write_response
    delete_response = (source_tree / CLIENT_REL / "models/WorkspaceDeleteResponse.ts").read_text()
    assert "ok: boolean" in delete_response


@pytest.mark.parametrize(
    "contract",
    ["put_path", "save_content", "save_modified", "rename_query", "delete_path", "response_modified"],
)
def test_workspace_mutation_contract_drift_fails_at_unchanged_callers(
    source_tree, contract,
):
    callers = {
        "file-editor.js": source_tree / STATIC_REL / "components/file-editor.js",
        "file-page.js": source_tree / STATIC_REL / "components/file-page.js",
    }
    original_callers = {name: path.read_bytes() for name, path in callers.items()}

    def mutate(original):
        if contract in {"save_content", "save_modified", "response_modified"}:
            before, after = {
                "save_content": ("class WorkspaceSaveRequest(BaseModel):\n    content: str",
                                 "class WorkspaceSaveRequest(BaseModel):\n    content: int"),
                "save_modified": (
                    "class WorkspaceSaveRequest(BaseModel):\n"
                    "    content: str\n"
                    "    modified: float | None = None",
                    "class WorkspaceSaveRequest(BaseModel):\n"
                    "    content: str\n"
                    "    modified: str | None = None",
                ),
                "response_modified": ("class WorkspaceWriteResponse(BaseModel):\n    ok: Literal[True]\n    modified:",
                                      "class WorkspaceWriteResponse(BaseModel):\n    ok: Literal[True]\n    renamed_modified:"),
            }[contract]
            assert original.count(before) == 1
            return original.replace(before, after)

        route = "workspace_delete" if contract == "delete_path" else "workspace_write"
        start = original.index(f'APIRoute("/api/workspace/{{path:path}}", {route}')
        end_marker = ("APIRoute(\"/api/config/files\"" if route == "workspace_delete"
                      else 'APIRoute("/api/workspace/{path:path}", workspace_delete')
        end = original.index(end_marker, start)
        block = original[start:end]
        parameter = "rename_to" if contract == "rename_query" else "path"
        before = (f'"name": "{parameter}", "in": '
                  + ('"query"' if parameter == "rename_to" else '"path"'))
        parameter_start = block.index(before)
        schema_start = block.index('"schema": {"type": "string"}', parameter_start)
        schema_end = schema_start + len('"schema": {"type": "string"}')
        changed_block = (block[:schema_start] + '"schema": {"type": "integer"}'
                         + block[schema_end:])
        return original[:start] + changed_block + original[end:]

    output = _mutate_workspace_contract(source_tree, mutate)
    diagnostics = [line for line in output.splitlines() if "error TS" in line]
    expectations = {
        "put_path": [
            ("file-editor.js", "this.path,", "TS2345"),
            ("file-page.js", "this.path,", "TS2345"),
        ],
        "save_content": [("file-editor.js", "{ content, modified:", "TS2322")],
        "save_modified": [("file-editor.js", "{ content, modified:", "TS2322")],
        "rename_query": [("file-page.js", "newPath,", "TS2345")],
        "delete_path": [("file-page.js", "wrapperApiWorkspacePathDelete(this.path", "TS2345")],
        "response_modified": [("file-editor.js", "data.modified", "TS2339")],
    }[contract]
    for caller, marker, code in expectations:
        line_no = next(
            line_no for line_no, line in enumerate(original_callers[caller].decode().splitlines(), 1)
            if marker in line
        )
        assert any(
            diagnostic.startswith(f"components/{caller}({line_no},") and code in diagnostic
            for diagnostic in diagnostics
        ), output
    for name, path in callers.items():
        assert path.read_bytes() == original_callers[name]
