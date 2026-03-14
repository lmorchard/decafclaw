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

## Channel abstraction

Extract a channel interface so the bot isn't Mattermost-specific.
Terminal mode is already a second "channel." Could add Discord, Slack,
IRC, or a simple HTTP API. The event bus and context are already
channel-agnostic — the main coupling is in `mattermost.py`.

## Graceful shutdown

Handle SIGTERM properly: finish in-flight agent turns, unsubscribe
from the event bus, close the websocket cleanly. Currently a kill
just drops everything.

## Feed SSE stream into prompt

The automate/research SSE events could be fed into the LLM as
incremental context, letting it reason about partial results as
they arrive. Deferred from the Tabstack tools session.
