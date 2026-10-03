# Make File Editor Theme and Cursor Visible Across Application Themes Implementation Plan

**Goal:** Provide full theme and palette support for the CodeMirror 6 file editor with visible cursor, proper contrast, and gutter styling.

**Approach:** Export `HighlightStyle` and `tags` from the CodeMirror vendor bundle, define a CSS-variable-backed highlight style in `file-editor.js`, style CodeMirror elements in `wiki-editor.css` using `--pico-*` variables, define `--cm-*` token variables for light, dark, Dracula, and Solarized Light themes, and add Vitest unit tests.

**Tech stack:** Lit, CodeMirror 6 (`@codemirror/view`, `@codemirror/language`, `@lezer/highlight`), Pico CSS v2, Vitest.

---

## Phase 1: Vendor Bundle Updates

Export `HighlightStyle` from `@codemirror/language` and `tags` from `@lezer/highlight` in `codemirror-entry.js`. Ensure `@lezer/highlight` is declared in `package.json`, and rebuild `vendor/bundle/codemirror.js` via `make vendor`.

**Files:**
- Modify: `src/decafclaw/web/static/package.json` — add `@lezer/highlight` dependency
- Modify: `src/decafclaw/web/static/codemirror-entry.js` — export `HighlightStyle` and `tags`
- Generate: `src/decafclaw/web/static/vendor/bundle/codemirror.js` via `make vendor`

**Key changes:**
- Export `HighlightStyle` from `@codemirror/language`
- Export `tags` from `@lezer/highlight`

**Verification — automated:**
- [x] `make vendor` succeeds and regenerates `src/decafclaw/web/static/vendor/bundle/codemirror.js` — **regenerated and exported HighlightStyle, tags**
- [x] `make check-js` passes — **tsc --noEmit passed**
- [x] `make test-js` passes — **395 passed**

---

## Phase 2: Stylesheet Rules for CodeMirror and Syntax Variables

Define CSS custom properties for code token highlights (`--cm-*`) for light mode, dark mode, Dracula palette, and Solarized Light palette. Style `.file-editor-mount .cm-editor`, cursor, line number gutters, active line, selection, and matching brackets using `--pico-*` and `--cm-*` variables.

**Files:**
- Modify: `src/decafclaw/web/static/styles/wiki-editor.css` — add CodeMirror element styles and default light/dark `--cm-*` variables
- Modify: `src/decafclaw/web/static/styles/palettes/dracula.css` — add Dracula `--cm-*` token overrides
- Modify: `src/decafclaw/web/static/styles/palettes/solarized-light.css` — add Solarized Light `--cm-*` token overrides

**Verification — automated:**
- [ ] `make check` passes

**Verification — manual:**
- [ ] Inspect stylesheet syntax and specificity

---

## Phase 3: FileEditor Component Theme Integration

Update `FileEditor` in `src/decafclaw/web/static/components/file-editor.js` to define and use a CSS-variable-backed `HighlightStyle` instead of `defaultHighlightStyle`.

**Files:**
- Modify: `src/decafclaw/web/static/components/file-editor.js` — import `HighlightStyle` and `tags` from `codemirror`, construct `editorHighlightStyle`, and apply it to editor extensions

**Verification — automated:**
- [ ] `make check-js` passes
- [ ] `make test-js` passes

---

## Phase 4: Automated Tests and Full Verification

Add Vitest unit tests in `src/decafclaw/web/static/components/file-editor.test.js` to verify:
1. Editor mounts with `editorHighlightStyle` extension.
2. CodeMirror elements (.cm-editor, .cm-gutters, .cm-cursor, .cm-activeLine) receive proper styles and variables.
3. Syntax tokens receive highlight classes.
4. Switching themes dynamically updates computed styles or variable inheritance.

Run full project gates.

**Files:**
- Create: `src/decafclaw/web/static/components/file-editor.test.js`

**Verification — automated:**
- [ ] `make test-js` passes with new tests
- [ ] `make check` passes
- [ ] `make test` passes
