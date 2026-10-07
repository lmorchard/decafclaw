# Specification: Fix Shell Allow Patterns for Embedded Newlines & Security Monitor Precedence

- **Issue:** #966 (https://github.com/lmorchard/decafclaw/issues/966)
- **Author:** Caffeina
- **Date:** 2026-10-07

## 1. Problem Statement

Commands matching persisted allow patterns (e.g. `gh issue *`, `git commit *`) continue to trigger interactive confirmation prompts in everyday workflows due to two issues in `src/decafclaw/tools/shell_tools.py`:

1. **Embedded Newlines Trip Shell Chaining Guard for Wildcards:**
   `_has_shell_metacharacters()` checks `\n` naively across the entire raw command string. Commands containing quoted multi-line content (e.g. `git commit -m "title\n\nbody"` or `gh issue create --body "line 1\n\nline 2"`) are flagged as having shell chaining tokens. Consequently, wildcard allow patterns (like `git commit *`) are skipped, triggering interactive prompts despite user approval.

2. **Security Monitor Preempts Persisted Allow Patterns:**
   In `check_shell_approval()`, `evaluate_command_llm()` is called before checking `ctx.tools.preapproved`, `ctx.tools.preapproved_shell_patterns`, or `_load_allow_patterns(ctx.config)`. When commands are judged as `SecurityStatus.ASK`, confirmation is requested immediately. Because explicit allow patterns are checked after `evaluate_command_llm()`, previously saved allow patterns cannot auto-approve these commands, requiring confirmation every time.

## 2. Goals & Non-Goals

### Goals
- Differentiate between statement-separating newlines outside quotes and literal newlines embedded inside quoted strings (`"..."` or `'...'`).
- Ensure quote-aware checking prevents command injection/chaining while allowing multi-line string arguments.
- Reorder approval checks in `check_shell_approval()` so explicit allow patterns (`ctx.tools.preapproved`, `ctx.tools.preapproved_shell_patterns`, and persistent `shell_allow_patterns.json`) are checked *before* invoking `evaluate_command_llm()`.
- Auto-approve matching commands without incurring Tier 2 LLM classification latency or repeated confirmation prompts.
- Maintain existing security guarantees: chained commands (e.g., `;`, `&`, `|`, `$(...)`, `` `...` ``, or unquoted `\n`) cannot be smuggled through wildcard patterns.

### Non-Goals
- Full POSIX shell parser / AST generation (a character-level quote/escape state machine is sufficient for token boundaries).
- Bypassing checks when commands are explicitly marked as chained or contain command substitutions like `$(...)` inside double quotes.

## 3. Architecture & Design

### 3.1 Quote-Aware Metacharacter Detection (`_has_shell_metacharacters`)
Replace the naive `any(tok in command for tok in _SHELL_CHAIN_TOKENS)` with a scanning state machine:
- Tracks quote state: `None`, `'` (single quote), `"` (double quote).
- Handles backslash escaping:
  - Outside quotes: `\<newline>` is line continuation (not statement separation). `\X` escapes `X`.
  - Inside double quotes: `\"` escapes quote, `\\` escapes backslash. Backticks and `$(` inside double quotes remain active command substitutions and must be detected as chaining.
  - Inside single quotes: all characters are literal in POSIX shell until the closing `'`.
- Unclosed quotes at end of command return `True` (safe default for malformed commands).
- Outside quotes: detects `;`, `&`, `|`, `` ` ``, `$(` and unquoted `\n`.

### 3.2 Approval Check Precedence (`check_shell_approval`)
Check allow patterns in order:
1. Pre-approved tool names: `if "shell" in ctx.tools.preapproved or tool_name in ctx.tools.preapproved`
2. Scoped shell patterns: `if _command_matches_pattern(command, ctx.tools.preapproved_shell_patterns)`
3. Persisted allow patterns: `patterns = _load_allow_patterns(ctx.config); if _command_matches_pattern(command, patterns)`
4. If no explicit allow pattern matched, proceed to `evaluate_command_llm()`.
5. If `evaluate_command_llm` returns `SecurityStatus.BLOCK`: block.
6. If `evaluate_command_llm` returns `SecurityStatus.ASK`: prompt user for confirmation.
7. If `evaluate_command_llm` passes, proceed to aux-LLM auto-approval / fallback prompt.

## 4. Acceptance Criteria
1. `_has_shell_metacharacters('git commit -m "title\n\nbody"')` returns `False`.
2. `_has_shell_metacharacters("gh issue create --body 'line1\nline2'")` returns `False`.
3. `_has_shell_metacharacters('git commit -m "title\n\nbody"; rm -rf ~')` returns `True`.
4. `_has_shell_metacharacters('git commit -m "title"\nrm -rf ~')` returns `True`.
5. `_has_shell_metacharacters('git commit -m "msg $(whoami)"')` returns `True`.
6. `_has_shell_metacharacters('git commit -m "msg `whoami`"')` returns `True`.
7. `check_shell_approval()` auto-approves commands matching allow patterns without calling `evaluate_command_llm()`.
8. All existing unit tests pass without regressions.
9. `make check` (formatting, linting, typechecking) passes cleanly.
