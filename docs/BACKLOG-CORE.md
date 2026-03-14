# Core Modules Backlog

These shape the agent loop, context, and fundamental infrastructure.
Not portable as skills — they're the platform skills run on.

## Conversation management

- Persistent conversation history (SQLite?)
- History truncation strategies
- Per-user history in channels (not just per-channel)
- Session reset command

## Conversation summarization

When history exceeds a token budget, summarize older messages to stay
within the context window. Different from simple truncation — the agent
retains the gist of earlier conversation. Could use a cheap/fast model
for the summarization step.

## Token budget tracking

Track token usage per conversation, warn when approaching limits,
tie into conversation summarization.

- Count tokens on each LLM call (most APIs return usage in response)
- Track cumulative per-conversation budget
- Trigger summarization when budget reaches threshold
- Log costs per conversation for operational visibility

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

## Agent workspace sandbox

The agent should have a dedicated filesystem workspace directory.
File tools (`read_file`, `write_file`, `shell`) should be confined to
this directory by default.

- Config: `AGENT_WORKSPACE=/path/to/workspace` (base path)
- Per-agent subdirectory: `workspace/{agent_id}/`
- Path resolution rejects escapes (no `../../etc/passwd`)
- `shell` runs with `cwd` set to workspace
- Explicit permission model: agent can request access outside the
  workspace, user must approve (pairs with tool confirmation)

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

## Graceful shutdown

Handle SIGTERM properly: finish in-flight agent turns, unsubscribe
from the event bus, close the websocket cleanly. Currently a kill
just drops everything.

## Streaming LLM responses

Stream tokens to the placeholder as they arrive instead of waiting for
the full response. The async architecture already supports this.
