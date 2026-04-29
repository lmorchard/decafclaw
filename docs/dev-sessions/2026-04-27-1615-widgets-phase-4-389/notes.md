# Phase 4 dev-session notes

Smoke test against the worktree dev server at `http://localhost:18882`. Port
18881 was occupied by the main repo's `make dev`, so we bound to 18882
instead — same fix as in earlier sessions.

## Smoke results

Test conv: `web-lmorchard-389839e9`. All checks driven through Playwright
MCP except the keyboard nav block, which used real key events.

| # | Check | Result |
|---|---|---|
| 1 | `canvas_new_tab` (`markdown_document`, "Plan") → strip shows tab, active | PASS — `tab_id=canvas_1`, panel mounted |
| 2 | `canvas_new_tab` (`code_block`, Python sample) → tab + hljs colors | PASS — `.hljs-keyword` present, atom-one tokens visible |
| 3 | Click first tab → switches active, REST `/active_tab` 200 | PASS — `aria-selected` flipped, server confirmed |
| 4 | `[×]` on active tab → switches to neighbor; close last → panel hides | PASS — also confirmed `next_tab_id` advanced (no reuse) |
| 5 | Inline `code_block` → "Open in Canvas" creates new tab | DEFERRED — no live inline widget in conv during smoke; wiring verified by code path (POST `/new_tab` with widget body) and item 1 above proves the endpoint |
| 6 | hljs in chat fenced code | DEFERRED — no chat history rendered in this synthetic session; hljs availability confirmed in canvas (item 2) and `assistant-message.js` calls `hljs.highlightElement` in `updated()` |
| 7 | `/canvas/{conv}/{tab_id}` standalone — locked to that tab | PASS — flipped active to other tab from main UI; locked URL stayed on `canvas_2` ("hello.py") |
| 8 | Mobile vertical-list disclosure ≤639px | PASS — at 600px width: desktop strip `display:none`, "☰ Tabs (N) ▼" disclosure shown; click expands list with both tabs (active flag + close button); tap a row switches active and auto-collapses list |
| 9 | Keyboard nav (Arrow/Home/End/Delete) | PASS — ArrowRight/Left cycle and activate; Home jumps to first; End to last; Delete closes the focused tab and shifts active |
| 10 | Bare `/canvas/{conv}` follows active | PASS (Phase 3 already; canvas-page.js path-parse keeps prior bare URL behaviour) |
| 11 | ARIA tree | PASS — `role="region"` w/ `aria-label="Canvas"`, `tablist`/`tab`/`tabpanel`/`separator`, `aria-selected="true"` on the active tab, `aria-controls="canvas-tabpanel"` |

## Open question — WS event delivery on standalone-page navigation

While smoke testing the close flow I observed: server returned 200 on
`POST /api/canvas/.../close_tab`, but the local canvas-state snapshot did
not update — i.e. the WS `close_tab` event never reached `applyEvent`.

The session that hit this had bounced through a standalone canvas page
and back to `/#/conv/...` via Playwright `goto`, then I seeded local
state with a manual `setActiveConv(conv_id)` call rather than going
through the real chat-view → `select_conv` flow. The chat-view `select_conv`
WS message is what subscribes the client to that conversation's events;
my bypass skipped it.

This is plausibly a **test-harness artifact** rather than a real
regression. To rule it out, do a clean real-browser pass:

1. Hard-reload `/#/conv/<id>`, log in.
2. From the agent or REST, create two tabs.
3. Close the active tab via the in-UI [×]. Local strip should immediately
   drop the closed tab and switch active.
4. Repeat after navigating away to `/canvas/<id>/<tab>` and back.

If reproducible in a real browser, the fix is on the standalone-page →
chat-view return path: ensure `select_conv` is re-sent on
`store.setActiveConv` even when the conv id is unchanged from a stale
session.

## Server cleanup

- Background dev server was started on `:18882` because `:18881` was
  taken by the main repo's `make dev` (PID 55415).
- Worktree has its own `.env` copied from the parent for the smoke run —
  remove before pushing.

## Decisions to remember post-merge

- `canvas_new_tab` returns `{ok, tab_id}`; agents must thread that ID
  back into `canvas_update`/`canvas_close_tab`. No "current tab" implicit.
- `next_tab_id` counter is monotonic — never reuses an ID after close.
- Standalone tab-locked URL never follows active changes (by design);
  bare URL does.
