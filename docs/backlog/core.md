# Core Modules Backlog

These shape the agent loop, context, and fundamental infrastructure.
Not portable as skills — they're the platform skills run on.

## More flexible config

Environment variables only go so far for configuration, we should support
JSON and/or YAML for more expressive and organized configuration.

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

## Max message length

Truncate or reject absurdly long messages before sending to the LLM.
Prevents context window abuse and accidental paste bombs.

## Tool execution resilience

Tools that use anyio (e.g., MCP via the `mcp` SDK) can leak cancel
scopes back to the calling task, crashing the agent. Currently
mitigated with `asyncio.shield` in MCP restart and `BaseException`
catch in `_process_conversation`.

Broader concerns:
- Tool timeout enforcement (not just MCP — any tool could hang)
- Cancel scope isolation for all external tool calls
- Per-tool error budget / circuit breaker (N failures → disable tool)
- Graceful degradation when a tool repeatedly fails mid-turn

Related: anyio and raw asyncio don't always play nicely together.
The MCP SDK uses anyio internally while DecafClaw uses raw asyncio.

