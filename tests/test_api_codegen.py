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
    workspace_folder = "Browser files/日本語 #?"
    workspace_rel_path = f"{workspace_folder}/Browser note #?.md"
    renamed_workspace_rel_path = f"{workspace_folder}/Renamed 日本語 & #?.md"
    workspace_path = config.workspace_path / workspace_rel_path
    workspace_path.parent.mkdir(parents=True, exist_ok=True)
    workspace_path.write_text("first browser content")
    config.vault_root.mkdir(parents=True, exist_ok=True)
    (config.vault_root / "Browser Vault.md").write_text("# Browser Vault")
    vault_page_name = "agent/pages/Browser & 日本語"
    vault_page_path = config.vault_root / f"{vault_page_name}.md"
    vault_page_path.parent.mkdir(parents=True, exist_ok=True)
    vault_page_path.write_text(
        "---\nsummary: Browser summary\ntags: [BrowserTag]\n"
        "nested:\n  rows: [1, true, null]\n---\n# Browser vault body\n",
    )
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
    workspace_read_requests = []
    workspace_mutation_requests = []
    vault_read_requests = []
    vault_mutation_requests = []

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
        if (request.method == "GET" and (
            request.url.path in {"/api/workspace", "/api/workspace/recent", "/api/autocomplete"}
            or request.url.path.startswith("/api/workspace-file/")
        )):
            workspace_read_requests.append((request.scope["path"],
                                            list(request.query_params.multi_items()),
                                            bool(request.cookies)))
        if (request.method in {"PUT", "DELETE"}
                and request.url.path.startswith("/api/workspace/")):
            workspace_mutation_requests.append((
                request.method, request.scope["path"], list(request.query_params.multi_items()),
                await request.body(), request.headers.get("content-type"), bool(request.cookies),
            ))
        if request.method == "GET" and (
            request.url.path in {"/api/vault", "/api/vault/recent", "/api/vault/tags"}
            or request.url.path.startswith("/api/vault/")
        ):
            vault_read_requests.append((request.scope["path"],
                                        list(request.query_params.multi_items()),
                                        bool(request.cookies)))
        if (request.method in {"POST", "PUT", "DELETE"} and (
            request.url.path in {"/api/vault", "/api/vault/folders"}
            or request.url.path.startswith("/api/vault/")
        )):
            vault_mutation_requests.append((
                request.method, request.scope["path"],
                await request.json() if request.method != "DELETE" else None,
                request.headers.get("content-type"), bool(request.cookies),
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
                    "codemirror": "/static/vendor/bundle/codemirror.js",
                }}))
                workspace_result = page.evaluate("""async ({ folder, path }) => {
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
                    return true;
                }""", {"folder": workspace_folder, "path": workspace_rel_path})
                assert workspace_result
                page.wait_for_function("""() => {
                    const sidebar = document.querySelector('#browser-files');
                    const filePage = document.querySelector('#browser-file-page');
                    const chat = document.querySelector('#browser-chat-input');
                    return sidebar?._files?.length === 1
                        && filePage?._content === 'first browser content'
                        && chat?._mentionMatches?.some(item => item.type === 'vault'
                            && item.id === 'Browser Vault');
                }""")
                assert page.evaluate("document.querySelector('#browser-files')._files[0].path") == workspace_rel_path
                assert page.evaluate("document.querySelector('#browser-file-page')._modified > 0")
                assert page.evaluate("""document.querySelector('#browser-chat-input')
                    ._mentionMatches.find(item => item.id === 'Browser Vault').label""") == "Browser Vault"
                workspace_path.write_text("second browser content")
                page.evaluate("document.querySelector('#browser-file-page').reload()")
                page.wait_for_function("document.querySelector('#browser-file-page')._content === 'second browser content'")

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
                page.locator('#browser-file-page .file-editor-conflict button').click()
                page.wait_for_function(
                    "document.querySelector('#browser-file-page')._content === 'server conflict content'")

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
                renamed_workspace_path = config.workspace_path / renamed_workspace_rel_path
                assert renamed_workspace_path.read_text() == "server conflict content"
                assert not workspace_path.exists()

                page.evaluate("""path => {
                    const filePage = document.querySelector('#browser-file-page');
                    filePage.kind = 'binary';
                    filePage.path = path;
                }""", renamed_workspace_rel_path)
                page.wait_for_function(
                    "document.querySelector('#browser-file-page .file-delete-btn') !== null")
                page.evaluate("""() => {
                    window.workspaceFileDeleted = false;
                    window.addEventListener('workspace-file-deleted',
                        () => window.workspaceFileDeleted = true, {once: true});
                }""")
                page.once("dialog", lambda dialog: dialog.accept())
                page.locator('#browser-file-page .file-delete-btn').click()
                page.wait_for_function("window.workspaceFileDeleted === true")
                # The standalone test page has no app-level close handler; route
                # completion is the observable delete result here.
                assert not renamed_workspace_path.exists()
                assert workspace_read_requests == [
                    ("/api/workspace", [("folder", workspace_folder)], True),
                    (f"/api/workspace-file/{workspace_rel_path}", [], True),
                    ("/api/autocomplete", [("q", "Browser")], True),
                    (f"/api/workspace-file/{workspace_rel_path}", [], True),
                    (f"/api/workspace-file/{workspace_rel_path}", [], True),
                    ("/api/workspace", [("folder", workspace_folder)], True),
                ]
                assert [
                    (method, path, query, content_type, authenticated)
                    for method, path, query, _body, content_type, authenticated
                    in workspace_mutation_requests
                ] == [
                    ("PUT", f"/api/workspace/{workspace_rel_path}", [], "application/json", True),
                    ("PUT", f"/api/workspace/{workspace_rel_path}", [], "application/json", True),
                    ("PUT", f"/api/workspace/{workspace_rel_path}",
                     [("rename_to", renamed_workspace_rel_path)], None, True),
                    ("DELETE", f"/api/workspace/{renamed_workspace_rel_path}", [], None, True),
                ]
                first_save = json.loads(workspace_mutation_requests[0][3])
                conflict_save = json.loads(workspace_mutation_requests[1][3])
                assert first_save["content"] == "saved browser content"
                assert isinstance(first_save["modified"], float)
                assert conflict_save["content"] == "stale browser content"
                assert isinstance(conflict_save["modified"], float)
                assert workspace_mutation_requests[2][3] == b""
                assert workspace_mutation_requests[3][3] == b""
                page.locator('#browser-files, #browser-file-page, #browser-chat-input').evaluate_all(
                    "nodes => nodes.forEach(node => node.remove())")
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
                vault_url = base + "/vault/agent/pages/Browser%20%26%20%E6%97%A5%E6%9C%AC%E8%AA%9E"
                with page.expect_response("**/api/auth/me") as guard_response:
                    page.goto(vault_url)
                assert guard_response.value.status == 200
                page.wait_for_function("document.querySelector('wiki-page')._loaded")
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
                assert page.locator("wiki-page").evaluate("node => node.page") == vault_page_name
                assert page.url == vault_url

                page.evaluate("""async (pageName) => {
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

                    const editor = document.createElement('wiki-editor');
                    editor.id = 'browser-vault-editor';
                    editor.page = pageName;
                    editor.content = 'stale editor body';
                    editor.modified = 0;
                    document.body.append(editor);
                    await editor.updateComplete;
                    editor._status = 'conflict';
                    await editor.updateComplete;
                }""", vault_page_name)
                page.wait_for_function("""() =>
                    document.querySelector('#browser-vault-sidebar')._wikiPages.length === 1
                    && document.querySelector('#browser-tags-sidebar')._tags.length === 1
                """)
                assert page.locator('#browser-vault-sidebar').evaluate(
                    "node => node._wikiPages[0].path") == vault_page_name
                assert page.locator('#browser-tags-sidebar').evaluate(
                    "node => node._tags[0].pages") == [f"{vault_page_name}.md"]
                page.locator('#browser-vault-sidebar button', has_text='Recent').click()
                page.wait_for_function(
                    "document.querySelector('#browser-vault-sidebar')._recentPages.length === 1")
                page.locator('#browser-vault-editor .wiki-editor-conflict button',
                             has_text='Reload').click()
                page.wait_for_function(
                    "document.querySelector('#browser-vault-editor').content === '# Browser vault body\\n'")
                assert vault_read_requests == [
                    (f"/api/vault/{vault_page_name}", [], True),
                    ("/api/vault", [("folder", "agent/pages")], True),
                    ("/api/vault/tags", [], True),
                    ("/api/vault/recent", [], True),
                    (f"/api/vault/{vault_page_name}", [], True),
                ]

                page.locator('#browser-vault-sidebar button', has_text='Browse').click()
                page.wait_for_function("""() => document.querySelector(
                    '#browser-vault-sidebar .wiki-new-page-btn') !== null""")
                page.once("dialog", lambda dialog: dialog.accept("Created #1"))
                page.locator('#browser-vault-sidebar .wiki-new-page-btn').click()
                created_vault_page = config.vault_root / "agent/pages/Created #1.md"
                page.wait_for_function("""() => document.querySelector('#browser-vault-sidebar')
                    ._wikiPages.some(item => item.path === 'agent/pages/Created #1')""")
                assert created_vault_page.exists()

                page.once("dialog", lambda dialog: dialog.accept("Folder #1"))
                page.locator('#browser-vault-sidebar .wiki-new-folder-btn').click()
                page.wait_for_function("""() => document.querySelector('#browser-vault-sidebar')
                    ._vaultFolder === 'agent/pages/Folder #1'""")
                assert (config.vault_root / "agent/pages/Folder #1").is_dir()

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

                page.evaluate("document.querySelector('wiki-page')._onMetadataReload()")
                page.wait_for_function("""() => document.querySelector('wiki-page')._body
                    === '# Forced through generated client\\n'""")
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
                with page.expect_response("**/api/vault/agent/pages/Browser**") as rename_response:
                    page.locator('wiki-page .wiki-rename-ok').click()
                assert rename_response.value.status == 200
                assert renamed_vault_path.exists()
                assert not vault_page_path.exists()

                page.evaluate("""name => { document.querySelector('wiki-page').page = name; }""",
                              renamed_vault_name)
                page.wait_for_function("""() => document.querySelector('wiki-page')._loaded
                    && document.querySelector('wiki-page').page
                        === 'agent/archive/Renamed 日本語 #1'""")
                page.once("dialog", lambda dialog: dialog.accept())
                with page.expect_response("**/api/vault/agent/archive/Renamed**") as delete_response:
                    page.locator('wiki-page .wiki-delete-btn').click()
                assert delete_response.value.status == 200
                assert not renamed_vault_path.exists()

                assert vault_mutation_requests == [
                    ("POST", "/api/vault", {"name": "agent/pages/Created #1"},
                     "application/json", True),
                    ("POST", "/api/vault/folders", {"folder": "agent/pages/Folder #1"},
                     "application/json", True),
                        ("PUT", f"/api/vault/{vault_page_name}", {
                            "content": "# Saved through generated client\n",
                            "modified": initial_vault_mtime,
                    }, "application/json", True),
                    ("PUT", f"/api/vault/{vault_page_name}", {
                        "content": "# Forced through generated client\n",
                        "modified": stale_vault_mtime,
                    }, "application/json", True),
                    ("PUT", f"/api/vault/{vault_page_name}", {
                        "content": "# Forced through generated client\n",
                    }, "application/json", True),
                    ("PUT", f"/api/vault/{vault_page_name}", {
                        "frontmatter_raw": "summary: Generated write\nnested: [1, true, null]",
                        "modified": metadata_modified,
                    }, "application/json", True),
                    ("PUT", f"/api/vault/{vault_page_name}", {
                        "rename_to": renamed_vault_name,
                    }, "application/json", True),
                    ("DELETE", f"/api/vault/{renamed_vault_name}", None, None, True),
                ]
                assert len(failed_requests) == 1
                assert failed_requests[0].endswith(
                    "/api/vault/agent/pages/Browser%20%26%20%E6%97%A5%E6%9C%AC%E8%AA%9E")
                failed_requests.clear()
                assert not errors, errors
                assert not failed_requests, failed_requests

                # Restore the fixture removed by the delete scenario so the
                # remaining auth-client regressions can revisit this page.
                vault_page_path.parent.mkdir(parents=True, exist_ok=True)
                vault_page_path.write_text("# Restored after delete\n")

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
