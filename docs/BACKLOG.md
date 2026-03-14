# DecafClaw — Backlog

Future session ideas and enhancements.

## ~~Live tool progress in placeholder messages~~ (DONE)

Implemented via event bus, runtime context, and async agent loop.

## Bot/channel allowlists

Allow listening to specific bots in specific channels. For example,
respond to messages from a CI bot in #deployments, or relay messages
from another chat bot. Currently bots are globally ignored/allowed
via `MATTERMOST_IGNORE_BOTS`.

**Design:** Config could take a list of `bot_username:channel_id` pairs,
or separate allowlists for bot usernames and channel IDs.

## Agent workspace sandbox

The agent should have a dedicated filesystem workspace directory
(e.g., `workspace/` or configurable via `AGENT_WORKSPACE`). File
tools (`read_file`, `write_file`, `shell`) should be confined to
this directory by default.

**Design ideas:**
- Config: `AGENT_WORKSPACE=/path/to/workspace` (base path)
- Per-agent subdirectory: `workspace/{agent_id}/` — each agent gets
  its own isolated workspace, anticipating multi-agent setups
- `read_file` and `write_file` resolve paths relative to workspace,
  reject anything outside it (no `../../etc/passwd`)
- `shell` runs with `cwd` set to workspace
- Memories, to-do lists, and other agent-owned files live here
- Explicit permission model: agent can request access outside the
  workspace, user must approve (pairs with tool confirmation flow)
- Could use the context to carry the workspace path so tools
  discover it naturally

**Why now:** As we add memory files, to-do lists, and potentially
file attachments, the agent needs a place to put things. Without
a sandbox, those files could end up anywhere.

## Max message length

Truncate or reject absurdly long messages before sending to the LLM.
Prevents context window abuse and accidental paste bombs.

## Experiments from the spec

- Strip the system prompt to nothing — what happens?
- Tool selection as a separate LLM call
- Result verification ("did this answer the question?")
- Context window hygiene — aggressive truncation vs full history
- Move instructions between system prompt and tool descriptions

## Additional tools

- `write_file` — write content to a local file
- Tabstack `automate` with `--guardrails` and `--data` support
- Tabstack geo-targeting (`--geo CC`) for region-specific content

## Deployment

- Run DecafClaw on a Proxmox VM as a persistent service
- Systemd user service (like picoclaw)
- Or Docker container

## Per-conversation to-do list

Give the agent a scratchpad to plan multi-step work. Complements memory
(long-term) with short-term intent tracking within a conversation.

**Tools:**
- `todo_add(item)` — add an item to the list
- `todo_complete(index)` — mark an item done
- `todo_list()` — show current state
- `todo_clear()` — reset the list

**Design notes:**
- Lives in-memory on the context or alongside conversation history
- Ephemeral — dies with the conversation (or could persist if history persists)
- Agent could post the to-do list in Mattermost and update it as items
  complete, similar to placeholder progress updates
- Pairs naturally with memory: agent plans in to-do, saves learnings to memory

## Conversation management

- Persistent conversation history (SQLite?)
- History truncation strategies
- Per-user history in channels (not just per-channel)
- Session reset command

## File attachments as a channel capability

Some channels (like Mattermost) support sending files alongside messages.
Expose this as a capability that the agent and tools can use — e.g., a
tool could generate a report and attach it as a file, or the agent could
send an image result from a Tabstack automation.

**Design:** The context or channel abstraction could advertise capabilities
(e.g., `supports_file_upload`). Tools and the agent could use a
`send_file(channel, filename, data)` primitive. Mattermost's file upload
API (`POST /files`) supports this natively.

## Streaming LLM responses

Stream tokens to the Mattermost placeholder as they arrive instead of
waiting for the full response. The async architecture already supports
this. Would make the bot feel much more responsive for long answers.

## Tool confirmation / approval flow

Before executing dangerous tools (`shell`, `tabstack_automate`), ask the
user for confirmation in Mattermost. "I'm about to run `rm -rf /tmp/data`.
React with :+1: to confirm." Could use Mattermost reactions as an
approval mechanism.

## Conversation summarization

When history exceeds a token budget, summarize older messages to stay
within the context window. Different from simple truncation — the agent
retains the gist of earlier conversation. Could use a cheap/fast model
for the summarization step.

## Multi-model routing

Use a fast model for simple questions, a more capable model for complex
ones. The context fork design already supports different configs per fork.
Could be automatic (let a classifier decide) or explicit (user says
"think harder about this").

## User memory across conversations

Remember things about users across conversations using a directory of
daily markdown files per user, searchable via grep.

**Structure:**
```
memories/
  lmorchard/
    2026/
      2026-03-13.md
      2026-03-14.md
```

**Entry format:**
```markdown
## 2026-03-13 22:45

- **channel:** Meta-Decafclaw (3abxtztu9t81ff7r3z4donjcua)
- **thread:** og3ye9rh
- **tag:** preference

Les prefers concise answers and doesn't like summaries of what was just done.
```

**Tools:**
- `memory_save(user_id, tag, content)` — appends entry with timestamp,
  channel/thread pulled from context automatically
- `memory_search(query, user_id=None, context_lines=3)` — greps across
  memory files with `-B`/`-C` for surrounding context. Optional user filter.
- `memory_recent(user_id, n=5)` — last N entries for a user, for quick
  recall at conversation start

**Design notes:**
- Per-user directories, daily files, append-only
- Human-readable and editable — just markdown
- Grep is fast even over hundreds of files
- Agent decides when to remember, or user says "remember this"
- Each entry carries channel/thread/tag metadata for context

## Scheduled / recurring tasks

"Check this URL every hour and tell me if it changes." Would use the
event bus naturally. Needs a scheduler and a way to store task
definitions (SQLite?).

## Observability and metrics

The event bus already sees every lifecycle event. Feed them into
metrics — response times, tool usage frequency, error rates, circuit
breaker trips. Could be as simple as a log-based subscriber or a
Prometheus endpoint.

## Channel management tools

Give the agent tools to manage the communication channel itself, not
just send messages. On Mattermost, with the right permissions:

- `create_channel(name, purpose)` — spin up a new channel for a topic
- `invite_user(channel, user)` — invite a user to a channel
- `set_channel_header(channel, text)` — update channel header/purpose
- `archive_channel(channel)` — archive when done

**Use cases:**
- Agent is researching a complex topic, creates a dedicated channel
  to keep the discussion organized and invites the user
- Agent spins up a "war room" channel for an incident
- Agent creates a channel per project/task to keep threads separate
- Could pair with to-do lists — one channel per to-do, agent works
  through items and posts results

**Design notes:**
- Needs Mattermost bot permissions: `create_public_channel`,
  `create_private_channel`, `add_channel_members`
- Should be exposed as agent tools, gated by config/permissions
- The channel abstraction (below) would make this portable across
  platforms

## Channel abstraction

Extract a channel interface so the bot isn't Mattermost-specific.
Terminal mode is already a second "channel." Could add Discord, Slack,
IRC, or a simple HTTP API. The event bus and context are already
channel-agnostic — the main coupling is in `mattermost.py`.

## Graceful shutdown

Handle SIGTERM properly: finish in-flight agent turns, unsubscribe
from the event bus, close the websocket cleanly. Currently a kill
just drops everything.

## Skills system with progressive resource loading

Claude Code / OpenClaw-style skills: bundled knowledge + tools that the
agent can discover and load on demand rather than stuffing everything
into the system prompt upfront.

**Design ideas:**
- Skills live in a directory (e.g., `skills/web-research/`)
- Each skill has a manifest: name, description, trigger conditions,
  and a list of resources (prompt fragments, tool definitions, examples)
- Agent sees a lightweight skill index in its system prompt
- When a skill is relevant, the agent loads its resources progressively —
  fetch the prompt fragment, register the tools, load examples as needed
- Keeps the base context small while enabling deep specialization
- Skills could be first-party (bundled) or user-defined

**Parallels:**
- Claude Code's skill system with selective resource fetch
- OpenClaw's progressive context loading
- MCP's tool discovery (see below)

## MCP server support

Support Model Context Protocol (MCP) servers as additional tool
providers. The agent could connect to external MCP servers and
use their tools alongside built-in ones.

**Design ideas:**
- Config lists MCP server endpoints (stdio or HTTP/SSE)
- On startup, discover available tools from each server
- Merge MCP tool definitions into the tool registry alongside
  core and Tabstack tools
- `execute_tool` routes MCP tool calls to the appropriate server
- The async architecture already supports this — MCP calls are
  just async tool executions
- Could be bidirectional: DecafClaw itself could expose an MCP
  server interface, letting other agents use its tools

**Use cases:**
- Connect to a database MCP server for SQL queries
- Connect to a GitHub MCP server for repo management
- Connect to a home automation MCP server
- Let users extend the agent without modifying core code

## Feed SSE stream into prompt

The automate/research SSE events could be fed into the LLM as
incremental context, letting it reason about partial results as
they arrive. Deferred from the Tabstack tools session.
