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
Final `make test-js` passed with 400 tests across 40 test files (+5 in `file-editor.test.js`).
Backend `tests/web/` passed with 101 tests.

All four phases completed:
- Phase 1: vendor bundle updates for HighlightStyle and tags
- Phase 2: CodeMirror theme variables and element styling
- Phase 3: FileEditor component theme integration
- Phase 4: Vitest unit tests in file-editor.test.js and verification gates
