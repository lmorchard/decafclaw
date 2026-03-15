# Core Modules Backlog

These shape the agent loop, context, and fundamental infrastructure.
Not portable as skills — they're the platform skills run on.

## Conversation management

- Per-user history in channels (not just per-channel)
- Session reset command

## Multi-model routing

Use a fast model for simple questions, a more capable model for complex
ones. The context fork design already supports different configs per fork.
Could be automatic (let a classifier decide) or explicit (user says
"think harder about this").

## Channel abstraction

Extract a channel interface so the bot isn't Mattermost-specific.
Terminal mode is already a second "channel." Could add Discord, Slack,
IRC, or a simple HTTP API. The event bus and context are already
channel-agnostic — the main coupling is in `mattermost.py`.

## User permissions / roles

Different users can access different tools. Admin can use `shell`,
regular users can't.

- Config maps user IDs to roles, roles to tool allowlists
- `execute_tool` checks permissions before running
- Pairs with multi-user mapping (channel user → agent user)

## Context stats debug tool

Extend `debug_context` (or add `debug_context_stats`) to report token
budget statistics. Needs access to agent internals — not portable.

- Total prompt_tokens from last LLM call
- Estimated breakdown: system prompt, tool definitions, conversation
  summary (if present), history messages, free space remaining
- Number of messages by role (user, assistant, tool)
- Number of compactions performed in this conversation
- Archive file size on disk

Helps diagnose context pressure — "why is the agent forgetting things?"
might be "tool definitions are eating 30% of your budget."

## Max message length

Truncate or reject absurdly long messages before sending to the LLM.
Prevents context window abuse and accidental paste bombs.

## Streaming LLM responses

Stream tokens to the placeholder as they arrive instead of waiting for
the full response. The async architecture already supports this.
