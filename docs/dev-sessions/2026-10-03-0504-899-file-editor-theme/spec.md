# Make File Editor Theme and Cursor Visible Across Application Themes Spec

**Goal:** Ensure the file editor pane adapts to application themes (light, dark, and custom palettes) with readable syntax highlighting, proper gutters, and a visible cursor.

**Source:** https://github.com/lmorchard/decafclaw/issues/899

## Current state

The file editor component (`src/decafclaw/web/static/components/file-editor.js:148-181`) configures CodeMirror 6 extensions using `defaultHighlightStyle` from `@codemirror/language`.

- `defaultHighlightStyle` defines hardcoded syntax colors designed solely for light backgrounds (`#708` keywords, `#a11` strings, `#164` numbers, `#000` identifiers). On dark backgrounds, these dark colors fail WCAG AA contrast.
- CodeMirror's base styles set `.cm-cursor { border-left: 1.2px solid black; }`. On dark backgrounds, the cursor is black and invisible.
- `.cm-gutters` defaults to `#f5f5f5` background with `#999` text, displaying as a bright light-gray column in dark mode.
- In `src/decafclaw/web/static/styles/wiki-editor.css:60-68`, `.file-editor-mount .cm-editor` only specifies `height: 100%`, with no color or theme variables.
- The editor does not adapt when the user switches themes or palettes via `src/decafclaw/web/static/components/theme-toggle.js` and `src/decafclaw/web/static/lib/theme.js`.

## Desired end state

1. The file editor adapts dynamically to the active application theme (`light`, `dark`, and custom palettes `dracula` and `solarized-light`).
2. The cursor is always visible against the editor background in both light and dark themes.
3. Syntax highlighting tokens meet WCAG AA contrast against the background in light, dark, and palette modes.
4. Gutter margins, line numbers, active line highlights, bracket matching, and text selection harmonize with the application's theme colors.
5. Theme switching occurs reactively without requiring a full page reload or editor remount.
6. Vitest unit tests in `src/decafclaw/web/static` verify theme adaptation, contrast, and cursor styling.

## Design decisions

- **Decision:** Use CSS custom properties (`--cm-*` and `--pico-*`) for syntax highlighting tokens and editor chrome styling.
  - **Why:** CSS custom properties recompute immediately upon `data-theme` or `data-palette` attribute changes on `<html>` without requiring JavaScript listeners, component remounting, or complex CodeMirror compartment reconfigurations.
  - **Rejected:** Creating separate CodeMirror `Compartment` instances and dispatching state updates on DOM mutation observer events. That approach adds unnecessary runtime state and complexity.

- **Decision:** Export `HighlightStyle` from `@codemirror/language` and `tags` from `@lezer/highlight` in `codemirror-entry.js`, and define a CSS-variable-backed `HighlightStyle`.
  - **Why:** `HighlightStyle.define` accepts CSS custom properties (`var(--cm-keyword)`), allowing CodeMirror to emit token classes styled by our theme stylesheets.
  - **Rejected:** Plain `classHighlighter` with custom CSS classes for all tags, which requires writing manual class mappings and loses fine-grained modifier support.

- **Decision:** Align token colors with `src/decafclaw/web/static/styles/hljs-themes.css` (Atom One Dark for dark mode, Atom One Light for light mode) and provide authentic overrides for Dracula and Solarized Light.
  - **Why:** Consistent visual language across chat code blocks, markdown blocks, and the file editor pane.
  - **Rejected:** Inventing a completely different color palette for the file editor.

## Patterns to follow

- Two-axis theming model: `src/decafclaw/web/static/lib/theme.js:17-34` and `docs/web-ui-design.md:51-87`.
- Syntax highlighting themes: `src/decafclaw/web/static/styles/hljs-themes.css:37-199`.
- Component styles and primitives: `src/decafclaw/web/static/styles/wiki-editor.css:53-68`.
- Component unit testing with Vitest: `src/decafclaw/web/static/components/workspace-mutations.test.js:17-34`.

## What we're NOT doing

- We are NOT modifying the Milkdown wiki editor or its styles.
- We are NOT replacing CodeMirror 6 with Monaco or another editor library.
- We are NOT adding a separate editor-specific theme selector in the UI; the editor strictly follows the application theme.
- We are NOT altering file saving, debouncing, conflict handling, or backend endpoints.

## Open questions

None.
