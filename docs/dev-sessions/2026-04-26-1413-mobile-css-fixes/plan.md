# Mobile CSS fixes — plan

## Phasing

One PR, one branch, multiple commits — one per phase so bisects are easy.

### Phase 1 — Notification panel portal

- Modify `notification-inbox.js`: when opening, append the panel `<div>` to `document.body` instead of letting it render in place inside the component. Keep the bell + badge in place.
- Re-render panel contents into the body-mounted element on each open, and on prop changes while open.
- Position it the same way (fixed, computed against viewport). Now actually fixed-to-viewport because the body has no transformed ancestor.
- On close, remove the panel element from body.
- Make sure event listeners (mark-all, row clicks) survive the move — bind on the body-mounted element after attach.

Risk control: keep the `position: fixed` math in `#positionPanel` exactly as-is. The CSS-side fix is just changing the *render parent*.

### Phase 2 — `confirm-view` styles

- Create `src/decafclaw/web/static/styles/confirm-view.css` with:
  - `.confirm-card` — card container with margin-bottom
  - `.confirm-header` — flex layout for tool name + command
  - `.confirm-command` — `white-space: pre-wrap; word-break: break-all; overflow-x: auto; max-width: 100%`
  - `.confirm-buttons` — `display: flex; flex-wrap: wrap; gap: 0.5rem`
  - `.confirm-buttons button` — sane padding
- Import in `style.css`.

### Phase 3 — Tap-target sweep + hover-only fixes + hamburger

In `mobile.css`:

```css
/* Touch tap-target floor on mobile */
@media (max-width: 639px) {
  button,
  [role="button"] {
    min-height: 44px;
  }

  /* Hamburger gets a square footprint */
  .hamburger-btn {
    min-width: 44px;
    min-height: 44px;
  }

  /* Always-show actions that are hover-revealed on desktop */
  chat-message pre .copy-btn { opacity: 1 !important; }
  conversation-sidebar .conv-item .conv-archive { display: block; }

  /* Mobile-header gets explicit z-index so nothing stacks above the hamburger */
  #mobile-header { z-index: 100; position: relative; }
}
```

Then visually scan each component CSS file and add targeted exceptions where 44px breaks layout (e.g., a row of icon-only inline buttons inside a tight bar). Each exception annotated with a one-line reason.

### Phase 4 — Convention doc

Create `docs/web-ui-mobile.md` capturing the conventions. Cross-link from project `CLAUDE.md` (the "Web UI" / "Mattermost-specific" area or a new short bullet under conventions).

### Phase 5 — Verify and ship

- `make lint`
- `make typecheck`
- `make check-js`
- `make test`
- Open PR
- Request Copilot review
- Wait for Les to smoke-test on his actual mobile device, address anything that surfaces

## Self-review of plan

What could go wrong?

- **Notification panel portal — Lit reactivity.** Lit renders into the component's root by default; appending the panel element to body bypasses Lit's update cycle for that subtree. Need to either (a) keep the panel rendered by Lit but move it via `appendChild` (Lit will still re-render it, just in a different parent), or (b) maintain the panel manually outside Lit. Option (a) is simpler and tested behavior — Lit doesn't care where the rendered children live, only what the render root is. Will verify this assumption when implementing.
- **Generic `min-height: 44px` on `button`.** This might change Pico CSS rendering for things like the chat-input buttons. Worth eyeballing. If it breaks the input row layout, scope the rule more narrowly or override per-element.
- **`.conv-archive { display: block }` on mobile.** This will show the archive button inside every conv list row, all the time, taking up space. Acceptable for mobile UX (vs invisible-and-unreachable). Verify it doesn't push the title text off screen.
- **`pre .copy-btn { opacity: 1 }`.** Same — always-visible button on every code block. Acceptable.

What's not covered?

- Actual testing in a browser. Code-only changes; trust the audit + run unit tests. Real verification needs Les's device.
- The fact that `<meta maximum-scale=1>` blocks pinch-zoom system-wide. Already documented as intentional; not a fix target.
