# Spec: Tabstack Tools for DecafClaw

## What

Add all five Tabstack API endpoints as native tools in DecafClaw, using
the Python Tabstack SDK. This gives the agent web browsing, data
extraction, content transformation, research, and browser automation
capabilities.

## Tools to add

1. **tabstack_extract_markdown** — read a page or PDF as clean Markdown
2. **tabstack_extract_json** — pull structured data using a JSON schema
3. **tabstack_generate** — transform web/PDF content with LLM instructions
4. **tabstack_automate** — multi-step browser automation in natural language
5. **tabstack_research** — search the web and synthesize answers from multiple sources

## Implementation approach

- **Use the Python Tabstack SDK** (`tabstack` package, v2.3.0) rather
  than raw HTTP. Dogfooding the SDK, and the SSE streaming for automate/
  research is handled by the SDK.

- **Streaming tools (automate, research)**: block until complete, log
  progress events to the process log. Don't send progress to Mattermost
  yet — but do show a typing indicator while the tool is running.

- **Keep existing `web_fetch` tool** alongside `tabstack_extract_markdown`.
  Let the LLM decide which to use based on tool descriptions. `web_fetch`
  returns raw HTML, `extract_markdown` returns clean readable content.
  Interesting to see which the LLM prefers.

- **Refactor tools.py into a `tools/` subdirectory** to keep things
  organized as the tool count grows.

## Config

- `TABSTACK_API_KEY` env var, loaded in config.py
- Added to `.env.example`

## Mattermost UX improvements

**Placeholder message:** When a message comes in, immediately post
"Thinking..." to the channel. When the agent finishes, edit that
message with the actual response. This gives instant feedback instead
of silence during LLM + tool execution.

- POST `/api/v4/posts` → get post ID
- PUT `/api/v4/posts/{id}/patch` → replace with final response

**Typing indicator:** Send typing indicator while tools are running,
especially for automate/research which can take 30-120s.

- POST `/api/v4/users/me/typing` with channel_id

## Acceptance criteria

- All 5 Tabstack tools work end-to-end via Mattermost
- "Summarize this article" uses extract_markdown
- "Extract prices from this page" uses extract_json with a schema
- "Research quantum computing" uses research
- Typing indicator shows while tools are running
- Progress events from automate/research appear in process log
- Existing tools (shell, read_file, web_fetch) still work
- Total source stays under ~700 lines

## Out of scope

- Feeding SSE stream into prompts
- Progress messages to Mattermost
- Geo-targeting, guardrails, --data flags (can add later)
- Persistent conversation history
