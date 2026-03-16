"""Conversation compaction — summarize old history to stay within context budget."""

import logging
from pathlib import Path

from .archive import read_archive
from .llm import call_llm

log = logging.getLogger(__name__)

DEFAULT_COMPACTION_PROMPT = """\
Summarize the following conversation, preserving:
- Key facts and decisions made
- User preferences and corrections
- Important tool results and findings
- The current topic and any open questions

Be concise but don't lose critical details. Format as a brief narrative."""


def _load_compaction_prompt(config) -> str:
    """Load custom prompt from workspace, or use default."""
    prompt_path = config.agent_path / "COMPACTION.md"
    if prompt_path.exists():
        return prompt_path.read_text().strip()
    return DEFAULT_COMPACTION_PROMPT


def _split_into_turns(messages: list[dict]) -> list[list[dict]]:
    """Split a flat message list into turns.

    A turn starts with a user message and includes everything until
    the next user message.
    """
    turns = []
    current_turn = []
    for msg in messages:
        if msg.get("role") == "user" and current_turn:
            turns.append(current_turn)
            current_turn = []
        current_turn.append(msg)
    if current_turn:
        turns.append(current_turn)
    return turns


def _flatten_messages(messages: list[dict]) -> str:
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


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 characters per token."""
    return len(text) // 4


async def _single_summarize(ctx, config, flattened_text: str, prompt: str) -> str:
    """Summarize a single block of flattened text."""
    summary_messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": flattened_text},
    ]
    response = await call_llm(
        config, summary_messages,
        llm_url=config.compaction_url,
        llm_model=config.compaction_model,
        llm_api_key=config.compaction_api_key,
    )
    return response.get("content", "")


async def _chunked_summarize(ctx, config, turns: list[list[dict]],
                              prompt: str, budget: int) -> str:
    """Summarize turns in chunks that fit the compaction LLM's context window."""
    chunks = []
    current_chunk = []
    current_size = 0

    for turn in turns:
        turn_text = _flatten_messages(turn)
        turn_size = _estimate_tokens(turn_text)
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
        flattened = _flatten_messages(chunk)
        log.info(f"Summarizing chunk {i + 1}/{len(chunks)}")
        summary = await _single_summarize(ctx, config, flattened, prompt)
        if summary:
            chunk_summaries.append(summary)

    if not chunk_summaries:
        return ""

    # Combine chunk summaries
    combined = "\n\n---\n\n".join(chunk_summaries)

    # If combined summaries are still too long, do a final pass
    if _estimate_tokens(combined) > budget:
        log.info("Combined summaries too long, doing final summarize pass")
        return await _single_summarize(ctx, config, combined, prompt)

    return combined


async def compact_history(ctx, history: list) -> bool:
    """Compact conversation history using the archive as source.

    Reads the full archive, splits into old/recent, summarizes old
    messages, and replaces history with [summary] + [recent].

    Returns True if compaction was performed, False if skipped.
    """
    config = ctx.config
    conv_id = getattr(ctx, "conv_id", None) or getattr(ctx, "channel_id", "unknown")

    # Read the full archive
    archive = read_archive(config, conv_id)
    if not archive:
        log.debug("No archive found, skipping compaction")
        return False

    # Split into turns
    turns = _split_into_turns(archive)
    preserve = config.compaction_preserve_turns

    if len(turns) <= preserve:
        log.debug(f"Only {len(turns)} turns, need >{preserve} to compact")
        return False

    # Split: old turns to summarize, recent turns to keep
    old_turns = turns[:-preserve]
    recent_turns = turns[-preserve:]

    old_messages = [msg for turn in old_turns for msg in turn]
    recent_messages = [msg for turn in recent_turns for msg in turn]

    log.info(f"Compacting {len(old_messages)} messages ({len(old_turns)} turns) "
             f"into summary, preserving {len(recent_messages)} messages ({len(recent_turns)} turns)")

    # Load the summarization prompt
    prompt = _load_compaction_prompt(config)

    # Flatten old messages
    flattened = _flatten_messages(old_messages)

    # Check if we need chunked compaction
    budget = config.compaction_context_budget
    estimated = _estimate_tokens(flattened)

    import time as _time
    before_messages = len(history)
    compact_start_time = _time.monotonic()

    try:
        await ctx.publish("compaction_start")

        if estimated > budget:
            log.info(f"Flattened text ({estimated} est. tokens) exceeds compaction budget ({budget}), using chunked compaction")
            summary = await _chunked_summarize(ctx, config, old_turns, prompt, budget)
        else:
            summary = await _single_summarize(ctx, config, flattened, prompt)

        if not summary:
            log.warning("Compaction LLM returned empty summary, skipping")
            return False

        # Rebuild history: summary + recent messages
        summary_msg = {"role": "user", "content": f"[Conversation summary]: {summary}"}

        history.clear()
        history.append(summary_msg)
        history.extend(recent_messages)

        log.info(f"Compaction complete: {len(old_messages)} messages -> "
                 f"1 summary + {len(recent_messages)} recent = {len(history)} total")
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
