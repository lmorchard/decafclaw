# issue-1189-sidebar-hide-button

## Resume here

- Issue: https://github.com/lmorchard/decafclaw/issues/1189 — sidebar hide button disappears when sidebar is too narrow
- Branch: issue-1189-sidebar-hide-button (branched from local HEAD e3e7e595; origin/main is 2 commits ahead — user's in-flight chat-controls work)
- Base: e3e7e595
- Head (code commit): 8c661447 "fix(web): keep sidebar collapse button visible when sidebar is narrow (#1189)"
- Checks: browser scenario suite green locally; lint/format green
- Next action: publish draft PR via submit-pr flow, run same-model review pass, mark ready, report.

## Log

- 2026-11-03: Triaged mechanism from issue triage comment: `.sidebar-tabs` (styles/sidebar.css) lacked `min-width: 0`, so its automatic flex minimum was the sum of the 5 tab labels' min-content; `.collapse-btn` (conversation-sidebar.js render header) lacked `flex-shrink: 0`; conversation-sidebar has `overflow: hidden`.
- 2026-11-03: Baseline: new browser scenario regression test `test_browser_sidebar_collapse_button_visible_when_narrow` (tests/test_web_browser_scenarios.py) failed pre-fix — collapse button right edge (302.4) was past the sidebar right edge (280) at default width and clipped. After fix, passed at default, 180px, and 160px (drag-resize floor).
- 2026-11-03: Fix: `.sidebar-tabs { min-width: 0 }` (let tab strip shrink) + `conversation-sidebar button.collapse-btn { flex-shrink: 0 }` (keep intrinsic width).
- 2026-11-03: Checks run: `uv run pytest tests/test_web_browser_scenarios.py -q` → 26 passed; new test → 1 passed; `make fmt`, `make lint`, `make fmt-check` → green; `npx vitest run components/conversation-sidebar.test.js` → 16 passed.

## Decisions

- Minimal fix only (tab strip shrink + button pin); broader narrow-width sidebar tab interface treatment deferred as an issue follow-on, per triage scope notes.

## Post-delivery feedback

- 2026-11-03 (post-delivery peek): user reported the collapse button still not easily visible — needs an opaque background and looked like it overlaps text. Addressed post-ready: gave `conversation-sidebar button.collapse-btn` an opaque surface (`background: var(--pico-background-color)` + button-scoped `color: var(--pico-color`) in src/decafclaw/web/static/styles/sidebar.css. Regression test still green; fmt/lint green.
