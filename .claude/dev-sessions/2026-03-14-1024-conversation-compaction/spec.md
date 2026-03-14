# Conversation Compaction and Archival

## Overview

When conversation history grows too long, automatically summarize older
messages to stay within the model's context window. Compaction runs after
each agent turn, uses a configurable (potentially cheaper) LLM for
summarization, and preserves recent turns intact.

All messages are also archived to per-conversation JSONL files in the
agent workspace. The archive is append-only and preserves the full
unmodified history for debugging, later reflection, and future
re-compaction from the original source.

## Goals

- Prevent context window overflow in long conversations
- Transparent to the user — happens after they get their response
- Configurable compaction LLM (separate from main agent LLM)
- Customizable summarization prompt via workspace file
- Preserve recent conversation detail while compressing older context
- Archive full conversation history to disk for persistence and debugging

## Context Budget

The LLM's context window is a fixed resource shared by everything:

```
┌─────────────────────────────────────────┐ 0x0000
│ SYSTEM PROMPT                           │
│ - Base personality/instructions          │
│ - Memory system instructions            │
│ - Iterative search guidance             │
├─────────────────────────────────────────┤
│ TOOL DEFINITIONS (sent as `tools` param)│
│ - Separate from messages, but eats      │
│   context budget: core tools, tabstack  │
│   tools, memory tools, debug tool       │
│ - Each tool description is tokens too!  │
├─────────────────────────────────────────┤
│ [CONVERSATION SUMMARY]                  │
│ - Only present after compaction         │
│ - Single user message with prefix       │
├─────────────────────────────────────────┤
│ CONVERSATION HISTORY                    │
│ - user messages                         │
│ - assistant messages (with tool_calls)  │
│ - tool result messages                  │
│ - Grows unbounded until compaction      │
├─────────────────────────────────────────┤
│ LATEST USER MESSAGE                     │
│ - Part of history, appended at start    │
│   of run_agent_turn                     │
├─────────────────────────────────────────┤
│ ~~~ FREE SPACE ~~~                      │
│ - Room for LLM's response              │
│ - Room for tool call/result cycles      │
│ - This is what shrinks as history grows │
└─────────────────────────────────────────┘ CONTEXT_WINDOW_MAX
```

`COMPACTION_MAX_TOKENS` measures `prompt_tokens` from the API, which
includes system prompt + tool definitions + all messages. So compaction
triggers based on the *total* context usage, not just history size.

**Fixed overhead:** System prompt and tool definitions are present on
every turn. As we add more tools, this fixed cost grows — a strong
argument for the skills system (only load relevant tools) in the future.

## When Compaction Runs

- **After each agent turn completes** — the user already has their response
- **Condition:** total conversation tokens exceed `COMPACTION_MAX_TOKENS`
- Token count is tracked from the LLM API `usage` field in each response
- If token count isn't available (API doesn't return it), skip compaction
  for that turn rather than guessing

## Token Counting

- `call_llm` returns the `usage` dict from the API response alongside
  the message content
- The agent loop tracks the most recent `prompt_tokens` value, which
  represents the full conversation sent to the LLM
- This is the number compared against `COMPACTION_MAX_TOKENS`

## What Gets Compacted

History is a flat list of messages:
```
[user 1] [assistant 1 (tool calls)] [tool results] [assistant 1 (response)]
[user 2] [assistant 2 (response)]
[user 3] [assistant 3 (tool calls)] [tool results] [assistant 3 (response)]
...
```

Compaction reads from the **conversation archive** (the JSONL file),
not from in-memory history. The archive is the source of truth;
in-memory history is the working set the LLM sees.

The archive is split into two parts:
- **Old messages** — everything before the last N turns (to be summarized)
- **Recent messages** — the last N turns (preserved intact)

A "turn" is defined as: a `user` message plus all subsequent messages
until the next `user` message (or end of history). This keeps tool
call/result pairs together with their associated user request.

Before sending to the compaction LLM, old messages are **flattened**
into a simple readable format. This avoids sending `tool_calls` and
`tool` role messages that the compaction LLM wouldn't understand
(it doesn't have tools defined). The flattened format:

```
User: What's the weather?
Assistant: Let me check that for you.
Tool (web_fetch): [fetched weather data...]
Assistant: It's 72°F and sunny.
```

The compaction LLM's summary plus the recent messages **replace the
entire in-memory history**:
```
[{"role": "user", "content": "[Conversation summary]: ..."}]
+ [recent turn messages from archive]
```

This is non-destructive — the archive still has everything. Compaction
just rebuilds the working set from the archive.

**Re-compaction:** On subsequent compactions, the archive is re-read
from the start. The full original history is always available, so
re-compaction doesn't suffer from summaries-of-summaries. Future work
could explore caching the summary to avoid re-reading the entire
archive each time.

**Chunked compaction:** The old messages from the archive may themselves
be too large to fit in the compaction LLM's context window. In this case,
split the old messages into chunks (at turn boundaries), summarize each
chunk separately, then combine the chunk summaries into a final summary.

Config: `COMPACTION_LLM_MAX_TOKENS` — the compaction LLM's context
window budget. If the old messages (estimated by character count / 4)
exceed this, chunk them. Default: same as `COMPACTION_MAX_TOKENS`.

**Note on token estimation:** The automatic trigger uses real
`prompt_tokens` from the API, but chunking uses character estimation
(~4 chars/token) since we're working with raw text, not API responses.
These may diverge for non-English text or code-heavy conversations.
Acceptable for a first pass; could add `tiktoken` later for accuracy.

The chunking process:
1. Split old messages into chunks that fit the compaction LLM's window
2. Summarize each chunk independently
3. If the combined chunk summaries are still too long, recursively
   summarize them (but in practice 2-3 chunks should be enough)
4. The final summary replaces the old messages in the working set

## Conversation Archive

Every message added to a conversation is also appended to a JSONL file:

```
data/workspace/{agent_id}/conversations/{conv_id}.jsonl
```

Each line is the raw message dict as JSON — `role`, `content`,
`tool_calls`, `tool_call_id`, etc. — exactly as it appears in the
in-memory history list.

**When to write:** Each message is appended as it's added to the
in-memory history (user messages, assistant messages, tool results).
This happens in `run_agent_turn` as messages are produced.

**Properties:**
- Append-only — never modified or truncated
- One file per conversation ID (same keying as in-memory history)
- Survives restarts — the archive persists even though in-memory
  history is lost
- Raw format — no flattening or transformation, preserves tool_calls
  and all metadata
- JSONL for easy line-by-line processing

**Future uses:**
- **Resume conversations after restart** — conv_id maps directly to
  the filename (channel_id or root_id), so on restart the agent can
  check for an existing archive, replay it to rebuild history, then
  compact if needed
- Re-compact from original messages with improved summarizer
- Extract memories or patterns from old conversations
- Debugging — inspect exactly what the agent saw
- Analytics — token usage, tool frequency, conversation length

## Error Handling

Compaction failure must not break the conversation.

- If the compaction LLM call fails (timeout, error, quota), log the
  error and continue with uncompacted history
- If the archive file can't be read, skip compaction for this turn
- If chunked compaction partially fails (some chunks succeed, some don't),
  skip the entire compaction — don't apply partial summaries
- The agent continues operating normally in all failure cases

## Explicit Compaction Tool

In addition to automatic compaction, provide a `compact_conversation`
tool that the user or agent can invoke manually.

```
compact_conversation() -> str
```

- Triggers compaction immediately regardless of token count
- Returns a confirmation with the summary length and number of
  messages compacted
- Useful when: the user wants a fresh start, the agent notices the
  conversation is getting unwieldy, or for testing compaction behavior

Tool description should note: "Manually compact the conversation
history into a summary. Use when the conversation is getting long
or when you want to consolidate context."

## Compaction Context

Compaction needs access to:
- **conv_id** — to find the archive file. Passed via the context
  object or derived from it.
- **history list** — the in-memory list to replace after compaction.
  This is the same list reference from `run_agent_turn`.

## Thread Fork After Compaction

If channel history has been compacted (summary + recent turns), and a
new thread forks from that channel, the thread inherits the compacted
version. This is expected — the thread gets the same context the
channel agent currently sees.

## Compaction LLM

Separate from the main agent LLM. Defaults to the main LLM settings
if not configured.

`call_llm` is updated to accept optional override parameters for URL,
model, and API key. When compaction calls it, these overrides point to
the compaction LLM. When not provided, the existing config values are
used. This avoids building temporary config objects.

### Config

```
COMPACTION_LLM_URL=            # default: LLM_URL
COMPACTION_LLM_MODEL=          # default: LLM_MODEL
COMPACTION_LLM_API_KEY=        # default: LLM_API_KEY
COMPACTION_MAX_TOKENS=100000   # compact when history exceeds this
COMPACTION_LLM_MAX_TOKENS=     # compaction LLM's context budget (default: COMPACTION_MAX_TOKENS)
COMPACTION_PRESERVE_TURNS=5    # keep this many recent turns intact
```

All `COMPACTION_*` settings are added to the Config dataclass with
appropriate defaults falling back to the main LLM settings.

## Summarization Prompt

The prompt sent to the compaction LLM to generate the summary.

**Source:** `data/workspace/{agent_id}/COMPACTION.md` if it exists,
otherwise a built-in default. This allows per-agent customization.

**Built-in default:**
```
Summarize the following conversation, preserving:
- Key facts and decisions made
- User preferences and corrections
- Important tool results and findings
- The current topic and any open questions

Be concise but don't lose critical details. Format as a brief narrative.
```

The compaction LLM receives:
```
[system: summarization prompt from COMPACTION.md or default]
[the old messages to be summarized]
```

## Event Publishing

Compaction publishes events for subscribers:

- `compaction_start` — compaction is beginning
- `compaction_end` — compaction is complete

### Mattermost subscriber behavior

On `compaction_start`: send a temporary message "Compacting conversation..."
On `compaction_end`: delete the temporary message via `DELETE /api/v4/posts/{post_id}`

Requires adding a `delete_message` method to `MattermostClient`. The bot
needs `delete_post` permission (or `delete_others_posts` — but bots
deleting their own posts typically works with default bot permissions).

This gives the user visibility without cluttering the chat.

### Terminal subscriber behavior

On `compaction_start`: print "  [compacting conversation...]"

## Implementation Location

- **Archive writes:** in `run_agent_turn`, append each message as it's
  added to history
- **Token tracking:** in `call_llm` (return usage) and `run_agent_turn`
  (track prompt_tokens)
- **Compaction logic:** new module `compaction.py` — called at the end
  of `run_agent_turn`
- **Compaction LLM call:** reuses `call_llm` with compaction config
  (different model/url/key)
- **Config:** new fields in `config.py`
- **Prompt file:** read from workspace at compaction time

## Scope

### In scope

- Config: `COMPACTION_LLM_URL`, `COMPACTION_LLM_MODEL`, `COMPACTION_LLM_API_KEY`,
  `COMPACTION_MAX_TOKENS`, `COMPACTION_LLM_MAX_TOKENS`, `COMPACTION_PRESERVE_TURNS`
- Chunked compaction when archive exceeds compaction LLM's window
- Token counting from LLM API usage field
- Compaction logic: split history, summarize old part, replace with summary
- Message flattening for compaction LLM input
- Customizable summarization prompt via workspace file
- Event publishing for compaction start/end
- Mattermost subscriber (temporary compaction message + delete)
- Terminal subscriber (print compaction status)
- `delete_message` method on MattermostClient
- `compact_conversation` tool for manual compaction
- Conversation archive: per-conversation JSONL files in workspace
- `call_llm` updated with optional URL/model/key overrides

### Out of scope (future)

- Replay archive to rebuild history on restart (resume conversations)
- Cached summaries to avoid re-reading full archive on each compaction
- Compaction of tool results specifically (e.g., truncating large tool outputs)
- Token estimation when API doesn't return usage
- Compaction during a turn (between iterations)
