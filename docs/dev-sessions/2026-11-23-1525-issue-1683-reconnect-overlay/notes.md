# issue-1683-reconnect-overlay

## Resume here

- Issue: https://github.com/lmorchard/decafclaw/issues/1683
- Worktree: /Users/lmorchard/devel/mine/decafclaw/.claude/worktrees/issue-1683-reconnect-overlay
- Branch: issue-1683-reconnect-overlay
- Base: origin/main at 4513e7b2f65b9c46cc1e09dac942bef43c534da1
- Status: implemented, gates run, committed; ready for PR preparation.

## Decisions

- Replaced the red `#connection-banner` (and the empty `#mobile-header` wrapper)
  with a full-screen overlay. The overlay is a sibling of `#chat-layout`, so it
  stays interactive/announced while `#chat-layout` is `inert` (blocks pointer,
  keyboard, and screen-reader access to the dead UI).
- Narration comes from a new `WebSocketClient` `reconnecting` event carrying
  `{attempt, delayMs}` (1-based). The attempt line is `aria-hidden` so the
  `role="alert"` announces the loss once, not on every retry.
- Logout fires `close` after the layout is hidden; the close handler ignores it
  and `showLogin()` hides the overlay explicitly.
- Browser scenario harness ran uvicorn with `ws="none"`. A dead socket is now a
  blocking state, so two hit-test/mouse scenarios failed under the overlay.
  The harness now serves a real WebSocket; all 34 scenarios pass.

## Verification

- `make test` baseline: 4 `tests/test_host_metrics.py` failures (macOS host
  probe returns None) + one xdist flake in `test_background_tools.py`. Same 4
  host failures after the change; flake did not recur.
- `make check`, `make test-js`, `make check-tui test-tui`: pass.
- New unit tests: `lib/connection-overlay.test.js` (show/hide/inert/narration),
  `lib/websocket-client.test.js` (retry delay, 30s cap, attempt reset,
  disconnect).
- Live proof on a worktree dev server (`make run-dev`, port 18991): killed the
  server -> overlay appeared, `#chat-layout.inert === true`, attempt line
  updated ("Attempt 2 · next try in 2s"); restarted -> overlay hidden, inert
  removed, Agent Config panel opened with a real click. Mobile viewport
  (390x844): overlay covers the full viewport.
