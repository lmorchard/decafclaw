# Notes

## Implementation notes

- **Task 1 — traversal guard divergence:** `canvas._canvas_sidecar_path` rejects conv_ids containing `/`, `\`, or `..` outright rather than stripping those characters as the plan and `context_composer._context_sidecar_path` do. Stripping has a subtle bug: `..conv` becomes `conv` and silently looks valid. Strict rejection is unambiguous. Worth aligning `context_composer` to the same shape in a future cleanup (separate PR).

## Smoke test results (T12)

Driven via Playwright MCP against the worktree dev server on port 18881 with `.env` copied from the main checkout (so `DATA_HOME` points at the same data dir). Conversation `web-lmorchard-e5a17d78` created via the new-chat button.

| # | Item | Result |
|---|------|--------|
| 1 | `canvas_set` (via agent through chat with claude-haiku-4.5) → panel appears, label "Hello Canvas", markdown rendered | ✅ PASS |
| 2 | Update widget data on existing instance → scroll position preserved (scrollTop=500 retained after `.data` swap), content swapped | ✅ PASS |
| 3 | Dismiss panel → hidden + pill appears. Simulated `canvas_update kind=update` → panel stays hidden, pill gets `data-unread="true"` dot. Click pill → resummons, dot cleared | ✅ PASS |
| 4 | Dismiss → panel hidden + pill. POST `/api/canvas/.../set` → panel auto-reveals, pill removed, label updated | ✅ PASS |
| 5 | Inline widget: initial `max-height: 8rem` collapsed (124px), 2 buttons "Expand" + "Open in Canvas". Expand → 524px + label flips to "Collapse". Open in Canvas → POST happens, canvas panel updates to widget's content | ✅ PASS |
| 6 | Drag canvas-resize-handle left by 100px → canvas grows from 540px → 642px. localStorage `canvas-width` = `"642"` (persists) | ✅ PASS |
| 7 | `/canvas/{conv_id}` standalone view: initial REST load renders content, title "Canvas — Standalone Test". WS subscribe + canvas_update event arrives end-to-end (verified manually after WS bug fixes — see below) | ✅ PASS |
| 8 | Viewport 600px → canvas-main `position: fixed; inset: 0; z-index: 100; width: 600px`, resize handle `display: none`, close button 44×44px | ✅ PASS |
| 9 | Conversation switch | ⏭ SKIPPED (would need a second conv with canvas state) |
| 10 | `applyEvent({kind: 'clear', tab: null})` → panel hides, pill cleared | ✅ PASS |

### Bugs found during smoke testing (fixed)

1. **Widget JS imports broke under `/widgets/bundled/...` URL.** `markdown_document/widget.js` used relative paths `../../lib/markdown.js` and `../../lib/canvas-state.js`. From `/widgets/bundled/markdown_document/widget.js` those resolve to `/widgets/lib/markdown.js` — 404. Fixed by switching to absolute `/static/lib/...` paths.
2. **Canvas-mode widget didn't fill canvas-body height.** The flex/height chain through `<dc-widget-host>` and `.widget-host` wrappers wasn't propagating `height: 100%`, so `.md-doc-scroll` took content height (3806px) instead of canvas-body height (886px) — no actual scrolling possible. Fixed by adding `display: flex; flex: 1; min-height: 0` chain in `canvas.css` for `.canvas-body > dc-widget-host` and descendants.
3. **Standalone view connected to wrong WS path.** `canvas-page.js:openWebSocket` used `/ws` but the actual endpoint is `/ws/chat`. Fixed.
4. **`_handle_select_conv` didn't subscribe to per-conv events.** Existing main UI works because subsequent `_handle_load_history` and `_handle_send` call `_subscribe_to_conv`. The standalone canvas page only sends `select_conv` and never gets a per-conv subscription, so `canvas_update` events were never forwarded. Fixed by adding `_subscribe_to_conv(state, conv_id)` to both branches of `_handle_select_conv`.

All four fixes are small and contained. Backend tests still pass.
