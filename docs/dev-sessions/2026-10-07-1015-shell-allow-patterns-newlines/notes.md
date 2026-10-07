# Dev Session Notes: Issue #966

- **Issue:** #966
- **Started:** 2026-10-07 10:15
- **Branch:** `fix/966-shell-allow-patterns`

## Findings & Design Decisions
- Clarified design with user: explicit allow patterns should bypass LLM latency for known commands.
- `_has_shell_metacharacters()` needs to handle:
  - Single quotes: literal content until closing `'`.
  - Double quotes: literal newlines/semicolons/pipes/ampersands; command substitution (`$()`, `` ` ``) remains active.
  - Backslash: escape next character; `\<newline>` is line continuation outside quotes.
  - Unclosed quotes: treated as unsafe (returns `True`).
- Precedence in `check_shell_approval()`:
  - Catastrophic danger checks (Tier 1 regex) run first: blocks `rm -rf /`, `mkfs`, fork bomb.
  - Explicit allow checks run second (`ctx.tools.preapproved_shell_patterns`, `_load_allow_patterns(ctx.config)`).
  - Security monitor `evaluate_command_llm()` only called if not explicitly allowlisted.
  - Blanket tool pre-approval (`ctx.tools.preapproved = ["shell"]`) evaluated after security monitor, ensuring unvetted scripts cannot run unapproved package installations or sensitive actions without confirmation.

## Quality Gates & Verification
- Unit test suite: 79 passed (`test_shell_approval.py`, `test_scoped_shell.py`, `test_security_monitor.py`).
- Ruff formatting check: passed cleanly.
- Ruff linter check: passed cleanly (0 errors).
- Pyright static analysis: passed cleanly (0 errors, 0 warnings).
