# Issue 1193: diff row spacing

## Resume here

- Issue: https://github.com/lmorchard/decafclaw/issues/1193
- Worktree: `.claude/worktrees/issue-1193-diff-row-spacing`
- Branch: `issue-1193-diff-row-spacing`
- Base: `a9b6865917983aa2bbe36104baaafcdf2f02b134` (fetched `origin/main`)
- Scope: shared diff layout only; no payload, mutation, fallback, or large-input changes.
- Implementation: compact unified row templates to avoid preserved template whitespace inside `pre`.
- Model identity: runtime states GPT-6; a more precise ID is unavailable.
- Next: finish final checks, commit, publish draft; independent review belongs to coordinator. No merge authorized.

## Findings and verification

- Worktree created with `uv sync` and copied `.env`; local HTTP port set to 18993. Board moved Ready → In progress, read-back matched.
- Baseline `make test`: 5015 passed, 2 skipped. Initial sandbox invocation could not open uv cache; reran with approved host access.
- Test-first browser regression loaded the real widget, Pico, and app CSS. On unchanged renderer it failed specifically on a 61.078125 CSS pixel gap between adjacent unified rows (allowed maximum: 1px). Log: `/tmp/ghflow-1193-red.log`.
- Same test passed after compacting only the unified row template. It checks both inline and canvas modes; unified → split → unified switching; exact contents/markers including a real blank line, indentation and repeated spaces; contiguous equal-height short rows; split empty-cell alignment; syntax highlighting and distinct added/removed colors; long-line content, wrapping, and existing horizontal overflow policy.
- `make fmt` passed (formatted the new test). `make check` passed, including pyright with 0 errors/0 warnings, JS typechecking and static module graph. Pyright printed an upstream-version advisory. Log: `/tmp/ghflow-1193-check.log`.
- Reviewed issue scope and implementation diff: source data and existing highlighting/wrapping code are unchanged. No feature documentation change is needed for this layout correction. No LLM eval is needed for browser-only layout.
- Final `make test`: 5016 passed, 2 skipped. Relevant existing tests plus new browser test: 4 passed; new browser call 0.66s under `--durations=25`, with no fixed sleeps. Logs: `/tmp/ghflow-1193-test.log`, `/tmp/ghflow-1193-targeted.log`.
- `make test-js`: 55 files / 609 tests passed. Log: `/tmp/ghflow-1193-test-js.log`. npm prints existing dependency-audit notices; dependency changes are out of scope.
