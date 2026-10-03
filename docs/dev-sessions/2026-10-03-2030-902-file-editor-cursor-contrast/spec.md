# Spec: File Editor Cursor Contrast and Subtle Active Line

## Problem
In the CodeMirror 6 file editor pane, the text cursor is difficult to see against the current line highlight:
1. `.cm-activeLine` has a 1px solid outline (`--cm-active-line-border`) creating a bounding box around the active line that visually dominates and distracts from the cursor.
2. The active line background is too dramatic and needs to be dimmed to a much more subtle tint across all themes.
3. The cursor is a 1.2px hairline styled with text foreground color, making it blend in with adjacent text glyphs and border lines.

## Solution
1. Remove `outline` from `.cm-activeLine` and `.cm-active-line-border` variables.
2. Dim `--cm-active-line-bg` across all themes to a subtle, unobtrusive tint:
   - Light: `#fbfcfd` or subtle tint on `#ffffff`
   - Dark: `#161b24` (subtle tint on `#13171f`)
   - Dracula: `#2c2e3b` (subtle tint on `#282a36`)
   - Solarized Light: `#fbf0d9` (subtle tint on `#fdf6e3`)
3. Set `.cm-cursor` to `2px solid var(--cm-cursor-color, var(--pico-primary)) !important; margin-left: -1px;`.
4. Set `--cm-cursor-color` across themes to high-contrast accent colors:
   - Base Light: `var(--pico-primary)` (`#1095c1`)
   - Base Dark: `var(--pico-primary)` (`#1095c1` or `#56b6c2`)
   - Dracula: `var(--pico-primary)` (`#bd93f9`)
   - Solarized Light: `var(--pico-primary)` (`#268bd2`)
5. Update unit tests in `file-editor.test.js` to assert the 2px cursor width, primary/accent cursor color, subtle active line background, and absence of active line outline.
