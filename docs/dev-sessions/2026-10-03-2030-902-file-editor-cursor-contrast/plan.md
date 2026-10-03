# Implementation Plan: File Editor Cursor Contrast and Subtle Active Line

## Phases

### Phase 1: Style Adjustments
- Update `src/decafclaw/web/static/styles/wiki-editor.css`:
  - Update `--cm-cursor-color` to use `var(--pico-primary)` (or theme accent).
  - Dim `--cm-active-line-bg` across light and dark themes to a subtle tint.
  - Remove `--cm-active-line-border` declarations and remove `outline: 1px solid var(--cm-active-line-border)` from `.cm-activeLine`.
  - Style `.cm-cursor, .cm-dropCursor` with `border-left: 2px solid var(--cm-cursor-color, var(--pico-primary)) !important; margin-left: -1px;`.
- Update `src/decafclaw/web/static/styles/palettes/dracula.css`:
  - Update `--cm-cursor-color` to `#bd93f9` (`var(--pico-primary)`).
  - Dim `--cm-active-line-bg` to `#2c2e3b`.
  - Remove `--cm-active-line-border`.
- Update `src/decafclaw/web/static/styles/palettes/solarized-light.css`:
  - Update `--cm-cursor-color` to `#268bd2` (`var(--pico-primary)`).
  - Dim `--cm-active-line-bg` to `#fbf0d9`.
  - Remove `--cm-active-line-border`.

### Phase 2: Unit Test Updates and Verification
- Update `src/decafclaw/web/static/components/file-editor.test.js`:
  - Verify `--cm-cursor-color` across all themes.
  - Assert `.cm-cursor` computed style has `borderLeftWidth` of `2px` and `borderLeftColor`.
  - Assert `.cm-activeLine` has dimmed `--cm-active-line-bg` and no outline.
  - Verify contrast of cursor against active line background meets WCAG non-text criteria (>= 3.0:1).
- Run `make check-js && make test-js`.
- Run full gate `make check`.
