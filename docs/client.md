# Headless client (`decafclaw-client`)

`decafclaw-client` is a headless CLI that drives a conversation in a **running**
decafclaw instance over its `/ws/chat` WebSocket gateway — the same surface the
web UI and the [TUI](../tui/README.md) drive. It emits machine-readable JSON so a
coding agent (or a shell script) can smoke-test agent features — skills, tools,
confirmation gates, reflection — without loading the web UI in a browser.

It is a *client* of an already-running server; it does not start one. Many
clients can connect concurrently — the "one bot per Mattermost token" rule does
not apply to web-gateway clients.

## Install

The console script installs with the package:

```bash
uv sync
uv run decafclaw-client --help
# equivalently:
uv run python -m decafclaw.client --help
```

## Authentication

The client authenticates exactly like the browser: a web token sent as the
`decafclaw_session` cookie. Tokens are `dfc_<random>` strings, stored as keys in
`data/<agent_id>/web_tokens.json`. Create one with `decafclaw-token create
<username>` if you don't have one.

Pass the token with `--token` or the `DECAFCLAW_TOKEN` env var. The server host
comes from `--host` or `DECAFCLAW_HOST` (default `http://localhost:8088`). Note
the default web port in a typical dev/deploy config is **18880** (`config.http.port`),
so you will usually pass `--host http://localhost:18880` or set `DECAFCLAW_HOST`.

```bash
export DECAFCLAW_TOKEN=dfc_…
export DECAFCLAW_HOST=http://localhost:18880
```

## Actions

### `send` — run a turn and record it

```bash
decafclaw-client send --prompt "Reply with the single word PONG."
```

Flow: connect → `select_conv` (or `POST /api/conversations` to create a fresh
conversation when `--conv` is omitted) → optionally `set_model` → `send` the
prompt → record every event until the turn completes, a confirmation gate fires,
or the timeout elapses → print the summary → exit.

Run several prompts sequentially over one connection with repeated `--prompt`, or
a file of prompts (one per line, blank lines skipped) via `--script`:

```bash
decafclaw-client send --conv web-123 --prompt "first" --prompt "second"
decafclaw-client send --script prompts.txt
```

The run stops early if a prompt halts on a confirmation or times out — later
prompts are not sent.

### `respond` — answer a pending confirmation

The client **never auto-responds** to a confirmation gate. When `send` hits one,
it records the confirmation and stops, leaving the turn paused server-side
(confirmations are persistent conversation messages). To continue, respond
explicitly:

```bash
decafclaw-client respond --conv web-123 --confirmation-id <id> --approve
decafclaw-client respond --conv web-123 --confirmation-id <id> --deny
```

`--approve` is the default. `--confirmation-id` is required — copy it from the
`confirmations` array in the halted `send`'s output. After responding, the
resumed turn is recorded to completion and a summary is printed.

This always-halt model keeps smoke tests deterministic and observable: the agent
always sees the gate, and continuing requires an intentional response.

## Output

Default `--format summary` prints one JSON object per turn (a single object for
one prompt; a JSON array for multiple):

```json
{
  "conv_id": "web-123",
  "status": "complete",
  "assistant_text": "PONG",
  "tool_calls": [
    {"tool_call_id": "…", "name": "vault_read", "status": "done",
     "status_message": "", "result_text": "…"}
  ],
  "confirmations": [],
  "errors": [],
  "reflection": null,
  "model": "",
  "usage": {"input_tokens": 1234, "output_tokens": 56},
  "raw_event_count": 7
}
```

- `status` — `complete` | `halted_confirmation` | `error` | `timeout`.
- `tool_calls` — every tool call in order. `status` is the lifecycle state
  (`started` until `tool_end` makes it `done`); `status_message` holds the last
  mid-flight progress string, if any.
- `confirmations` — confirmation gates seen (populated when `status` is
  `halted_confirmation`).
- `reflection` — the post-turn reflection result, if one was emitted.
- `usage` — token usage from the final assistant message, when reported.

`--format jsonl` instead streams every raw WebSocket event verbatim as
newline-delimited JSON (a deep-debugging escape hatch); no summary object is
printed.

## Exit codes

A shell script can branch on the exit code without parsing JSON:

| Code | Meaning |
|---|---|
| `0` | turn completed |
| `1` | other error (includes a turn that ended without completing, e.g. a dropped socket) |
| `2` | halted on a confirmation |
| `3` | timeout |
| `4` | auth / connection failure (could not reach the server) |

For a multi-prompt `send`, the exit code reflects the **first** turn whose status
is not `complete`.

## Flags

| Flag | Env var | Default | Actions |
|---|---|---|---|
| `--token` | `DECAFCLAW_TOKEN` | (required) | both |
| `--host` | `DECAFCLAW_HOST` | `http://localhost:8088` | both |
| `--timeout` | — | `180` (seconds) | both |
| `--format` | — | `summary` (`summary` \| `jsonl`) | both |
| `--conv` | — | new conversation if omitted | `send` (required for `respond`) |
| `--model` | — | server default | `send` |
| `--prompt` | — | — (repeatable) | `send` |
| `--script` | — | — (file, one prompt per line) | `send` |
| `--confirmation-id` | — | (required) | `respond` |
| `--approve` / `--deny` | — | `--approve` | `respond` |

## Example: confirmation halt → approve

```bash
# 1. A prompt that trips a confirmation gate halts and reports it (exit 2):
decafclaw-client send --prompt "Run the shell command: echo hello"
# → {"status": "halted_confirmation", "conv_id": "web-…",
#    "confirmations": [{"confirmation_id": "abc", ...}], ...}

# 2. Approve it to resume the turn (exit 0):
decafclaw-client respond --conv web-… --confirmation-id abc --approve
# → {"status": "complete", "tool_calls": [{"name": "shell_exec", ...}], ...}
```

## Scope

v1 is deliberately minimal. Canvas, widget, and notification events are recorded
only as part of `raw_event_count` (no rich handling). There is no interactive
REPL mode, and no auto-approve confirmation policy (always-halt is the only mode).
The transport does not reconnect — a smoke run is short-lived, and a dropped
socket is reported as a failure rather than silently retried.

## Implementation

`src/decafclaw/client/`:

- `recorder.py` — a pure reducer (`TurnRecorder`) that turns the WebSocket event
  stream into a `TurnSummary`. Lossless, unlike the TUI's display-oriented
  dispatcher.
- `cli.py` — argument parsing (`SmokeArgs`).
- `transport.py` — the thin network layer: `websockets` for `/ws/chat`, `httpx`
  for `POST /api/conversations`.
- `run.py` — orchestration: the turn loop, output, and exit-code mapping.

The wire contract is defined in
[`src/decafclaw/web/message_types.json`](websocket-messages.md).
