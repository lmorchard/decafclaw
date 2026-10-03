# Make file editor color theme and cursor visible across application themes

Implementation record for [issue #899](https://github.com/lmorchard/decafclaw/issues/899),
2026-10-03. Base: `4165293`.
Implementation dispatch model: `gemini-3.8-flash`.

The change resolves invisible cursor and low-contrast syntax highlighting in the CodeMirror 6
file editor pane across dark mode, light mode, and custom palettes (Dracula, Solarized Light).
CodeMirror's hardcoded default highlight style is replaced with a CSS-variable-backed
HighlightStyle matching Atom One Dark and Light (from `hljs-themes.css`), with authentic
overrides for Dracula and Solarized Light. CodeMirror chrome (gutters, cursor, selection,
active line) is styled via `--pico-*` custom properties.

Baseline `make check` passed. Baseline `make test-js` passed with 395 tests.

Final `make check` passed (lint, typecheck, check-js, module graph).
Final `make test-js` passed with 401 tests across 40 test files (+6 in `file-editor.test.js`).
Backend `tests/web/` passed with 101 tests.

All four phases completed:
- Phase 1: vendor bundle updates for HighlightStyle and tags
- Phase 2: CodeMirror theme variables and element styling
- Phase 3: FileEditor component theme integration
- Phase 4: Vitest unit tests in file-editor.test.js and verification gates

Copilot review feedback addressed:
- Addressed cascade override where later :root[data-theme="dark"] rules stomped on :root[data-palette="dracula"] by qualifying dark rules with :not([data-palette]).
- Lightened Dracula comment color (#95a7db) and system-dark comment color (#8590a4) to achieve >= 4.5:1 contrast against dark backgrounds.
- Darkened Solarized Light syntax tokens (#5b6c00 keyword, #15615a atom/string, #9e1a5a number, #4f636a comment/meta, #145c8f variable/property, #745700 type, #9e340a special, #a81c19 invalid) to achieve >= 4.5:1 contrast against #fdf6e3 and active lines.
- Introduced dedicated editor chrome tokens (--cm-foreground, --cm-background, --cm-cursor-color, --cm-gutter-color, --cm-gutter-bg, --cm-gutter-active-color, --cm-active-line-bg, --cm-selection-bg) across all themes to ensure >= 4.5:1 contrast for editor text and line numbers.
- Extended file-editor.test.js to load actual stylesheets and assert computed CSS variables, chrome styling, and contrast across all themes and palettes.
