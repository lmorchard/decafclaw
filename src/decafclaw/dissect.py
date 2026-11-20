"""Dissection template — extract a failure and build a structured brief.

Dissection is a specialized fork preset for tool failures: it copies the
triggering prompt, tool call, arguments, and exact error into the child so the
debugging work (source reading, diffs, tests) stays out of the parent context.

The general fork machinery lives in :mod:`decafclaw.fork`; this module layers
the failure-specific focus and brief on top of it.
"""

from __future__ import annotations

from .fork import (
    ForkFocus,
    bounded_timeline,
    clip_text,
    find_fork_focus,
    format_arguments,
    tool_calls_by_id,
)

# The failure body gets the largest share because exact error text
# (tracebacks, compiler output) is the point of the dissection.
MAX_TIMELINE_CHARS = 16_000
MAX_TIMELINE_MESSAGES = 16
MAX_FAILURE_CHARS = 8_000
MAX_ARGUMENT_CHARS = 2_000
MAX_MESSAGE_CHARS = 4_000


def find_dissection_focus(messages: list[dict], tool_call_id: str = "") -> ForkFocus | None:
    """Pick the failure to dissect.

    Uses the shared fork focus: an explicit ``tool_call_id`` may point at any
    recorded failure, while an empty id only considers an error in the
    immediate latest turn/exchange. If the latest exchange has no error, this
    is a conversational focus (the caller treats it as a general fork).
    """
    return find_fork_focus(messages, tool_call_id)


def build_dissection_brief(
    messages: list[dict],
    focus: ForkFocus,
    *,
    source_conv_id: str,
    source_title: str = "",
    instruction: str = "",
) -> str:
    """Render the seeded first user message for a failure dissection."""
    calls = tool_calls_by_id(messages)
    start = focus.user_index if focus.user_index is not None else 0
    end = focus.tool_index if focus.tool_index is not None else len(messages) - 1

    timeline_blocks = bounded_timeline(
        messages,
        start,
        end,
        calls,
        # The failing result is rendered in full under Failure; keep the
        # timeline readable by not duplicating it there.
        skip_index=focus.tool_index,
        max_messages=MAX_TIMELINE_MESSAGES,
        max_chars=MAX_TIMELINE_CHARS,
        message_limit=MAX_MESSAGE_CHARS,
        argument_limit=MAX_ARGUMENT_CHARS,
    )

    source_label = f"“{source_title}”" if source_title else source_conv_id
    lines = [
        "# Dissection request",
        "",
        "You are working in a fork created to investigate a failure without polluting",
        f"the parent conversation {source_label} (`{source_conv_id}`). Do the debugging",
        "work here: read source, create a git worktree, run tests, and open a PR.",
        "",
    ]

    if focus.has_error:
        lines += [
            "## What failed",
            "",
            f"- Tool: `{focus.tool_name or 'unknown'}`",
            f"- Tool call id: `{focus.tool_call_id or 'unknown'}`",
        ]
        if focus.timestamp:
            lines.append(f"- When: {focus.timestamp}")
        lines += [
            "",
            "### Arguments",
            "```json",
            format_arguments(focus.arguments, MAX_ARGUMENT_CHARS),
            "```",
            "",
            "### Exact error",
            "```",
            clip_text(focus.error, MAX_FAILURE_CHARS),
            "```",
            "",
        ]
    else:
        lines += [
            "## What to investigate",
            "",
            "No failing tool result was recorded in the latest exchange. The most",
            "recent context is included below; decide from the instruction what",
            "needs work. If this is exploratory, a general fork brief may be a",
            "better fit.",
            "",
        ]

    if timeline_blocks:
        lines += [
            "## Conversation leading to the failure",
            "",
            "\n\n".join(timeline_blocks),
            "",
        ]

    lines += [
        "## Your task",
        "",
        instruction.strip()
        or (
            "Investigate this failure, identify the root cause, fix it, and verify"
            " the fix with the narrowest relevant test. Report what changed and any"
            " remaining risks. Do not modify the parent conversation."
        ),
        "",
        "## Working notes",
        "",
        "- Before investigating, activate the relevant skills when available: "
        "`project` (this repository's workflow) and `postmortem`.",
        "- The parent conversation archive remains on disk. If you need more"
        f" history, use `conversation_search` to search it (parent conv id:"
        f" `{source_conv_id}`).",
        "- Use an isolated worktree under `.claude/worktrees/` for code changes.",
        "",
    ]
    return "\n".join(lines)
