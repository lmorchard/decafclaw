# Mobile CSS fixes — spec

## Context

Issue #386 was filed as an investigation. The full audit comment on that issue is the source of truth for what's broken; this session executes against that punch list.

Constraint: `<meta name="viewport" content="... maximum-scale=1">` is **intentional** — Les wants app-like sizing, not browser-zoom. So pinch-zoom is not an escape hatch. Every overflow is a hard bug.

## Goal

Land every mobile-CSS fix that doesn't require interactive device-side iteration, in one PR. Leave iOS visual-viewport keyboard handling as a follow-up (or its own phase) because it requires Les to test on the actual device.

## In scope

### Bugs (definitely fix)

1. **Notification panel clipped on mobile.** `notification-inbox.js#positionPanel` uses `position: fixed`, but the panel lives inside `conversation-sidebar`, which has `transform: translateX` on mobile. Per CSS spec, transformed ancestor becomes containing block for fixed descendants → panel is positioned and clipped relative to sidebar. **Fix:** render the panel into `document.body` (manual portal) so it escapes.

2. **`confirm-view` has no CSS.** `.confirm-card` / `.confirm-header` / `.confirm-command` / `.confirm-buttons` aren't styled in any file. The `<pre class="confirm-command">` will horizontally overflow on long shell commands. **Fix:** add a styles file for confirm-view with mobile-friendly defaults (wrap, scroll for code, flex-wrap on buttons).

### Tap targets (sweep)

Enumerated in audit Section C — bring all interactive elements to ≥44×44 CSS px on mobile. Approach:

- A generic `@media (max-width: 639px)` rule in `mobile.css` that bumps `min-height: 44px` on `button` and friends.
- Targeted overrides where the generic rule breaks layout (e.g., theme-toggle row).
- Exceptions allowed where 44px would clearly break the layout — must be commented in CSS with a one-line reason.

### Hover-only fixes (touch UX)

- `.copy-btn` (chat.css) — currently `opacity: 0` until `pre:hover`. Show always on mobile.
- `.conv-archive` (sidebar.css) — currently `display: none` until `.conv-item:hover`. Show always on mobile.

### Hamburger button

- Mobile-only UI element, currently ~28×28px. Bump to 44×44 explicitly. Add `z-index: 100` on `#mobile-header` so nothing accidentally stacks over it.

### Convention doc

Add a short `docs/web-ui-mobile.md` capturing:
- Mobile breakpoint = `@media (max-width: 639px)`
- Tap-target floor = 44×44 mobile
- No hover-only affordances; no `position: fixed` inside transformed ancestors
- Long content (`<pre>`, tables, URLs) needs intrinsic overflow handling
- iOS visual viewport handling (placeholder note for follow-up)

Cross-link from project `CLAUDE.md`.

## Out of scope

1. **iOS visual-viewport keyboard handling** — needs device-side iteration. Filed/tracked as Phase 3 in todo, deferred until Les confirms his target device is iOS (vs Android).
2. **Refactoring the `mobile.css` structure** (e.g., splitting per-component) — current single-file is fine.
3. **Tablet breakpoint** — the audit confirmed mobile (≤639) and desktop work; no need to introduce a third breakpoint right now.
4. **Vault.html standalone page** — uses inline styles that are already mobile-friendly enough.
5. **Replacing the wiki/file editor toolbars with bigger touch UI.** Toolbars use `flex-wrap: wrap` and have small buttons; a touch-friendly redesign is its own dev session — for this PR, just bump tap targets where the generic rule applies.

## Success criteria

- Every confirmed bug from audit Section A and B1 is fixed.
- Every interactive element listed in audit Section C is ≥44×44 on mobile (or has an inline comment explaining why not).
- Hover-only `.copy-btn` and `.conv-archive` are visible on mobile.
- `make lint` and `make typecheck` and `make check-js` clean.
- `make test` green.
- Smoke test live on Les's actual mobile device (last step before merge).

## Risks

- **Bumping `min-height: 44px` on every button could change the look of desktop toolbars.** Mitigate by scoping the rule inside `@media (max-width: 639px)` only.
- **Portal-ing the notification panel changes its render parent.** Lit re-renders may not pick up updates if the portal isn't wired correctly. Mitigate: re-render the panel content imperatively on each open + while open, and tear down on close.
- **Existing tests likely don't cover mobile CSS.** Behavior verification has to be visual on a real device. We trust unit tests for non-CSS regressions.

## Confirmed scope decisions

- **Devices in scope:** Firefox on Android (Les's daily driver) and iOS Safari. Both platforms get fixes.
- **Phase 3 (iOS visual viewport keyboard) is IN scope** since iOS is a target.
- **Notification panel portal:** minimal version — let Lit keep rendering the panel inside the component, but on open imperatively `document.body.appendChild(this._panelEl)` so it escapes the sidebar's transform context. Move it back on close. Lit's reactive updates target rendered DOM nodes regardless of where they're attached, so this preserves reactivity.
