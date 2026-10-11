# Research — #1037 mobile context inspector

All claims verified first-hand in the worktree at `origin/main` (e4b1b78).

## Current mount structure

- `<context-inspector>` is rendered inline in the `conversation-sidebar` light-DOM
  template, immediately after the `.context-usage` meter
  (`src/decafclaw/web/static/components/conversation-sidebar.js:714-742`).
  Lit bindings: `.convId`, `.open`, `.contextVersion`, `@close`.
- The component uses `createRenderRoot() { return this; }` (light DOM) and renders
  a single `.inspector` div with `position: fixed; z-index: 1000`
  (`src/decafclaw/web/static/styles/context-inspector.css:11-29`).
- The host element is `display: none` unless it has the reflected `open` attribute
  (`context-inspector.css:3-9`).

## The trap (verified)

- Mobile sidebar: `position: fixed; width: 90%; max-width: 90vw; z-index: 200;
  transform: translateX(-100%)`, and `translateX(0)` under `[mobile-open]`
  (`src/decafclaw/web/static/styles/mobile.css:80-96`).
- `conversation-sidebar` also has `overflow: hidden` on all viewports
  (`src/decafclaw/web/static/styles/sidebar.css:8`).
- Per CSS spec, the `transform` makes the sidebar the containing block for the
  inspector's `position: fixed`, so the panel is positioned and clipped relative
  to the 90vw sidebar box, not the viewport.

## The established fix pattern (#386, notifications)

- `notification-inbox.js` is the canonical implementation:
  - `_panelMount` div (class `notification-panel-mount`) created in
    `connectedCallback`, appended to `document.body` (`notification-inbox.js:50-54`).
  - `updated()` calls Lit's standalone `render(this.#renderPanel(), this._panelMount)`
    so the panel stays reactive outside the component's render root
    (`notification-inbox.js:71-79`).
  - `disconnectedCallback` clears Lit's tree (`render(nothing, mount)`) then removes
    the mount (`notification-inbox.js:57-69`).
  - Doc-click dismissal checks **both** `this.contains(e.target)` and
    `this._panelMount?.contains(e.target)` before closing
    (`notification-inbox.js:139-146`).
- CSS scoping: the bell stays scoped under `notification-inbox .notification-bell`,
  but all panel styles are scoped under the mount class —
  `.notification-panel-mount .notification-panel`
  (`src/decafclaw/web/static/styles/notification-inbox.css:48`).
- The pattern is documented as the house rule in
  `docs/web-ui-mobile.md` ("No `position: fixed` inside transformed ancestors",
  lines 42-54), including a quick-checklist item ("does it portal to
  `document.body`?").

## Inspector specifics that interact with a portal

- Doc-click dismissal (`context-inspector.js:88-103`): registered in `updated()`
  on open, capture-phase on `document`. Exempts `this.contains(e.target)` and the
  trigger via `this.previousElementSibling` (the `.context-usage` div — a sibling
  of the host element, so this check survives a portal unchanged). Needs a
  `_panelMount?.contains(e.target)` check added.
- `#onTabKeyDown` focus (`context-inspector.js:175-179`): uses
  `this.querySelector('#tab-…')` — must be re-pointed at the mount after portaling.
- The `.inspector` div has `@click=${(e) => e.stopPropagation()}`
  (`context-inspector.js:413`) — redundant with the contains-checks but harmless.
- `dc-widget-json-view` widgets render inside the raw pane; they are light-DOM
  custom elements and are unaffected by the portal.

## CSS re-scope requirement

- Every panel selector is prefixed `context-inspector .foo`
  (~75 rules in `context-inspector.css`). After portaling, `.inspector` and
  descendants are no longer inside the host element, so the prefix must become
  the mount class (e.g. `.context-inspector-mount .foo`), mirroring
  `notification-inbox.css:48`. The host-element rules (lines 3-9) can be dropped
  or simplified since the host becomes an empty anchor.

## Class-of-bug analogues (other fixed-position elements)

Grepped all `position: fixed` in `src/decafclaw/web/static/styles/`:
- `toast.css:3` — toast container is `#toast-container`, a top-level child of
  `body` in `index.html:61`. Not trapped.
- `mobile.css:52,81,101` — hamburger button, sidebar itself, `#sidebar-backdrop`:
  all direct children of `#chat-layout` (top level). Not trapped.
- `notification-inbox.css:49` — already portaled to body. Fixed.
- `canvas.css:118` — `#canvas-main` is a top-level child of `#chat-layout`
  (`index.html:56`). Not trapped.
- `context-inspector.css:12` — **the only fixed element rendered inside the
  transformed sidebar.** `context-inspector` is the sole remaining instance of
  this bug class.
- `copy-conversation-menu` is dynamically appended to the chat-actions cluster in
  `#chat-main` (`app.js:847-854`), not the sidebar — unaffected.

## Test surfaces

- `src/decafclaw/web/static/components/context-export.test.js` — vitest/jsdom unit
  tests for the inspector (10 inspector tests + copy-conversation tests). All
  content assertions query through the host element (`el.querySelector`,
  `el.textContent`); they must be re-pointed at the mount (e.g. `el._panelMount`).
  Existing tests already poke private state (`el._tab`, `el._rawLoading`), so
  reading `el._panelMount` is consistent with the file's style.
- `tests/test_web_browser_scenarios.py` — Playwright harness serving the real app
  in headless Chromium (`browser_scenario` context manager, lines 108-194).
  Currently `browser.new_context()` with the default 1280×720 viewport (line 157).
  Precedent for a custom viewport: `tests/test_file_editor_browser.py:49`
  (`browser.new_page(viewport={"width": 1000, "height": 600})`). Adding an
  optional viewport param to `browser_scenario` is a one-line change; a mobile
  scenario (390×844) can assert the panel's bounding rect sits at viewport
  coordinates outside the sidebar's 90vw box — the one test that can actually
  catch a containing-block regression, since jsdom computes no CSS layout.

## Mobile layout that already exists

- `context-inspector.css:112-148` already has a `@media (max-width: 639px)` block:
  near-full-screen panel (`left: 0.5rem; width: calc(100vw - 1rem);
  top: 0.5rem; bottom: 4.5rem`), hidden raw-drawer toggle, visible mobile tabs,
  single-column panes. The `bottom: 4.5rem` leaves the sticky chat input
  (`mobile.css:9-14`) visible below the panel.
- Tap-target floor: `mobile.css:133-136` enforces `min-height: 44px` on all
  buttons; inspector tab buttons and the close button inherit it. The
  `.context-usage` trigger is `role="button"` so it inherits the floor too.
