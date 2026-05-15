# TUI #511 — App + picker UX rough edges

**Issue:** [#511](https://github.com/lmorchard/decafclaw/issues/511)
**Branch:** `tui-511-app-picker-polish` (worktree at `.claude/worktrees/tui-511-app-picker-polish/`)
**Type:** Bug-fix bundle. S.

## Context

Seven small items from the spike retro (`docs/dev-sessions/2026-05-13-1039-tui-spike/notes.md` → "Retro candidates"). Items live in `tui/src/App.tsx`, `tui/src/conversationPicker.tsx`, and (for one item) `tui/src/wsClient.ts`. Two of the seven items turn out to be already addressed in the merged spike — noted below and out of scope.

## Item scoping (after code read)

| # | Item | Status | Scope |
|---|---|---|---|
| 1 | Replace `setInterval` send-on-mount hack | **Real** | Buffer outbound sends in WSClient; simplify App's poller. |
| 2 | Track `cancelArmed` timeout | **Real** | `useRef` for the timer; clear on re-arm and unmount. |
| 3 | Pretty-print confirm payload | **Partially done** | Current display uses specific fields (not `JSON.stringify`). Remaining gap: show `suggested_pattern` when present. |
| 4 | Add `[disconnected]` system line | **Real** | Mirror of `[reconnected]` in App's reducer. |
| 5 | Picker: Ctrl+C / Esc / `q` abort during fetch | **Real (partial)** | Ctrl+C already works via App's `useInput` (nested registration). Esc and `q` don't — add them to the picker, above the loading guard. |
| 6 | Picker: fix `newConvId()` (POST first) | **Already done** | Merged spike code uses POST. Will close out in commit message. |
| 7 | Picker: display or remove `updated_at` | **Real** | Display as relative time (e.g. "3h ago"). |

## Design choices

- **Item 1 — buffer over onOpen.** WSClient buffers outbound `ClientMessage`s while not OPEN; flushes on `ws.on("open")`. Caller surface is unchanged. App.tsx drops the 250ms polling loop and the immediate-send hack — its `useEffect([pickedConv, state.conv_id])` reduces to: "if pickedConv differs from state.conv_id, send select_conv once." The buffer absorbs the pre-open race; the existing `__reconnected` re-send covers reconnects.
- **Item 2 — `useRef<NodeJS.Timeout | null>`.** Mirrors the WSClient pattern from #510.
- **Item 4 — message format.** `[disconnected: <code> <reason>]` matching the `[error: ...]` style. Use empty string fallbacks when reason is missing.
- **Item 5 — exit semantics.** Picker uses `useApp().exit()`, same as App.tsx. Esc and `q` are aliases.
- **Item 7 — relative time format.** Inline helper: ISO timestamp → "Xs ago" / "Xm ago" / "Xh ago" / "Xd ago" / locale date if very old. No new dep; ~10 LOC.

## Non-goals

- Replacing `ink-text-input` with a multi-line composer — that's #498 (P1, M).
- Adding `disconnected: code reason` styling beyond a yellow system marker.
- Persistent picker preferences (last-used folder, sort order, etc.).
- Heavy buffer logic in WSClient (no overflow handling, no LRU). Buffer is unbounded — acceptable for a single-client TUI.

## Acceptance criteria

- [ ] App.tsx has no `setInterval` for WS-open polling.
- [ ] WSClient buffers outbound messages when not OPEN; vitest case confirms flush-on-open.
- [ ] `cancelArmed` timeout is stored in a ref and cleared when re-armed.
- [ ] Confirm display shows `suggested_pattern` when non-empty.
- [ ] App appends a `[disconnected: <code> <reason>]` system line on `__closed`.
- [ ] Picker handles Esc and `q` during loading state (exits cleanly).
- [ ] Picker displays a relative `updated_at` next to each conversation when present.
- [ ] `cd tui && npm test` clean (28 + new tests).
- [ ] `cd tui && npm run typecheck` clean.
- [ ] `make check` clean.

## Files affected

- `tui/src/wsClient.ts` — outbound buffer.
- `tui/src/wsClient.test.ts` — buffer-flush test.
- `tui/src/App.tsx` — drop setInterval polling, track cancelArmed timeout, [disconnected] line, suggested_pattern display.
- `tui/src/conversationPicker.tsx` — Esc/q abort, updated_at display.
