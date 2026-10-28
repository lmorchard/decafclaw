# issue-1150-consolidate-theme-controls

## Resume here

- Issue: https://github.com/lmorchard/decafclaw/issues/1150
- Worktree: /home/lmorchard/devel/decafclaw/.claude/worktrees/issue-1150-consolidate-theme-controls
- Branch: issue-1150-consolidate-theme-controls
- Base: origin/main at 45c54f8166ea091e715713c454aac631a11cb074
- Implementation and verification complete.

## Changes

- Consolidated the separate light, dark, system, and palette buttons into a single `<theme-toggle>` control.
- The trigger button displays the active theme's icon (`☀️`, `🌙`, `💻` for base modes, `🎨` for palettes) with `title="Theme: ${label}"` and `aria-label="Select theme"`.
- The upward-opening disclosure menu unifies both sections:
  - Base modes: Light, Dark, System
  - Divider
  - Color Palettes: Dracula, Solarized Light, Solarized Dark, Nord, etc.
- `theme-toggle.test.js` updated with tests for initial trigger render, menu opening, selection of base and palette themes, Escape key focus return, and click-outside dismissal.
- `sidebar.css` styled with `.theme-palette-icon`, `.theme-menu-divider`, and `.theme-menu-section-header`.

## Verification

- `make check`: exit 0 (npm policy, message types, ruff format check, ruff lint, pyright, API client generation, static module graph).
- `make test-js`: 557 passed (50 test files).
- Browser screenshot verification in Playwright confirming appearance, menu opening, and theme switching.
