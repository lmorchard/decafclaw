"""Pure reducer that turns a stream of WebSocket event dicts into a TurnSummary.

No I/O. Feed each event dict to `record()`, then call `finalize(stop_reason)`
to compute the terminal status and assistant text. Kept lossless on purpose:
unlike the interactive TUI dispatcher, this captures every tool call, every
confirmation, and every error so an agent can assert on them.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ToolCallRecord:
    tool_call_id: str
    name: str
    status: str = "started"  # "started" | "done"
    status_message: str = ""
    result_text: str = ""


@dataclass
class ConfirmationRecord:
    confirmation_id: str
    action_type: str = ""
    tool: str = ""
    command: str = ""
    message: str = ""


@dataclass
class TurnSummary:
    conv_id: str = ""
    status: str = "incomplete"  # complete|halted_confirmation|error|timeout
    assistant_text: str = ""
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    confirmations: list[ConfirmationRecord] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    reflection: dict | None = None
    model: str = ""
    usage: dict = field(default_factory=dict)
    raw_event_count: int = 0


class TurnRecorder:
    def __init__(self, conv_id: str = "") -> None:
        self.summary = TurnSummary(conv_id=conv_id)
        self._assistant_parts: list[str] = []
        self._tool_index: dict[str, ToolCallRecord] = {}

    def record(self, event: dict) -> None:
        self.summary.raw_event_count += 1
        etype = event.get("type", "")

        if etype == "message_complete":
            # message_complete is only emitted for assistant turns; missing role defaults to assistant
            role = event.get("role") or "assistant"
            text = event.get("text") or ""
            if role == "assistant" and text:
                self._assistant_parts.append(text)
            usage = event.get("usage")
            if isinstance(usage, dict) and usage:
                self.summary.usage = usage

        elif etype == "tool_start":
            tcid = event.get("tool_call_id", "")
            rec = ToolCallRecord(tool_call_id=tcid, name=event.get("tool", ""))
            self._tool_index[tcid] = rec
            self.summary.tool_calls.append(rec)

        elif etype == "tool_status":
            rec = self._tool_index.get(event.get("tool_call_id", ""))
            if rec is not None:
                rec.status_message = event.get("message", "") or rec.status_message

        elif etype == "tool_end":
            tcid = event.get("tool_call_id", "")
            rec = self._tool_index.get(tcid)
            if rec is None:
                rec = ToolCallRecord(tool_call_id=tcid, name=event.get("tool", ""))
                self._tool_index[tcid] = rec
                self.summary.tool_calls.append(rec)
            rec.status = "done"
            rec.result_text = event.get("result_text", "") or rec.result_text

        elif etype == "confirm_request":
            self.summary.confirmations.append(ConfirmationRecord(
                confirmation_id=event.get("confirmation_id", ""),
                action_type=event.get("action_type", ""),
                tool=event.get("tool", ""),
                command=event.get("command", ""),
                message=event.get("message", ""),
            ))

        elif etype == "error":
            msg = event.get("message", "")
            if msg:
                self.summary.errors.append(msg)

        elif etype == "reflection_result":
            self.summary.reflection = {
                "passed": event.get("passed"),
                "critique": event.get("critique", ""),
                "retry_number": event.get("retry_number"),
            }

        elif etype == "model_changed":
            self.summary.model = event.get("model", "") or self.summary.model

    def finalize(self, stop_reason: str) -> TurnSummary:
        self.summary.assistant_text = "\n\n".join(
            p for p in self._assistant_parts if p)
        self.summary.status = _status_for(stop_reason, self.summary.errors)
        return self.summary


def _status_for(stop_reason: str, errors: list[str]) -> str:
    if stop_reason == "confirmation":
        return "halted_confirmation"
    if stop_reason == "timeout":
        return "timeout"
    if stop_reason == "turn_complete":
        return "error" if errors else "complete"
    return "error"  # "disconnect" or any unexpected reason: turn did not finish
