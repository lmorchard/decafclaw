# DIY Agent — Spec Notes

A minimal AI agent built for learning, not production. The goal is to
understand how agent frameworks work by building one from scratch, without
the complexity of OpenClaw/nanobot/picoclaw.

## Goals

- Understand the core agent loop (prompt → LLM → tool → loop → response)
- Experiment with prompt engineering for tool use
- Learn what makes agents feel "smart" vs "dumb"
- Keep it small enough to fit in one file initially

## Non-goals

- Plugin/skill system
- Multi-agent orchestration
- Session persistence (initially)
- Framework-level abstractions (channel traits, provider interfaces, etc.)

## Language: Python

Fastest iteration loop for a learning project. No compilation, change and
re-run. Libraries are the most mature for LLM interaction.

## Architecture: One file, one loop

```
Mattermost (WebSocket) → agent.py → LiteLLM (HTTP) → Gemini
                              ↕
                         Tool execution
```

### The core loop

```python
while True:
    message = receive_from_mattermost()
    prompt = build_prompt(system_prompt, history, message)

    while True:
        response = call_llm(prompt)

        if response.has_tool_calls:
            results = execute_tools(response.tool_calls)
            prompt.append(tool_results=results)
            continue
        else:
            send_to_mattermost(response.text)
            break
```

That's it. Everything else is details.

## Dependencies

- `httpx` — Mattermost REST API + LiteLLM calls
- `websockets` — Mattermost event stream
- `json` — everything else

No LLM SDK. Just raw HTTP to LiteLLM's OpenAI-compatible endpoint.

## Channel: Mattermost only

No channel abstraction. Hardcoded to one Mattermost server. Connect via
WebSocket, send via REST, same pattern as the picoclaw Mattermost channel
we built.

## LLM: LiteLLM only

Call `http://192.168.0.199:4000/v1/chat/completions` with the OpenAI
format. No provider abstraction. Model selection via config or env var.

## Tools: 3-4 hardcoded

Start with the minimum:

1. **shell** — run a command, return stdout/stderr
2. **web_fetch** — GET a URL, return the body (or use Tabstack extract-markdown)
3. **read_file** — read a local file
4. **write_file** — write a local file

Tools are just Python functions. No registry, no dynamic loading. Define
them as a dict of `{name: function}` and pass them to the LLM as the
`tools` parameter in the OpenAI format.

## Experiments to try

### Prompt minimalism
What's the least system prompt that produces useful tool-calling behavior?
Start with nothing and add instructions only when the agent fails.

### Tool selection as a separate step
Instead of one LLM call that both picks the tool and generates arguments,
try: "Which tool should I use?" → "Now use it with these arguments."
Does this improve accuracy?

### Result verification
After a tool call returns, ask the LLM: "Did this result answer the
question?" before sending the response to the user. Does this catch
bad tool calls?

### Context window hygiene
How does conversation history length affect response quality? Try
aggressive truncation (last 5 messages only) vs full history.
At what point does more context hurt rather than help?

### System prompt vs tool descriptions
Move instructions from the system prompt into tool descriptions and
vice versa. Where does guidance have the most impact on behavior?

## Infrastructure

Already have everything needed:
- LiteLLM on synobian (Vertex AI → Gemini models)
- Mattermost at comms.lmorchard.com
- Can run the agent on any Proxmox VM or even locally

## What this is NOT

This is not a framework, not a product, not a replacement for
OpenClaw/nanobot/picoclaw. It's a learning tool. The code should be
simple enough that every line is understandable and every design
decision is intentional.

If it grows beyond ~500 lines, something has gone wrong.
