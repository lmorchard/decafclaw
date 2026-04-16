"""Checklist tools — mechanical step-by-step execution loop.

Always-loaded tools that drive the agent through a checklist one step
at a time. The agent iterates within a single turn: do step → call
step_done → get next step → do next step. ``end_turn=True`` is only
set when all steps are complete (agent summarizes and stops).
"""

import logging

from .. import checklist
from ..media import ToolResult

log = logging.getLogger(__name__)


def tool_checklist_create(ctx, steps: list[str]) -> ToolResult:
    """Create a checklist and return the first step."""
    conv_id = ctx.conv_id or "default"
    if not steps:
        return ToolResult(text="[error: steps list is empty]")
    items = checklist.checklist_create(ctx.config, conv_id, steps)
    first = items[0]["text"]
    return ToolResult(
        text=f"Checklist created ({len(items)} steps). "
             f"Do step 1 now: {first}\n\n"
             f"When done, call checklist_step_done.",
    )


def tool_checklist_step_done(ctx, note: str = "") -> ToolResult:
    """Mark current step done and advance. end_turn=True only when all complete."""
    conv_id = ctx.conv_id or "default"
    next_item = checklist.checklist_complete_current(ctx.config, conv_id, note)
    if next_item is None:
        # Check if there was nothing to complete vs all done
        items = checklist.checklist_status(ctx.config, conv_id)
        if not items:
            return ToolResult(text="[error: no active checklist]")
        done = sum(1 for i in items if i["done"])
        return ToolResult(
            text=f"All {done} steps complete! Summarize what was accomplished.",
            end_turn=True,
        )
    return ToolResult(
        text=f"Step {next_item['index'] - 1}/{next_item['total']} done. "
             f"Do step {next_item['index']} now: {next_item['text']}\n\n"
             f"When done, call checklist_step_done.",
    )


def tool_checklist_abort(ctx, reason: str = "") -> ToolResult:
    """Abandon the current checklist."""
    conv_id = ctx.conv_id or "default"
    items = checklist.checklist_status(ctx.config, conv_id)
    if not items:
        return ToolResult(text="No active checklist to abort.")
    done = sum(1 for i in items if i["done"])
    checklist.checklist_abort(ctx.config, conv_id)
    msg = f"Checklist aborted ({done}/{len(items)} steps were complete)."
    if reason:
        msg += f" Reason: {reason}"
    return ToolResult(text=msg)


def tool_checklist_status(ctx) -> ToolResult:
    """Show current checklist progress."""
    conv_id = ctx.conv_id or "default"
    items = checklist.checklist_status(ctx.config, conv_id)
    if not items:
        return ToolResult(text="No active checklist.")
    lines = []
    current_found = False
    for i, item in enumerate(items, 1):
        if item["done"]:
            note_suffix = f" — {item['note']}" if item.get("note") else ""
            lines.append(f"  {i}. [x] {item['text']}{note_suffix}")
        else:
            marker = " ← current" if not current_found else ""
            lines.append(f"  {i}. [ ] {item['text']}{marker}")
            if not current_found:
                current_found = True
    done = sum(1 for i in items if i["done"])
    lines.append(f"\n{done}/{len(items)} complete")
    return ToolResult(text="\n".join(lines))


CHECKLIST_TOOLS = {
    "checklist_create": tool_checklist_create,
    "checklist_step_done": tool_checklist_step_done,
    "checklist_abort": tool_checklist_abort,
    "checklist_status": tool_checklist_status,
}

CHECKLIST_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "priority": "critical",
        "function": {
            "name": "checklist_create",
            "description": (
                "When you already know the steps for a task (3 or more), create "
                "a checklist to execute them methodically. This is for direct "
                "execution of known steps — not for tasks that need brainstorming "
                "or planning first (use the project skill for those). Each step "
                "will be presented one at a time. Overwrites any existing checklist."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "steps": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of step descriptions",
                    },
                },
                "required": ["steps"],
            },
        },
    },
    {
        "type": "function",
        "priority": "critical",
        "function": {
            "name": "checklist_step_done",
            "description": (
                "Mark the current checklist step as complete and advance to "
                "the next one. You MUST call this after finishing each step — "
                "the checklist will not advance otherwise. Optionally include "
                "a brief note about what was done."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "note": {
                        "type": "string",
                        "description": "Brief note about what was done (optional)",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "priority": "critical",
        "function": {
            "name": "checklist_abort",
            "description": (
                "Abandon the current checklist. Use when the plan needs "
                "rethinking or the task is no longer relevant."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "Why the checklist is being abandoned",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "priority": "critical",
        "function": {
            "name": "checklist_status",
            "description": "Show current checklist progress without advancing.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
]
