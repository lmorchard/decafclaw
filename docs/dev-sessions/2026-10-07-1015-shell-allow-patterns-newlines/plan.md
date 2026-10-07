# Plan: Fix Shell Allow Patterns for Embedded Newlines & Security Monitor Precedence

- **Issue:** #966
- **Worktree:** `.claude/worktrees/fix-966-shell-allow-patterns`
- **Branch:** `fix/966-shell-allow-patterns`

## Phase 1: Metacharacter & Quote Detection Refactoring
- [x] Implement quote-aware `_has_shell_metacharacters()` in `src/decafclaw/tools/shell_tools.py`.
- [x] Add unit test coverage in `tests/test_shell_approval.py` for:
  - Multi-line quoted arguments with single and double quotes.
  - Multi-line commands with trailing/leading unquoted newlines (must reject).
  - Escaped quotes and characters.
  - Command substitution inside double quotes (must reject).
  - Malformed/unclosed quotes (must reject).

## Phase 2: Reorder Approval Precedence in `check_shell_approval()`
- [x] Move explicit allow pattern checks (`preapproved_shell_patterns`, `_load_allow_patterns`) before `evaluate_command_llm()` in `src/decafclaw/tools/shell_tools.py`.
- [x] Add unit tests in `tests/test_shell_approval.py`:
  - Verify that matching an allow pattern does NOT invoke `evaluate_command_llm`.
  - Verify that commands not matching allow patterns still invoke `evaluate_command_llm` and handle `BLOCK` and `ASK`.

## Phase 3: Verification & Quality Gates
- [x] Run test suite: `pytest tests/test_shell_approval.py tests/test_scoped_shell.py tests/test_security_monitor.py`.
- [x] Run quality gates: `ruff format`, `ruff check`, `pyright`.
- [ ] Commit and open Pull Request referencing Issue #966.
- [ ] Open Pull Request referencing Issue #966.
