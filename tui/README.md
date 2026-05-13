# decafclaw-tui

Minimal Ink TUI that connects to a running decafclaw bot over its WebSocket gateway.
Spec: `docs/dev-sessions/2026-05-13-1039-tui-spike/spec.md`.

## Running

```bash
cd tui
npm install
DECAFCLAW_TOKEN=<token> npm run dev
# or
npm run dev -- --token <token> --conv <conv_id>
```

Requires the bot to be running locally (e.g. `make dev` in the repo root).

## Configuration

| Flag | Env var | Default |
|---|---|---|
| `--token <t>` | `DECAFCLAW_TOKEN` | (required) |
| `--host <url>` | `DECAFCLAW_HOST` | `http://localhost:8088` |
| `--conv <id>` | — | (picker shown if absent) |

Get a token from `data/{agent_id}/web_tokens.json` in the main clone.

## Scripts

- `npm run dev` — run the TUI
- `npm test` — run dispatcher unit tests
- `npm run typecheck` — type-check without emitting

## Status

Spike. See `docs/dev-sessions/2026-05-13-1039-tui-spike/spec.md` for scope and deferred items.
