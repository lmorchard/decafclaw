# decafclaw-tui

Minimal Ink (React-for-terminal) TUI that connects to a running decafclaw bot over
its existing WebSocket gateway — the same surface the web UI drives. No competing
bot process; the TUI is a thin viewer/driver.

Design rationale and Phase 2 candidates: [`docs/dev-sessions/2026-05-13-1039-tui-spike/spec.md`](../docs/dev-sessions/2026-05-13-1039-tui-spike/spec.md).

## Running

```bash
cd tui
npm install
DECAFCLAW_TOKEN=<token> npm run dev
# or
npm run dev -- --token <token> --conv <conv_id>
```

Requires the bot running locally (`make dev` in the repo root, default port 8088).

## Configuration

| Flag | Env var | Default |
|---|---|---|
| `--token <t>` | `DECAFCLAW_TOKEN` | (required) |
| `--host <url>` | `DECAFCLAW_HOST` | `http://localhost:8088` |
| `--conv <id>` | — | (picker shown if absent) |

Tokens look like `dfc_<random>`. Find them as keys in
`data/<agent_id>/web_tokens.json` in the main clone. `<agent_id>` is the
subdirectory name under `data/` for the agent you want to connect to.

## Scripts

```
npm run dev       run the TUI
npm test          dispatcher unit tests (vitest)
npm run typecheck type-check without emitting
```

## Key bindings

- **Enter** — send message / confirm picker selection
- **y / n / a** — approve / deny / always when a confirmation prompt is shown
- **Ctrl+C** — cancel in-flight turn (first press while agent is busy); exit cleanly
  (when idle, or second press within 2 s of cancel)
- **Up / Down** — move cursor in conversation picker

## Known limitations

This is a spike validating the "thin network client over the existing WebSocket
gateway" architecture. The following are explicitly deferred to Phase 2:

- **No markdown rendering** — assistant text is plain. Code blocks, tables, and
  emphasis are not rendered. Likely the first Phase 2 add.
- **Single-line composer** — no multi-line input, no `$EDITOR` integration, no
  input history.
- **No tab completion** — slash commands, `@[[Page]]` mentions, and file paths.
- **Activity lane tracks one tool at a time** — concurrent tools show only the
  last one started.
- **No widget support** — `widget_input` flows from skills are silently ignored.
- **No canvas panel** — `canvas_update` events are acknowledged but not rendered.
- **Confirmation payload display is raw JSON** — pretty-printing is deferred.
- **Mac/Linux only** — not tested on Windows.

For the full Phase 2 candidates table (markdown rendering, multi-line composer,
history, tab completion, theme, model picker, widgets, canvas, files, vault, etc.)
see the spec linked above.
