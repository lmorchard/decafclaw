# Plan: DecafClaw Initial Agent

Build a minimal working agent in incremental steps. Each step produces
something runnable and testable before moving to the next.

---

## Step 1: Project scaffold with uv

**Context:** Empty repo with just the spec. Need a Python project
structure that's easy to run and iterate on.

**What to do:**
- `uv init` to create the project
- Add dependencies: `httpx`, `websockets`
- Create `decafclaw/agent.py` as the entry point
- Create `decafclaw/config.py` for env var / config loading
- Add a `.env.example` with the required vars
- Verify `uv run python -m decafclaw.agent` starts without errors

**After this step:** A Python project that runs and exits cleanly.

---

## Step 2: LLM client — talk to LiteLLM

**Context:** Project exists but can't do anything. Start with the
simplest useful thing: send a message to the LLM and print the response.

**What to do:**
- Write a `call_llm(messages, tools=None)` function that POSTs to
  the LiteLLM `/v1/chat/completions` endpoint using httpx
- Parse the response: extract `choices[0].message.content` and
  `choices[0].message.tool_calls` if present
- Load the LLM URL and model name from config/env
- Test it: hardcode a prompt, call the LLM, print the response
- Verify it works: `uv run python -m decafclaw.agent` should print
  a response from Gemini

**After this step:** Can talk to the LLM from Python. No tools, no
Mattermost — just stdin/stdout proof that the API works.

---

## Step 3: Tool definitions and execution

**Context:** Can talk to the LLM. Now teach it about tools.

**What to do:**
- Define 3 tools as plain Python functions:
  - `shell(command)` → run subprocess, return stdout+stderr
  - `read_file(path)` → read and return file contents
  - `web_fetch(url)` → GET a URL, return body text
- Build the OpenAI-format `tools` array from the function signatures
  (hand-written JSON schema, not auto-generated — keep it visible)
- Write `execute_tool(name, arguments)` that dispatches to the right
  function and returns the result as a string

**After this step:** Tools exist and can be called, but nothing invokes
them yet. They're just functions + JSON descriptions.

---

## Step 4: The agent loop

**Context:** Have LLM client + tool definitions. Wire them together.

**What to do:**
- Write the core loop:
  1. Build messages array: system prompt + conversation history + user message
  2. Call LLM with messages + tools
  3. If response has tool_calls: execute each, append results, loop back to 2
  4. If response has content only: that's the final answer
- Cap tool iterations (e.g. max 10) to prevent infinite loops
- Keep conversation history as a simple list of message dicts
- Test interactively: read from stdin, print responses
- Try asking it to do things that require tools ("list files in the
  current directory", "what's on example.com")

**After this step:** A working agent in the terminal. No Mattermost yet,
but the core loop works. This is the most important step — spend time
here understanding what happens at each iteration.

---

## Step 5: Connect to Mattermost

**Context:** Agent loop works in the terminal. Now connect it to a real
chat channel.

**What to do:**
- Write Mattermost WebSocket connection:
  - Connect to `wss://{server}/api/v4/websocket`
  - Authenticate with bot token
  - Listen for `posted` events
  - Parse the double-encoded post JSON
  - Ignore own messages, filter by bot user ID
- Write Mattermost REST send:
  - POST to `/api/v4/posts` with channel_id + message
  - Handle threading (optional — can skip initially)
- Replace stdin/stdout with Mattermost receive/send
- Load Mattermost URL + bot token from config/env
- Test: message the bot on Mattermost, get a response

**After this step:** The agent is live on Mattermost. Same loop as
step 4, just with a real channel instead of stdin.

---

## Step 6: Polish and experiment

**Context:** Working agent on Mattermost with tools. Now the
interesting part — experimentation.

**What to do:**
- Add `write_file` tool (held back until now to keep early steps simpler)
- Tune the system prompt — start minimal and add only what's needed
- Add basic error handling (LLM timeouts, Mattermost reconnection,
  tool execution failures)
- Add logging so you can see what the agent is doing
- Try the experiments from the spec:
  - Strip the system prompt to nothing — what happens?
  - Change conversation history length — what's the sweet spot?
  - Move instructions between system prompt and tool descriptions

**After this step:** A working, instrumented agent you can experiment
with. The code should still be under 500 lines.

---

## File layout after completion

```
decafclaw/
├── pyproject.toml
├── .env.example
├── .env              (gitignored)
└── decafclaw/
    ├── __init__.py
    ├── agent.py      (the loop — main entry point)
    ├── llm.py        (call_llm function)
    ├── tools.py      (tool definitions + execute_tool)
    ├── mattermost.py (WebSocket + REST client)
    └── config.py     (env var loading)
```

Split into a few files for readability, but no abstractions. Each file
is a flat collection of functions. No classes except maybe a dataclass
for config.
