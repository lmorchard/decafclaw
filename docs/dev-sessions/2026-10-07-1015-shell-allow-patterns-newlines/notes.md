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
- Unit test suite: 82 passed (`test_shell_approval.py`, `test_scoped_shell.py`, `test_security_monitor.py`).
- Ruff formatting check: passed cleanly.
- Ruff linter check: passed cleanly (0 errors).
- Pyright static analysis: passed cleanly (0 errors, 0 warnings).

## PR Review Address (Copilot Review Feedback)
1. **Nested Interpreter Execution:**
   - In `_has_shell_metacharacters()`, added recursive inspection of script arguments for commands using `eval` or `sh/bash/zsh/dash/ksh -c` so quoted chaining operators cannot bypass confirmation in a second parsing pass.
   - In `_command_matches_pattern()`, added `_is_ineligible_wildcard_pattern()` to reject wildcard patterns for `sh -c *` and `eval *`.
   - In `_suggest_pattern()`, prohibited wildcarding for interpreter commands (`sh -c`, `eval`), retaining literal command strings instead.
2. **Spec Scoping:**
   - Updated `spec.md` to specify that only scoped and persisted patterns precede `evaluate_command_llm()`, matching the implementation and security boundary.
3. **Documentation:**
   - Updated `docs/tools.md` to document the security monitor stages, approval hierarchy, quote-aware token scanning, and nested interpreter protections.
