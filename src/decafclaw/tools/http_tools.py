"""HTTP request tool — general-purpose HTTP client for API testing."""

import fnmatch
import json
import logging
import time
from pathlib import Path

import httpx

from ..media import ToolResult
from .confirmation import request_confirmation

log = logging.getLogger(__name__)

_VALID_METHODS = {"GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE"}
_DEFAULT_MAX_RESPONSE_SIZE = 50000
_REQUEST_TIMEOUT = 30


# -- Allowlist ----------------------------------------------------------------

def _allow_patterns_path(config) -> Path:
    """Path to the HTTP allow patterns file (admin-managed)."""
    return config.agent_path / "http_allow_patterns.json"


def _load_allow_patterns(config) -> list[str]:
    """Load HTTP allow patterns from disk. Returns [] if missing or corrupt."""
    path = _allow_patterns_path(config)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
        if isinstance(data, list):
            return [p for p in data if isinstance(p, str)]
        if isinstance(data, dict):
            patterns = data.get("patterns", [])
            return [p for p in patterns if isinstance(p, str)]
        return []
    except (json.JSONDecodeError, OSError) as e:
        log.warning(f"Could not read HTTP allow patterns: {e}")
        return []


def _save_allow_pattern(config, pattern: str) -> None:
    """Add a pattern to the HTTP allow list."""
    path = _allow_patterns_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    patterns = _load_allow_patterns(config)
    if pattern not in patterns:
        patterns.append(pattern)
        path.write_text(json.dumps(patterns, indent=2) + "\n")
        log.info(f"Added HTTP allow pattern: {pattern}")


def _url_matches_pattern(url: str, patterns: list[str]) -> bool:
    """Check if a URL matches any allow pattern (glob-style)."""
    for pattern in patterns:
        if fnmatch.fnmatch(url, pattern):
            return True
    return False


def _suggest_pattern(url: str) -> str:
    """Generate a suggested allow pattern from a URL.

    Heuristic: keep scheme + host + port, wildcard the path.
    """
    try:
        parsed = httpx.URL(url)
        base = f"{parsed.scheme}://{parsed.host}"
        if parsed.port and parsed.port not in (80, 443):
            base += f":{parsed.port}"
        return f"{base}/*"
    except Exception:
        return url


# -- Tool function ------------------------------------------------------------

async def tool_http_request(ctx, url: str, method: str = "GET",
                            headers: dict | None = None,
                            body: str = "",
                            max_response_size: int = _DEFAULT_MAX_RESPONSE_SIZE,
                            ) -> ToolResult:
    """Make an HTTP request and return the response."""
    method = method.upper()
    if method not in _VALID_METHODS:
        return ToolResult(
            text=f"[error: invalid method '{method}'. Use: {', '.join(sorted(_VALID_METHODS))}]",
            data={"status_code": None, "error": f"invalid method: {method}",
                  "method": method, "url": url},
        )

    log.info(f"[tool:http_request] {method} {url}")

    # Confirmation — check allowlist, else request approval
    patterns = _load_allow_patterns(ctx.config)
    if not _url_matches_pattern(url, patterns):
        suggested_pattern = _suggest_pattern(url)
        result = await request_confirmation(
            ctx, tool_name="http_request",
            command=f"{method} {url}",
            message=f"HTTP request: `{method} {url}`",
            suggested_pattern=suggested_pattern,
        )
        if not result.get("approved"):
            return ToolResult(
                text="[error: HTTP request was denied by user]",
                data={"status_code": None, "error": "denied",
                      "method": method, "url": url},
            )
        if result.get("add_pattern"):
            _save_allow_pattern(ctx.config, suggested_pattern)

    # Make the request
    start = time.monotonic()
    try:
        async with httpx.AsyncClient(
            follow_redirects=True, timeout=_REQUEST_TIMEOUT,
        ) as client:
            resp = await client.request(
                method, url,
                headers=headers,
                content=body if body else None,
            )
    except httpx.HTTPError as e:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        return ToolResult(
            text=f"[error: HTTP request failed: {e}]",
            data={"status_code": None, "error": str(e),
                  "method": method, "url": url, "elapsed_ms": elapsed_ms},
        )

    elapsed_ms = int((time.monotonic() - start) * 1000)

    # Build response
    resp_body = resp.text
    truncated = False
    if max_response_size > 0 and len(resp_body) > max_response_size:
        resp_body = resp_body[:max_response_size]
        truncated = True

    resp_headers = dict(resp.headers)

    data = {
        "status_code": resp.status_code,
        "headers": resp_headers,
        "body": resp_body,
        "body_truncated": truncated,
        "method": method,
        "url": str(resp.url),  # may differ from request URL after redirects
        "elapsed_ms": elapsed_ms,
    }

    # Text summary
    content_type = resp.headers.get("content-type", "unknown")
    parts = [f"**{method}** {url} → **{resp.status_code}**"]
    parts.append(f"- Content-Type: {content_type}")
    parts.append(f"- Elapsed: {elapsed_ms}ms")
    if truncated:
        parts.append(f"- Body truncated at {max_response_size} chars")
    if resp_body:
        preview = resp_body[:500]
        if len(resp_body) > 500:
            preview += "..."
        parts.append(f"\n```\n{preview}\n```")

    return ToolResult(text="\n".join(parts), data=data)


# -- Registration -------------------------------------------------------------

HTTP_TOOLS = {
    "http_request": tool_http_request,
}

HTTP_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "http_request",
            "description": (
                "Make an HTTP request. Supports all methods (GET, POST, PUT, PATCH, "
                "DELETE, HEAD, OPTIONS). Use for testing APIs, verifying endpoints, "
                "or making webhook calls. REQUIRES USER CONFIRMATION unless the URL "
                "matches an admin-configured allow pattern."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The URL to request",
                    },
                    "method": {
                        "type": "string",
                        "enum": ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
                        "description": "HTTP method (default: GET)",
                    },
                    "headers": {
                        "type": "object",
                        "description": "Request headers as key-value pairs",
                    },
                    "body": {
                        "type": "string",
                        "description": (
                            "Request body as a string. For JSON, pass a JSON string "
                            "and set Content-Type header to application/json."
                        ),
                    },
                    "max_response_size": {
                        "type": "integer",
                        "description": "Max response body chars to return (default 50000, 0 = unlimited)",
                    },
                },
                "required": ["url"],
            },
        },
    },
]
