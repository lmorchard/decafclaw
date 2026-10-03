# Spec: Fix File Editor Cursor Scroll Follow and Cursor Blinking

## Problem
In the CodeMirror 6 file editor pane in the Files tab:
1. When cursoring down in a file that extends beyond the viewport, the editor viewport does not scroll to follow the cursor. The cursor moves out of view because custom elements `<file-page>` and `<file-editor>` lack flex and height rules. Furthermore, `.file-editor-mount` has `overflow: auto` while `#wiki-main` has `overflow-y: auto`. The editor expands to the natural height of its document rather than being constrained to the pane height, causing CodeMirror's internal `.cm-scroller` to have `clientHeight == scrollHeight`. When cursor movements dispatch `scrollIntoView`, CodeMirror detects no scrollable space in `.cm-scroller` and does not scroll, leaving the cursor hidden below the pane fold.
2. The cursor does not blink. Pico CSS's `@media (prefers-reduced-motion: reduce)` rule applies `animation-iteration-count: 1 !important; animation-duration: 1ms !important;` to all elements, freezing CodeMirror's `cm-blink` animation. Furthermore, CodeMirror's default `cursorBlinkRate` of 1200ms is sluggish and feels unresponsive.

## Solution
1. Constrain container sizing and delegate scrolling to CodeMirror's `.cm-scroller`:
   - Set `file-page, .file-page { display: flex; flex-direction: column; flex: 1; min-height: 0; height: 100%; }`.
   - Set `file-editor, .file-editor { display: flex; flex-direction: column; flex: 1; min-height: 0; height: 100%; }`.
   - Set `.file-editor-mount { flex: 1; min-height: 0; height: 100%; display: flex; flex-direction: column; overflow: hidden; }`.
   - Set `.file-editor-mount .cm-editor { flex: 1; min-height: 0; height: 100%; }`.
   - Set `.file-editor-mount .cm-editor .cm-scroller { overflow: auto !important; height: 100%; }`.
   - Set `#wiki-main:has(file-page:not(.hidden)), #wiki-main:has(.file-page) { overflow-y: hidden; }` so `#wiki-main` does not create an outer scrollbar when the file page is active.
2. Fix cursor blinking:
   - Configure `drawSelection({ cursorBlinkRate: 800 })` in `file-editor.js`.
   - Ensure `.file-editor-mount .cm-editor .cm-cursorLayer` maintains `animation-iteration-count: infinite !important;` so reduced-motion overrides do not permanently freeze the blinking caret.
3. Tests:
   - Add unit tests verifying `.file-editor-mount`, `.cm-editor`, and `.cm-scroller` layout properties and cursorLayer animation properties.
