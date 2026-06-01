# Headless WebSocket smoke-test client — spec

Tracking issue: [#567](https://github.com/lmorchard/decafclaw/issues/567)

## Goal

A headless Python CLI that drives a conversation in a running decafclaw instance
over the existing `/ws/chat` WebSocket gateway — the same surface the web UI and
`tui/` drive — so a coding agent (Claude Code or similar) can smoke-test agent
features (skills, tools, confirmations, reflection) without loading the web UI in
Playwright or spinning up a second bot process.

It is a *client* of an already-running server (`make dev`), not a server
launcher. Many clients can connect concurrently — the "one bot per Mattermost
token" rule does not apply to web-gateway clients.

## Wire contract (verified against the server)

- **Auth:** WS upgrade with header `Cookie: decafclaw_session=<dfc_ token>`.
  `get_current_user` (`web/auth.py`) reads that cookie. Tokens are keys in
  `data/<agent_id>/web_tokens.json`.
- **Create conversation:** `POST /api/conversations` with the same cookie,
  body `{"title": "..."}` → `201` with body containing `conv_id`
  (`http_server.py:create_conversation`).
- **Handshake:** open WS → server sends `models_available` → client sends
  `{"type":"select_conv","conv_id":...}` → server replies `conv_selected`
  (may carry `read_only` and `pending_confirmation`) and subscribes the socket
  to that conversation's event stream.
- **Send a turn:** `{"type":"send","conv_id":...,"text":...}`. Server emits
  `turn_start`, `chunk`*, `tool_start`/`tool_status`/`tool_end`,
  `message_complete` (carries final `text`, optional `usage`), and finally
  `turn_complete`. A confirmation gate emits `confirm_request` mid-turn.
- **Set model:** `{"type":"set_model","conv_id":...,"model":...}` →
  `model_changed`.
- **Respond to a confirmation:** `{"type":"confirm_response","conv_id":...,
  "confirmation_id":...,"approved":bool,"always":false,"add_pattern":false}`.
  The paused turn then resumes and eventually emits `turn_complete`.

Full manifest: `src/decafclaw/web/message_types.json`.

## Behavior

### Two actions
- **`send`** — connect → `select_conv` (or `POST /api/conversations` for a new
  one) → optional `set_model` → `send` prompt(s) → record events until
  `turn_complete`, `confirm_request`, or timeout → print summary → exit.
  Supports repeated `--prompt` and `--script <file>` (one prompt per line),
  run sequentially over one connection.
- **`respond`** — given `--conv` and `--confirmation-id` (required; copied from
  the halted `send`'s `confirmations` output), send `confirm_response`
  (approve/deny) and resume recording the turn to completion. (Auto-resolving the
  pending confirmation from `conv_selected` was descoped from v1 — see notes.md.)

### Confirmation handling — always halt and report
Never auto-respond. On `confirm_request`, record it and stop. The turn stays
paused server-side (confirmations are persistent conversation messages). The
agent inspects the recorded confirmation, then re-invokes `respond` to continue.

### Output
Default `--format summary`: one JSON object per turn. Single prompt → a single
object; multiple prompts → a JSON array of objects. Shape:

```json
{
  "conv_id": "web-…",
  "status": "complete | halted_confirmation | error | timeout",
  "assistant_text": "…",
  "tool_calls": [{"tool_call_id": "…", "name": "…", "status": "done", "result_text": "…"}],
  "confirmations": [{"confirmation_id": "…", "action_type": "…", "tool": "…", "command": "…", "message": "…"}],
  "errors": ["…"],
  "reflection": {"passed": true, "critique": "…"} ,
  "model": "…",
  "usage": {},
  "raw_event_count": 12
}
```

`--format jsonl`: stream every raw WS event verbatim as newline-delimited JSON
(deep-debugging escape hatch); no summary object.

### Exit codes
- `0` — turn completed
- `2` — halted on confirmation
- `3` — timeout
- `4` — auth / connection failure
- `1` — other error

For multi-prompt runs the exit code reflects the **first** turn whose status is
not `complete` (else `0`).

### Config / flags
| Flag | Env var | Default |
|---|---|---|
| `--token` | `DECAFCLAW_TOKEN` | (required) |
| `--host` | `DECAFCLAW_HOST` | `http://localhost:8088` |
| `--conv` | — | omit to create a new conversation (`send` only) |
| `--model` | — | server default (`send` only) |
| `--timeout` | — | `180` seconds |
| `--prompt` / `--script` | — | — (`send` only) |
| `--confirmation-id` | — | — (`respond`; required) |
| `--approve` / `--deny` | — | `--approve` (`respond` only) |
| `--format` | — | `summary` (`summary` \| `jsonl`) |

## Out of scope for v1 (YAGNI)
- Canvas / widget / notification events: record-and-ignore (counted in
  `raw_event_count`), no rich handling.
- No interactive REPL mode.
- No auto-approve confirmation policy (always-halt is the only mode in v1).
- No reconnect/backoff — a smoke run is short-lived; a dropped socket is a
  failure (`disconnect` → exit `1`).
