# Responsive UI — Plan

## Overview

Four small steps, each independently testable:

1. CSS media query foundation — bubbles, input pinning, hide resize handle
2. Off-canvas sidebar overlay CSS
3. `conversation-sidebar.js` mobile open/close state + backdrop
4. Hamburger button in chat layout + auto-close on conversation select

---

## Step 1 — CSS foundation (no JS changes)

**Builds on:** existing `style.css`

**What it does:**
- Add `@media (max-width: 639px)` block:
  - `.message` max-width → `95%`
  - `chat-input` → `position: sticky; bottom: 0; z-index: 10`
  - `chat-view` → `padding-bottom: 0` (sticky input handles its own spacing)
  - `#sidebar-resize-handle` → `display: none`
  - `#chat-layout` — ensure sidebar doesn't take space when off-canvas (handled in step 2)

**State after:** bubbles are wider, input sticks to bottom, resize handle gone on mobile. Sidebar still broken (overflows) — fixed next step.

---

## Step 2 — Off-canvas sidebar CSS

**Builds on:** Step 1

**What it does:**
- In the `@media (max-width: 639px)` block:
  - `conversation-sidebar` → `position: fixed; top: 0; left: 0; height: 100%; z-index: 100; transform: translateX(-100%); transition: transform 0.25s ease`
  - `conversation-sidebar[mobile-open]` → `transform: translateX(0)`
  - `#sidebar-backdrop` → `display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.4); z-index: 99`
  - `#sidebar-backdrop.visible` → `display: block`
  - `.hamburger-btn` → `display: flex` (shown on mobile)
- On desktop (`@media (min-width: 640px)`):
  - `.hamburger-btn` → `display: none`
  - `#sidebar-backdrop` → `display: none !important`
  - Ensure `conversation-sidebar[mobile-open]` transform override doesn't bleed (scoped to mobile media query so it won't)
- Add `#sidebar-backdrop` div to `index.html` (inside `#chat-layout`, before `conversation-sidebar`)
- Add `.hamburger-btn` placeholder to `index.html` (inside `#chat-main`, before `chat-view`) — wired in step 4

**State after:** on mobile, sidebar is off-screen by default. Adding `mobile-open` attribute slides it in. Backdrop div exists but is hidden. No JS yet — can test by manually toggling the attribute in devtools.

---

## Step 3 — Sidebar mobile open/close state

**Builds on:** Step 2

**What it does** (in `conversation-sidebar.js`):
- Add `_mobileOpen: { type: Boolean, state: true }` property (default `false`)
- In `updated()`, toggle `mobile-open` attribute on `this` based on `_mobileOpen`
- Add `openMobile()` / `closeMobile()` public methods
- In `#handleSelect()` — after calling `store.selectConversation()`, call `this.closeMobile()`
- In the render template — add a `✕` close button visible only on mobile (`.mobile-close-btn`), calls `this.closeMobile()`
- Backdrop click handler: `app.js` will wire this (step 4), but expose `closeMobile()` as the API

**State after:** sidebar has `openMobile()`/`closeMobile()` methods. Selecting a conversation auto-closes. Close button works. No hamburger trigger yet.

---

## Step 4 — Hamburger button + backdrop wiring

**Builds on:** Step 3

**What it does** (in `app.js` and `index.html`):
- `index.html`: add `<button class="hamburger-btn" id="hamburger-btn" aria-label="Open sidebar">☰</button>` inside `#chat-main` header area (or a new `#chat-header` wrapper div if needed for layout)
- `app.js`:
  - `document.getElementById('hamburger-btn')?.addEventListener('click', () => sidebar.openMobile())`
  - `document.getElementById('sidebar-backdrop')?.addEventListener('click', () => sidebar.closeMobile())`
- CSS: `.hamburger-btn` position — absolute top-left inside `#chat-main`, or part of a thin mobile header bar above `chat-view`

**State after:** full mobile flow works end-to-end. All acceptance criteria met.

---

## Commit strategy

One commit per step. Each is independently reviewable and the UI is never in a broken state between steps.
