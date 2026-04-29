# Web UI Design System — Research

## 1. CSS File Inventory + Load Order

**Files under `src/decafclaw/web/static/styles/`** (18 files):

| File | Purpose |
|------|---------|
| `variables.css` | Shared custom properties (root `--sidebar-width`, `--header-height`), shared primitive classes (`.dc-floating-btn`, `.dc-overlay-header`, `.dc-overlay-close-x`), global `.hidden` |
| `layout.css` | Core flex layout: `#app`, `#chat-layout`, `#chat-main`, `#canvas-main` container sizing |
| `login.css` | Login page styles (`#login-form`, `#login-error`, etc.) |
| `sidebar.css` | Left sidebar (conversation list): `conversation-sidebar`, `.sidebar-header`, `.conv-item`, theme toggle |
| `context-inspector.css` | Token usage inspector: `context-inspector`, `.inspector-header`, `.close-btn`, `.candidates-header` |
| `notification-inbox.css` | Bell icon & inbox panel: `notification-inbox`, `.notification-panel-header` |
| `chat.css` | Chat messages & rendering: `chat-message`, `.message`, `.tool-result-header`, `.copy-btn`, code blocks |
| `chat-input.css` | Input box bottom of chat: `chat-input`, `.input-row`, `.stop-btn`, `.attach-btn` |
| `widgets.css` | Widget rendering: `.widget-host`, `.widget-data-table`, `.code-block-header`, `.code-block-copy` |
| `wiki.css` | Wiki page display: `.wiki-page-header`, `.wiki-edit-btn`, `.wiki-delete-btn`, `.wiki-close-btn` |
| `wiki-editor.css` | Wiki markdown editor: `.file-editor-status`, `.wiki-editor-toolbar-btn`, code blocks |
| `config-panel.css` | Settings panel: `.config-panel-header`, `.config-back-btn`, `.config-close-btn` |
| `confirm-view.css` | Confirmation dialogs: `.confirm-header` |
| `toast.css` | Toast notifications (not inspected) |
| `resize.css` | Resize handles & hamburger default: `#canvas-resize-handle`, `.hamburger-btn` |
| `canvas.css` | Canvas panel (right sidebar): `#canvas-main`, `.canvas-header`, `.canvas-btn`, `.canvas-tab-*`, `.canvas-mobile-*`, `.scroll-to-bottom` |
| `mobile.css` | `@media (max-width: 639px)` overrides: hamburger float, sidebar slide-out, tap targets |
| `hljs-themes.css` | Highlight.js code theme integration |

**Load order** (centralized in `style.css`):
- `index.html` (line 6): `<link rel="stylesheet" href="/static/vendor/bundle/pico.min.css">`
- `index.html` (line 7): `<link rel="stylesheet" href="/static/style.css">`
- `style.css` (lines 1–18): sequential `@import` statements in order listed above.

---

## 2. Button / Control / Header Class Inventory

### Shared Primitives (variables.css)
- **`.dc-overlay-close-x`** (lines 29–41): close-X for mobile overlays. `button.dc-overlay-close-x` rule; border:none, bg:none, 1.5rem font, color:muted→primary on hover.
- **`.dc-overlay-header`** (lines 48–57): header row top of mobile overlays. `@media (max-width: 639px)`; flex display, 57px min-height, 1px bottom border.
- **`.dc-floating-btn`** (lines 66–80): shared floating control look (hamburger, canvas resummon, disclosure, scroll button). border 1px, 6px radius, 44px tap target, box-shadow 0 2px 8px.

### Canvas (canvas.css)
- **`.canvas-header`** (line 45): top bar of canvas. flex, 48px implicit height (0.4rem padding + border + 44px content buttons), bottom border.
- **`.canvas-btn`** (lines 61, 65–69): buttons in canvas header/strip. color:inherit, bg:none, border:0, 1.1rem font, 2.5rem min-height.
- **`.canvas-header-mobile`** (line 57): display:none desktop; `@media` shows it flex.
- **`.canvas-mobile-disclosure`** (lines 315–321): disclosure triangle button on mobile canvas header; extends `.dc-floating-btn`; `@media (max-width: 639px)`.
- **`.canvas-resummon-pill`** (lines 92–123): resummon button in chat area; extends `.dc-floating-btn`; primary-filled, shows unread dot.
- **`.canvas-tab`** / **`.canvas-tab-close`** (lines 214–258): tab strip on desktop; close icon opacity on hover.
- **`.canvas-mobile-tab`** / **`.canvas-mobile-tab-close`** (lines 269–307): mobile tab list; accent underline on active.

### Sidebar (sidebar.css)
- **`.sidebar-header`** (line 12): heading row (no closing button here; mobile uses `.mobile-close-btn` + `.dc-overlay-close-x`).
- **`.theme-btn`** (lines 244–259): light/dark toggle; opacity + color on hover/active.
- **`.theme-toggle`** — container for theme buttons.

### Config Panel (config-panel.css)
- **`.config-panel-header`** (line 9): header. border-bottom:muted.
- **`.config-back-btn`** / **`.config-close-btn`** (lines 23–37): back & close icons. muted color → primary on hover.

### Mobile (mobile.css)
- **`.hamburger-btn`** (lines 50–60): fixed position hamburger menu trigger; extends `.dc-floating-btn`. bg:pico-background-color; font 1.3rem; z-index 150.
- **`.mobile-close-btn`** (line 115): sidebar close button (references `.dc-overlay-close-x`).

### Chat Input (chat-input.css)
- **`.stop-btn`** (line 27): del-color (red) stop button.
- **`.attach-btn`** (lines 33–40): attachment icon; opacity 0.7→1 on hover.

### Chat Messages (chat.css)
- **`.copy-btn`** (lines 358–374): code block copy icon; opacity on hover.
- **`.tool-result-header`** (lines 133–141): collapsible tool result header; hover underline.

### Confirm View (confirm-view.css)
- **`.confirm-header`** (line 15): header flex container; **strong** inline title.

### Wiki (wiki.css)
- **`.wiki-edit-btn`** / **`.wiki-delete-btn`** / **`.wiki-close-btn`** (lines 51–78): action buttons. muted→primary on hover.
- **`.wiki-rename-btn`** / **`.file-rename-btn`** (lines 126–138): inline rename icon buttons.
- **`.file-edit-btn`** / **`.file-delete-btn`** / **`.file-close-btn`** (lines 51–78): file action buttons.
- **`.wiki-page-header`** (line 34): page title bar.

### Wiki Editor (wiki-editor.css)
- **`.wiki-editor-toolbar-btn`** (lines 70–86): markdown toolbar buttons. opacity 0.6→1 on hover; bg:muted→primary on active.

### Widgets (widgets.css)
- **`.widget-data-table__sort-btn`** (lines 92–133): column sort icon; transparent bg; hover/focus bg:form-element.
- **`.code-block-header`** (line 259): code block label bar.
- **`.code-block-copy`** (lines 278–282): copy button in code block footer.

### Context Inspector (context-inspector.css)
- **`.inspector-header`** (line 29): inspector panel header.
- **`.close-btn`** (line 41): close button in inspector.
- **`.candidates-header`** (line 143): section header for candidates list.

---

## 3. Shared Primitive Classes — Full Definitions

### `.dc-floating-btn` (variables.css:66–80)
```css
.dc-floating-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 0.4rem;
  border: 1px solid var(--pico-muted-border-color);
  border-radius: 6px;
  min-width: 44px;
  min-height: 44px;
  line-height: 1;
  cursor: pointer;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.25);
  margin: 0;
  box-sizing: border-box;
}
```

### `.dc-overlay-header` (variables.css:48–57)
```css
@media (max-width: 639px) {
  .dc-overlay-header {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    padding: 0.4rem 0.75rem;
    min-height: 57px;
    border-bottom: 1px solid var(--pico-muted-border-color);
    box-sizing: border-box;
  }
}
```

### `button.dc-overlay-close-x` (variables.css:29–41)
```css
button.dc-overlay-close-x {
  background: none;
  border: none;
  font-size: 1.5rem;
  line-height: 1;
  padding: 0.4rem 0.6rem;
  margin: 0;
  cursor: pointer;
  color: var(--pico-muted-color);
}
button.dc-overlay-close-x:hover {
  color: var(--pico-color);
}
```

### Components using shared primitives:
- **`chat-view.js:185`**: `.scroll-to-bottom.dc-floating-btn` (scroll-to-bottom pill)
- **`conversation-sidebar.js:466`**: `.sidebar-header.dc-overlay-header`
- **`conversation-sidebar.js:475`**: `.mobile-close-btn.dc-overlay-close-x` (sidebar close button)
- **`canvas-panel.js:207`**: `.canvas-header.canvas-header-mobile.dc-overlay-header`
- **`canvas-panel.js:208`**: `.canvas-mobile-disclosure.dc-floating-btn` (disclosure triangle)
- **`canvas-panel.js:214`**: `.canvas-close.dc-overlay-close-x` (canvas panel close button)

### Similar visual treatment **without** shared classes:
- **`.hamburger-btn`** (resize.css:26, mobile.css:50): sets border/radius/shadow/tap-target locally instead of extending `.dc-floating-btn` (though mobile.css:46 comment states it uses it).
- **`.canvas-resummon-pill`** (canvas.css:92): styled to look like `.dc-floating-btn` (border, radius, shadow) but not explicitly applying the class; uses `@extend` pattern or manual replication.

---

## 4. Pico Variable Usage

**Enumeration of `--pico-*` properties used in custom styles/**:

| Variable | File:Line | Selector Context |
|----------|-----------|------------------|
| `--pico-muted-color` | variables.css:37, 40 | `.dc-overlay-close-x`, hover |
| `--pico-muted-border-color` | variables.css:54, 71 | `.dc-overlay-header`, `.dc-floating-btn` |
| `--pico-background-color` | mobile.css:56 | `.hamburger-btn` bg |
| `--pico-color` | mobile.css:57, canvas.css:62 | `.hamburger-btn`, `.canvas-btn:hover` |
| `--pico-primary` | canvas.css:32, 62 | resize handle, canvas btn hover |
| `--pico-primary-focus` | wiki-editor.css:87 | toolbar btn active |
| `--pico-primary-inverse` | config-panel.css:91 | text color in context |
| `--pico-del-color` | chat-input.css:28, login.css:20 | `.stop-btn`, error text |
| `--pico-ins-color` | wiki-editor.css:22 | saved status |
| `--pico-card-background-color` | widgets.css:85, 254 | data table, code block bg |
| `--pico-form-element-background-color` | widgets.css:128, 133 | sort button hover |
| `--pico-mark-background-color` | wiki-editor.css:99 | markdown highlighting |
| `--pico-code-background-color` | wiki-editor.css:142, 205 | inline code, code blocks |

(Plus many more in chat.css, context-inspector.css, config-panel.css; all read via `var()` fallback syntax.)

---

## 5. Visual Asset / Screenshot Location

**No dedicated visual asset directory found.**

Search results from `find` for `assets/`, `images/`, `screenshots/`, `docs/` (excluding node_modules, .venv, vendor):
- **Only result**: `/Users/lorchard/devel/decafclaw/.claude/worktrees/web-ui-design-system/docs/` 

This `docs/` directory contains **session documentation** (session reports, plans, notes), **not** visual assets or screenshots. No `docs/assets/`, `docs/images/`, or `static/images/` subdirectory exists.

**Conclusion**: The codebase currently does not maintain a directory for visual assets, screenshots, or documentation images.

