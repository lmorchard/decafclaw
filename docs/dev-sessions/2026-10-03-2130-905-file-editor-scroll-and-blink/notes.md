# Dev Session Notes: Fix File Editor Cursor Scroll Follow and Cursor Blinking

Implementation record for issue #905.
Worktree: `.claude/worktrees/fix/905-file-editor-scroll-and-blink`
Base: `81b0dc9` (`origin/main`)

## Background
In the CodeMirror 6 file editor, two regressions were noted:
1. When navigating down with arrow keys, the editor viewport didn't scroll to keep the cursor in view.
2. The cursor didn't blink.

## Analysis
1. Scrolling: `<file-page>`, `<file-editor>`, `.file-editor-mount`, and `#wiki-main` had nested/missing overflow and flex sizing rules, causing the editor to stretch to the natural height of the file. `.cm-scroller` had no overflow to scroll, and CodeMirror's `scrollIntoView` had no effect.
2. Blinking: Pico CSS's reduced-motion media query set `animation-iteration-count: 1 !important` across all elements, freezing CodeMirror's `cm-blink` animation. Additionally, CodeMirror's default 1200ms blink rate was too slow.

## Changes
- Updated `#wiki-main`, `file-page`, `file-editor`, `.file-editor-mount`, `.cm-editor`, and `.cm-scroller` layout rules so CodeMirror's `.cm-scroller` is the sole scrolling container with constrained height.
- Set `drawSelection({ cursorBlinkRate: 800 })` and exempt `.cm-cursorLayer` from reduced-motion animation-iteration-count suppression.
- Added tests in `file-editor.test.js`.

## Review Feedback Addressed
- Scoped overflow-y: hidden on `#wiki-main` specifically to `file-page:not(.hidden) file-editor`, ensuring non-editor views like image previews preserve `#wiki-main` scrolling.
- Avoided overriding CodeMirror's animation-name shorthand on `.cm-cursorLayer`, retaining only duration and iteration-count overrides so CodeMirror's alternation between `cm-blink` and `cm-blink2` restarts caret visibility on navigation.
- Added real browser regression test in `tests/test_file_editor_browser.py` verifying scroller advancement and caret bounds in `#wiki-main` hierarchy.

## Verification
- `make test-js`: 402 tests passed across 40 test files (+1 in `file-editor.test.js`).
- `make check-js`: tsc --noEmit passed with 0 errors.
- `uv run ruff check src/ tests/ scripts/ contrib/`: 0 errors.
- `uv run pyright`: 0 errors, 0 warnings.
- `uv run pytest tests/test_web_static_module_graph.py -n 0 -q`: 2 passed.
- `uv run pytest tests/test_file_editor_browser.py -q`: 1 passed.

