"""Isolated browser coverage for typed uploads and native workspace delivery."""

import base64
import json
import socket
import threading

import uvicorn
from playwright.sync_api import sync_playwright

from decafclaw.events import EventBus
from decafclaw.http_server import create_app
from decafclaw.web.auth import create_token
from decafclaw.web.conversations import ConversationIndex


def test_browser_upload_image_and_download_use_native_authenticated_routes(config):
    config.http.secret = "attachment-native-browser-secret"
    config.agent_path.mkdir(parents=True, exist_ok=True)
    token = create_token(config, "browser-user")
    conv_id = ConversationIndex(config).create("browser-user", "Attachment browser").conv_id

    image_rel = "native files/safe 日本語 #?.png"
    image_path = config.workspace_path / image_rel
    image_path.parent.mkdir(parents=True, exist_ok=True)
    image_bytes = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
    )
    image_path.write_bytes(image_bytes)
    download_rel = "native files/report.txt"
    download_path = config.workspace_path / download_rel
    download_path.write_bytes(b"native download bytes")
    unicode_download_rel = "native files/日本語.txt"
    unicode_download_bytes = "unicode download bytes 日本語".encode()
    (config.workspace_path / unicode_download_rel).write_bytes(unicode_download_bytes)

    app = create_app(config, EventBus())
    requests = []
    dispositions = {}

    @app.middleware("http")
    async def record_native_requests(request, call_next):
        if request.url.path.startswith(("/api/upload/", "/api/workspace/")):
            requests.append((
                request.method,
                request.scope["raw_path"].decode(),
                request.headers.get("content-type"),
                bool(request.cookies),
            ))
        response = await call_next(request)
        if request.url.path.startswith("/api/workspace/"):
            dispositions[request.scope["raw_path"].decode()] = (
                response.headers.get("content-disposition")
            )
        return response

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
                page.goto(base + "/static/lib/auth-client.js")
                page.evaluate("""async token => {
                    const { AuthClient } = await import('/static/lib/auth-client.js');
                    await new AuthClient().login(token);
                }""", token)
                page.add_script_tag(type="importmap", content=json.dumps({"imports": {
                    "lit": "/static/vendor/bundle/lit.js",
                    "marked": "/static/vendor/bundle/marked.js",
                    "dompurify": "/static/vendor/bundle/dompurify.js",
                    "codemirror": "/static/vendor/bundle/codemirror.js",
                    "diff": "/static/vendor/bundle/diff.js",
                }}))

                uploaded = page.evaluate("""async convId => {
                    const { uploadFile } = await import('/static/lib/upload-client.js');
                    return uploadFile(convId, new File(
                        ['browser upload bytes'], 'browser 日本語.txt', {type: 'text/plain'}));
                }""", conv_id)
                assert uploaded["mime_type"] == "text/plain"
                assert uploaded["filename"].startswith("browser 日本語-")
                assert (config.workspace_path / uploaded["path"]).read_bytes() == b"browser upload bytes"

                with page.expect_response(
                    lambda response: "/api/workspace/native%20files/safe%20" in response.url
                ) as image_response_info:
                    urls = page.evaluate("""async ({imagePath, downloadPath, unicodeDownloadPath}) => {
                        await Promise.all([
                            import('/static/components/file-page.js'),
                            import('/static/components/messages/user-message.js'),
                        ]);
                        const imagePage = document.createElement('file-page');
                        imagePage.id = 'native-image-page';
                        imagePage.kind = 'image'; imagePage.path = imagePath;
                        document.body.append(imagePage);
                        await imagePage.updateComplete;

                        const downloadPage = document.createElement('file-page');
                        downloadPage.id = 'native-download-page';
                        downloadPage.kind = 'binary'; downloadPage.path = downloadPath;
                        document.body.append(downloadPage);
                        await downloadPage.updateComplete;

                        const unicodeDownloadPage = document.createElement('file-page');
                        unicodeDownloadPage.id = 'native-unicode-download-page';
                        unicodeDownloadPage.kind = 'binary';
                        unicodeDownloadPage.path = unicodeDownloadPath;
                        document.body.append(unicodeDownloadPage);
                        await unicodeDownloadPage.updateComplete;

                        const message = document.createElement('user-message');
                        message.id = 'native-user-message';
                        message.attachments = [{
                            filename: imagePath.split('/').at(-1),
                            path: imagePath,
                            mime_type: 'image/png',
                        }];
                        document.body.append(message);
                        await message.updateComplete;
                        return {
                            image: imagePage.querySelector('img').getAttribute('src'),
                            messageImage: message.querySelector('img').getAttribute('src'),
                            download: downloadPage.querySelector('a').getAttribute('href'),
                            unicodeDownload:
                                unicodeDownloadPage.querySelector('a').getAttribute('href'),
                        };
                    }""", {
                        "imagePath": image_rel,
                        "downloadPath": download_rel,
                        "unicodeDownloadPath": unicode_download_rel,
                    })

                encoded_image = (
                    "/api/workspace/native%20files/safe%20"
                    "%E6%97%A5%E6%9C%AC%E8%AA%9E%20%23%3F.png"
                )
                assert urls == {
                    "image": encoded_image,
                    "messageImage": encoded_image,
                    "download": "/api/workspace/native%20files/report.txt",
                    "unicodeDownload": (
                        "/api/workspace/native%20files/%E6%97%A5%E6%9C%AC%E8%AA%9E.txt"
                    ),
                }
                page.wait_for_function(
                    "document.querySelector('#native-image-page img').naturalWidth === 1"
                )
                image_response = image_response_info.value
                assert image_response.status == 200
                assert image_response.headers["content-type"] == "image/png"
                assert image_response.headers["x-content-type-options"] == "nosniff"
                assert "content-disposition" not in image_response.headers
                assert "etag" in image_response.headers
                assert "last-modified" in image_response.headers

                with page.expect_download() as download_info:
                    page.locator("#native-download-page .file-download-link").click()
                download = download_info.value
                assert download.suggested_filename == "report.txt"
                assert download.path().read_bytes() == b"native download bytes"

                # #895: a non-Latin filename must survive the browser download.
                with page.expect_download() as unicode_download_info:
                    page.locator(
                        "#native-unicode-download-page .file-download-link"
                    ).click()
                unicode_download = unicode_download_info.value
                assert unicode_download.suggested_filename == "日本語.txt"
                assert unicode_download.path().read_bytes() == unicode_download_bytes
            finally:
                browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()

    upload_requests = [request for request in requests if request[1].startswith("/api/upload/")]
    assert len(upload_requests) == 1
    assert upload_requests[0][0] == "POST"
    assert upload_requests[0][2].startswith("multipart/form-data; boundary=")
    assert upload_requests[0][3] is True
    workspace_requests = [request for request in requests if request[1].startswith("/api/workspace/")]
    assert workspace_requests
    assert all(method == "GET" and authenticated for method, _, _, authenticated in workspace_requests)
    assert any(raw_path.endswith("/native%20files/report.txt") for _, raw_path, _, _ in workspace_requests)
    # The bare `download` attribute lets Chromium fall back to the URL's last
    # segment, so also check the header that the server sent to the browser.
    assert dispositions["/api/workspace/native%20files/report.txt"] == (
        'attachment; filename="report.txt"'
    )
    assert dispositions["/api/workspace/native%20files/%E6%97%A5%E6%9C%AC%E8%AA%9E.txt"] == (
        "attachment; filename*=utf-8''%E6%97%A5%E6%9C%AC%E8%AA%9E.txt"
    )
