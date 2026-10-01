import json
import shutil
import subprocess
from pathlib import Path

import yaml

from decafclaw.config import Config
from decafclaw.http_server import create_app


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
    # Preserve the stock transport for all operations except the sticky lookup.
    # The adapter uses generated helpers but retains that caller's encoding and
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
