# Web UI — mobile conventions

The web UI runs in two device classes: desktop (Chrome/Firefox/Safari at typical laptop widths) and mobile (Firefox on Android is the daily driver, plus iOS Safari). The viewport meta is `<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">` — pinch-zoom is intentionally disabled to keep the experience app-like. **That means there is no escape hatch when content overflows on mobile.** Every responsive bug is a hard bug.

## Breakpoint

Single mobile breakpoint at `@media (max-width: 639px)`, defined once in `src/decafclaw/web/static/styles/mobile.css`. Tablet (640–1023px) currently follows the desktop layout — fine for now.

When you add a new component, **default to thinking through the mobile case at the same time you write the desktop CSS**. If you ship a component without mobile rules and rely on Pico defaults, you will eventually overflow on a 360×640 screen.

## Tap-target floor

WCAG 2.5.5 / Apple HIG specify 44×44 CSS pixels for interactive elements. The vertical axis is the one that typical button styling actually compresses; horizontal width is usually fine from the button's text + padding. So `mobile.css` enforces a height floor with a generic rule, and lets icon-only buttons that need the full 44×44 floor enforce min-width explicitly:

```css
@media (max-width: 639px) {
  button, [role="button"] { min-height: 44px; }

  /* Icon-only buttons that need the full 44×44 floor */
  .hamburger-btn { min-width: 44px; min-height: 44px; }
}
```

Plus targeted exceptions for inline / overlay buttons where 44px would obviously break the design (corner X on attachment chips, copy-button overlay on code blocks, breadcrumb segments styled as inline text, the secondary archive button inside conv-item rows). **Each exception is annotated with a one-line reason in `mobile.css`** — when you add another exception, do the same.

When you add a new icon-only button that doesn't have text padding to give it natural width, set `min-width: 44px` on it explicitly.

## No hover-only affordances on mobile

Touch devices don't fire `:hover`. If you reveal an action via `:hover`, mobile users can't reach it. Either show the action always on mobile, or move it behind an explicit menu trigger.

Existing examples in `mobile.css`:

```css
/* Hover-only on desktop, always visible on mobile */
chat-message pre .copy-btn { opacity: 1; }
conversation-sidebar .conv-item .conv-archive { display: block; }
```

When you add a new hover-revealed action, add the matching mobile override in the same PR.

## No `position: fixed` inside transformed ancestors

When any ancestor has `transform`, `perspective`, or `filter`, it becomes the **containing block** for `position: fixed` descendants — the descendant is positioned and clipped relative to the ancestor, not the viewport. The mobile sidebar uses `transform: translateX` for its slide-in, so any `position: fixed` element rendered inside `conversation-sidebar` (or any other transformed container) will be trapped.

The pattern that works: **portal the popover to `document.body`** so it has no transformed ancestor. See `notification-inbox.js` for the canonical implementation:

- The bell stays inside the component (light DOM).
- A `_panelMount` div is created in `connectedCallback` and appended to `document.body`.
- `updated()` calls Lit's standalone `render(this.#renderPanel(), this._panelMount)` so the panel template stays reactive.
- `disconnectedCallback` clears the mount and removes it.
- The `closeOnDocClick` listener checks `this._panelMount.contains(e.target)` in addition to `this.contains(e.target)` so panel clicks don't close the panel.

If you add a popover/dropdown that lives anywhere inside the sidebar, do the same.

## Constrain wide content at the source

Long `<pre>` blocks, wide tables, and unbroken URLs will push layout sideways on a narrow screen. With pinch-zoom disabled, this means the user can't reach whatever sits past the right edge. Always add intrinsic overflow handling to the content style:

```css
/* Wrap long shell commands and long URLs */
.confirm-command {
  white-space: pre-wrap;
  word-break: break-all;
  overflow-x: auto;
  max-width: 100%;
}

/* Code blocks that contain long lines: scroll inside the bubble */
chat-message .message.assistant .content pre { overflow-x: auto; }

/* Tables that may be wider than the viewport: wrap in a scroller */
.widget-data-table__scroll { max-height: 360px; overflow: auto; }
```

Don't rely on an ancestor's `overflow: hidden` to silently clip — the overflow is still unreachable.

## iOS Safari / Firefox Android keyboard handling

When the soft keyboard opens, `100vh` does **not** shrink — neither does `dvh`. The visible viewport (`window.visualViewport.height`) does. Without compensation, anything sized via `100vh` extends below the keyboard and the user can't see it.

Fix is in `app.js`'s `setupVisualViewportTracking()`:

```js
const root = document.documentElement;
const update = () => {
  const h = window.visualViewport?.height ?? window.innerHeight;
  root.style.setProperty('--vh', `${h}px`);
};
window.visualViewport?.addEventListener('resize', update);
window.visualViewport?.addEventListener('scroll', update);
update();
```

Then layout CSS uses `var(--vh, 100vh)` instead of raw `100vh` for any full-height container (`#app`, `#chat-layout`, `body`, `login-view`). The fallback covers older browsers.

If you add a new full-height surface, use `var(--vh, 100vh)` not `100vh`.

## Quick checklist before merging a UI change

- Does every new interactive element have a 44×44 tap area on mobile? If smaller, is there a comment explaining why?
- Does every hover-revealed action have a mobile-visible alternative?
- If you added a popover/dropdown, does it portal to `document.body`?
- Does any new wide content (`<pre>`, table, long URLs) wrap or scroll inside its parent?
- Does any full-height container use `var(--vh, 100vh)` instead of bare `100vh`?
