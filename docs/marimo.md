# Marimo pairing

The bundled `marimo` skill drives a running [marimo](https://marimo.io) notebook
through its HTTP API. It does not need the marimo Python package: the server
does the Python work.

## Why native tools

The upstream `marimo-pair` skill shells out to `discover-servers.sh` and
`execute-code.sh` with heredocs and then parses unstructured errors. The native
skill replaces that with:

- server discovery from marimo's registry;
- stable session resolution by file, path, or session ID;
- structured results for cell mutations and inspection;
- one-call canvas embedding.

## Requirements

- A marimo server with a notebook open in a browser. The browser tab owns the
  session; closing it removes the session.
- Servers started with `--no-token` register themselves in
  `~/.local/state/marimo/servers/*.json` (Windows:
  `%USERPROFILE%/.marimo/servers`). Servers with a token do not register;
  configure `default_url` and `token` instead.
- No extra Python dependency. The skill uses the project's `httpx` client.

## Security boundary

`marimo_eval` and the cell tools run arbitrary Python in the paired kernel;
they are equivalent to a Python shell in that environment. The notebook server
is user-started, and the browser session is the user's own. A token-protected
server reached through `default_url` should keep its token in an environment
variable or secret reference, not a literal config value. The canvas helper
still passes through the `web_frame` origin allowlist.

## Configuration

`skills.marimo` in `data/{agent_id}/config.json`, with environment overrides:

| Field | Env | Default | Purpose |
|---|---|---|---|
| `token` | `MARIMO_TOKEN` | empty | Bearer token for a protected server. Prefer an env var or secret reference. |
| `default_url` | `MARIMO_URL` | empty | Server base URL when registry discovery cannot find it (remote host, token-protected server). |
| `servers_dir` | `MARIMO_SERVERS_DIR` | auto | Override the registry directory. |
| `canvas_url` | `MARIMO_CANVAS_URL` | empty | Browser-facing base URL for `marimo_open_in_canvas` when the discovered server URL is not reachable from the user's browser. |
| `request_timeout_sec` | `MARIMO_REQUEST_TIMEOUT_SEC` | `15` | Health/session request timeout. |
| `execute_timeout_sec` | `MARIMO_EXECUTE_TIMEOUT_SEC` | `120` | Per-execute SSE read timeout. Tool definitions allow up to 240 s. |

Registry entries are probed at `/health`, so a stale registry file is skipped
rather than reported as a notebook.

## Tools

| Tool | Read/write | Behavior |
|---|---|---|
| `marimo_list_sessions` | read | Lists servers and browser sessions; resolves a bare filename; reports live cell count and aggregate status. `include_details=false` skips the per-session scratchpad round-trip. |
| `marimo_inspect` | read | Returns cell IDs, names, status, errors, code or previews, dataflow `defs`/`refs`, and public kernel variables. |
| `marimo_add_cell` | write | Appends or inserts a cell through `marimo._code_mode`; optionally runs it. |
| `marimo_edit_cell` | write | Reads the cell, then replaces its code and optionally runs it. |
| `marimo_delete_cell` | write | Deletes a cell. Destructive: it removes the globals that cell owns. |
| `marimo_eval` | write | Runs arbitrary Python in the kernel scratchpad and returns stdout, stderr, and the rendered result. |
| `marimo_open_in_canvas` | write | Opens the notebook as a `web_frame` canvas tab. |

Every tool takes an optional `file` selector: session ID, absolute or relative
path, `file://` URL, or unique filename. With one open notebook it can be
omitted. Multiple matches return an error with the candidate list in
`ToolResult.data` rather than guessing.

## Execution model

`/api/kernel/execute` is marimo's scratchpad endpoint and streams Server-Sent
Events:

- `stdout` / `stderr` carry console output, including output from cells that
  `_code_mode` runs;
- `done` carries the success bit and the rendered last-expression output.

The scratchpad has a shallow copy of the kernel globals. Notebook variables are
readable, but new top-level bindings are discarded after the call. Use the cell
tools to persist work. `marimo._code_mode` is a private, unstable agent API;
the skill keeps generated snippets small so a signature change is a localized
fix.

## Notebook changes

`marimo_add_cell` and `marimo_edit_cell` generate a small `_code_mode` block,
call `create_cell` / `edit_cell`, optionally `run_cell`, then read the cell back
for status and errors. `marimo_edit_cell` reads the current code first because
marimo rejects an edit whose cell was not read at its current version.

marimo validates the notebook DAG: no cycles, each public name has one owning
cell, and wildcard imports are rejected. Validation errors come back as the
tool's error text.

The live kernel is the source of truth. Do not edit the notebook `.py` file
while the session is active; the kernel can overwrite it.

## Canvas embedding

`marimo_open_in_canvas` builds `{base}/?file=<notebook path>` and creates a
`web_frame` canvas tab. `base` is `canvas_url` when configured, otherwise the
discovered server URL. The `web_frame` allowlist accepts loopback, private LAN
and Tailnet addresses, and configured origins (see [widgets](widgets.md#web_frame-widget)).
Set `canvas_url` when the user's browser reaches marimo through a different
hostname, such as `http://decafclaw:2718`.

## Tests

- Unit tests: `uv run pytest tests/test_marimo_skill.py -n 0`.
- Live check: start `marimo edit --no-token --headless --port 2718 smoke_test.py`,
  open the notebook in a browser, then exercise the tools. The unit tests use a
  fake `marimo._code_mode` module and an `httpx.MockTransport`; they do not
  prove behavior against a real marimo version.
