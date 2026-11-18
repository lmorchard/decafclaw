# Sidebar actions (#1515, #1524)

## Resume here

- Issues: https://github.com/lmorchard/decafclaw/issues/1515 (open session in a
  new browser tab) and https://github.com/lmorchard/decafclaw/issues/1524
  (move + Chat / + Page / + Folder to the bottom of the sidebar).
- Worktree: `.claude/worktrees/issue-1515-1524-sidebar-actions`, branch
  `issue-1515-1524-sidebar-actions`, base `origin/main` at `2373b466`.
- Baseline: `make test` 5796 passed, 2 skipped; targeted sidebar vitest
  64 passed.
- Implementation and verification done: `make check` passed, full
  `vitest run` 774 passed, live browser check passed. Next action: commit and
  publish the PR.

## Findings

- Conversation deep link is the `?conv=<id>` query param restored by
  `app.js` on startup (`getConvFromUrl`), so a new tab can use
  `${location.pathname}?conv=<id>`.
- `#renderConversationItem` is shared by session root rows, subagent rows,
  archived/system rows, so one click handler covers every conversation row.
- Create actions lived inside each list: Chats in `conversation-sidebar`
  `.conv-list`, Vault in `vault-sidebar`'s browse render. `vault-sidebar` and
  `files-sidebar` are `display: contents`, so a sibling after their
  `.conv-list` becomes a bottom flex child of `conversation-sidebar`.

## Decisions

- Keep rows as `<div>` (the #555 keyboard contract and nested action buttons
  make an `<a>` row invalid/awkward). Implement new-tab manually on
  `click` (Cmd/Ctrl/Shift) and `auxclick` (middle). The optional custom
  right-click menu from #1515 is not implemented; its "open in new tab"
  behavior is covered by the modifier and middle click paths.
- Bottom actions render after the scrollable list and before the mode/model
  pickers. Vault keeps the actions browse-only.
- CSS inset uses the adjacent-sibling selector
  `conversation-sidebar .conv-list + .vault-action-btns`, so Files/Schedules
  action rows that remain inside their lists are unaffected.

## Checks

- 2026-11-18: `make test` (baseline) -> 5796 passed, 2 skipped.
- 2026-11-18: `npx vitest run components/{conversation-sidebar,vault-sidebar,sidebar-row-a11y,vault-writers}.test.js`
  -> 72 passed.
- 2026-11-18: `make check-js` -> tsc clean.
- 2026-11-18: `make test-js` -> 774 passed (63 files).

## Live browser check (2026-11-18, worktree dev server :18991)

- `make run-dev` on the worktree, headless browser.
- Chats: + Chat / + Folder render at the bottom of the sidebar above the
  footer; Vault: + Page / + Folder render at the bottom (action row y=817,
  vault list y=47..817).
- Ctrl-click (`ctrlKey`) on a conversation row opened a new page at
  `/?conv=<other-id>` and left the current tab's URL unchanged. Middle click
  (`auxclick`, button 1) did the same. Plain click selected in place.
- The modifier events were dispatched with `page.evaluate` MouseEvents; the
  Puppeteer keyboard-modifier path did not register ctrlKey on a real click,
  so native modifier propagation is covered by unit events, not that live
  click.
- Collapsed sidebar renders none of the action rows.

## Final gate (2026-11-18)

- `make check` -> all targets passed (npm policy, message types, fmt-check,
  ruff, pyright, gen-api-client, tsc, static module graph).
- `make test-js` -> 774 passed.
- Added an auxclick guard so middle-clicking a row action button does not
  open a new tab.
