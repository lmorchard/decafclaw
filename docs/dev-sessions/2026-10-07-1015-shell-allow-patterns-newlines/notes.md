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
1. **Interpreter Wildcards & Process Substitution:**
   - Categorically prohibited wildcard patterns for shell interpreters and execution wrappers (`sh`, `bash`, `zsh`, `dash`, `ksh`, `eval`, `exec`, `command`, `sudo`, `env`, etc.) in `_is_ineligible_wildcard_pattern()`, requiring exact literal approvals for specific scripts.
   - Prohibited wildcarding of interpreter and wrapper commands in `_suggest_pattern()`.
   - In `_has_shell_metacharacters()`, added detection for process substitutions (`<()` and `>()`) both unquoted and inside double quotes.
   - In `_load_allow_patterns()`, ensured malformed sidecar contents safely satisfy `list[str]` contract without crashing.
2. **Spec Scoping:**
   - Updated `spec.md` to specify that only scoped and persisted patterns precede `evaluate_command_llm()`, matching the implementation and security boundary.
3. **Documentation:**
   - Updated `docs/tools.md` to document the security monitor stages, approval hierarchy, quote-aware token scanning, and interpreter wildcard restrictions.
