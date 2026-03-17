# Responsive UI — Spec

## Goal

Make the DecafClaw web chat UI usable on mobile and narrow-screen devices. The current layout is desktop-first with a fixed-width sidebar and no responsive behaviour below ~600px.

## Breakpoints

Two breakpoints only:

- **Mobile**: `< 640px` — overlay sidebar, pinned input, wider bubbles
- **Desktop**: `≥ 640px` — current layout (sidebar inline, drag-to-resize, etc.)

Tablet is treated as desktop for now.

## Changes by area

### Sidebar (mobile)

- Sidebar becomes an **off-canvas overlay**: slides in from the left, sits on top of the chat content.
- A **semi-transparent backdrop** covers the chat area behind the open sidebar; tapping/clicking it closes the sidebar.
- Sidebar is **closed by default** on mobile (hidden off-screen to the left).
- The existing collapse/expand attribute and CSS are desktop-only; mobile open/close is a separate `_mobileOpen` state.
- **Drag-to-resize** (`#sidebar-resize-handle`) is hidden on mobile — no touch equivalent needed.

### Hamburger trigger

- A **hamburger button** (`☰`) appears in the top-left of the chat area on mobile only.
- On desktop it is hidden (`display: none`).
- Tapping the hamburger opens the overlay sidebar.
- The sidebar has a close button (`✕`) visible on mobile only (or the backdrop tap closes it).

### Chat input (mobile)

- The `chat-input` component is **pinned to the bottom of the viewport** on mobile via `position: sticky; bottom: 0`.
- The chat message list gets `padding-bottom` to prevent the last message from being obscured by the pinned input.

### Message bubbles (mobile)

- `.message` max-width increases from `85%` to `95%` on mobile.

### Out of scope

- Swipe-to-open gesture for sidebar
- Tablet-specific layout
- Changes to font sizes or spacing beyond what's needed for the layout fixes

## Acceptance criteria

- [ ] On a viewport < 640px: sidebar is hidden, hamburger button visible in chat header area
- [ ] Tapping hamburger slides sidebar in as overlay with backdrop
- [ ] Tapping backdrop or a close button closes the sidebar
- [ ] Selecting a conversation on mobile closes the sidebar automatically
- [ ] Chat input is visible and usable above the mobile keyboard (pinned)
- [ ] Message bubbles use 95% max-width on mobile
- [ ] On a viewport ≥ 640px: no visible change from current behaviour
- [ ] Drag-to-resize handle hidden on mobile
