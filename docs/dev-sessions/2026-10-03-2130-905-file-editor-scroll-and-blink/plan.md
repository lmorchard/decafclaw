# Implementation Plan: Fix File Editor Cursor Scroll Follow and Cursor Blinking

## Phases

### Phase 1: Style and Component Updates
- Update `src/decafclaw/web/static/styles/wiki.css`:
  - When `<file-page>` is active inside `#wiki-main`, prevent `#wiki-main` from scrolling (`overflow-y: hidden`).
- Update `src/decafclaw/web/static/styles/wiki-editor.css`:
  - Add rules for `file-page` and `file-editor` custom element tags to participate in flex layout (`flex: 1; min-height: 0; height: 100%; display: flex; flex-direction: column;`).
  - Set `.file-editor-mount` to `overflow: hidden; flex: 1; min-height: 0; height: 100%; display: flex; flex-direction: column;`.
  - Set `.file-editor-mount .cm-editor` to `flex: 1; min-height: 0; height: 100%;`.
  - Set `.file-editor-mount .cm-editor .cm-scroller` to `overflow: auto !important; height: 100%;`.
  - Add rule ensuring `.file-editor-mount .cm-editor .cm-cursorLayer` preserves `animation-iteration-count: infinite !important;` and `animation: steps(1) cm-blink 0.8s infinite !important;` when focused.
- Update `src/decafclaw/web/static/components/file-editor.js`:
  - Pass `{ cursorBlinkRate: 800 }` to `drawSelection()`.

### Phase 2: Unit Tests and Verification
- Update `src/decafclaw/web/static/components/file-editor.test.js`:
  - Test scroller sizing and overflow behavior.
  - Test cursorLayer animation properties.
- Run `make check-js && make test-js`.
- Run full gate `make check`.
