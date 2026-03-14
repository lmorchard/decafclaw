# DecafClaw — Backlog

Future session ideas and enhancements.

## Live tool progress in placeholder messages

Update the "Thinking..." placeholder with real-time progress from tools.
For example, during a research call:

- "Researching... Searching with 8 queries"
- "Researching... Analyzing 13 pages"
- "Researching... Writing report"

**Design:** Give tools a callback function that edits the placeholder.
The callback needs to bridge sync tools → async Mattermost client.
Options: thread an event loop through, use a queue, or make tools async.

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

## Feed SSE stream into prompt

The automate/research SSE events could be fed into the LLM as
incremental context, letting it reason about partial results as
they arrive. Deferred from the Tabstack tools session.
