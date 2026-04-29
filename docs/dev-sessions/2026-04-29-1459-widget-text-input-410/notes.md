# Notes: Widget — text_input

## Session start — 2026-04-29

- Issue: [#410](https://github.com/lmorchard/decafclaw/issues/410) — Widget: text_input — free-form text / multi-field form
- Branch: `widget-text-input-410`
- Worktree: `/Users/lorchard/devel/decafclaw/.claude/worktrees/widget-text-input-410`
- Baseline: `make test` → 2243 passed (green)

## Execute summary

- **Phase 1** (rename): mechanical, all green.
- **Phase 2** (widget files): `make check-js` initially flagged untyped `seeded` object; fixed with `@type` JSDoc annotation.
- **Phase 3** (tool): ruff flagged un-sorted imports in the new test file; auto-fixed via `ruff check --fix`.
- **Phase 4** (docs + eval): all 3 new `ask_user_*` disambiguation cases pass. Pre-existing `workspace-write-vs-canvas-save-blog-post` eval failure is unrelated to this PR (no canvas/workspace_write code touched).
- Final test count: 2267 (was 2243).
- Manual verification done via Playwright MCP against a local server on port 18881 (Mattermost dev token coexists with prod, per memory). Single-field, multi-field (`name` + multiline `bio`), renamed `ask_user_multiple_choice`, and page-refresh recovery all worked end-to-end.

## Pre-existing bug found and fixed in this PR

Live-tab `submitted` state didn't flip after submission. In `src/decafclaw/web/static/lib/tool-status-store.js`, `respondToWidget` removed the confirm from `#pendingConfirms` *before* the backend broadcast `CONFIRMATION_RESPONSE`. The `CONFIRMATION_RESPONSE` handler then looked the confirm up by id (`#pendingConfirms.find(...)`) and missed on the submitting tab, so `markToolWidgetSubmitted` never fired locally. Other tabs (which still had the confirm in their pending list) flipped correctly via the broadcast. Refresh-from-archive path always worked.

Bug landed with #366 (the original `multiple_choice` widget). Affected `multiple_choice` and `text_input` equally.

**Fix:** call `markToolWidgetSubmitted(toolCallId, data)` directly in `respondToWidget` right after sending the WS — we already have the data, no reason to wait for the broadcast on the submitting tab. Other tabs continue to use the broadcast path. Verified end-to-end via Playwright: both widgets now flip immediately on submit.
