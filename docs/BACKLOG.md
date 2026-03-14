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

## Feed SSE stream into prompt

The automate/research SSE events could be fed into the LLM as
incremental context, letting it reason about partial results as
they arrive. Deferred from the Tabstack tools session.
