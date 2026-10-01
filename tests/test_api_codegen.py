"""Exercise current backend types and browser output in disposable source trees."""

import json
import os
import pathlib
import shutil
import socket
import subprocess
import threading

import pytest
import uvicorn
from playwright.sync_api import sync_playwright
from starlette.staticfiles import StaticFiles

from decafclaw import notifications as notifs
from decafclaw.archive import append_message, archive_path
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


@pytest.fixture
def source_tree(tmp_path):
    root = tmp_path / "source"
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


def test_browser_uses_clean_built_client(source_tree, config):
    shutil.rmtree(source_tree / CLIENT_REL)
    result, output = run_make(source_tree, "gen-api-client", "check-browser-assets")
    assert result.returncode == 0, output
    config.http.secret = "isolated-browser-test-secret"
    config.agent_path.mkdir(parents=True, exist_ok=True)
    token = create_token(config, "browser-user")
    conv_id = ConversationIndex(config).create("browser-user", title="Sticky test").conv_id
    payload = {"content": "# Doc", "extra": {"rows": [1, True, None, {"label": "nested"}]}}
    assert write_sticky_state(config, conv_id, {
        "schema_version": 1, "widget_type": "markdown_document", "data": payload,
    })
    append_message(config, conv_id, {"role": "user", "content": "Export 日本語"})
    append_message(config, conv_id, {"role": "assistant", "content": "Second line"})
    diagnostics_payload = {
        "total_tokens_estimated": 123, "context_window_size": 1000,
        "sources": [{"source": "memory", "tokens_estimated": 123, "items_included": 1,
                     "details": {"top_score": 0.9, "budget_source": "dynamic"}}],
    }
    write_context_sidecar(config, conv_id, diagnostics_payload)
    init_widgets(config)
    app = create_app(config, EventBus())
    for route in app.routes:
        if getattr(route, "path", None) == "/static":
            route.app = StaticFiles(directory=source_tree / STATIC_REL)
    notification_id = "record ?#%é"
    notification = notifs.NotificationRecord(
        id=notification_id, timestamp="2026-10-01T00:00:00Z", category="background",
        title="Browser notification", body="Finished work", conv_id=conv_id,
    )
    inbox = config.workspace_path / "notifications" / "inbox.jsonl"
    inbox.parent.mkdir(parents=True, exist_ok=True)
    inbox.write_text(json.dumps(notification.to_dict()) + "\n")
    notification_requests = []
    context_export_requests = []
    listing_requests = []
    patch_requests = []
    folder_requests = []
    lifecycle_requests = []
    canvas_requests = []
    widget_catalog_requests = []

    @app.middleware("http")
    async def record_listing_request(request, call_next):
        if request.url.path.startswith("/api/notifications"):
            notification_requests.append((request.method, request.scope["path"],
                                          list(request.query_params.multi_items()),
                                          await request.body(), bool(request.cookies)))
        if request.url.path.endswith(("/context", "/export")):
            context_export_requests.append((request.method, request.url.path,
                                            list(request.query_params.multi_items()),
                                            await request.body(), bool(request.cookies)))
        if request.method == "GET" and request.url.path in {
            "/api/conversations", "/api/conversations/archived", "/api/conversations/system",
        }:
            listing_requests.append((request.url.path, list(request.query_params.multi_items()), bool(request.cookies)))
        if request.method == "PATCH":
            patch_requests.append((request.url.path, await request.json(),
                                   request.headers.get("content-type"), bool(request.cookies)))
        if request.method in {"POST", "PUT", "DELETE"} and request.url.path.startswith("/api/conversations/folders"):
            folder_requests.append((request.method, request.scope["path"],
                                    await request.json() if request.method != "DELETE" else None,
                                    bool(request.cookies)))
        if (request.method in {"POST", "DELETE"} and request.url.path.startswith("/api/conversations")
                and not request.url.path.startswith("/api/conversations/folders")):
            lifecycle_requests.append((request.method, request.scope["path"],
                                       await request.json() if request.url.path == "/api/conversations" else None,
                                       bool(request.cookies)))
        if request.url.path.startswith(f"/api/canvas/{conv_id}"):
            canvas_requests.append((
                request.method, request.scope["path"],
                await request.json() if request.method == "POST" else None,
                request.headers.get("content-type"), bool(request.cookies),
            ))
        if request.url.path == "/api/widgets":
            widget_catalog_requests.append(bool(request.cookies))
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
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                page = browser.new_page()
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
                page.add_script_tag(type="importmap", content=json.dumps({"imports": {
                    "lit": "/static/vendor/bundle/lit.js",
                    "lit/directives/unsafe-html.js": "/static/vendor/bundle/lit-unsafe-html.js",
                    "diff": "/static/vendor/bundle/diff.js",
                    "marked": "/static/vendor/bundle/marked.js",
                    "dompurify": "/static/vendor/bundle/dompurify.js",
                    "hljs": "/static/vendor/bundle/highlight.js",
                }}))
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
                assert notification_requests == [
                    ("GET", "/api/notifications/unread-count", [], b"", True),
                    ("GET", "/api/notifications", [("limit", "20")], b"", True),
                    ("POST", f"/api/notifications/{notification_id}/read", [], b"", True),
                    ("GET", "/api/notifications", [("limit", "20")], b"", True),
                    ("POST", "/api/notifications/read-all", [], b"", True),
                ]
                page.locator('notification-inbox').evaluate("el => el.remove()")
                inspected = page.evaluate("""async (convId) => {
                    await import('/static/components/context-inspector.js');
                    const el = document.createElement('context-inspector');
                    el.convId = convId; el.open = true; document.body.append(el);
                    return true;
                }""", conv_id)
                assert inspected
                page.wait_for_function("document.querySelector('context-inspector')._data !== null")
                assert page.locator("context-inspector").inner_text().find("dynamic budget") >= 0
                assert page.evaluate("document.querySelector('context-inspector')._data") == diagnostics_payload
                copied = page.evaluate("""async (convId) => {
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
                assert context_export_requests == [
                    ("GET", f"/api/conversations/{conv_id}/context", [], b"", True),
                    ("GET", f"/api/conversations/{conv_id}/export", [("format", "jsonl")], b"", True),
                    ("GET", f"/api/conversations/{conv_id}/export", [("format", "markdown")], b"", True),
                ]
                page.locator("context-inspector").evaluate("el => el.remove()")
                sticky_requests = []
                page.on("request", lambda request: sticky_requests.append(request.url)
                        if "/api/sticky/" in request.url else None)
                snapshot = page.evaluate("""async (convId) => {
                    const sticky = await import('/static/lib/sticky-state.js');
                    await sticky.setActiveConv(convId);
                    return sticky.currentSnapshot();
                }""", conv_id)
                assert snapshot == {
                    "widgetType": "markdown_document", "data": payload,
                    "collapsed": False, "visible": True,
                }
                assert sticky_requests == [f"{base}/api/sticky/{conv_id}"]
                nested = "Work space/日本語 & plus+ #hash"
                assert page.request.post(base + "/api/conversations/folders", data={"path": nested}).status == 200
                active = page.request.post(base + "/api/conversations", data={"title": "Nested", "folder": nested}).json()
                archived = page.request.post(base + "/api/conversations", data={"title": "Archived", "folder": nested}).json()
                assert page.request.post(base + f"/api/conversations/{archived['conv_id']}/archive").status == 200
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
                assert listing_requests == expected_requests
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
                assert patch_requests == [
                    (f"/api/conversations/{conv_id}", {"title": patch_title}, "application/json", True),
                    (f"/api/conversations/{conv_id}", {"folder": "  " + nested + "  "}, "application/json", True),
                ]
                saved = page.request.get(base + "/api/conversations", params={"folder": nested}).json()
                assert any(c["conv_id"] == conv_id and c["title"] == patch_title for c in saved["conversations"])
                folder_requests.clear()
                folder_path = "Folder space/日本語 & plus+ #hash%?"
                renamed_path = "Folder space/new + %?"
                folders = page.evaluate("""async ({ path, renamed }) => {
                    const { DefaultService } = await import('/static/lib/api-client/index.js');
                    return [
                        await DefaultService.createConvFolderApiConversationsFoldersPost({ path }),
                        await DefaultService.renameConvFolderApiConversationsFoldersPathPut(path, { path: renamed }),
                        await DefaultService.deleteConvFolderApiConversationsFoldersPathDelete(renamed),
                    ];
                }""", {"path": folder_path, "renamed": renamed_path})
                assert folders == [{"ok": True, "path": folder_path}, {"ok": True}, {"ok": True}]
                assert folder_requests == [
                    ("POST", "/api/conversations/folders", {"path": folder_path}, True),
                    ("PUT", "/api/conversations/folders/" + folder_path, {"path": renamed_path}, True),
                    ("DELETE", "/api/conversations/folders/" + renamed_path, None, True),
                ]
                lifecycle_requests.clear()
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
                assert lifecycle_requests == [
                    ("POST", "/api/conversations", {"title": created["title"], "folder": nested}, True),
                    ("POST", f"/api/conversations/{created['conv_id']}/archive", None, True),
                    ("POST", f"/api/conversations/{created['conv_id']}/unarchive", None, True),
                    ("DELETE", f"/api/conversations/{created['conv_id']}", None, True),
                ]
                assert ConversationIndex(config).get(created["conv_id"]) is None
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
                         {nested: [1, true, null]}],
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
                assert widget_catalog_requests == [True]
                assert canvas_requests == [
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
                        "widget_type": "json_view", "data": {"nested": [1, True, None]},
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
                errors.clear()
                failed_requests.clear()
                page.goto(base + f"/canvas/{conv_id}/canvas_1")
                page.wait_for_function("document.querySelector('dc-widget-code-block') !== null")
                assert page.locator("dc-widget-code-block").inner_text().find("print(1)") >= 0
                assert page.title() == "Canvas — demo.py"
                assert canvas_requests[-1] == (
                    "GET", f"/api/canvas/{conv_id}", None, None, True,
                )
                assert widget_catalog_requests == [True, True]
                assert not errors, errors
                assert not failed_requests, failed_requests

                # Load the actual standalone page and its module graph, including
                # decoding a page name and observing the loaded title.
                page.evaluate("localStorage.setItem('wiki-edit-mode', 'false')")
                page.route("**/api/vault/**", lambda route: route.fulfill(json={
                    "title": "Browser vault title", "body": "# Vault body", "modified": 1,
                }))
                vault_url = base + "/vault/Page%20%26%20%E6%97%A5%E6%9C%AC%E8%AA%9E"
                with page.expect_response("**/api/auth/me") as guard_response:
                    page.goto(vault_url)
                assert guard_response.value.status == 200
                page.wait_for_function("document.querySelector('wiki-page')._loaded")
                # The component currently renders no .wiki-page-title element.
                # Preserve the page's existing observer reaction without fixing
                # that independent mismatch in this auth migration.
                assert page.title() == "Vault — DecafClaw"
                page.locator("wiki-page").evaluate("""node => {
                    const title = document.createElement('span');
                    title.className = 'wiki-page-title';
                    title.textContent = 'Browser vault title';
                    node.append(title);
                }""")
                page.wait_for_function("document.title === 'Browser vault title — DecafClaw Vault'")
                assert page.locator("wiki-page").evaluate("node => node.page") == "Page & 日本語"
                assert page.url == vault_url
                assert not errors, errors
                assert not failed_requests, failed_requests

                # The no-body /me overload must not decode a successful response.
                page.route("**/api/auth/me", lambda route: route.fulfill(status=200, body="not JSON"))
                with page.expect_response("**/api/auth/me"):
                    page.goto(vault_url)
                page.wait_for_function("document.querySelector('wiki-page')._loaded")
                assert page.url == vault_url
                assert not errors, errors
                page.unroute("**/api/auth/me")

                # Real generated logout deletes the browser cookie. The guard's
                # unauthenticated HTTP response redirects the standalone page.
                page.evaluate("""async () => {
                    const { AuthClient } = await import('/static/lib/auth-client.js');
                    await new AuthClient().logout();
                }""")
                assert not any(cookie["name"] == "decafclaw_session" for cookie in page.context.cookies())
                page.route(base + "/", lambda route: route.fulfill(body="Login destination"))
                # /vault itself requires auth. Serve the real shell as though
                # it loaded before the session expired, then exercise its guard
                # against the real unauthenticated backend response.
                page.route(vault_url, lambda route: route.fulfill(
                    content_type="text/html", body=(source_tree / STATIC_REL / "vault.html").read_text(),
                ))
                with page.expect_response("**/api/auth/me") as guard_response:
                    page.goto(vault_url)
                assert guard_response.value.status == 401
                page.wait_for_url(base + "/")
                assert not errors, errors
            finally:
                browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=15)
        sock.close()
        assert not thread.is_alive(), "test server did not stop"


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


@pytest.mark.parametrize("contract", [
    "state_path", "new_tab_path", "active_tab_path", "close_tab_path",
    "widget_type", "data", "label", "tab_id",
])
def test_canvas_used_input_contract_drift_fails_at_unchanged_callers(source_tree, contract):
    widgets = ["code_block/widget.js", "diff_view/widget.js", "json_view/widget.js",
               "markdown_document/widget.js"]
    if contract.endswith("_path"):
        operation = {
            "state_path": "get_canvas_state",
            "new_tab_path": "post_canvas_new_tab",
            "active_tab_path": "post_canvas_active_tab",
            "close_tab_path": "post_canvas_close_tab",
        }[contract]
        replacements = [(f"async def {operation}(request: Request, conv_id: str)",
                         f"async def {operation}(request: Request, conv_id: int)")]
        callers = {
            # canvas-page derives convId from location.pathname, so it remains a
            # statically typed string. canvas-state's public setter accepts
            # untyped JavaScript input and cannot prove this mismatch.
            "state_path": ["canvas-page.js"],
            "new_tab_path": widgets,
            "active_tab_path": ["canvas-state.js"],
            "close_tab_path": ["canvas-state.js"],
        }[contract]
        diagnostic, code = "not assignable to parameter of type 'number'", "TS2345"
    elif contract == "tab_id":
        replacements = [("class CanvasTabRequest(BaseModel):\n    tab_id: str",
                         "class CanvasTabRequest(BaseModel):\n    tab_id: int")]
        callers = ["canvas-state.js"]
        diagnostic, code = "Type 'string' is not assignable to type 'number'", "TS2322"
    else:
        replacement = {
            "widget_type": ("    widget_type: str", "    widget_type: int"),
            "data": ("    data: dict[str, object]", "    renamed_data: dict[str, object]"),
            "label": ("    label: str | None = None", "    label: int | None = None"),
        }[contract]
        replacements = [replacement]
        callers = widgets
        diagnostic, code = {
            "widget_type": ("Type 'string' is not assignable to type 'number'", "TS2322"),
            "data": ("'data' does not exist", "TS2353"),
            "label": ("Type 'string' is not assignable to type 'number'", "TS2322"),
        }[contract]
        if contract == "label":
            # json_view supplies a literal label. diff_view computes its label
            # through untyped widget data, while the other callers omit it.
            callers = ["json_view/widget.js"]
    output = _mutate_canvas_contract(source_tree, replacements)
    diagnostics = [line for line in output.splitlines() if "error TS" in line]
    for caller in callers:
        assert any(caller in line and code in line and diagnostic in line
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
