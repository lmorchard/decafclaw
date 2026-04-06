# HTTP Request Tool — Spec

## Goal

Add a general-purpose `http_request` tool so the agent can make HTTP requests with any method, headers, and body. Primarily for testing APIs the agent is building. Separate from the existing `web_fetch` (which stays as a simple GET tool).

Covers issue: #204.

## 1. New tool: `http_request`

Lives in a new module `src/decafclaw/tools/http_tools.py`.

### Parameters

- `url` (required) — the URL to request
- `method` (optional, default "GET") — HTTP method: GET, POST, PUT, PATCH, DELETE, HEAD, OPTIONS
- `headers` (optional) — dict of request headers
- `body` (optional) — request body as string. For JSON, pass a JSON string and set `Content-Type: application/json` in headers.
- `max_response_size` (optional, default 50000) — max response body chars to return (0 = unlimited)

### Returns

`ToolResult` with structured `data`:
```python
{
    "status_code": int,
    "headers": dict[str, str],  # response headers
    "body": str,                # response body (truncated if over max)
    "body_truncated": bool,
    "method": str,
    "url": str,
    "elapsed_ms": int,
}
```

Text summary includes status code, content-type, body preview.

### Confirmation model

All requests require confirmation unless the URL matches a pattern in the allowlist. URL-based allowlist patterns using glob matching (e.g., `http://localhost:*`, `https://api.example.com/*`).

Allowlist stored in `{agent_path}/http_allow_patterns.json` — separate from the shell allowlist. Same format: JSON array of glob patterns.

Confirmation flow reuses `request_confirmation` from `confirmation.py`.

### Implementation

- Uses `httpx.AsyncClient` (already a dependency, used by `web_fetch`)
- Timeout: 30 seconds (same as `web_fetch`)
- Follows redirects
- Response headers returned as a flat dict (last value wins for duplicates)

### Error handling

- Connection errors (refused, DNS, timeout): return `ToolResult(text="[error: ...]", data={"status_code": None, "error": str(e), "method": method, "url": url})`
- HTTP error responses (4xx, 5xx): NOT treated as errors — return the response normally with the status code. The agent decides what to do with it.

## 2. Files changed

- `src/decafclaw/tools/http_tools.py` — new module: tool function, allowlist helpers, HTTP_TOOLS, HTTP_TOOL_DEFINITIONS
- `src/decafclaw/tools/__init__.py` — register
- `CLAUDE.md` — add to key files
- Tests

## 3. Out of scope

- Modifying `web_fetch` (stays as-is)
- File upload / multipart form data
- Cookie jar / session persistence
- Streaming responses
