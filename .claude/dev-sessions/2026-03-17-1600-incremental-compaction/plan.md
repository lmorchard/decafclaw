# Plan: Incremental Compaction

## Context

The compacted sidecar (`{conv_id}.compacted.jsonl`) stores `[summary_msg] + [recent_messages]`. The summary message has role "user" with content prefixed `[Conversation summary]:`. The full archive (`{conv_id}.jsonl`) is append-only and never modified.

Currently `compact_history` always reads the full archive, splits all turns into old/recent, and summarizes all old turns from scratch.

## Step 1: Extract previous summary from compacted history

**What**: Add a helper `_extract_previous_summary` that reads the compacted sidecar and returns the summary text (if any) and the timestamp of the last message in the compacted history.

**Why**: We need to know what was already summarized and where the boundary is.

**Prompt**: In `compaction.py`, add a function that:
- Calls `read_compacted_history(config, conv_id)`
- If the first message starts with `[Conversation summary]:`, extract the summary text
- Return `(summary_text, last_compacted_timestamp)` or `(None, None)` if no previous compaction

## Step 2: Identify newly-old turns

**What**: Modify `compact_history` to compare the full archive against the previous compaction boundary. Only turns between the old summary and the current recent-preserve window are "newly old" and need summarizing.

**Why**: This is the core optimization — skip turns that were already summarized.

**Prompt**: In `compact_history`:
- After reading the archive and splitting into turns, check for a previous summary via `_extract_previous_summary`
- If a previous summary exists, find the turns in the archive that come after the previous compaction boundary but before the current recent-preserve window
- These are the "newly-old" turns that need to be folded into the summary
- If there are no newly-old turns (recent window hasn't moved), skip compaction

## Step 3: Incremental summarization

**What**: When a previous summary exists, combine it with newly-old turns and produce an updated summary, rather than re-summarizing everything.

**Why**: This is the payoff — each compaction only processes a small delta.

**Prompt**: Modify the summarization call:
- If we have a previous summary + newly-old turns, build the input as: `"Previous summary:\n{old_summary}\n\nNew turns to incorporate:\n{flattened_new_turns}"`
- Use a slightly modified prompt that instructs the LLM to update the summary with new information rather than summarize from scratch
- If no previous summary, fall through to the existing full-summarize path

## Step 4: Lint, test, commit

**What**: Verify everything passes, commit the change.

**Prompt**: Run `make check && make test`. Fix any issues. Commit with a message referencing #57.
