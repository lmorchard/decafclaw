# Notes — mobile CSS fixes

## Source

Audit and prior investigation in issue #386. The audit comment is the punch list this session works against.

## Session log

- Phase 1 — notification panel portal: refactored `notification-inbox.js` to keep the bell in the component but render the panel into a `document.body`-mounted `<div>` via Lit's standalone `render(template, container)`. Mount lives between `connectedCallback` and `disconnectedCallback`. Doc-click outside-detection now checks both `this` and `_panelMount`. The CSS positioning math (`#positionPanel`) is unchanged — it now actually resolves against the viewport because the mount has no transformed ancestor.
- Phase 1 — confirm-view styles: added `styles/confirm-view.css` with rules for `.confirm-card`, `.confirm-header`, `.confirm-command` (wrap + scroll for long shell commands), `.confirm-buttons` (flex-wrap). Imported in `style.css`.
- Phase 2 — tap-target sweep: in `mobile.css`, added a generic `@media (max-width: 639px) { button, [role="button"] { min-height: 44px } }` rule, with documented exceptions for inline/overlay buttons (`.copy-btn`, `.attachment-remove`, breadcrumb segments). Hamburger button bumped to 44×44 explicitly. `#mobile-header` got `position: relative; z-index: 100` so it can't be covered by sticky chat-input or transient overlays.
- Phase 2 — hover-only fixes: `chat-message pre .copy-btn { opacity: 1 }` and `conversation-sidebar .conv-item .conv-archive { display: block }` on mobile so touch users can reach those actions.
- Phase 3 — visual viewport / iOS keyboard: `setupVisualViewportTracking()` in `app.js` maintains a `--vh` CSS variable that follows `window.visualViewport.height`, with a fallback to `window.innerHeight`. `body`, `#app`, `#chat-layout`, and `login-view` now use `var(--vh, 100vh)` for height. When the soft keyboard opens on iOS Safari or Firefox Android, the layout shrinks accordingly so the chat input stays above the keyboard.
- Phase 4 — convention doc: `docs/web-ui-mobile.md` captures the breakpoint, tap-target floor with annotated exceptions, no-hover-only rule, no `position: fixed` inside transformed ancestors (with the notification-inbox portal as canonical example), wide-content overflow handling, and the visual-viewport approach. Added a quick-checklist at the bottom for code review. Cross-linked from `CLAUDE.md` (Mattermost-specific section header) and `docs/index.md` (Interfaces).
- Verification: `make lint` / `make typecheck` / `make check-js` / `make test` all clean. Real verification requires Les to test on his actual devices (Firefox Android + iOS Safari) — that's the last step before merge.

## Outcome

All in-scope items shipped. Pinch-zoom remains intentionally disabled per Les's product call; everything that could overflow now wraps or scrolls intrinsically. The notification panel renders at body level so it actually escapes the sidebar transform that was clipping it.
