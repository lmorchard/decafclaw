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
