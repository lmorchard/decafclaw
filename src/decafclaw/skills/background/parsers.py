"""Deterministic log output parsers for background process progress tracking.

Extracts structured progress (steps, percentages, test counts) from ordinary
streaming command output (stdout/stderr) to update live widgets without
conversational turns or per-update LLM calls (#1137).
"""

import logging
import re
from dataclasses import dataclass

log = logging.getLogger(__name__)

# ANSI escape sequence stripper: matches CSI, OSC, and basic escape sequences.
_ANSI_ESCAPE_RE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")

# Max line length processed by regexes to avoid pathological ReDoS or memory spikes.
_MAX_LINE_LENGTH = 2000

# Max length of notes in the sticky slot.
_STICKY_NOTE_MAX = 200


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences and trailing carriage returns."""
    cleaned = _ANSI_ESCAPE_RE.sub("", text)
    return cleaned.replace("\r", "")


def cmd_preview(command: str) -> str:
    """Short command preview for sticky-slot labels."""
    return command[:80] + ("..." if len(command) > 80 else "")


@dataclass
class ProgressUpdate:
    """Represents a point-in-time progress snapshot derived from log output."""

    current: int | float | None = None
    total: int | float | None = None
    label: str = ""
    status: str = "in_progress"  # in_progress, done, failed
    unit: str = ""  # steps, %, tests, lines


class BaseProgressParser:
    """Base interface for background log progress parsers."""

    def __init__(self) -> None:
        self.line_count: int = 0
        self.last_line: str = ""

    def feed_line(self, raw_line: str, stream: str = "stdout") -> ProgressUpdate | None:
        """Process a new line of output. Returns an update if progress changed."""
        raise NotImplementedError

    def get_progress(self) -> ProgressUpdate:
        """Return the latest progress update."""
        raise NotImplementedError

    def format_running_sticky(self, command: str) -> dict:
        """Format a progress_tracker widget payload for a running job."""
        raise NotImplementedError

    def format_final_sticky(self, command: str, exit_code: int | None, status: str) -> dict:
        """Format a progress_tracker widget payload for a completed/failed job."""
        raise NotImplementedError


class NoneParser(BaseProgressParser):
    """Raw line counter fallback with no structured parsing."""

    def feed_line(self, raw_line: str, stream: str = "stdout") -> ProgressUpdate | None:
        self.line_count += 1
        cleaned = strip_ansi(raw_line).strip()
        if cleaned:
            self.last_line = cleaned
        return self.get_progress()

    def get_progress(self) -> ProgressUpdate:
        return ProgressUpdate(
            current=self.line_count,
            total=None,
            label=self.last_line,
            status="in_progress",
            unit="lines",
        )

    def format_running_sticky(self, command: str) -> dict:
        last = self.last_line[:_STICKY_NOTE_MAX]
        step: dict[str, str] = {"label": cmd_preview(command), "status": "in_progress"}
        if last:
            step["note"] = last
        summary = last or f"Running: {cmd_preview(command)}"
        return {
            "title": "Background job",
            "summary": summary,
            "steps": [step],
        }

    def format_final_sticky(self, command: str, exit_code: int | None, status: str) -> dict:
        step_status = "done" if status == "completed" else "failed"
        action = "Completed" if status == "completed" else "Failed"
        summary = f"{action}: {cmd_preview(command)} (exit {exit_code})"
        note = f"exit {exit_code}"
        if self.last_line and status != "completed":
            note += f" — {self.last_line[:120]}"
        return {
            "title": "Background job",
            "summary": summary,
            "steps": [{"label": cmd_preview(command), "status": step_status, "note": note}],
        }


class StepParser(BaseProgressParser):
    """Matches [N/M], (N/M), or Step N of M to extract current and total steps."""

    # Matches: [1/10], (2/5), Step 3 of 10, step 4/12
    _STEP_RE = re.compile(
        r"(?:\[\s*(\d+)\s*/\s*(\d+)\s*\]|\(\s*(\d+)\s*/\s*(\d+)\s*\)|[Ss]tep\s+(\d+)\s*(?:of|/)\s*(\d+))"
    )

    def __init__(self) -> None:
        super().__init__()
        self.current_step: int | None = None
        self.total_steps: int | None = None
        self.step_label: str = ""

    def feed_line(self, raw_line: str, stream: str = "stdout") -> ProgressUpdate | None:
        self.line_count += 1
        cleaned = strip_ansi(raw_line)
        truncated = cleaned[:_MAX_LINE_LENGTH]
        if cleaned.strip():
            self.last_line = cleaned.strip()

        match = self._STEP_RE.search(truncated)
        if match:
            groups = [g for g in match.groups() if g is not None]
            if len(groups) >= 2:
                try:
                    c, t = int(groups[0]), int(groups[1])
                    if t > 0:
                        self.current_step = c
                        self.total_steps = t
                        # Remainder of the line after match as label
                        after = truncated[match.end() :].strip()
                        # Clean leading separators like ':', '-', '...'
                        after = re.sub(r"^[:\-\s]+", "", after)
                        if after:
                            self.step_label = after
                except (ValueError, OverflowError):
                    pass

        return self.get_progress()

    def get_progress(self) -> ProgressUpdate:
        if self.current_step is not None and self.total_steps is not None:
            return ProgressUpdate(
                current=self.current_step,
                total=self.total_steps,
                label=self.step_label or self.last_line,
                status="in_progress",
                unit="steps",
            )
        return ProgressUpdate(
            current=self.line_count,
            total=None,
            label=self.last_line,
            status="in_progress",
            unit="lines",
        )

    def format_running_sticky(self, command: str) -> dict:
        if self.current_step is not None and self.total_steps is not None:
            progress_str = f"[{self.current_step}/{self.total_steps}]"
            label = self.step_label or cmd_preview(command)
            summary = f"{progress_str} {label}" if self.step_label else f"{progress_str} {cmd_preview(command)}"
            note = f"{progress_str} {self.step_label}" if self.step_label else progress_str
            return {
                "title": "Background job",
                "summary": summary[:_STICKY_NOTE_MAX],
                "steps": [{"label": cmd_preview(command), "status": "in_progress", "note": note[:_STICKY_NOTE_MAX]}],
            }
        # Fallback to line counting
        last = self.last_line[:_STICKY_NOTE_MAX]
        step: dict[str, str] = {"label": cmd_preview(command), "status": "in_progress"}
        if last:
            step["note"] = last
        return {
            "title": "Background job",
            "summary": last or f"Running: {cmd_preview(command)}",
            "steps": [step],
        }

    def format_final_sticky(self, command: str, exit_code: int | None, status: str) -> dict:
        step_status = "done" if status == "completed" else "failed"
        action = "Completed" if status == "completed" else "Failed"
        summary = f"{action}: {cmd_preview(command)} (exit {exit_code})"
        note = f"exit {exit_code}"
        if self.current_step is not None and self.total_steps is not None:
            note += f" — [{self.current_step}/{self.total_steps}]"
        elif self.last_line and status != "completed":
            note += f" — {self.last_line[:120]}"
        return {
            "title": "Background job",
            "summary": summary,
            "steps": [{"label": cmd_preview(command), "status": step_status, "note": note}],
        }


class PercentParser(BaseProgressParser):
    """Matches (\\d+)% for percentage-based progress when total is unknown."""

    _PERCENT_RE = re.compile(r"\b(\d{1,3}(?:\.\d+)?)\s*%")

    def __init__(self) -> None:
        super().__init__()
        self.percent: float | None = None
        self.percent_label: str = ""

    def feed_line(self, raw_line: str, stream: str = "stdout") -> ProgressUpdate | None:
        self.line_count += 1
        cleaned = strip_ansi(raw_line)
        truncated = cleaned[:_MAX_LINE_LENGTH]
        if cleaned.strip():
            self.last_line = cleaned.strip()

        match = self._PERCENT_RE.search(truncated)
        if match:
            try:
                val = float(match.group(1))
                if 0.0 <= val <= 100.0:
                    self.percent = val
                    # Extract label around or after percentage
                    after = truncated[match.end() :].strip()
                    before = truncated[: match.start()].strip()
                    label = after or before
                    label = re.sub(r"^[:\-\s]+", "", label)
                    if label:
                        self.percent_label = label
            except (ValueError, OverflowError):
                pass

        return self.get_progress()

    def get_progress(self) -> ProgressUpdate:
        if self.percent is not None:
            pct_display = int(self.percent) if self.percent.is_integer() else self.percent
            return ProgressUpdate(
                current=pct_display,
                total=100,
                label=self.percent_label or self.last_line,
                status="in_progress",
                unit="%",
            )
        return ProgressUpdate(
            current=self.line_count,
            total=None,
            label=self.last_line,
            status="in_progress",
            unit="lines",
        )

    def format_running_sticky(self, command: str) -> dict:
        if self.percent is not None:
            pct_str = f"{int(self.percent) if self.percent.is_integer() else self.percent}%"
            label = self.percent_label or cmd_preview(command)
            summary = f"{pct_str} · {label}" if self.percent_label else f"{pct_str} · {cmd_preview(command)}"
            note = f"{pct_str} — {self.percent_label}" if self.percent_label else pct_str
            return {
                "title": "Background job",
                "summary": summary[:_STICKY_NOTE_MAX],
                "steps": [{"label": cmd_preview(command), "status": "in_progress", "note": note[:_STICKY_NOTE_MAX]}],
            }
        last = self.last_line[:_STICKY_NOTE_MAX]
        step: dict[str, str] = {"label": cmd_preview(command), "status": "in_progress"}
        if last:
            step["note"] = last
        return {
            "title": "Background job",
            "summary": last or f"Running: {cmd_preview(command)}",
            "steps": [step],
        }

    def format_final_sticky(self, command: str, exit_code: int | None, status: str) -> dict:
        step_status = "done" if status == "completed" else "failed"
        action = "Completed" if status == "completed" else "Failed"
        summary = f"{action}: {cmd_preview(command)} (exit {exit_code})"
        note = f"exit {exit_code}"
        if self.percent is not None:
            pct_str = f"{int(self.percent) if self.percent.is_integer() else self.percent}%"
            note += f" — {pct_str}"
        elif self.last_line and status != "completed":
            note += f" — {self.last_line[:120]}"
        return {
            "title": "Background job",
            "summary": summary,
            "steps": [{"label": cmd_preview(command), "status": step_status, "note": note}],
        }


class PytestParser(BaseProgressParser):
    """Matches collected test count and live pass/fail counts to track test execution."""

    # Matches pytest collection lines:
    # "collected 39 items" or "collected 1 item" or "8 workers [39 items]"
    _COLLECTED_RE = re.compile(r"(?:collected\s+(\d+)\s+items?|\[\s*(\d+)\s+items\s*\])")

    # Matches test outcome lines:
    # "tests/test_foo.py::test_bar PASSED [ 25%]"
    # "tests/test_foo.py::test_bar FAILED [ 50%]"
    # "tests/test_foo.py::test_bar SKIPPED"
    _TEST_OUTCOME_RE = re.compile(r"(\S+::\S+)\s+(PASSED|FAILED|SKIPPED|XFAIL|XPASS|ERROR)(?:\s+\[\s*(\d+)%\])?")

    # Matches pytest percentage in progress line:
    # "tests/test_background_tools.py ...... [ 50%]"
    _PROGRESS_PCT_RE = re.compile(r"\[\s*(\d+)%\s*\]")

    # Matches final summary line:
    # "=== 35 passed, 2 failed in 1.23s ==="
    _SUMMARY_RE = re.compile(
        r"===+\s+(?:(\d+)\s+passed)?(?:,\s*)?(?:(\d+)\s+failed)?(?:,\s*)?(?:(\d+)\s+skipped)?.*?===+"
    )

    def __init__(self) -> None:
        super().__init__()
        self.total_tests: int | None = None
        self.passed: int = 0
        self.failed: int = 0
        self.skipped: int = 0
        self.current_test: str = ""
        self.pct: int | None = None

    def feed_line(self, raw_line: str, stream: str = "stdout") -> ProgressUpdate | None:
        self.line_count += 1
        cleaned = strip_ansi(raw_line)
        truncated = cleaned[:_MAX_LINE_LENGTH]
        if cleaned.strip():
            self.last_line = cleaned.strip()

        # 1. Check collection
        coll_match = self._COLLECTED_RE.search(truncated)
        if coll_match:
            try:
                val = coll_match.group(1) or coll_match.group(2)
                if val:
                    self.total_tests = int(val)
            except (ValueError, OverflowError):
                pass

        # 2. Check individual test result
        outcome_match = self._TEST_OUTCOME_RE.search(truncated)
        if outcome_match:
            test_name = outcome_match.group(1)
            outcome = outcome_match.group(2)
            pct_str = outcome_match.group(3)
            self.current_test = test_name
            if outcome == "PASSED":
                self.passed += 1
            elif outcome in ("FAILED", "ERROR"):
                self.failed += 1
            elif outcome in ("SKIPPED", "XFAIL", "XPASS"):
                self.skipped += 1

            if pct_str:
                try:
                    self.pct = int(pct_str)
                except ValueError:
                    pass
        else:
            # 3. Check progress percentage if line has [ N%] without full test name
            pct_match = self._PROGRESS_PCT_RE.search(truncated)
            if pct_match:
                try:
                    self.pct = int(pct_match.group(1))
                except ValueError:
                    pass

        # 4. Check terminal summary line
        sum_match = self._SUMMARY_RE.search(truncated)
        if sum_match:
            p_str, f_str, s_str = sum_match.group(1), sum_match.group(2), sum_match.group(3)
            if p_str:
                self.passed = int(p_str)
            if f_str:
                self.failed = int(f_str)
            if s_str:
                self.skipped = int(s_str)

        return self.get_progress()

    def get_progress(self) -> ProgressUpdate:
        executed = self.passed + self.failed + self.skipped
        current = executed
        if self.total_tests is not None:
            # If percentage gives a higher or more accurate estimate
            if self.pct is not None and self.total_tests > 0:
                pct_count = int(round(self.total_tests * self.pct / 100))
                current = max(executed, pct_count)
            return ProgressUpdate(
                current=current,
                total=self.total_tests,
                label=self.current_test or self.last_line,
                status="failed" if self.failed > 0 else "in_progress",
                unit="tests",
            )
        if executed > 0:
            return ProgressUpdate(
                current=executed,
                total=None,
                label=self.current_test or self.last_line,
                status="failed" if self.failed > 0 else "in_progress",
                unit="tests",
            )
        return ProgressUpdate(
            current=self.line_count,
            total=None,
            label=self.last_line,
            status="in_progress",
            unit="lines",
        )

    def format_running_sticky(self, command: str) -> dict:
        executed = self.passed + self.failed + self.skipped
        if self.total_tests is not None or executed > 0:
            total_part = f"/{self.total_tests}" if self.total_tests is not None else ""
            counts_part = f"{self.passed} passed"
            if self.failed > 0:
                counts_part += f", {self.failed} failed"
            if self.skipped > 0:
                counts_part += f", {self.skipped} skipped"

            summary = f"pytest: {executed}{total_part} ({counts_part})"
            if self.current_test:
                summary += f" · {self.current_test}"

            note = f"{executed}{total_part} ({counts_part})"
            if self.current_test:
                note += f" — {self.current_test}"

            return {
                "title": "Background job",
                "summary": summary[:_STICKY_NOTE_MAX],
                "steps": [{"label": cmd_preview(command), "status": "in_progress", "note": note[:_STICKY_NOTE_MAX]}],
            }

        last = self.last_line[:_STICKY_NOTE_MAX]
        step: dict[str, str] = {"label": cmd_preview(command), "status": "in_progress"}
        if last:
            step["note"] = last
        return {
            "title": "Background job",
            "summary": last or f"Running: {cmd_preview(command)}",
            "steps": [step],
        }

    def format_final_sticky(self, command: str, exit_code: int | None, status: str) -> dict:
        step_status = "done" if status == "completed" else "failed"
        action = "Completed" if status == "completed" else "Failed"
        summary = f"{action}: {cmd_preview(command)} (exit {exit_code})"
        note = f"exit {exit_code}"

        executed = self.passed + self.failed + self.skipped
        if executed > 0:
            counts_part = f"{self.passed} passed"
            if self.failed > 0:
                counts_part += f", {self.failed} failed"
            if self.skipped > 0:
                counts_part += f", {self.skipped} skipped"
            note += f" — {counts_part}"
        elif self.last_line and status != "completed":
            note += f" — {self.last_line[:120]}"

        return {
            "title": "Background job",
            "summary": summary,
            "steps": [{"label": cmd_preview(command), "status": step_status, "note": note}],
        }


class AutoParser(BaseProgressParser):
    """Attempts presets in order, falling back to line count on unrecognized output."""

    def __init__(self) -> None:
        super().__init__()
        self.pytest_parser = PytestParser()
        self.step_parser = StepParser()
        self.percent_parser = PercentParser()
        self.none_parser = NoneParser()
        self._active_preset: str | None = None

    def feed_line(self, raw_line: str, stream: str = "stdout") -> ProgressUpdate | None:
        self.line_count += 1
        cleaned = strip_ansi(raw_line).strip()
        if cleaned:
            self.last_line = cleaned

        # Always update line counter fallback
        self.none_parser.feed_line(raw_line, stream=stream)

        # 1. If a preset has locked in, update it
        if self._active_preset == "pytest":
            self.pytest_parser.feed_line(raw_line, stream=stream)
            return self.get_progress()
        if self._active_preset == "step":
            self.step_parser.feed_line(raw_line, stream=stream)
            return self.get_progress()
        if self._active_preset == "percent":
            self.percent_parser.feed_line(raw_line, stream=stream)
            return self.get_progress()

        # 2. Try detection in order: pytest -> step -> percent
        try:
            self.pytest_parser.feed_line(raw_line, stream=stream)
            if (
                self.pytest_parser.total_tests is not None
                or (self.pytest_parser.passed + self.pytest_parser.failed) > 0
            ):
                self._active_preset = "pytest"
                return self.get_progress()
        except Exception:
            log.debug("AutoParser: pytest probe failed", exc_info=True)

        try:
            self.step_parser.feed_line(raw_line, stream=stream)
            if self.step_parser.current_step is not None and self.step_parser.total_steps is not None:
                self._active_preset = "step"
                return self.get_progress()
        except Exception:
            log.debug("AutoParser: step probe failed", exc_info=True)

        try:
            self.percent_parser.feed_line(raw_line, stream=stream)
            if self.percent_parser.percent is not None:
                self._active_preset = "percent"
                return self.get_progress()
        except Exception:
            log.debug("AutoParser: percent probe failed", exc_info=True)

        return self.get_progress()

    def get_progress(self) -> ProgressUpdate:
        if self._active_preset == "pytest":
            return self.pytest_parser.get_progress()
        if self._active_preset == "step":
            return self.step_parser.get_progress()
        if self._active_preset == "percent":
            return self.percent_parser.get_progress()
        return self.none_parser.get_progress()

    def format_running_sticky(self, command: str) -> dict:
        if self._active_preset == "pytest":
            return self.pytest_parser.format_running_sticky(command)
        if self._active_preset == "step":
            return self.step_parser.format_running_sticky(command)
        if self._active_preset == "percent":
            return self.percent_parser.format_running_sticky(command)
        return self.none_parser.format_running_sticky(command)

    def format_final_sticky(self, command: str, exit_code: int | None, status: str) -> dict:
        if self._active_preset == "pytest":
            return self.pytest_parser.format_final_sticky(command, exit_code, status)
        if self._active_preset == "step":
            return self.step_parser.format_final_sticky(command, exit_code, status)
        if self._active_preset == "percent":
            return self.percent_parser.format_final_sticky(command, exit_code, status)
        return self.none_parser.format_final_sticky(command, exit_code, status)


def get_progress_parser(name: str = "auto") -> BaseProgressParser:
    """Return an instantiated progress parser matching name."""
    lowered = (name or "auto").strip().lower()
    if lowered == "step":
        return StepParser()
    if lowered == "percent":
        return PercentParser()
    if lowered == "pytest":
        return PytestParser()
    if lowered == "none":
        return NoneParser()
    return AutoParser()
