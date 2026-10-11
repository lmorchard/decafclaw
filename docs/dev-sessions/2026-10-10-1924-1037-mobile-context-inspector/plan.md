# #1037 Mobile Context Inspector — Implementation Plan

**Goal:** Portal the context inspector panel to `document.body` so the mobile
off-canvas sidebar's CSS transform can no longer trap/clip the `position: fixed`
panel, keeping the approved near-full-screen mobile layout (decision A).

**Approach:** Mirror the established `notification-inbox.js` portal pattern
(#386): the host stays in the sidebar template as an empty anchor; the panel
renders into a body-level `context-inspector-mount` div. Re-scope the panel CSS
from the `context-inspector` tag to the mount class. Keep the existing mobile
layout, adding `env(safe-area-inset-*)` to the panel box. Tests: re-pointed
vitest tests + a new 390×844 Playwright scenario that reproduces the
containing-block trap (the only test that can catch it — jsdom computes no CSS
layout).

**Tech stack:** Lit (light-DOM components, standalone `render`), plain CSS
(`style.css` `@import` chain), vitest/jsdom, Playwright (headless Chromium).

Worktree: `.claude/worktrees/issue-1037-mobile-context-inspector` (branch
`issue-1037-mobile-context-inspector` from `origin/main`). Baselines green:
`make test-js` 499/499, `make test` 4492 passed / 2 skipped.

---

## Phase 1: Portal + regression tests (test-first)

Reproduce the mobile clipping with a failing Playwright scenario and failing
unit tests, then fix: panel portaled to `document.body`, CSS re-scoped to the
mount class, existing scenario updated. End state: inspector works identically
on desktop and correctly on mobile, all suites green.

**Files:**
- Test: `src/decafclaw/web/static/components/context-export.test.js` — re-point
  DOM assertions to `el._panelMount`, add mount-lifecycle tests
- Test: `tests/test_web_browser_scenarios.py` — `browser_scenario` gains an
  optional `viewport` param; new `test_browser_context_inspector_mobile`;
  existing `test_browser_context_inspector` (line ~537) queries the mount
- Modify: `src/decafclaw/web/static/components/context-inspector.js` — portal
- Modify: `src/decafclaw/web/static/styles/context-inspector.css` — re-scope
  all panel selectors to `.context-inspector-mount`

**Step 1 (red) — new Playwright mobile scenario.** Add `viewport=None` param to
`browser_scenario` (line ~109) and pass it through at line ~157:
`browser.new_context(viewport=viewport)` (Playwright treats `None` as the
default 1280×720; precedent `tests/test_file_editor_browser.py:49`). Then add,
next to `test_browser_context_inspector` (line 511):

```python
def test_browser_context_inspector_mobile(built_static, chromium, config):
    """#1037: the panel must escape the transformed sidebar's containing block."""
    conv_id = seed_conversation(config, "Mobile context")
    write_context_sidecar(config, conv_id, {
        "total_tokens_estimated": 123, "context_window_size": 1000,
        "sources": [{"source": "memory", "tokens_estimated": 123, "items_included": 1}],
    })
    with browser_scenario(built_static, chromium, config, viewport={"width": 390, "height": 844}) as scenario:
        page = scenario.page
        page.evaluate(
            """async (convId) => {
            for (const href of ['/static/vendor/bundle/pico.min.css', '/static/style.css']) {
                const link = document.createElement('link');
                link.rel = 'stylesheet'; link.href = href;
                document.head.append(link);
            }
            await import('/static/components/context-inspector.js');
            // Un-upgraded element: mobile.css/sidebar.css target the tag name,
            // which is exactly the production trap (transform + overflow:hidden).
            const sidebar = document.createElement('conversation-sidebar');
            sidebar.setAttribute('mobile-open', '');
            const el = document.createElement('context-inspector');
            el.convId = convId; el.open = true;
            sidebar.append(el);
            document.body.append(sidebar);
        }""",
            conv_id,
        )
        page.wait_for_function("document.querySelector('.context-inspector-mount .inspector') !== null")
        # Portaled: the mount is a direct child of body, not inside the sidebar.
        assert page.evaluate("document.querySelector('.context-inspector-mount').parentElement === document.body")
        # Panel box sits at viewport coordinates: 0.5rem gutters top/left/right,
        # 4.5rem bottom (measured in rem so root font-size can't skew it).
        geom = page.evaluate("""() => {
            const rem = parseFloat(getComputedStyle(document.documentElement).fontSize);
            const r = document.querySelector('.context-inspector-mount .inspector').getBoundingClientRect();
            return {left: r.left / rem, top: r.top / rem,
                    right: (innerWidth - r.right) / rem, bottom: (innerHeight - r.bottom) / rem};
        }""")
        assert geom == pytest.approx({"left": 0.5, "top": 0.5, "right": 0.5, "bottom": 4.5})
        # The right edge is actually visible — pre-fix, the sidebar's
        # overflow:hidden clips the panel at 90vw and this point hits nothing.
        assert page.evaluate("""() => {
            const rem = parseFloat(getComputedStyle(document.documentElement).fontSize);
            return document.elementFromPoint(innerWidth - rem - 4, innerHeight / 2)
                   ?.closest('.inspector') !== null;
        }""")
```

Run `pytest tests/test_web_browser_scenarios.py -k context_inspector_mobile` —
**fails** (no `.context-inspector-mount` exists yet).

**Step 2 (red) — re-point vitest assertions.** In `context-export.test.js`,
panel DOM now lives in `el._panelMount` (private-state access matches the
file's existing convention). Mechanical change across the inspector tests
(copy-conversation-menu tests untouched):
- `el.textContent` → `el._panelMount.textContent` (lines ~48, 54-55, 60, 67,
  69, 122-124, 136-137, 157, 171)
- `el.querySelector(...)` / `el.querySelectorAll(...)` →
  `el._panelMount.querySelector(...)` / `...querySelectorAll(...)` (lines ~50,
  56, 126, 132, 139, 182-183, 193, 200, 218, 245-249, 257-258, 263)
- Host state assertions (`el._loading`, `el._tab`, `el._data`, `el._error`,
  `el._rawLoading`) stay on the host.

Add two tests (after the existing "does not close when clicking inside…"
test, which itself needs the `rawTab` lookup re-pointed to the mount — its
`trigger` / `previousElementSibling` setup is unchanged because the host
stays a sibling of `.context-usage`):

```js
it('portals the panel to a body-level mount and removes it on disconnect', async () => {
  fetchMock.mockResolvedValue(json({ sources: [] }));
  const el = document.createElement('context-inspector');
  el.convId = 'id'; el.open = true;
  document.body.append(el);
  await el.updateComplete;
  expect(el._panelMount.parentElement).toBe(document.body);
  expect(el._panelMount.classList.contains('context-inspector-mount')).toBe(true);
  expect(el._panelMount.querySelector('.inspector')).not.toBeNull();
  el.remove();
  expect(document.querySelector('.context-inspector-mount')).toBeNull();
});

it('renders nothing into the mount when closed', async () => {
  fetchMock.mockResolvedValue(json({ sources: [] }));
  const el = document.createElement('context-inspector');
  el.convId = 'id'; el.open = false;
  document.body.append(el);
  await el.updateComplete;
  expect(el._panelMount.parentElement).toBe(document.body);
  expect(el._panelMount.querySelector('.inspector')).toBeNull();
});
```

(The existing doc-click test already covers "click inside the mount does not
close" once re-pointed, because the tab button now sits in the mount.)

Run `npx vitest run src/decafclaw/web/static/components/context-export.test.js`
— **fails** (`_panelMount` undefined).

**Step 3 (green) — portal in `context-inspector.js`.** Mirror
`notification-inbox.js:37-79` exactly:

- Import: `import { LitElement, html, nothing, render } from 'lit';`
  (module-level `render` doesn't collide with the `render()` method).
- Constructor: `/** @type {HTMLElement|null} */ this._panelMount = null;`
- `connectedCallback` (new — currently only `disconnectedCallback` exists):

```js
connectedCallback() {
  super.connectedCallback();
  // Body-level mount so the panel escapes any transformed ancestor (the
  // mobile sidebar's translateX, which would otherwise become the
  // containing block for `position: fixed` and clip the panel). Mirrors
  // notification-inbox.js (#386).
  if (!this._panelMount) {
    this._panelMount = document.createElement('div');
    this._panelMount.className = 'context-inspector-mount';
    document.body.appendChild(this._panelMount);
  }
}
```

- `render()` becomes `return nothing;` — move the current template body
  (lines 391-497) into a new `#panel()` private method (keep the
  `if (!this.open) return nothing;` guard inside it).
- `updated()`: at the end, render the panel into the mount on every update
  (same shape as `notification-inbox.js:71-79`):

```js
if (this._panelMount) {
  render(this.#panel(), this._panelMount);
}
```

- `disconnectedCallback`: after `this.#cleanupListeners();`,

```js
if (this._panelMount) {
  render(nothing, this._panelMount);
  this._panelMount.remove();
  this._panelMount = null;
}
```

- Doc-click handler in `updated()` (line ~92): insert
  `if (this._panelMount?.contains(e.target)) return;` before the
  `this.contains(e.target)` check. The `previousElementSibling` trigger check
  stays (the host keeps its position in the sidebar template).
- `#onTabKeyDown` (line ~176): `this.querySelector(...)` →
  `this._panelMount?.querySelector(...)`.
- Keep `@click=${(e) => e.stopPropagation()}` on `.inspector` (line 413) —
  harmless, minimal diff. `close` events still dispatch on the host, so the
  sidebar's `@close` binding (`conversation-sidebar.js:738`) is unchanged.

**Step 4 (green) — re-scope `context-inspector.css`.**
- Delete the host rules at lines 3-9 (`context-inspector { display: none; }`
  and `context-inspector[open] { display: block; }`) — the host renders
  nothing; it is an empty inline anchor.
- Re-scope every panel selector from the tag to the mount class, mirroring
  `notification-inbox.css:48` (`.notification-panel-mount .notification-panel`):
  - `context-inspector .foo` → `.context-inspector-mount .foo` (all ~43 rules)
  - `context-inspector dc-widget-json-view` → `.context-inspector-mount dc-widget-json-view`
  - `context-inspector button.close-btn` → `.context-inspector-mount button.close-btn`
- Desktop geometry is unchanged: with the portal, `.inspector`'s
  `position: fixed` now resolves against the viewport, which is the box it was
  always meant to use (`bottom: 5.25rem; left: 1rem; width: min(640px, …)`).
  The mobile `@media (max-width: 639px)` block (lines 112-148) keeps its
  values verbatim in this phase (safe-area insets are Phase 2).

**Step 5 (green) — fix the existing scenario.** In
`test_browser_context_inspector` (line ~537), the host is now empty:
`page.locator("context-inspector").inner_text()` →
`page.locator(".context-inspector-mount .inspector").inner_text()`.
(The scenario mounts the component on `document.body` directly, so it never
exercised the trap — keep it as the desktop-behavior guard.)

**Verification — automated:**
- [x] `pytest tests/test_web_browser_scenarios.py -k context_inspector -v` — both scenarios pass (2 passed in 3.32s); the mobile scenario was confirmed failing pre-fix (step 1: 30s timeout waiting for `.context-inspector-mount .inspector`)
- [x] `npx vitest run components/context-export.test.js` — 25/25 pass (re-pointed + 2 new tests)
- [x] `make test-js` — 501/501 (baseline was 499)
- [x] `make test` — 4493 passed, 2 skipped (baseline was 4492)
- [x] `make check` — green after `make fmt` reformatted the scenario's dict literal

**Verification — manual** (needs Les — `make dev` is likely his running instance;
coordinate before touching it, or he verifies after merge):
- [ ] 390px-wide browser: tap context meter → panel fills the viewport with
      0.5rem gutters and 4.5rem bottom margin (chat input visible below);
      tabs, raw drawer, close button (top-right, no longer clipped), and
      doc-click-outside all work
- [ ] Desktop (≥640px): panel position/size, tabs, raw drawer, copy/export
      unchanged

---

## Phase 2: Mobile safe-area insets

CSS-only: the mobile `.inspector` box gets `env(safe-area-inset-*)` so the
panel respects device notches if `viewport-fit=cover` is ever added app-wide
(out of scope). No-op today, zero-risk.

**TDD opt-out:** safe-area insets can't be asserted in automation — neither
jsdom nor the Playwright sync API emulates `env(safe-area-inset-*)`, and with
no `viewport-fit=cover` the values are 0. Verified by `make check` + manual
visual instead.

**Files:**
- Modify: `src/decafclaw/web/static/styles/context-inspector.css` — mobile
  block only (the two re-scoped `.inspector` rules inside
  `@media (max-width: 639px)`, now at ~lines 107-118 after Phase 1's
  re-scope):

```css
.context-inspector-mount .inspector {
  left: calc(0.5rem + env(safe-area-inset-left, 0px));
  width: calc(100vw - 1rem - env(safe-area-inset-left, 0px) - env(safe-area-inset-right, 0px));
  top: calc(0.5rem + env(safe-area-inset-top, 0px));
  bottom: calc(4.5rem + env(safe-area-inset-bottom, 0px));
  padding: 0.75rem;
}

.context-inspector-mount .inspector.expanded {
  width: calc(100vw - 1rem - env(safe-area-inset-left, 0px) - env(safe-area-inset-right, 0px));
}
```

Keeps the existing `left`/`width` box scheme (no over-constrained
`left`+`right`+`width`); identical layout when insets are 0.

**Verification — automated:**
- [x] `make check` — green
- [x] `pytest tests/test_web_browser_scenarios.py -k context_inspector` — 2 passed (insets are 0 in the test, rect unchanged)

**Verification — manual:**
- [ ] Same 390px check as Phase 1 — layout pixel-identical (insets are 0
      without `viewport-fit=cover`)

---

## Phase 3: Docs

**TDD opt-out:** documentation.

**Files:**
- Modify: `docs/web-ui.md` — in the "### Context inspector" section (line ~145),
  one sentence: the panel portals to a body-level `context-inspector-mount`
  (same pattern as `notification-inbox`, #386) so it escapes the transformed
  mobile sidebar.
- Modify: `docs/web-ui-mobile.md` — in the "no `position: fixed` inside
  transformed ancestors" rule (lines ~42-54), list the context inspector as a
  second instance of the portal pattern.

**Verification — automated:**
- [x] `make check` — green (no code touched)

**Verification — manual:**
- [x] Both doc edits read true against the merged code (mount class name,
      pattern reference, Lit `this` gotcha)

---

## Integration

After Phase 3: `git push` the branch and open a PR referencing #1037
(`Closes #1037`), move the board item to In review (project 6, item
`PVTI_lAHNVLfOAUg5p84P989P`). Les reviews/merges; after merge, live-test in
the web UI at a phone viewport (Mattermost unaffected). One commit per phase:
`Phase N: <name>`, `make fmt` before each commit.
