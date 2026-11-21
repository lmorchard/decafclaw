# issue-1638-diff-view-render

## Resume here

- Issue: https://github.com/lmorchard/decafclaw/issues/1638
- Worktree: /Users/lmorchard/devel/mine/decafclaw/.claude/worktrees/issue-1638-diff-view-render
- Branch: issue-1638-diff-view-render
- Base: origin/main at b0dbdfd30cf42363d3c50872c03cc88201029dfc
- Next action: review/commit/publish.

## Log

### Research

- Screenshot shows two full widget cards nested: two filename headers, two
  Split View buttons, two Expand/Open in Canvas rows.
- Cause: `_renderUnified()` / `_renderSplit()` return a complete
  `.diff-view-widget` card when the computed diff exceeds
  `MAX_DIFF_RENDER_LINES` (500), and `render()` wraps that return in a second
  card. The fallback renders no `.diff-line` rows at all.
- `MAX_DIFF_RENDER_LINES` was added by #1168/#1203 as a backstop after the
  backend cap (50k chars / 1000 lines). The fallback text said "showing first
  500" but never rendered them.
- Every `workspace_edit` / `workspace_diff` / `admin_*` diff sends whole-file
  `before`/`after`, so a one-line edit in a 500+ line file produced a >500-row
  diff of mostly unchanged lines.
- Canvas calls the same renderer, so it showed the nested notice too.

### Decisions

- Build rows once in `_buildRows()`; both view renderers return content only,
  and `render()` owns the single widget chrome.
- Collapse unchanged runs to `CONTEXT_LINES = 3` on each side with a gap row
  ("N unchanged lines"), so inline shows the edit rather than the whole file.
- Inline renders the first 500 rows plus a notice; canvas renders every row.
- Expand appears only when the collapsed inline body actually overflows
  (measured in `updated()`), so short diffs have no dead control. A
  ResizeObserver on the body re-measures when chat is revealed after the
  widget mounted hidden. The first live check exposed this: observing the host
  element did not fire and the Expand button stayed missing; observing
  `.diff-view-body` fixed it.
- Drop per-line `highlightAuto` when no language is provided: one-language-per-
  line detection is meaningless and too slow for full-file canvas diffs. Plain
  text is HTML-escaped instead.
- Docs: added `diff_view` to the bundled widget list in `docs/widgets.md`.

### Evidence

- New failing tests first (`widgets/diff_view/widget.test.js`): 3 failed
  against the old widget (nested card count 2, 0 diff lines inline, 0 rows in
  canvas). Pass after the fix.
- `make test-js`: 71 files / 863 tests passed.
- `make test` baseline before changes: 6149 passed, 2 skipped.
- `make check`: passed (ruff, pyright 0 errors, gen-message-types clean,
  API client clean, tsc clean, static module graph tests).
- Live harness (real widget module + `styles/widgets.css` + import map,
  /tmp/diffharness, deleted after): 600-line file with an edit at line 500
  rendered one header and the changed line with gap rows; 40 changed lines
  showed Expand, which grew the body 310px→1430px and flipped to Collapse;
  Split View rendered two panes with one header; 600 changed rows showed
  "Showing first 500 of 1200 diff lines" plus rows; canvas mode rendered rows
  with no notice.
- Not exercised: a real conversation turn through `dc-widget-host` in the full
  web app; the harness mounts the same widget element and data shape.
