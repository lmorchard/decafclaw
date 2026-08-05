"""Session output logger — writes OpenCode JSON lines to JSONL and builds summaries."""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

class SessionLogger:
    """Writes OpenCode JSON output to a JSONL log file and tracks metrics."""

    def __init__(self, log_dir: Path, session_id: str):
        self.path = log_dir / f"{session_id}.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.files_changed: list[str] = []
        self.tools_used: list[str] = []
        self.total_cost_usd: float = 0
        self.duration_ms: int = 0
        self.errors: list[str] = []
        self.result_text: str = ""
        self.num_turns: int = 0
        self._start_time = None

    def log_event(self, event: dict) -> None:
        """Parse an OpenCode JSON event and track metrics."""
        with open(self.path, "a") as f:
            f.write(json.dumps(event) + "\n")

        evt_type = event.get("type")
        part = event.get("part", {})

        if evt_type == "step_start":
            self.num_turns += 1
            if self._start_time is None:
                self._start_time = event.get("timestamp")

        elif evt_type == "tool_use":
            tool_name = part.get("tool", "unknown")
            self.tools_used.append(tool_name)
            tool_input = part.get("state", {}).get("input", {})
            self._track_file_change(tool_name, tool_input)

            # Check if there is an error in the tool state
            state_status = part.get("state", {}).get("status")
            if state_status == "error":
                err_msg = part.get("state", {}).get("output", "")
                if err_msg:
                    self.errors.append(str(err_msg)[:200])

        elif evt_type == "step_finish":
            cost = part.get("cost")
            if cost is not None:
                self.total_cost_usd += cost
            
            # Record duration
            end_time = event.get("timestamp")
            if self._start_time and end_time:
                self.duration_ms = max(self.duration_ms, int(end_time - self._start_time))

        elif evt_type == "text":
            text = part.get("text", "")
            if text:
                # Keep accumulating result text, or just track the last block
                # OpenCode usually outputs the final answer in a text block at the end
                self.result_text += text

    def build_summary(self, session_id: str = "") -> str:
        """Build a concise summary string for the LLM."""
        parts = []

        cost_str = f"${self.total_cost_usd:.2f}" if self.total_cost_usd else "no cost data"
        if session_id:
            parts.append(f"**OpenCode completed** (session {session_id[:8]}, {cost_str})")
        else:
            parts.append(f"**OpenCode completed** ({cost_str})")

        unique_files = list(dict.fromkeys(self.files_changed))
        if unique_files:
            parts.append(f"- Files changed: {', '.join(unique_files)}")

        if self.tools_used:
            tool_summary = ", ".join(self.tools_used)
            parts.append(f"- {len(self.tools_used)} tool call(s): {tool_summary}")
        else:
            parts.append("- No tool calls")

        if self.errors:
            parts.append(f"- Errors: {len(self.errors)}")
            for err in self.errors[:3]:
                parts.append(f"  - {err[:200]}")

        if self.duration_ms:
            secs = self.duration_ms / 1000
            parts.append(f"- Duration: {secs:.1f}s")

        parts.append(f"- Full log: {self.path}")

        if self.result_text and not self.errors:
            preview = self.result_text[:500]
            if len(self.result_text) > 500:
                preview += "..."
            parts.append(f"\n**Result:**\n{preview}")

        return "\n".join(parts)

    def build_data(self, session_id: str = "", exit_status: str = "success",
                   sdk_session_id: str | None = None, send_count: int = 0,
                   diff: str | None = None) -> dict:
        from collections import Counter
        tool_counts = dict(Counter(self.tools_used))
        unique_files = list(dict.fromkeys(self.files_changed))
        errors = [{"message": e} for e in self.errors[:10]]
        return {
            "exit_status": exit_status,
            "files_changed": unique_files,
            "tools_used": tool_counts,
            "errors": errors,
            "cost_usd": self.total_cost_usd,
            "duration_ms": self.duration_ms,
            "send_count": send_count,
            "num_turns": self.num_turns,
            "result_text": self.result_text[:500] if self.result_text else "",
            "result_text_truncated": len(self.result_text) > 500,
            "sdk_session_id": sdk_session_id or "",
            "log_path": str(self.path),
            "diff": diff,
        }

    def log_exec(self, command: str, exit_code: int | None,
                 stdout: str, stderr: str, duration_ms: int) -> None:
        record = {
            "type": "exec",
            "command": command,
            "exit_code": exit_code,
            "stdout": stdout,
            "stderr": stderr,
            "duration_ms": duration_ms,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        with open(self.path, "a") as f:
            f.write(json.dumps(record) + "\n")

    def _track_file_change(self, tool_name: str, tool_input: dict) -> None:
        if tool_name in ("Edit", "Write", "NotebookEdit", "write", "edit", "replace"):
            # OpenCode might use different property names, e.g. "filePath" or "path"
            file_path = tool_input.get("filePath", tool_input.get("file_path", ""))
            if file_path and file_path not in self.files_changed:
                self.files_changed.append(file_path)

