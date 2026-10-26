# feat-1137-background-progress-widgets

## Resume here

- Issue: https://github.com/lmorchard/decafclaw/issues/1137
- Worktree: /home/lmorchard/devel/decafclaw/.claude/worktrees/feat-1137-background-progress-widgets
- Branch: feat-1137-background-progress-widgets
- Base: origin/main at 875f2b7ab70a001000b0f8d67814d57ed8a91dd2
- Next action: publish draft PR and submit handoff.

## Log

- Prepared worktree branched from origin/main at 875f2b7ab70a001000b0f8d67814d57ed8a91dd2.
- Created `src/decafclaw/skills/background/parsers.py` implementing declarative log output parsers:
  - `ProgressUpdate` dataclass (`current`, `total`, `label`, `status`, `unit`).
  - `strip_ansi` to strip ANSI escape codes and carriage returns.
  - `BaseProgressParser`, `NoneParser`, `StepParser`, `PercentParser`, `PytestParser`, and `AutoParser`.
  - Preset parser factory `get_progress_parser`.
- Updated `src/decafclaw/skills/background/tools.py`:
  - Added `progress_parser` and `parser` to `BackgroundJob`.
  - Added `progress_parser` parameter to `BackgroundJobManager.start` and `tool_shell_background_start` (defaulting to `"auto"`).
  - Added `progress_parser` schema to `TOOL_DEFINITIONS`.
  - Updated reader to hook stdout and stderr streams into `job.parser.feed_line(line, stream=stream)`.
  - Formatted live sticky updates and exit updates using parser methods (`format_running_sticky` and `format_final_sticky`) with fail-open safety.
- Added comprehensive unit and integration tests in `tests/test_background_tools.py`:
  - Parser unit tests for each preset and format.
  - Integration tests verifying sticky slot state transitions for step, percent, pytest, and auto presets.
  - Interleaved stdout/stderr, malformed line safety, and factory verification.
- Updated documentation in `docs/background-wake.md` and `docs/widgets.md`.
- Ran gate checks: `make fmt`, `make check`, `make test`, and `make test-js`. All passed.
