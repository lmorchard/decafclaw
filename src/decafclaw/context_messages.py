"""Provider-ready message sanitization (#1491).

Rewrites archived history into the shape providers accept: tool results
reattached to their assistant ``tool_call`` and background completions
expanded into a synthetic poll pair.
"""

from __future__ import annotations

import json
import logging

from decafclaw.skills.background.tools import format_status_text

log = logging.getLogger(__name__)


# -- History sanitization -----------------------------------------------------


def _reorder_tool_results(messages: list[dict]) -> list[dict]:
    """Ensure each tool result immediately follows its assistant tool_call.

    LiteLLM / Gemini require tool results to appear right after the assistant
    message that issued the call.  After compaction or concurrent archiving,
    results can be orphaned (no matching assistant) or displaced (appear after
    a different assistant message).

    Strategy: walk messages, collect tool results separately, and re-insert
    each batch right after the assistant message whose tool_calls they match.
    Orphans (no matching assistant) are dropped.
    """
    # Map tool_call_id → assistant message index (first match wins)
    tc_id_to_assistant_idx: dict[str, int] = {}
    non_tool_messages: list[tuple[int, dict]] = []  # (original_idx, msg)
    tool_results: list[dict] = []

    for msg in messages:
        if msg.get("role") == "tool":
            tool_results.append(msg)
        else:
            idx = len(non_tool_messages)
            non_tool_messages.append((idx, msg))
            for tc in msg.get("tool_calls") or []:
                tc_id = tc.get("id")
                if tc_id:
                    tc_id_to_assistant_idx[tc_id] = idx

    if not tool_results:
        return messages

    # Group tool results by their assistant message index
    assistant_tool_results: dict[int, list[dict]] = {}
    dropped = 0
    for tr in tool_results:
        tc_id = tr.get("tool_call_id", "")
        asst_idx = tc_id_to_assistant_idx.get(tc_id)
        if asst_idx is None:
            dropped += 1
            continue
        assistant_tool_results.setdefault(asst_idx, []).append(tr)

    if dropped:
        log.info(f"Dropped {dropped} orphaned tool result(s) from history")

    # Rebuild: interleave non-tool messages with their tool results
    result: list[dict] = []
    for idx, msg in non_tool_messages:
        result.append(msg)
        if idx in assistant_tool_results:
            result.extend(assistant_tool_results[idx])

    return result


def _expand_background_event(rec: dict) -> list[dict]:
    """Convert a background_event archive record into a pair of synthetic
    messages (assistant tool_call + tool result) matching what the agent
    would have seen if it had polled shell_background_status."""
    job_id = rec.get("job_id", "")
    call_id = f"bg-wake-{job_id}"
    args = json.dumps({"job_id": job_id})
    tool_text = format_status_text(
        job_id=job_id,
        status=rec.get("status", "?"),
        command=rec.get("command", ""),
        pid=0,  # not stored in archive; not meaningful for a completed job
        elapsed_ms=rec.get("elapsed_ms", 0),
        remaining_ms=None,  # job has exited
        exit_code=rec.get("exit_code"),
        stdout=rec.get("stdout_tail", ""),
        stderr=rec.get("stderr_tail", ""),
        # Records written since #1825 persist the note. Older rows have none,
        # and format_status_text re-derives it from exit_code for those.
        kill_note=rec.get("kill_note"),
    )
    return [
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": "shell_background_status",
                        "arguments": args,
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": call_id,
            "content": tool_text,
        },
    ]
