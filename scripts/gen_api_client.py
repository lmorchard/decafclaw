import json
import re
import shutil
import subprocess
from pathlib import Path

import yaml

from decafclaw.config import Config
from decafclaw.http_server import create_app


def add_discard_overload(service: Path, method_name: str, verb: str, url: str) -> None:
    """Derive both response call modes from the generated signature, never copy its types.

    Some callers read JSON; others only wait for HTTP success. The stock generator
    has no per-call option to skip decoding an unused response body.
    """
    source = service.read_text()
    pattern = (rf"    public static {re.escape(method_name)}\(\n"
               r"(?P<parameters>.*?)    \): CancelablePromise<(?P<response>[^>]+)> \{")

    def overload(match: re.Match) -> str:
        method = f"    public static {method_name}(\n"
        parameters = match["parameters"]
        response = match["response"]
        return (
            method + parameters + f"    ): CancelablePromise<{response}>;\n"
            + method + parameters + "        discardResponse: true,\n    ): CancelablePromise<void>;\n"
            + method + parameters + "        discardResponse = false,\n"
            + f"    ): CancelablePromise<{response} | void> {{"
        )

    source, count = re.subn(pattern, overload, source, flags=re.DOTALL)
    if count != 1:
        raise ValueError(f"Expected exactly one generated {method_name} method")
    marker = f"            method: '{verb}',\n            url: '{url}',"
    if source.count(marker) != 1:
        raise ValueError(f"Expected exactly one generated {verb} {url} request")
    service.write_text(source.replace(marker, "            discardResponse,\n" + marker))


def dump_openapi():
    app = create_app(Config(), None, None, None)
    openapi_schema = app.openapi()

    with open("openapi.json", "w") as f:
        json.dump(openapi_schema, f, indent=2)

    with open("openapi.yaml", "w") as f:
        yaml.dump(openapi_schema, f, sort_keys=False)

    static = Path("src/decafclaw/web/static")
    cmd = [
        str(static / "node_modules/.bin/openapi"),
        "--input", "openapi.json",
        "--output", "src/decafclaw/web/static/lib/api-client",
        "--client", "fetch"
    ]
    subprocess.run(cmd, check=True)
    for method, verb, url in (
        ("archiveConversationApiConversationsIdArchivePost", "POST", "/api/conversations/{id}/archive"),
        ("unarchiveConversationApiConversationsIdUnarchivePost", "POST", "/api/conversations/{id}/unarchive"),
        ("deleteConversationApiConversationsIdDelete", "DELETE", "/api/conversations/{id}"),
        ("renameConversationApiConversationsIdPatch", "PATCH", "/api/conversations/{id}"),
        ("createConvFolderApiConversationsFoldersPost", "POST", "/api/conversations/folders"),
        ("deleteConvFolderApiConversationsFoldersPathDelete", "DELETE", "/api/conversations/folders/{path}"),
        ("renameConvFolderApiConversationsFoldersPathPut", "PUT", "/api/conversations/folders/{path}"),
    ):
        add_discard_overload(static / "lib/api-client/services/DefaultService.ts", method, verb, url)
    # Preserve the stock transport outside migrated conversation operations.
    # The adapter uses generated helpers but retains those callers' encoding and
    # error behavior without mutating the shared OpenAPI configuration.
    core = static / "lib/api-client/core"
    (core / "request.ts").replace(core / "generated-request.ts")
    shutil.copyfile("scripts/sticky_api_request.ts", core / "request.ts")
    # Bundle only the generated runtime. The adjacent index.ts preserves
    # response types for checkJs callers importing index.js.
    subprocess.run([
        str(static / "node_modules/.bin/esbuild"),
        str(static / "lib/api-client/index.ts"),
        "--bundle", "--format=esm", "--platform=browser", "--target=es2022",
        "--outfile=" + str(static / "lib/api-client/index.js"),
    ], check=True)


if __name__ == "__main__":
    dump_openapi()
