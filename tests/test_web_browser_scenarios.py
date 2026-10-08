"""Playwright browser integration scenarios for the web UI.

Exercises live DOM interactions, authentication, forms, navigation,
and API client integration in real headless Chromium instances.
"""

import asyncio
import collections
import contextlib
import dataclasses
import json
import os
import pathlib
import re
import socket
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

_chromium_launches = 0
_server_starts = collections.Counter()


@pytest.fixture(scope="session")
def built_static():
    """Static assets served directly from the repository static directory."""
    return REPO_ROOT / STATIC_REL


@pytest.fixture(scope="session")
def chromium_process():
    """One Chromium process for every browser scenario in this worker.

    Playwright's sync API marks its own event loop as running in this thread
    after every call and does not clear the mark. While this session fixture
    stays open, every later async test in the worker would then fail with
    'Runner.run() cannot be called from a running event loop' (#941). So the
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
    url_path: str
    path: str
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
    """Serve the real app from ``static`` and log a fresh browser context in."""
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
            requests.append(
                RecordedRequest(
                    method=request.method,
                    url_path=request.url.path,
                    path=request.scope["path"],
                    raw_path=request.scope["raw_path"].decode(),
                    query=list(request.query_params.multi_items()),
                    body=await request.body(),
                    content_type=request.headers.get("content-type"),
                    authenticated=bool(request.cookies),
                )
            )
        return await call_next(request)

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
            page.on(
                "response", lambda response: failed_requests.append(response.url) if response.status >= 400 else None
            )
            page.goto(base + "/static/lib/auth-client.js")
            result = page.evaluate(
                """async (token) => {
                const { AuthClient } = await import('/static/lib/auth-client.js');
                const client = new AuthClient();
                await client.login(token);
                return { username: await client.checkSession(), currentUser: client.currentUser };
            }""",
                token,
            )
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
    page.evaluate(
        """([name, flag]) => {
        window[flag] = false;
        window.addEventListener(name, () => { window[flag] = true; }, { once: true });
    }""",
        [name, flag],
    )


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
        page.evaluate(
            """async ({ folder, path }) => {
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
        }""",
            {"folder": workspace_folder, "path": workspace_rel_path},
        )
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
        assert (
            page.evaluate("""document.querySelector('#browser-chat-input')
            ._mentionMatches.find(item => item.id === 'Browser Vault').label""")
            == "Browser Vault"
        )
        workspace_path.write_text("second browser content")
        page.evaluate("document.querySelector('#browser-file-page').reload()")
        page.wait_for_function("""() => {
            const filePage = document.querySelector('#browser-file-page');
            return filePage._content === 'second browser content' && !filePage._loading;
        }""")
        assert [(request.path, request.query, request.authenticated) for request in scenario.requests] == [
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
        page.evaluate(
            """async ({ folder, path }) => {
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
        }""",
            {"folder": workspace_folder, "path": workspace_rel_path},
        )
        page.wait_for_function("""() => {
            const sidebar = document.querySelector('#browser-files');
            const filePage = document.querySelector('#browser-file-page');
            return sidebar?._files?.length === 1 && !sidebar._loading
                && filePage?._content === 'first browser content' && !filePage._loading;
        }""")

        page.locator("#browser-file-page .cm-content").fill("saved browser content")
        page.evaluate("document.querySelector('#browser-file-page file-editor').flushSave()")
        page.wait_for_function("document.querySelector('#browser-file-page')._saveStatus === 'saved'")
        assert workspace_path.read_text() == "saved browser content"

        stale_mtime = workspace_path.stat().st_mtime
        workspace_path.write_text("server conflict content")
        os.utime(workspace_path, (stale_mtime + 100, stale_mtime + 100))
        page.locator("#browser-file-page .cm-content").fill("stale browser content")
        page.evaluate("document.querySelector('#browser-file-page file-editor').flushSave()")
        page.wait_for_function("document.querySelector('#browser-file-page')._conflict === true")
        assert workspace_path.read_text() == "server conflict content"
        assert len(scenario.failed_requests) == 1
        assert scenario.failed_requests[0].endswith(
            "/api/workspace/Browser%20files/%E6%97%A5%E6%9C%AC%E8%AA%9E%20%23%3F/Browser%20note%20%23%3F.md"
        )
        scenario.failed_requests.clear()
        page.locator("#browser-file-page .file-editor-conflict button").click()
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
        page.locator("#browser-file-page .file-rename-btn").click()
        page.locator("#browser-file-page .file-rename-input").fill(renamed_workspace_rel_path)
        page.locator("#browser-file-page .file-rename-ok").click()
        page.wait_for_function("window.workspaceFileOpen !== null")
        assert page.evaluate("window.workspaceFileOpen") == renamed_workspace_rel_path
        assert renamed_workspace_path.read_text() == "server conflict content"
        assert not workspace_path.exists()

        page.evaluate(
            """path => {
            const filePage = document.querySelector('#browser-file-page');
            filePage.kind = 'binary';
            filePage.path = path;
        }""",
            renamed_workspace_rel_path,
        )
        page.wait_for_function("document.querySelector('#browser-file-page .file-delete-btn') !== null")
        expect_window_event(page, "workspace-file-deleted", "workspaceFileDeleted")
        page.once("dialog", lambda dialog: dialog.accept())
        page.locator("#browser-file-page .file-delete-btn").click()
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
            "/api/workspace?folder=Browser%20files%2F%E6%97%A5%E6%9C%AC%E8%AA%9E%20%23%3F"
        )
        scenario.failed_requests.clear()
        assert [
            (request.path, request.query, request.authenticated)
            for request in scenario.requests
            if request.method == "GET"
        ] == [
            ("/api/workspace", [("folder", workspace_folder)], True),
            (f"/api/workspace-file/{workspace_rel_path}", [], True),
            (f"/api/workspace-file/{workspace_rel_path}", [], True),
            ("/api/workspace", [("folder", workspace_folder)], True),
        ]
        workspace_mutation_requests = [request for request in scenario.requests if request.method in {"PUT", "DELETE"}]
        assert [
            (request.method, request.path, request.query, request.content_type, request.authenticated)
            for request in workspace_mutation_requests
        ] == [
            ("PUT", f"/api/workspace/{workspace_rel_path}", [], "application/json", True),
            ("PUT", f"/api/workspace/{workspace_rel_path}", [], "application/json", True),
            ("PUT", f"/api/workspace/{workspace_rel_path}", [("rename_to", renamed_workspace_rel_path)], None, True),
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
        id=notification_id,
        timestamp="2026-10-01T00:00:00Z",
        category="background",
        title="Browser notification",
        body="Finished work",
        conv_id=conv_id,
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
        page.locator(".notification-bell").click()
        page.locator(".notification-row").wait_for()
        assert "Browser notification" in page.locator(".notification-panel").inner_text()
        page.evaluate("""() => {
            window.notificationNavigation = null;
            document.querySelector('notification-inbox').addEventListener('navigate-conversation',
                e => window.notificationNavigation = e.detail.convId);
        }""")
        # The row awaits its mark-read request before it navigates.
        page.locator(".notification-row").click()
        page.wait_for_function("window.notificationNavigation !== null")
        assert page.evaluate("window.notificationNavigation") == conv_id
        assert notification_id in notifs.get_read_ids(config)
        # A pushed count enables mark-all; its caller still reaches the real route.
        page.evaluate("window.dispatchEvent(new CustomEvent('notification-created', {detail: {unread_count: 1}}))")
        page.locator(".notification-bell").click()
        page.locator(".notification-row").wait_for()
        page.locator(".notification-mark-all").click()
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
        "total_tokens_estimated": 123,
        "context_window_size": 1000,
        "sources": [
            {
                "source": "memory",
                "tokens_estimated": 123,
                "items_included": 1,
                "details": {"top_score": 0.9, "budget_source": "dynamic"},
            }
        ],
    }
    write_context_sidecar(config, conv_id, diagnostics_payload)
    with browser_scenario(built_static, chromium, config) as scenario:
        page = scenario.page
        page.evaluate(
            """async (convId) => {
            await import('/static/components/context-inspector.js');
            const el = document.createElement('context-inspector');
            el.convId = convId; el.open = true; document.body.append(el);
        }""",
            conv_id,
        )
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
        copied = scenario.page.evaluate(
            """async (convId) => {
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
        }""",
            conv_id,
        )
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
    assert write_sticky_state(
        config,
        conv_id,
        {
            "schema_version": 1,
            "widget_type": "markdown_document",
            "data": payload,
        },
    )
    with browser_scenario(built_static, chromium, config) as scenario:
        snapshot = scenario.page.evaluate(
            """async (convId) => {
            const sticky = await import('/static/lib/sticky-state.js');
            await sticky.setActiveConv(convId);
            return sticky.currentSnapshot();
        }""",
            conv_id,
        )
        assert snapshot == {
            "widgetType": "markdown_document",
            "data": payload,
            "collapsed": False,
            "visible": True,
        }
        assert [(request.method, request.path, request.authenticated) for request in scenario.requests] == [
            ("GET", f"/api/sticky/{conv_id}", True)
        ]


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
        listings = page.evaluate(
            """async (folder) => {
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
        }""",
            nested,
        )
        assert listings[0] == listings[1]
        assert listings[3] == listings[4]
        assert listings[2]["conversations"][0]["conv_id"] == active["conv_id"]
        assert listings[5]["conversations"][0]["conv_id"] == archived["conv_id"]
        assert listings[2]["folder"] == listings[5]["folder"] == nested
        assert listings[6] == listings[7]
        expected_requests = []
        for route_path in ["/api/conversations", "/api/conversations/archived"]:
            expected_requests.extend(
                [(route_path, [], True), (route_path, [], True), (route_path, [("folder", nested)], True)]
            )
        for folder in [None, "", "heartbeat", "schedule", "delegated"]:
            expected_requests.append(("/api/conversations/system", [] if not folder else [("folder", folder)], True))
        assert [
            (request.url_path, request.query, request.authenticated)
            for request in scenario.requests
            if request.method == "GET"
        ] == expected_requests


def test_browser_conversation_patch(built_static, chromium, config):
    nested = NESTED_CONV_FOLDER
    conv_id = seed_conversation(config, "Patch test")
    with browser_scenario(built_static, chromium, config) as scenario:
        page, base = scenario.page, scenario.base
        assert page.request.post(base + "/api/conversations/folders", data={"path": nested}).status == 200
        scenario.requests.clear()
        patch_title = "Title 日本語 & plus+ #hash"
        patched = page.evaluate(
            """async ({ id, title, folder }) => {
            const { DefaultService } = await import('/static/lib/api-client/index.js');
            const renamed = await DefaultService.renameConversationApiConversationsIdPatch(id, { title });
            const moved = await DefaultService.renameConversationApiConversationsIdPatch(id, { folder }, true);
            return { renamed, ignored: moved === undefined };
        }""",
            {"id": conv_id, "title": patch_title, "folder": "  " + nested + "  "},
        )
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
        folders = scenario.page.evaluate(
            """async ({ path, renamed }) => {
            const { DefaultService } = await import('/static/lib/api-client/index.js');
            return [
                await DefaultService.createConvFolderApiConversationsFoldersPost({ path }),
                await DefaultService.renameConvFolderApiConversationsFoldersPathPut(path, { path: renamed }),
                await DefaultService.deleteConvFolderApiConversationsFoldersPathDelete(renamed),
            ];
        }""",
            {"path": folder_path, "renamed": renamed_path},
        )
        assert folders == [{"ok": True, "path": folder_path}, {"ok": True}, {"ok": True}]
        assert [
            (request.method, request.path, request.json(), request.authenticated) for request in scenario.requests
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
        lifecycle = page.evaluate(
            """async (folder) => {
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
        }""",
            nested,
        )
        created = lifecycle["created"]
        assert created["title"] == "Lifecycle 日本語 & + #"
        assert created["folder"] == nested
        assert lifecycle["deselected"] is True
        assert [
            (
                request.method,
                request.path,
                request.json() if request.url_path == "/api/conversations" else None,
                request.authenticated,
            )
            for request in scenario.requests
            if request.method in {"POST", "DELETE"}
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
        canvas_result = page.evaluate(
            """async (convId) => {
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
        }""",
            conv_id,
        )
        assert canvas_result["descriptor"]["name"] == "code_block"
        assert canvas_result["descriptor"]["schemaType"] == "object"
        assert canvas_result["descriptor"]["js_url"].startswith("/widgets/bundled/code_block/widget.js?v=")
        assert canvas_result["snapshot"]["activeTabId"] == "canvas_1"
        assert canvas_result["snapshot"]["tabs"] == []

        def widget_catalog_requests():
            return [request.authenticated for request in scenario.requests if request.url_path == "/api/widgets"]

        def canvas_requests():
            return [
                (
                    request.method,
                    request.path,
                    request.json() if request.method == "POST" else None,
                    request.content_type,
                    request.authenticated,
                )
                for request in scenario.requests
                if request.url_path.startswith(f"/api/canvas/{conv_id}")
            ]

        assert widget_catalog_requests() == [True]
        assert canvas_requests() == [
            ("GET", f"/api/canvas/{conv_id}", None, None, True),
            (
                "POST",
                f"/api/canvas/{conv_id}/new_tab",
                {
                    "widget_type": "code_block",
                    "data": {"code": "print(1)", "language": "python", "filename": "demo.py"},
                    "label": "demo.py",
                },
                "application/json",
                True,
            ),
            (
                "POST",
                f"/api/canvas/{conv_id}/new_tab",
                {
                    "widget_type": "diff_view",
                    "data": {"before": "a", "after": "b", "filename": "demo.txt", "view": "unified"},
                    "label": "demo.txt",
                },
                "application/json",
                True,
            ),
            (
                "POST",
                f"/api/canvas/{conv_id}/new_tab",
                {
                    "widget_type": "json_view",
                    "data": {"value": {"nested": [1, True, None]}},
                    "label": "JSON View",
                },
                "application/json",
                True,
            ),
            (
                "POST",
                f"/api/canvas/{conv_id}/new_tab",
                {
                    "widget_type": "markdown_document",
                    "data": {"content": "# Browser doc\n\nBody"},
                    "label": "Browser doc",
                },
                "application/json",
                True,
            ),
            ("POST", f"/api/canvas/{conv_id}/active_tab", {"tab_id": "canvas_1"}, "application/json", True),
            ("POST", f"/api/canvas/{conv_id}/close_tab", {"tab_id": "canvas_4"}, "application/json", True),
        ]
        page.goto(scenario.base + f"/canvas/{conv_id}/canvas_1")
        page.wait_for_function("document.querySelector('dc-widget-code-block') !== null")
        assert page.locator("dc-widget-code-block").inner_text().find("print(1)") >= 0
        assert page.title() == "Canvas — demo.py"
        assert canvas_requests()[-1] == (
            "GET",
            f"/api/canvas/{conv_id}",
            None,
            None,
            True,
        )
        assert widget_catalog_requests() == [True, True]


def test_browser_vault_standalone_page(built_static, chromium, config):
    seed_vault_page(config)
    with browser_scenario(built_static, chromium, config) as scenario:
        page = scenario.page
        open_vault_page(scenario)
        assert page.locator("wiki-page").evaluate("node => node._body") == "# Browser vault body\n"
        assert page.locator("wiki-page").evaluate("node => node._frontmatter.nested.rows") == [1, True, None]
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
            for request in scenario.requests
            if request.url_path.startswith("/api/vault")
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
        assert page.locator("#browser-vault-sidebar").evaluate("node => node._wikiPages[0].path") == VAULT_PAGE_NAME
        assert page.locator("#browser-tags-sidebar").evaluate("node => node._tags[0].pages") == [
            f"{VAULT_PAGE_NAME}.md"
        ]
        page.locator("#browser-vault-sidebar button", has_text="Recent").click()
        page.wait_for_function("""() => {
            const sidebar = document.querySelector('#browser-vault-sidebar');
            return sidebar._recentPages.length === 1 && !sidebar._loading;
        }""")
        assert [
            (request.path, request.query, request.authenticated)
            for request in scenario.requests
            if request.method == "GET"
        ] == [
            ("/api/vault", [("folder", "agent/pages")], True),
            ("/api/vault/tags", [], True),
            ("/api/vault/recent", [], True),
        ]

        page.locator("#browser-vault-sidebar button", has_text="Browse").click()
        page.wait_for_function("""() => {
            const sidebar = document.querySelector('#browser-vault-sidebar');
            return sidebar.querySelector('.wiki-new-page-btn') !== null && !sidebar._loading;
        }""")
        page.once("dialog", lambda dialog: dialog.accept("Created #1"))
        page.locator("#browser-vault-sidebar .wiki-new-page-btn").click()
        created_vault_page = config.vault_root / "agent/pages/Created #1.md"
        page.wait_for_function("""() => {
            const sidebar = document.querySelector('#browser-vault-sidebar');
            return sidebar._wikiPages.some(item => item.path === 'agent/pages/Created #1')
                && !sidebar._loading;
        }""")
        assert created_vault_page.exists()

        page.once("dialog", lambda dialog: dialog.accept("Folder #1"))
        page.locator("#browser-vault-sidebar .wiki-new-folder-btn").click()
        page.wait_for_function("""() => {
            const sidebar = document.querySelector('#browser-vault-sidebar');
            return sidebar._vaultFolder === 'agent/pages/Folder #1' && !sidebar._loading;
        }""")
        assert (config.vault_root / "agent/pages/Folder #1").is_dir()
        assert [
            (request.method, request.path, request.json(), request.content_type, request.authenticated)
            for request in scenario.requests
            if request.method in {"POST", "PUT", "DELETE"}
        ] == [
            ("POST", "/api/vault", {"name": "agent/pages/Created #1"}, "application/json", True),
            (
                "POST",
                "/api/vault/folders",
                {"folder": "agent/pages/Folder #1"},
                "application/json",
                True,
            ),
        ]


def test_browser_vault_editor_conflicts(built_static, chromium, config):
    vault_page_path = seed_vault_page(config)
    with browser_scenario(built_static, chromium, config) as scenario:
        page = scenario.page
        page.evaluate(
            """async (pageName) => {
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
        }""",
            VAULT_PAGE_NAME,
        )
        page.locator("#browser-vault-editor .wiki-editor-conflict button", has_text="Reload").click()
        page.wait_for_function("""() => {
            const editor = document.querySelector('#browser-vault-editor');
            return editor.content === '# Browser vault body\\n' && editor._status === 'saved';
        }""")
        assert [
            (request.path, request.query, request.authenticated)
            for request in scenario.requests
            if request.method == "GET"
        ] == [(f"/api/vault/{VAULT_PAGE_NAME}", [], True)]

        vault_editor = page.locator("#browser-vault-editor .milkdown .ProseMirror")
        initial_vault_mtime = page.evaluate("document.querySelector('#browser-vault-editor').modified")
        vault_editor.fill("Saved through generated client")
        page.wait_for_function("document.querySelector('#browser-vault-editor')._status === 'editing'")
        page.evaluate("document.querySelector('#browser-vault-editor').flushSave()")
        page.wait_for_function("document.querySelector('#browser-vault-editor')._status === 'saved'")
        assert vault_page_path.read_text().endswith("# Saved through generated client\n")

        stale_vault_mtime = vault_page_path.stat().st_mtime
        vault_page_path.write_text("# Server conflict")
        os.utime(vault_page_path, (stale_vault_mtime + 100, stale_vault_mtime + 100))
        vault_editor.fill("Forced through generated client")
        page.wait_for_function("document.querySelector('#browser-vault-editor')._status === 'editing'")
        page.evaluate("document.querySelector('#browser-vault-editor').flushSave()")
        page.wait_for_function("document.querySelector('#browser-vault-editor')._status === 'conflict'")
        page.locator("#browser-vault-editor .wiki-editor-conflict button", has_text="Overwrite").click()
        page.wait_for_function("document.querySelector('#browser-vault-editor')._status === 'saved'")
        assert vault_page_path.read_text().endswith("# Forced through generated client\n")
        assert [
            (request.method, request.path, request.json(), request.content_type, request.authenticated)
            for request in scenario.requests
            if request.method in {"POST", "PUT", "DELETE"}
        ] == [
            (
                "PUT",
                f"/api/vault/{VAULT_PAGE_NAME}",
                {
                    "content": "# Saved through generated client\n",
                    "modified": initial_vault_mtime,
                },
                "application/json",
                True,
            ),
            (
                "PUT",
                f"/api/vault/{VAULT_PAGE_NAME}",
                {
                    "content": "# Forced through generated client\n",
                    "modified": stale_vault_mtime,
                },
                "application/json",
                True,
            ),
            (
                "PUT",
                f"/api/vault/{VAULT_PAGE_NAME}",
                {
                    "content": "# Forced through generated client\n",
                },
                "application/json",
                True,
            ),
        ]
        assert len(scenario.failed_requests) == 1
        assert scenario.failed_requests[0].endswith(
            "/api/vault/agent/pages/Browser%20%26%20%E6%97%A5%E6%9C%AC%E8%AA%9E"
        )
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
        metadata_modified = page.evaluate("document.querySelector('wiki-page')._modified")
        page.evaluate("""() => document.querySelector('wiki-page')._onMetadataRawSave(
            new CustomEvent('metadata-raw-save', {
                detail: {raw: 'summary: Generated write\\nnested: [1, true, null]'},
            }))""")
        page.wait_for_function("""() => document.querySelector('wiki-page')
            ._frontmatter.summary === 'Generated write'""")
        assert "nested: [1, true, null]" in vault_page_path.read_text()

        renamed_vault_name = "agent/archive/Renamed 日本語 #1"
        renamed_vault_path = config.vault_root / f"{renamed_vault_name}.md"
        page.locator("wiki-page .wiki-rename-btn").click()
        page.locator("wiki-page .wiki-rename-input").fill(renamed_vault_name)
        expect_window_event(page, "wiki-open", "vaultPageRenamed")
        with page.expect_response("**/api/vault/agent/pages/Browser**") as rename_response:
            page.locator("wiki-page .wiki-rename-ok").click()
        assert rename_response.value.status == 200
        page.wait_for_function("window.vaultPageRenamed === true")
        assert renamed_vault_path.exists()
        assert not vault_page_path.exists()

        page.evaluate(
            """name => { document.querySelector('wiki-page').page = name; }""",
            renamed_vault_name,
        )
        page.wait_for_function("""() => document.querySelector('wiki-page')._loaded
            && document.querySelector('wiki-page').page
                === 'agent/archive/Renamed 日本語 #1'""")
        page.once("dialog", lambda dialog: dialog.accept())
        expect_window_event(page, "wiki-close", "vaultPageClosed")
        with page.expect_response("**/api/vault/agent/archive/Renamed**") as delete_response:
            page.locator("wiki-page .wiki-delete-btn").click()
        assert delete_response.value.status == 200
        page.wait_for_function("window.vaultPageClosed === true")
        assert not renamed_vault_path.exists()

        assert [
            (request.method, request.path, request.json(), request.content_type, request.authenticated)
            for request in scenario.requests
            if request.method in {"POST", "PUT", "DELETE"}
        ] == [
            (
                "PUT",
                f"/api/vault/{VAULT_PAGE_NAME}",
                {
                    "frontmatter_raw": "summary: Generated write\nnested: [1, true, null]",
                    "modified": metadata_modified,
                },
                "application/json",
                True,
            ),
            (
                "PUT",
                f"/api/vault/{VAULT_PAGE_NAME}",
                {
                    "rename_to": renamed_vault_name,
                },
                "application/json",
                True,
            ),
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

        page.locator(
            "#browser-config-panel .config-file-item",
            has_text="AGENT.md",
        ).click()
        page.wait_for_function("""() => document.querySelector('#browser-config-panel')
            ._selectedFile?.path === 'AGENT.md'""")
        assert (
            page.evaluate("""document.querySelector(
            '#browser-config-panel wiki-editor').content.length > 0""")
            is True
        )
        assert (
            page.evaluate("""document.querySelector(
            '#browser-config-panel wiki-editor').modified""")
            == 0
        )

        page.locator("#browser-config-panel .config-back-btn").click()
        page.wait_for_function("""() => {
            const panel = document.querySelector('#browser-config-panel');
            return panel._selectedFile === null && !panel._loading;
        }""")
        page.locator(
            "#browser-config-panel .config-file-item",
            has_text="Config Browser #1.md",
        ).click()
        page.wait_for_function("""() => document.querySelector('#browser-config-panel')
            ._selectedFile?.path === 'workspace/schedules/Config Browser #1.md'""")
        config_editor = page.locator("#browser-config-panel wiki-editor .milkdown .ProseMirror")
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
        os.utime(config_file_path, (saved_config_mtime + 100, saved_config_mtime + 100))
        config_editor.fill("First stale config edit")
        page.wait_for_function("""document.querySelector(
            '#browser-config-panel wiki-editor')._status === 'editing'""")
        page.evaluate("""document.querySelector(
            '#browser-config-panel wiki-editor').flushSave()""")
        page.wait_for_function("""document.querySelector(
            '#browser-config-panel wiki-editor')._status === 'conflict'""")
        page.locator(
            "#browser-config-panel .wiki-editor-conflict button",
            has_text="Reload",
        ).click()
        page.wait_for_function("""() => {
            const editor = document.querySelector('#browser-config-panel wiki-editor');
            return editor.content === '# First external config change\\n'
                && editor._status === 'saved';
        }""")

        reloaded_config_mtime = config_file_path.stat().st_mtime
        config_file_path.write_text("# Second external config change\n")
        os.utime(config_file_path, (reloaded_config_mtime + 100, reloaded_config_mtime + 100))
        config_editor.fill("Forced through generated config")
        page.wait_for_function("""document.querySelector(
            '#browser-config-panel wiki-editor')._status === 'editing'""")
        page.evaluate("""document.querySelector(
            '#browser-config-panel wiki-editor').flushSave()""")
        page.wait_for_function("""document.querySelector(
            '#browser-config-panel wiki-editor')._status === 'conflict'""")
        page.locator(
            "#browser-config-panel .wiki-editor-conflict button",
            has_text="Overwrite",
        ).click()
        page.wait_for_function("""document.querySelector(
            '#browser-config-panel wiki-editor')._status === 'saved'""")
        assert config_file_path.read_text() == "# Forced through generated config\n"

        config_path = "/api/config/files/workspace/schedules/Config Browser #1.md"
        assert [
            (
                request.method,
                request.path,
                request.json() if request.method == "PUT" else None,
                request.content_type,
                request.authenticated,
            )
            for request in scenario.requests
        ] == [
            ("GET", "/api/config/files", None, None, True),
            ("GET", "/api/config/files/AGENT.md", None, None, True),
            ("GET", "/api/config/files", None, None, True),
            ("GET", config_path, None, None, True),
            (
                "PUT",
                config_path,
                {
                    "content": "# Saved through generated config\n",
                    "modified": initial_config_mtime,
                },
                "application/json",
                True,
            ),
            (
                "PUT",
                config_path,
                {
                    "content": "# First stale config edit\n",
                    "modified": saved_config_mtime,
                },
                "application/json",
                True,
            ),
            ("GET", config_path, None, None, True),
            (
                "PUT",
                config_path,
                {
                    "content": "# Forced through generated config\n",
                    "modified": reloaded_config_mtime,
                },
                "application/json",
                True,
            ),
            (
                "PUT",
                config_path,
                {
                    "content": "# Forced through generated config\n",
                },
                "application/json",
                True,
            ),
        ]
        assert len(scenario.failed_requests) == 2
        assert all(
            request.endswith(
                "/api/config/files/workspace/schedules/Config%20Browser%20%231.md",
            )
            for request in scenario.failed_requests
        )
        scenario.failed_requests.clear()


SCHEDULE_NAME = "Browser_Schedule-1"
ENCODED_SCHEDULE = "/api/schedules/Browser_Schedule-1"


def seed_schedule(config):
    schedule_path = config.agent_path / "schedules" / f"{SCHEDULE_NAME}.md"
    schedule_path.parent.mkdir(parents=True, exist_ok=True)
    schedule_path.write_text(
        "---\nschedule: '0 3 * * *'\nenabled: true\nmodel: browser-model\n---\n# Initial schedule\n",
    )
    return schedule_path


def schedule_requests(scenario):
    return [
        (request.method, request.raw_path, request.json(), request.content_type, request.authenticated)
        for request in scenario.requests
        if request.url_path in {"/api/models", "/api/schedules"} or request.url_path.startswith("/api/schedules/")
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
        page.wait_for_function(
            """name => {
            const sidebar = document.querySelector('#browser-schedules-sidebar');
            return sidebar?._schedules?.some(item => item.name === name) && !sidebar._loading;
        }""",
            arg=SCHEDULE_NAME,
        )

        schedule_row = page.locator(
            "#browser-schedules-sidebar .schedule-row",
            has_text=SCHEDULE_NAME,
        )
        with page.expect_response("**/api/schedules/Browser_Schedule-1") as toggle_response:
            schedule_row.locator(".schedule-enabled-toggle").uncheck()
        assert toggle_response.value.status == 200
        page.wait_for_function(
            """name => {
            const sidebar = document.querySelector('#browser-schedules-sidebar');
            return sidebar._schedules.find(item => item.name === name)?.enabled === false
                && !sidebar._loading;
        }""",
            arg=SCHEDULE_NAME,
        )
        assert "enabled: false" in schedule_path.read_text()

        with page.expect_response("**/api/schedules/*/run") as sidebar_run_response:
            schedule_row.locator(".schedule-row-run").click()
        assert sidebar_run_response.value.status == 202
        page.wait_for_function(
            """name => document.querySelector(
            '#browser-schedules-sidebar')._runStatus[name] === 'started'""",
            arg=SCHEDULE_NAME,
        )
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
        page.evaluate(
            """async (name) => {
            await import('/static/components/schedule-page.js');
            const schedulePage = document.createElement('schedule-page');
            schedulePage.id = 'browser-schedule-page';
            schedulePage.name = name;
            document.body.append(schedulePage);
        }""",
            SCHEDULE_NAME,
        )
        page.wait_for_function(
            """name => {
            const schedulePage = document.querySelector('#browser-schedule-page');
            return schedulePage?._data?.name === name && !schedulePage._loading
                && schedulePage._modelsLoaded
                && schedulePage._models.includes('browser-model');
        }""",
            arg=SCHEDULE_NAME,
        )
        assert page.evaluate("""document.querySelector(
            '#browser-schedule-page schedule-metadata').models""") == ["browser-model"]

        with page.expect_response("**/api/schedules/*/run") as page_run_response:
            page.locator("#browser-schedule-page .schedule-run-btn").click()
        assert page_run_response.value.status == 202
        page.wait_for_function("""() => document.querySelector(
            '#browser-schedule-page')._runStatus === 'started'""")

        expect_window_event(page, "schedule-saved", "scheduleSaved")
        channel = page.locator("#browser-schedule-page .sched-md-channel")
        channel.fill("browser-channel")
        channel.dispatch_event("change")
        page.wait_for_function("""() => window.scheduleSaved === true && document.querySelector(
            '#browser-schedule-page')._data.channel === 'browser-channel'""")
        assert "channel: browser-channel" in schedule_path.read_text()

        schedule_editor = page.locator("#browser-schedule-page wiki-editor .milkdown .ProseMirror")
        schedule_editor.fill("Saved through generated schedule")
        page.wait_for_function("""document.querySelector(
            '#browser-schedule-page wiki-editor')._status === 'editing'""")
        expect_window_event(page, "schedule-saved", "scheduleSaved")
        page.evaluate("""document.querySelector(
            '#browser-schedule-page wiki-editor').flushSave()""")
        page.wait_for_function("""() => window.scheduleSaved === true && document.querySelector(
            '#browser-schedule-page wiki-editor')._status === 'saved'""")
        assert "# Saved through generated schedule\n" in schedule_path.read_text()

        schedule_path.write_text(
            schedule_path.read_text().replace("# Saved through generated schedule\n\n", "# Server schedule\n")
        )
        page.evaluate(
            """node => { node._status = 'conflict'; }""",
            page.locator("#browser-schedule-page wiki-editor").element_handle(),
        )
        page.locator(
            "#browser-schedule-page .wiki-editor-conflict button",
            has_text="Reload",
        ).click()
        page.wait_for_function("""() => {
            const editor = document.querySelector('#browser-schedule-page wiki-editor');
            return editor.content.includes('Server schedule') && editor._status === 'saved';
        }""")

        schedule_editor.fill("Forced through generated schedule")
        page.wait_for_function("""document.querySelector(
            '#browser-schedule-page wiki-editor')._status === 'editing'""")
        page.evaluate(
            """node => { node._status = 'conflict'; }""",
            page.locator("#browser-schedule-page wiki-editor").element_handle(),
        )
        expect_window_event(page, "schedule-saved", "scheduleSaved")
        page.locator(
            "#browser-schedule-page .wiki-editor-conflict button",
            has_text="Overwrite",
        ).click()
        page.wait_for_function("""() => window.scheduleSaved === true && document.querySelector(
            '#browser-schedule-page wiki-editor')._status === 'saved'""")
        assert "# Forced through generated schedule\n" in schedule_path.read_text()

        requests = schedule_requests(scenario)
        assert all(request[4] for request in requests)
        assert ("GET", "/api/models", None, None, True) in requests
        assert ("GET", ENCODED_SCHEDULE, None, None, True) in requests
        assert ("PUT", ENCODED_SCHEDULE, {"channel": "browser-channel"}, "application/json", True) in requests
        saved_schedule_requests = [
            request
            for request in requests
            if request[0] == "PUT"
            and request[1] == ENCODED_SCHEDULE
            and request[2].get("content") == "# Saved through generated schedule\n"
        ]
        assert len(saved_schedule_requests) == 1
        assert isinstance(saved_schedule_requests[0][2].get("modified"), float)
        assert saved_schedule_requests[0][3:] == ("application/json", True)
        assert (
            "PUT",
            ENCODED_SCHEDULE,
            {
                "content": "# Forced through generated schedule\n",
            },
            "application/json",
            True,
        ) in requests
        assert sum(request[0] == "POST" and request[1] == ENCODED_SCHEDULE + "/run" for request in requests) == 1


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
        page.evaluate("""async () => {
            const { AuthClient } = await import('/static/lib/auth-client.js');
            await new AuthClient().logout();
        }""")
        assert not any(cookie["name"] == "decafclaw_session" for cookie in page.context.cookies())
        assert [(request.method, request.path) for request in scenario.requests] == [
            ("POST", "/api/auth/logout"),
        ]
        page.route(base + "/", lambda route: route.fulfill(body="Login destination"))
        page.route(
            vault_url,
            lambda route: route.fulfill(
                content_type="text/html",
                body=(scenario.static / "vault.html").read_text(),
            ),
        )
        with page.expect_response("**/api/auth/me") as guard_response:
            page.goto(vault_url)
        assert guard_response.value.status == 401
        page.wait_for_url(base + "/")
        assert base + "/api/auth/me" in scenario.failed_requests
        assert set(scenario.failed_requests) <= {
            base + "/api/auth/me",
            base + "/api/vault/agent/pages/Browser%20%26%20%E6%97%A5%E6%9C%AC%E8%AA%9E",
        }, scenario.failed_requests
        scenario.failed_requests.clear()


def test_browser_shell_command_and_result(built_static, chromium, config):
    with browser_scenario(built_static, chromium, config) as scenario:
        page = scenario.page
        page.evaluate("""async () => {
            const { MessageStore } = await import('/static/lib/message-store.js');
            const { MESSAGE_TYPES } = await import('/static/lib/message-types.js');
            await import('/static/components/chat-view.js');
            const store = new MessageStore(() => {});
            store.handleMessage({ type: MESSAGE_TYPES.CONV_HISTORY, conv_id: '1', messages: [
                { role: 'assistant', tool_calls: [{ id: 'tc1', function: { name: 'shell', arguments: JSON.stringify({command: 'echo <hello>'}) } }], timestamp: 't1' },
                { role: 'tool', tool_call_id: 'tc1', content: '<hello>', timestamp: 't2' },
            ] }, '1');
            const view = document.createElement('chat-view');
            view.id = 'shell-test-view';
            view._convId = '1';
            view._messages = store.currentMessages;
            document.body.append(view);
            await view.updateComplete;
        }""")
        page.locator("#shell-test-view .tool-result-header").click()
        page.wait_for_function("""() => document.querySelector('#shell-test-view')
            .textContent.includes('echo <hello>')""")
        details = page.locator("#shell-test-view .tool-result-detail pre").all_text_contents()
        assert details == ["echo <hello>", "<hello>"]
        assert page.locator("#shell-test-view hello").count() == 0
