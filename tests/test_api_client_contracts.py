"""Static contract and typing guards for the generated browser API client.

Ensures that:
1. All FastAPI backend endpoints have corresponding typed methods in the generated client.
2. Web UI code in static/ uses the generated client and does not make untyped raw `fetch('/api/...')` calls.
3. Generated API models and service methods maintain strong typing without `any` escape hatches.
"""

import pathlib
import re

import pytest

from decafclaw.config import Config
from decafclaw.events import EventBus
from decafclaw.http_server import create_app

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
STATIC_DIR = REPO_ROOT / "src/decafclaw/web/static"
API_CLIENT_DIR = STATIC_DIR / "lib/api-client"
SERVICE_PATH = API_CLIENT_DIR / "services/DefaultService.ts"


def test_no_raw_api_fetch_in_frontend():
    """Ensure frontend JavaScript does not bypass the generated API client with raw fetch('/api/...')."""
    # Exclude api-client, vendor bundles, node_modules, and tests
    raw_fetch_pattern = re.compile(r"""fetch\s*\(\s*[`'"](/api/|\$\{.*\}?/api/)""")

    violations = []
    for js_file in STATIC_DIR.rglob("*.js"):
        rel_path = js_file.relative_to(STATIC_DIR)
        parts = rel_path.parts
        if "node_modules" in parts or "vendor" in parts or "api-client" in parts:
            continue
        # Also skip test files that mock fetch
        if js_file.name.endswith(".test.js"):
            continue

        text = js_file.read_text(encoding="utf-8")
        for line_no, line in enumerate(text.splitlines(), 1):
            if raw_fetch_pattern.search(line):
                violations.append(f"{rel_path}:{line_no}: {line.strip()}")

    assert not violations, "Found untyped raw fetch calls to /api/ (use DefaultService instead):\n" + "\n".join(
        violations
    )


def test_generated_client_covers_all_backend_api_routes():
    """Ensure every FastAPI /api/ route in the backend has a generated method in DefaultService.ts."""
    app = create_app(Config(), EventBus())
    service_text = SERVICE_PATH.read_text(encoding="utf-8")

    api_routes = []
    for route in app.routes:
        path = getattr(route, "path", "")
        if path.startswith("/api/") and not path.startswith(("/api/docs", "/api/openapi.json")):
            methods = getattr(route, "methods", None) or {"GET"}
            for method in methods:
                api_routes.append((method.lower(), path))

    assert api_routes, "No backend API routes found"

    # Verify each endpoint is represented in DefaultService.ts
    for method, path in api_routes:
        # Normalize {param:path} to {param}
        norm_path = re.sub(r"\{([a-zA-Z0-9_]+):path\}", r"{\1}", path)
        norm_path = norm_path.rstrip("/")
        # Verify that DefaultService.ts contains a method targeting this URL
        assert f"url: '{norm_path}'" in service_text or f"url: '{norm_path}/'" in service_text, (
            f"Backend endpoint {method.upper()} {norm_path} is missing from generated DefaultService.ts"
        )


def test_no_unconstrained_any_in_generated_client():
    """Ensure generated models and workspace mutation methods do not leak `any` return types."""
    service_text = SERVICE_PATH.read_text(encoding="utf-8")
    for method in ("wrapperApiWorkspacePathPut", "wrapperApiWorkspacePathDelete"):
        start = service_text.index(f"public static {method}(")
        end = service_text.index("    /**", start)
        method_text = service_text[start:end]
        assert "CancelablePromise<any>" not in method_text, f"{method} must not return CancelablePromise<any>"

    # Models that must be strictly typed without `any`
    strictly_typed_models = [
        "WorkspaceListingResponse",
        "WorkspaceRecentResponse",
        "WorkspaceTextResponse",
        "WorkspaceFolderEntry",
        "WorkspaceFileEntry",
        "AutocompleteResponse",
        "VaultCompletion",
        "McpCompletion",
        "FileCompletion",
        "AttachmentResponse",
        "VaultListingResponse",
        "VaultRecentResponse",
        "VaultTagsResponse",
        "VaultTagEntry",
        "VaultFolderEntry",
        "VaultPageListEntry",
        "VaultPageResponse",
        "ConfigFileEntry",
        "ConfigFileResponse",
        "ConfigWriteResponse",
        "ScheduleListResponse",
        "ScheduleDetailResponse",
        "ScheduleUpdateResponse",
        "ScheduleResponse",
        "WorkspaceDeleteResponse",
        "WorkspaceWriteResponse",
    ]

    for model in strictly_typed_models:
        model_file = API_CLIENT_DIR / f"models/{model}.ts"
        assert model_file.is_file(), f"Model file {model}.ts missing"
        model_text = model_file.read_text(encoding="utf-8")
        assert "any" not in model_text, f"Model {model}.ts must not use 'any' types:\n{model_text}"
