# TUI #510 — WSClient lifecycle hygiene

**Issue:** [#510](https://github.com/lmorchard/decafclaw/issues/510)
**Branch:** `tui-510-wsclient-hygiene` (worktree at `.claude/worktrees/tui-510-wsclient-hygiene/`)
**Type:** Bug-fix bundle. S.

## Context

Two items flagged during the TUI spike code review and retro (`docs/dev-sessions/2026-05-13-1039-tui-spike/notes.md`, "Retro candidates" → WSClient bullets) that didn't block the spike but should land before further TUI investment now that we're promoting toward daily-driver. Both live in `tui/src/wsClient.ts` only.

## Goals

1. **Track the reconnect timer ID and cancel it on `close()`.** The reconnect timer in `scheduleReconnect` (wsClient.ts:126) is fire-and-forget — its ID is discarded. The callback has a `if (!this.wantClosed)` guard so it no-ops when `close()` was called before it fires, but the timer still wakes up. More important: if anything (a future bug, a manual retry path) could call `scheduleReconnect` twice with no intervening fire, we'd have multiple pending timers all calling `connect()`. Fix: store the timer ID on the instance and `clearTimeout` it in both `close()` and at the top of `scheduleReconnect`.

2. **Add URL + attempt-count context to the WS error log.** Reconnect-loop debugging is painful without seeing which URL is failing and on what attempt. The current `console.error("[tui] ws error:", err.message)` (wsClient.ts:116) loses both. Fix: enhance with URL and `reconnectAttempt`.

## Non-goals

- **Jitter in backoff.** Explicitly deferred in the issue body. Pure exponential is fine for a single-client terminal tool; jitter only matters at scale.
- **Enhance the malformed-message log** (wsClient.ts:87). Not reconnect-related; out of scope.
- **New scheduling-side log line.** Adding `console.log("[tui] scheduling reconnect attempt=N delay=Xs")` would help reconnect-loop diagnostics, but the issue specifies "add URL + attempt-count context to the `console.error` lines" — enhance, not add. Keep scope narrow.
- **Restructure WSClient for full mockability.** The test for timer-cancel needs a minimal `vi.mock("ws", ...)` setup; we won't extract a WebSocketFactory injection point.

## Acceptance criteria

- [ ] `WSClient.close()` called after `scheduleReconnect()` (timer pending) cancels the pending reconnect — verified by a vitest test using fake timers + a mocked `ws` module.
- [ ] Calling `scheduleReconnect()` twice in succession cancels the first timer (defensive code; doesn't require a test).
- [ ] `ws.on("error")` log line includes URL and `reconnectAttempt`.
- [ ] `cd tui && npm test` clean (25 + new tests).
- [ ] `cd tui && npm run typecheck` clean.
- [ ] `make check` clean.

## Files affected

- `tui/src/wsClient.ts` — implementation.
- `tui/src/wsClient.test.ts` — **new** — at least one test for timer-cancel.
