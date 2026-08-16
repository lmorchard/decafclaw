import json
import os
import subprocess

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

    # Run npx openapi-typescript-codegen
    # Assume it is installed in node_modules
    cmd = [
        "npx", "openapi-typescript-codegen",
        "--input", "openapi.json",
        "--output", "src/decafclaw/web/static/lib/api-client",
        "--client", "fetch"
    ]
    # openapi-typescript-codegen emits a directory tree of .ts files, not a
    # single module. Nothing compiles them to .js, so browser-served code
    # cannot import the result yet — see #843.
    subprocess.run(cmd, check=True)

if __name__ == "__main__":
    dump_openapi()
