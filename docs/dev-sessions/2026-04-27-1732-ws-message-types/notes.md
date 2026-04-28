# Notes — Centralize WebSocket message types

PR [#406](https://github.com/lmorchard/decafclaw/pull/406) merged 2026-04-28 (`804486a`). Closes [#384](https://github.com/lmorchard/decafclaw/issues/384).

## Summary

Replaced ~50 hand-written WS message-type literals across Python and JS with a manifest-driven, drift-checked, code-generated single source of truth. 30 wire types now live in `src/decafclaw/web/message_types.json`; `scripts/gen_message_types.py` produces `WSMessageType` enum (Python), `MESSAGE_TYPES` constants (JS), and `docs/websocket-messages.md`. CI catches manifest-vs-generated drift via `make check-message-types` (wired into `make check`). Server and browser both log/warn on unknown inbound types as a migration safety net.

## What worked

- **Manifest-driven codegen with JSON as source.** Hybrid choice (StrEnum + Object.freeze, both generated from one JSON) gave us pyright/eslint typo-checking on both sides plus a single hand-edit point for new types.
- **Commit slicing within a single PR.** Five logical commits (manifest → generator → server flip → client flip → docs) made review tractable without splitting into multiple PRs.
- **Audit grep cross-checked against the manifest before flipping anything.** The Phase 1 sanity check caught that `tool_confirm_response` and `cancel_turn` in `http_server.py` are EventBus events, not WS wire types — kept them out of scope as the spec intended.
- **Script-driven mass replace.** A small Python regex pass for the server-side flip and a parallel one for the JS files made the mechanical part of the work safe and reviewable as a uniform diff.
- **Browser smoke test via Playwright MCP.** Exercised the full set of touched dispatch sites in a real Firefox session — `select_conv`, `conv_history`, `send`/`chunk`/`message_complete`, `set_model`/`model_changed`, `tool_start`/`tool_end` (with widget table), `reflection_result`, plus background `notification_*` events. No `unknown message type` warnings on either side.

## Friction

- **`load_sub_config` ignores empty-string env vars** — `MATTERMOST_URL=""` doesn't override the file value, only `os.getenv(...) is not None and != ""`. Means there's no env-only way to disable Mattermost; would need a config file override or a code change. Smoke test ended up running with the real Mattermost token, which was fine because no other instance was using it. Possible follow-up: support sentinel-empty env override, or an explicit `MATTERMOST_DISABLED=true` flag.
- **Out-of-band branch switch mid-execution.** Working tree got reset to `main` and `origin/main` advanced (PR #405 landed) while implementation was in progress. Recovery was a clean rebase since #405 only touched `http_server.py` (which I correctly excluded from this PR's scope), but it's a reminder that even a single-PR effort should `git fetch && git log main..origin/main` periodically during longer sessions.
- **Playwright MCP browser flakiness.** Firefox 1511 was segfaulting (`pthread_mutex_lock failed: Invalid argument`); a `npx playwright install firefox` reinstall fixed it. The MCP server also held a stale browser reference across sessions, requiring a Claude Code restart to pick up the configured `--browser firefox` after it had been on chrome. Worth noting in case it recurs.

## Future direction (deferred)

- **Stricter manifest field schema** — the generated docs page already calls this out. Today, `fields` is a flat dict from name to a human-readable type sketch. A future iteration could grow into typed entries (`{type: "string", optional: true, enum: [...]}`, `{type: "array", items: {...}}`) and emit a runtime validator. Out of scope here.
- **Centralize internal EventBus event types** — same drift class on the in-process pub/sub side (`tool_confirm_response`, `cancel_turn`, `confirmation_request`, etc.). Different lifecycle and consumers, so a separate effort. Worth doing only if drift bites us; today the EventBus has fewer producers than the WS surface and the consumers are co-located.
- **Direction-validated outbound** — the manifest carries `direction` and we already export per-direction frozensets, but we don't enforce at send time. A small extension would assert each `ws_send`/`ws.send` references a type tagged for the correct direction. Belt-and-suspenders; the enum + lint already catches typos at write time.
