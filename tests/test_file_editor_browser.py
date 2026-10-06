"""Browser regression tests for file editor scrolling and layout."""

import json
import socket
import threading

import uvicorn
from playwright.sync_api import sync_playwright

from decafclaw.events import EventBus
from decafclaw.http_server import create_app
from decafclaw.web.auth import create_token


def test_browser_file_editor_scroll_follow_and_overflow(config):
    config.http.secret = "file-editor-browser-secret"
    config.agent_path.mkdir(parents=True, exist_ok=True)
    config.workspace_path.mkdir(parents=True, exist_ok=True)
    token = create_token(config, "browser-user")

    tall_file = "tall.py"
    lines = [f"line {i}: testing scrolling in codemirror file editor" for i in range(1, 70)]
    (config.workspace_path / tall_file).write_text("\n".join(lines))

    image_file = "sample.png"
    (config.workspace_path / image_file).write_bytes(b"dummy image bytes")

    app = create_app(config, EventBus())

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
                page = browser.new_page(viewport={"width": 1000, "height": 600})
                page.goto(base + "/static/lib/auth-client.js")
                page.evaluate(
                    """async token => {
                    const { AuthClient } = await import('/static/lib/auth-client.js');
                    await new AuthClient().login(token);
                }""",
                    token,
                )
                page.add_script_tag(
                    type="importmap",
                    content=json.dumps(
                        {
                            "imports": {
                                "lit": "/static/vendor/bundle/lit.js",
                                "codemirror": "/static/vendor/bundle/codemirror.js",
                                "dompurify": "/static/vendor/bundle/dompurify.js",
                                "marked": "/static/vendor/bundle/marked.js",
                            }
                        }
                    ),
                )

                # Inject style links
                page.evaluate("""() => {
                    const pico = document.createElement('link');
                    pico.rel = 'stylesheet'; pico.href = '/static/vendor/bundle/pico.min.css';
                    document.head.appendChild(pico);

                    const style = document.createElement('link');
                    style.rel = 'stylesheet'; style.href = '/static/style.css';
                    document.head.appendChild(style);

                    const container = document.createElement('div');
                    container.id = 'wiki-main';
                    container.style.height = '400px';
                    container.style.display = 'flex';
                    container.style.flexDirection = 'column';
                    document.body.appendChild(container);
                }""")

                # Mount file-page in text mode
                page.evaluate("""async () => {
                    await import('/static/components/file-page.js');
                    const wikiMain = document.getElementById('wiki-main');
                    const filePage = document.createElement('file-page');
                    filePage.path = 'tall.py';
                    filePage.kind = 'text';
                    wikiMain.appendChild(filePage);
                }""")

                page.wait_for_selector(".cm-editor")
                page.wait_for_selector(".cm-scroller")

                # Verify #wiki-main has overflow-y: hidden when file-editor is present
                wiki_overflow = page.evaluate("""() => {
                    return getComputedStyle(document.getElementById('wiki-main')).overflowY;
                }""")
                assert wiki_overflow == "hidden"

                # Verify scroller height vs scrollHeight
                scroller_metrics = page.evaluate("""() => {
                    const scroller = document.querySelector('.cm-scroller');
                    return {
                        clientHeight: scroller.clientHeight,
                        scrollHeight: scroller.scrollHeight,
                        scrollTop: scroller.scrollTop,
                    };
                }""")
                assert scroller_metrics["scrollHeight"] > scroller_metrics["clientHeight"]
                assert scroller_metrics["scrollTop"] == 0

                # Focus editor and check caret animation under reduced motion
                page.emulate_media(reduced_motion="reduce")
                page.click(".cm-content")
                caret_anim = page.evaluate("""() => {
                    const cl = document.querySelector('.cm-cursorLayer');
                    return {
                        duration: getComputedStyle(cl).animationDuration,
                        iterationCount: getComputedStyle(cl).animationIterationCount,
                    };
                }""")
                assert caret_anim["duration"] == "0.8s"
                assert caret_anim["iterationCount"] == "infinite"

                for _ in range(35):
                    page.keyboard.press("ArrowDown")

                # Verify scroller advanced and cursor is within scroller bounds
                result = page.evaluate("""() => {
                    const scroller = document.querySelector('.cm-scroller');
                    const cursor = document.querySelector('.cm-cursor');
                    const sRect = scroller.getBoundingClientRect();
                    const cRect = cursor ? cursor.getBoundingClientRect() : null;
                    return {
                        scrollTop: scroller.scrollTop,
                        cursorVisible: cRect ? (cRect.top >= sRect.top && cRect.bottom <= sRect.bottom) : false,
                    };
                }""")
                assert result["scrollTop"] > 0
                assert result["cursorVisible"] is True

                # Test non-editor file-page (image mode): #wiki-main must NOT have overflow-y: hidden
                page.evaluate("""() => {
                    const wikiMain = document.getElementById('wiki-main');
                    wikiMain.innerHTML = '';
                    const imgPage = document.createElement('file-page');
                    imgPage.path = 'sample.png';
                    imgPage.kind = 'image';
                    wikiMain.appendChild(imgPage);
                }""")
                page.wait_for_selector(".file-page-image")
                img_wiki_overflow = page.evaluate("""() => {
                    return getComputedStyle(document.getElementById('wiki-main')).overflowY;
                }""")
                assert img_wiki_overflow != "hidden"
            finally:
                browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=15)
        sock.close()
        assert not thread.is_alive(), "test server did not stop"
