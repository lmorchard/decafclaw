"""Conversation compaction — summarize old history to stay within context budget."""

import logging
from pathlib import Path

from .archive import read_archive, read_compacted_history, write_compacted_history
from .llm import call_llm
from .util import estimate_tokens

log = logging.getLogger(__name__)

DEFAULT_COMPACTION_PROMPT = """\
Summarize the following conversation, preserving:
- Key facts and decisions made
- User preferences and corrections
- Important tool results and findings
- Approaches that were tried but didn't work, and why — this prevents re-exploration
- The current topic and any open questions

Be concise but don't lose critical details. Err on the side of including information
that would prevent duplicate work or repeated mistakes. Format as a brief narrative."""

INCREMENTAL_COMPACTION_PROMPT = """\
You have an existing conversation summary and new turns that need to be incorporated.
Update the summary to include the new information while preserving all important details
from the original summary. Do not lose any key facts, decisions, user preferences,
or failed approaches (and why they failed).

Be concise but don't lose critical details. Format as a brief narrative."""

SUMMARY_PREFIX = "[Conversation summary]: "


def _load_compaction_prompt(config) -> str:
    """Load custom prompt from workspace, or use default."""
    prompt_path = config.agent_path / "COMPACTION.md"
    if prompt_path.exists():
        return prompt_path.read_text().strip()
    return DEFAULT_COMPACTION_PROMPT


def _extract_previous_summary(config, conv_id: str) -> tuple[str | None, str | None]:
    """Extract the previous summary text and the last compacted timestamp.

    Returns (summary_text, last_timestamp) or (None, None) if no
    previous compaction exists.
    """
    compacted = read_compacted_history(config, conv_id)
    if not compacted:
        return None, None
    first = compacted[0]
    content = first.get("content", "")
    if first.get("role") == "user" and content.startswith(SUMMARY_PREFIX):
        summary_text = content[len(SUMMARY_PREFIX):]
        last_ts = compacted[-1].get("timestamp", "")
        return summary_text, last_ts
    return None, None


def _split_into_turns(messages: list[dict]) -> list[list[dict]]:
    """Split a flat message list into turns.

    A turn starts with a user or memory_context message and includes
    everything until the next turn boundary. memory_context is treated
    as a turn start because it's injected before the user message it
    belongs to.
    """
    turns = []
    current_turn = []
    for msg in messages:
        role = msg.get("role")
        if role in ("user", "memory_context") and current_turn:
            turns.append(current_turn)
            current_turn = []
        current_turn.append(msg)
    if current_turn:
        turns.append(current_turn)
    return turns


# Tool names whose results should be preserved verbatim during compaction.
# Skill activation injects SKILL.md content as a tool result — summarizing
# it away loses the skill's instructions for the rest of the conversation.
PROTECTED_TOOL_NAMES = {"activate_skill"}


def _turn_has_protected_tool(turn: list[dict]) -> bool:
    """Check if a turn contains a tool call whose result should not be summarized."""
    for msg in turn:
        for tc in (msg.get("tool_calls") or []):
            if tc.get("function", {}).get("name") in PROTECTED_TOOL_NAMES:
                return True
    return False


def flatten_messages(messages: list[dict]) -> str:
    """Flatten messages into a readable text format for the compaction LLM."""
    lines = []
    for msg in messages:
        role = msg.get("role", "unknown")
        content = msg.get("content") or ""

        if role == "user":
            lines.append(f"User: {content}")
        elif role == "assistant":
            tool_calls = msg.get("tool_calls")
            if tool_calls:
                names = [tc["function"]["name"] for tc in tool_calls]
                if content:
                    lines.append(f"Assistant: {content}")
                lines.append(f"Assistant: [called tools: {', '.join(names)}]")
            else:
                lines.append(f"Assistant: {content}")
        elif role == "tool":
            # Truncate long tool results
            preview = content[:500] + "..." if len(content) > 500 else content
            tool_id = msg.get("tool_call_id", "?")
            lines.append(f"Tool result ({tool_id}): {preview}")
        else:
            lines.append(f"{role}: {content}")
    return "\n".join(lines)


async def _single_summarize(ctx, config, flattened_text: str, prompt: str) -> str:
    """Summarize a single block of flattened text."""
    summary_messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": flattened_text},
    ]
    cc = config.compaction.resolved(config)
    response = await call_llm(
        config, summary_messages,
        llm_url=cc.url,
        llm_model=cc.model,
        llm_api_key=cc.api_key,
    )
    return response.get("content", "")


async def _chunked_summarize(ctx, config, turns: list[list[dict]],
                              prompt: str, budget: int) -> str:
    """Summarize turns in chunks that fit the compaction LLM's context window."""
    chunks = []
    current_chunk = []
    current_size = 0

    for turn in turns:
        turn_text = flatten_messages(turn)
        turn_size = estimate_tokens(turn_text)
        # Leave room for the prompt (~500 tokens)
        if current_size + turn_size > budget - 500 and current_chunk:
            chunks.append(current_chunk)
            current_chunk = []
            current_size = 0
        current_chunk.extend(turn)
        current_size += turn_size

    if current_chunk:
        chunks.append(current_chunk)

    # Summarize each chunk
    chunk_summaries = []
    for i, chunk in enumerate(chunks):
        flattened = flatten_messages(chunk)
        log.info(f"Summarizing chunk {i + 1}/{len(chunks)}")
        summary = await _single_summarize(ctx, config, flattened, prompt)
        if summary:
            chunk_summaries.append(summary)

    if not chunk_summaries:
        return ""

    # Combine chunk summaries
    combined = "\n\n---\n\n".join(chunk_summaries)

    # If combined summaries are still too long, do a final pass
    if estimate_tokens(combined) > budget:
        log.info("Combined summaries too long, doing final summarize pass")
        return await _single_summarize(ctx, config, combined, prompt)

    return combined


async def compact_history(ctx, history: list) -> bool:
    """Compact conversation history using the archive as source.

    If a previous compaction exists, performs incremental compaction:
    folds only newly-old turns into the existing summary. Otherwise
    falls back to full compaction from scratch.

    Returns True if compaction was performed, False if skipped.
    """
    config = ctx.config
    conv_id = ctx.conv_id or ctx.channel_id or "unknown"

    # Read the full archive
    archive = read_archive(config, conv_id)
    if not archive:
        log.debug("No archive found, skipping compaction")
        return False

    # Split into turns
    turns = _split_into_turns(archive)
    preserve = config.compaction.preserve_turns

    if len(turns) <= preserve:
        log.info(f"Compaction skipped: only {len(turns)} turns, need >{preserve} to compact")
        return False

    # Split: old turns to summarize, recent turns to keep.
    # Turns containing protected tool calls (e.g. activate_skill) are moved
    # from old to preserved so their content survives compaction.
    old_turns = []
    protected_turns = []
    for turn in turns[:-preserve]:
        if _turn_has_protected_tool(turn):
            protected_turns.append(turn)
        else:
            old_turns.append(turn)
    recent_turns = turns[-preserve:]
    if protected_turns:
        log.info(f"Protecting {len(protected_turns)} skill activation turn(s) from compaction")
    if not old_turns:
        log.info("Compaction skipped: all old turns are protected")
        return False
    old_messages = [msg for turn in old_turns for msg in turn]
    protected_messages = [msg for turn in protected_turns for msg in turn]
    recent_messages = [msg for turn in recent_turns for msg in turn]

    # Check for incremental compaction
    prev_summary, prev_last_ts = _extract_previous_summary(config, conv_id)
    incremental = False
    newly_old_turns: list[list[dict]] = []

    if prev_summary and prev_last_ts:
        # Find how many archive messages existed at the time of the last
        # compaction by finding the boundary using the last compacted timestamp.
        # Everything in the archive up to that timestamp was covered.
        prev_archive_len = 0
        for i, msg in enumerate(archive):
            if msg.get("timestamp", "") <= prev_last_ts:
                prev_archive_len = i + 1
            else:
                break

        # At the previous compaction, the old turns covered archive[0:prev_old_count].
        # The recent turns covered archive[prev_old_count:prev_archive_len].
        # Now old_turns may extend further. Newly-old turns are those whose
        # messages start at or after prev_old_count in the archive.
        # prev_old_count = prev_archive_len - prev_recent_count, but we don't
        # know prev_recent_count directly. However, the summary message in the
        # sidecar replaced everything before the recent window. The recent
        # messages in the sidecar correspond to the last N messages of the
        # archive at that time. We can count: the compacted sidecar had
        # (total_compacted - 1) recent messages covering archive positions
        # [prev_archive_len - (total_compacted - 1) : prev_archive_len].
        # Note: the sidecar may also contain protected turns (e.g. skill
        # activations) between the summary and recent messages — subtract
        # those so we count only actual recent messages.
        compacted = read_compacted_history(config, conv_id)
        if compacted:
            non_summary = compacted[1:]
            compacted_turns = _split_into_turns(non_summary)
            protected_msg_count = sum(
                len(turn) for turn in compacted_turns
                if _turn_has_protected_tool(turn)
            )
            prev_recent_count = len(compacted) - 1 - protected_msg_count
        else:
            prev_recent_count = 0
        prev_old_msg_count = prev_archive_len - prev_recent_count

        # Walk old_turns to find those with messages past the previous boundary
        msg_idx = 0
        for turn in old_turns:
            turn_end = msg_idx + len(turn)
            if turn_end > prev_old_msg_count:
                newly_old_turns.append(turn)
            msg_idx = turn_end

        if not newly_old_turns:
            log.info("No newly-old turns since last compaction, skipping")
            return False
        incremental = True

    log.info(f"Compacting {len(old_messages)} messages ({len(old_turns)} turns) "
             f"into summary, preserving {len(recent_messages)} messages "
             f"({len(recent_turns)} turns)"
             f"{f', incremental ({len(newly_old_turns)} new turns)' if incremental else ''}")

    budget = config.compaction_context_budget
    estimated = 0

    import time as _time
    before_messages = len(history)
    compact_start_time = _time.monotonic()

    try:
        await ctx.publish("compaction_start")

        if incremental:
            # Fold newly-old turns into existing summary
            newly_old_flat = flatten_messages(
                [msg for turn in newly_old_turns for msg in turn])
            combined_input = (
                f"Existing summary:\n{prev_summary}\n\n"
                f"New conversation turns to incorporate:\n{newly_old_flat}"
            )
            estimated = estimate_tokens(combined_input)
            log.info(f"Incremental summarization: ~{estimated} est. tokens")
            summary = await _single_summarize(
                ctx, config, combined_input, INCREMENTAL_COMPACTION_PROMPT)
        else:
            # Full compaction from scratch
            prompt = _load_compaction_prompt(config)
            flattened = flatten_messages(old_messages)
            estimated = estimate_tokens(flattened)
            if estimated > budget:
                log.info(f"Flattened text ({estimated} est. tokens) exceeds "
                         f"budget ({budget}), using chunked compaction")
                summary = await _chunked_summarize(
                    ctx, config, old_turns, prompt, budget)
            else:
                summary = await _single_summarize(ctx, config, flattened, prompt)

        if not summary:
            log.warning("Compaction LLM returned empty summary, skipping")
            return False

        # Rebuild history: summary + protected turns + recent messages
        summary_msg = {
            "role": "user",
            "content": f"{SUMMARY_PREFIX}{summary}",
        }

        history.clear()
        history.append(summary_msg)
        history.extend(protected_messages)
        history.extend(recent_messages)

        # Persist compacted working history so future turns don't re-expand from archive
        write_compacted_history(config, conv_id, list(history))

        log.info(f"Compaction complete: {len(old_messages)} messages -> "
                 f"1 summary + {len(protected_messages)} protected + "
                 f"{len(recent_messages)} recent = {len(history)} total")
        return True

    except Exception as e:
        log.error(f"Compaction LLM call failed: {e}")
        return False
    finally:
        elapsed = _time.monotonic() - compact_start_time
        after_messages = len(history)
        await ctx.publish("compaction_end",
                          before_messages=before_messages,
                          after_messages=after_messages,
                          elapsed_sec=round(elapsed, 1),
                          estimated_tokens_before=estimated)
