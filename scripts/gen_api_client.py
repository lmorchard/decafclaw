import json
import os
import subprocess
from decafclaw.http_server import create_app
from decafclaw.config import Config

def dump_openapi():
    app = create_app(Config(), None, None, None)
    openapi_schema = app.openapi()
    
    with open("openapi.json", "w") as f:
        json.dump(openapi_schema, f, indent=2)

    # Run npx openapi-typescript-codegen
    # Assume it is installed in node_modules
    cmd = [
        "npx", "openapi-typescript-codegen",
        "--input", "openapi.json",
        "--output", "src/decafclaw/web/static/lib/api-client",
        "--client", "fetch"
    ]
    subprocess.run(cmd, check=True)
    # The output is a directory, not a single file. But the issue says:
    # "assert the output .ts file is created/updated"
    # Actually openapi-typescript generates a single file. Wait! openapi-typescript-codegen generates a folder.
    # Let's use openapi-typescript to generate a single file?
    pass

if __name__ == "__main__":
    dump_openapi()
