# feat-1137-background-progress-widgets

## Resume here

- Issue: https://github.com/lmorchard/decafclaw/issues/1137
- Worktree: /home/lmorchard/devel/decafclaw/.claude/worktrees/feat-1137-background-progress-widgets
- Branch: feat-1137-background-progress-widgets
- Base: origin/main at 875f2b7ab70a001000b0f8d67814d57ed8a91dd2
- Next action: PR #1177 updated with review fixes.

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
- Published draft PR #1177 at commit `b5a37e1485bd1c3453c143ccec2798b3eddd0c92`.
- Addressed review findings from /tmp/findings-1137.md:
  - Finding 1: Extracted pytest summary counts using flexible word matches (`\b(\d+)\s+passed\b`, etc.) so reordered summary lines (`failed` before `passed`) do not drop passed counts.
  - Finding 2: In `PytestParser.format_running_sticky`, computed `current` considering `self.pct` when `total_tests` is known, and formatted note as `{current}/{total_tests} ({pct}%)` when `executed == 0`.
  - Finding 3: In `AutoParser.feed_line`, included `skipped` in pytest detection check (`passed + failed + skipped > 0`).
  - Note 1: In `StepParser.feed_line`, updated `self.step_label = after` so unlabeled steps reset previous labels.
  - Added regression unit tests for all findings and notes in `tests/test_background_tools.py`.
- Addressed Copilot review findings from /tmp/findings-1177-copilot.md:
  - Finding 1: In `_read_stream`, framed streaming chunks on both `\r` and `\n` using per-stream pending buffers; in `parsers.py`, `feed_line` splits on `[\r\n]+` so in-place carriage returns feed separate progress records.
  - Finding 2: Supported undecorated pytest summaries (`pytest -q`, e.g. `4 passed in 0.10s`).
  - Finding 3: Handled pytest collection with deselected items (`collected 50 items / 40 deselected / 10 selected`) to use the selected count as `total_tests`.
  - Finding 4: Supported xdist verbose outcome-first lines (`[gw0] [ 50%] FAILED tests/test_a.py::test_one`).
  - Finding 5: Added auto-parser re-detection when active preset stops matching or another preset matches.
  - Finding 6: Set `last["t"] = now` on trailing updates in `_sticky_publish_when_due` to preserve the 500ms minimum interval.
  - Added unit and integration tests covering all findings in `tests/test_background_tools.py`.
- Ran `make fmt`, `make check`, `make test`, and `make test-js`. All passed.
