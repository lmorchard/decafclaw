# Spec — #1037 Mobile context inspector: portal + mobile layout

## Problem

On mobile, tapping the context meter either fails to show the context inspector
or shows it clipped/mispositioned and hard to interact with. Root cause: the
mobile off-canvas sidebar is positioned with `transform: translateX(±100%)`
(`styles/mobile.css`) and has `overflow: hidden` (`styles/sidebar.css:8`).
`<context-inspector>` is rendered inline inside the sidebar
(`conversation-sidebar.js:734`), and its `.inspector` panel is
`position: fixed` — which the CSS transform re-parents into the sidebar's
90vw box instead of the viewport. Same bug class as the notifications panel
(#386), which was fixed with a `document.body` portal.

The context inspector is the **only** remaining `position: fixed` element
rendered inside the transformed sidebar (verified: toasts, hamburger,
backdrop, `#canvas-main`, and `copy-conversation-menu` are all top-level;
`notification-inbox` is already portaled).

## Approach

### 1. Portal the panel to `document.body` (`context-inspector.js`)

Mirror the established `notification-inbox.js` pattern (#386):

- `connectedCallback`: create a `_panelMount` div (class
  `context-inspector-mount`) and append it to `document.body`.
- `render()` returns `nothing` (the host becomes an empty anchor).
- `updated()`: when open, render the panel template into the mount via
  Lit's standalone `render(panel, this._panelMount)`; when closed,
  `render(nothing, …)` to clear it.
- `disconnectedCallback`: clear the mount's Lit tree, remove the div,
  null the reference.
- Doc-click dismissal: add the `this._panelMount?.contains(e.target)`
  check alongside the existing `this.contains` and
  `previousElementSibling` (trigger) checks. The trigger check is
  unaffected — `.context-usage` remains a sibling of the host, which
  stays in the sidebar template.
- `#onTabKeyDown` focus: `this.querySelector('#tab-…')` →
  `this._panelMount?.querySelector('#tab-…')`.
- `close` events still dispatch on the host element, so the sidebar's
  `@close` binding (`conversation-sidebar.js:740`) is unchanged.

### 2. Re-scope panel CSS to the mount class (`context-inspector.css`)

- All `context-inspector .foo` panel selectors (~75 rules) become
  `.context-inspector-mount .foo`, mirroring
  `notification-inbox.css:48` (`.notification-panel-mount .notification-panel`).
- Drop the host-element `display: none` / `[open]` rules (lines 3–9): the
  host renders nothing.

### 3. Mobile layout — keep near-full-screen (decision A)

The existing `@media (max-width: 639px)` block (lines 112–148) stays as-is:
near-full-screen panel (`left/right 0.5rem`, `top 0.5rem`, `bottom 4.5rem`
so the sticky chat input remains reachable), mobile tabs, single-column
panes. Add `env(safe-area-inset-*)` padding to the panel's mobile box
(no-op today since the viewport meta lacks `viewport-fit=cover`, but
future-proof and zero-risk).

No new visual surface; desktop and mobile keep one shared layout system.

### 4. Tests

- **`components/context-export.test.js` (vitest/jsdom):** re-point content
  assertions from the host element to `el._panelMount` (consistent with the
  file's existing private-state access). Add tests: mount is appended to
  `document.body` on connect and removed on disconnect; doc-click inside the
  mount does not close the inspector; closing renders nothing into the
  mount.
- **`tests/test_web_browser_scenarios.py` (Playwright):** add a mobile
  viewport scenario. Extend `browser_scenario` with an optional `viewport`
  param (one line: `browser.new_context(viewport=…)`; precedent at
  `tests/test_file_editor_browser.py:49`). Scenario: 390×844 context, open
  the sidebar, open the inspector, assert the panel's bounding rect sits at
  viewport coordinates (not inside the sidebar's 90vw box) and is visible.
  This is the only test that can catch a containing-block regression —
  jsdom computes no CSS layout.

## Out of scope

- `viewport-fit=cover` in the viewport meta (app-wide layout impact).
- Bottom-sheet treatment (evaluated, rejected in brainstorm — mockups on
  the Television screen).
- Any other component (all other fixed-position elements verified
  top-level).

## Docs

- `docs/web-ui.md:145` ("### Context inspector"): one sentence noting the
  panel portals to a body-level `context-inspector-mount` (same pattern as
  `notification-inbox`) so it escapes the transformed mobile sidebar.
- `docs/web-ui-mobile.md`: the "no fixed inside transformed ancestors"
  rule already covers this; optionally list the inspector as a second
  instance of the pattern.

## Acceptance

- Mobile (390px): tap context meter → inspector fills the viewport with
  0.5rem gutters, chat input visible below, tabs/close work, doc-click
  outside closes it.
- Desktop behavior unchanged (panel position, tabs, raw drawer, export).
- `make check`, `make test`, `make test-js` green.
