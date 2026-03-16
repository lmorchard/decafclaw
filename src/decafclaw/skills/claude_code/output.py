"""Session output logger — writes SDK messages to JSONL and builds summaries."""

import json
import logging
from pathlib import Path

from claude_code_sdk import (
    AssistantMessage,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

log = logging.getLogger(__name__)


class SessionLogger:
    """Writes Claude Code SDK output to a JSONL log file and tracks metrics."""

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

    def log_message(self, message) -> None:
        """Serialize and append an SDK message to the log file. Track metrics."""
        record = self._serialize(message)
        with open(self.path, "a") as f:
            f.write(json.dumps(record) + "\n")

        # Track metrics from message content
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, ToolUseBlock):
                    self.tools_used.append(block.name)
                    self._track_file_change(block.name, block.input)
        elif isinstance(message, ResultMessage):
            if message.total_cost_usd is not None:
                self.total_cost_usd = message.total_cost_usd
            self.duration_ms = message.duration_ms
            self.num_turns = message.num_turns
            self.result_text = message.result or ""
            if message.is_error:
                self.errors.append(self.result_text)

    def build_summary(self, session_id: str = "") -> str:
        """Build a concise summary string for the LLM."""
        parts = []

        # Header
        cost_str = f"${self.total_cost_usd:.2f}" if self.total_cost_usd else "no cost data"
        if session_id:
            parts.append(f"**Claude Code completed** (session {session_id[:8]}, {cost_str})")
        else:
            parts.append(f"**Claude Code completed** ({cost_str})")

        # Files changed
        unique_files = list(dict.fromkeys(self.files_changed))  # dedupe, preserve order
        if unique_files:
            parts.append(f"- Files changed: {', '.join(unique_files)}")

        # Tools used
        if self.tools_used:
            tool_summary = ", ".join(self.tools_used)
            parts.append(f"- {len(self.tools_used)} tool call(s): {tool_summary}")
        else:
            parts.append("- No tool calls")

        # Errors
        if self.errors:
            parts.append(f"- Errors: {len(self.errors)}")
            for err in self.errors[:3]:  # limit to first 3
                parts.append(f"  - {err[:200]}")

        # Duration
        if self.duration_ms:
            secs = self.duration_ms / 1000
            parts.append(f"- Duration: {secs:.1f}s")

        # Log path
        parts.append(f"- Full log: {self.path}")

        # Result summary (last text from Claude Code)
        if self.result_text and not self.errors:
            preview = self.result_text[:500]
            if len(self.result_text) > 500:
                preview += "..."
            parts.append(f"\n**Result:**\n{preview}")

        return "\n".join(parts)

    def _track_file_change(self, tool_name: str, tool_input: dict) -> None:
        """Track file paths from Edit/Write tool calls."""
        if tool_name in ("Edit", "Write", "NotebookEdit"):
            file_path = tool_input.get("file_path", "")
            if file_path and file_path not in self.files_changed:
                self.files_changed.append(file_path)

    def _serialize(self, message) -> dict:
        """Convert an SDK message to a JSON-safe dict."""
        if isinstance(message, AssistantMessage):
            return {
                "type": "assistant",
                "model": message.model,
                "content": [self._serialize_block(b) for b in message.content],
            }
        elif isinstance(message, ResultMessage):
            return {
                "type": "result",
                "subtype": message.subtype,
                "session_id": message.session_id,
                "total_cost_usd": message.total_cost_usd,
                "duration_ms": message.duration_ms,
                "num_turns": message.num_turns,
                "is_error": message.is_error,
                "result": message.result,
            }
        elif isinstance(message, UserMessage):
            return {"type": "user", "content": str(message)}
        elif isinstance(message, SystemMessage):
            return {"type": "system", "content": str(message)}
        else:
            return {"type": "unknown", "repr": repr(message)}

    def _serialize_block(self, block) -> dict:
        """Convert a content block to a JSON-safe dict."""
        if isinstance(block, TextBlock):
            return {"type": "text", "text": block.text}
        elif isinstance(block, ToolUseBlock):
            return {"type": "tool_use", "name": block.name, "input": block.input}
        elif isinstance(block, ToolResultBlock):
            return {
                "type": "tool_result",
                "tool_use_id": block.tool_use_id,
                "is_error": block.is_error,
                "content": block.content if isinstance(block.content, str) else str(block.content),
            }
        else:
            return {"type": "unknown", "repr": repr(block)}
