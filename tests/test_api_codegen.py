"""Exercise current backend types and browser output in disposable source trees."""

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

from decafclaw.events import EventBus
from decafclaw.http_server import create_app
from decafclaw.sticky import write_sticky_state
from decafclaw.web.auth import create_token
from decafclaw.web.conversations import ConversationIndex

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
    app = create_app(config, EventBus())
    for route in app.routes:
        if getattr(route, "path", None) == "/static":
            route.app = StaticFiles(directory=source_tree / STATIC_REL)
    listing_requests = []

    @app.middleware("http")
    async def record_listing_request(request, call_next):
        if request.method == "GET" and request.url.path in {
            "/api/conversations", "/api/conversations/archived", "/api/conversations/system",
        }:
            listing_requests.append((request.url.path, list(request.query_params.multi_items()), bool(request.cookies)))
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
                login = page.request.post(base + "/api/auth/login", data={"token": token})
                assert login.status == 200
                result = page.evaluate("""async () => {
                    const { AuthClient } = await import('/static/lib/auth-client.js');
                    const client = new AuthClient();
                    return { username: await client.checkSession(), currentUser: client.currentUser };
                }""")
                assert result == {"username": "browser-user", "currentUser": "browser-user"}
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
                assert not errors, errors
                assert not failed_requests, failed_requests
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
