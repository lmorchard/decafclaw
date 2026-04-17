# HTTP Server & Interactive Buttons

DecafClaw includes an HTTP server that powers the web UI gateway and Mattermost interactive message buttons for tool confirmations.

## Setup

Add to your `.env`:

```bash
HTTP_ENABLED=true
HTTP_HOST=0.0.0.0       # bind address (default: 0.0.0.0)
HTTP_PORT=18880          # listen port (default: 18880)
HTTP_SECRET=your-random-secret-here   # required — shared secret for callbacks
HTTP_BASE_URL=http://decafclaw.example.com:18880  # optional — auto-detected if empty
```

The HTTP server starts alongside the Mattermost websocket listener when `HTTP_ENABLED=true`.

**Important:** Your Mattermost server must be able to reach the HTTP server's URL. If they're on different machines, ensure the port is accessible (firewall, Tailscale, etc.).

## How it works

### Interactive buttons

When a tool needs user confirmation (shell commands, skill activation, etc.), DecafClaw posts a message with interactive buttons:

- **Shell tool:** Approve / Deny / Allow Pattern
- **Other tools:** Approve / Deny / Always

Clicking a button sends an HTTP POST to DecafClaw's callback endpoint. The handler publishes a confirmation event on the internal event bus, and the message updates to show the result (buttons are removed).

### Emoji fallback

Emoji reactions still work alongside buttons. Both paths can resolve the same confirmation — whichever arrives first wins. Emoji instructions are hidden automatically when `HTTP_ENABLED=true`. Set `MATTERMOST_ENABLE_EMOJI_CONFIRMS=true` to force them on alongside buttons.

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `HTTP_ENABLED` | `false` | Enable the HTTP server |
| `HTTP_HOST` | `0.0.0.0` | Bind address |
| `HTTP_PORT` | `18880` | Listen port |
| `HTTP_SECRET` | `""` | Shared secret for callback verification (required) |
| `HTTP_BASE_URL` | `""` | External URL for callbacks (auto-detected from host/port if empty) |
| `MATTERMOST_ENABLE_EMOJI_CONFIRMS` | auto | Show emoji reaction instructions (default: true when HTTP off, false when HTTP on) |

## Routes

### `GET /health`

Returns `{"status": "ok"}`. Useful for monitoring and load balancer health checks.

### `POST /actions/confirm?secret=<secret>`

Receives Mattermost interactive message button callbacks. Verifies the shared secret, publishes a `tool_confirm_response` event, and returns a response that updates the original message (removes buttons, shows the result).

## Security

The shared secret (`HTTP_SECRET`) is included in callback URLs as a query parameter. Mattermost sends it back with each button click, and the handler rejects requests with an invalid secret. Generate a random string for the secret:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

## Mattermost configuration

Mattermost must be configured to allow outbound requests to the DecafClaw HTTP server:

1. **System Console → Environment → Developer → Allow untrusted internal connections to** — add the DecafClaw hostname or IP (e.g., `192.168.0.149` or `decafclaw.example.com`). Without this, Mattermost silently drops button callback requests to LAN addresses.

2. **System Console → Integrations → Integration Management → Enable interactive messages** — must be enabled (usually on by default).

## Mattermost gotchas

- **Button action IDs must not contain underscores.** Mattermost silently drops HTTP callbacks for interactive message buttons when the action `id` field contains underscores. No error, no log — the click just does nothing. Use camelCase or flat lowercase for button IDs.
- **"Allow untrusted internal connections"** must include the DecafClaw host (see above).

## Deployment notes

- The HTTP server runs as an asyncio task in the same process as the bot — no separate service needed.
- For production, consider putting it behind a reverse proxy (nginx, Caddy) for HTTPS termination.
- If using the systemd service from `deploy/decafclaw.service`, add the HTTP config vars to your `.env` file. No service changes needed.
- Ensure the Mattermost server can reach the HTTP port. If using Tailscale, the Tailscale hostname works well as `HTTP_BASE_URL`.
- If Mattermost and DecafClaw are on the same LAN, use the LAN IP for `HTTP_BASE_URL` (e.g., `http://192.168.0.149:18880`).
