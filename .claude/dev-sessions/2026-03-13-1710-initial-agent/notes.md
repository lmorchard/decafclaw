# Session Notes

## Steps 1-4: Scaffold + LLM + Tools + Agent Loop

Implemented in one pass since the pieces are small and interdependent.

- uv project with httpx, websockets, python-dotenv
- LLM client: raw httpx POST to LiteLLM, no SDK
- 3 tools: shell, read_file, web_fetch — hand-written OpenAI schemas
- Agent loop with tool iteration and max iteration cap
- Interactive terminal mode works end-to-end
- Tested: "hi" → direct response, "list files" → shell tool → response

## Step 5: Mattermost

Mattermost client written and tested live. Bot connects, receives
messages, runs the agent loop, and responds with tool results.

Tested on Mattermost:
- "Can you list my home directory?" → shell tool → clean response
- "Can you fetch headlines from Hacker News?" → web_fetch on HN → extracted headlines

## File count

6 source files in `src/decafclaw/`, ~495 lines total.

---

# Retrospective

## Recap

Built DecafClaw — a minimal AI agent in Python that connects to
Mattermost and can use tools (shell, read_file, web_fetch) via a
LiteLLM/Vertex AI backend. The entire thing is ~495 lines across 6
files, with no frameworks, no SDKs, no abstractions beyond plain
functions.

Completed steps 1-5 of the plan in one session. Step 6 (polish and
experimentation) is future work.

## Divergences from plan

- **Steps 1-4 collapsed into one pass.** The plan had them as separate
  steps but they're so interdependent (can't test the LLM client without
  a config loader, can't test tools without the agent loop) that doing
  them together was faster.
- **Mattermost tested immediately** rather than spending time in
  interactive mode. The interactive mode worked on first try, so we
  moved straight to Mattermost.
- **No write_file tool yet.** Plan had it in step 6. Not needed for the
  initial proof of concept.

## Insights

- **The agent loop is genuinely simple.** The core logic in `agent.py`
  is about 50 lines. The rest is plumbing (config, HTTP, WebSocket).
  This confirms the spec's hypothesis that agent frameworks add a lot
  of complexity on top of a very simple core.

- **Tool descriptions matter more than system prompt.** With just
  "You are a helpful assistant" as the system prompt, the model
  correctly chose `shell` for directory listing and `web_fetch` for
  HN — purely from reading the tool descriptions.

- **Raw HTTP is fine.** No OpenAI SDK needed. The chat completions
  API is just a POST with JSON. httpx handles it in ~15 lines.

- **The model identifies itself as Google-trained** because we're
  using Gemini and gave it no identity. This is a good baseline for
  experimenting with system prompt impact.

- **Mattermost WebSocket + REST pattern** is well-understood from
  the picoclaw PR work. Rewriting it in Python was straightforward.

## Efficiency

Very fast session — from empty repo to working Mattermost bot in
about 30 minutes of implementation time. The plan paid off: having
the file layout and dependency list pre-decided meant no deliberation
during coding.

## Process improvements

- For a project this small, the 6-step plan was more granular than
  needed. Steps 1-4 were naturally one unit of work.
- The interactive mode (`make run` with stdin) is valuable for quick
  iteration without needing Mattermost running.

## Next steps

From the spec's experiment list:
- Strip system prompt to nothing — what happens?
- Try aggressive history truncation
- Add write_file tool
- Add logging/observability to see exactly what goes to the LLM
- Try the "tool selection as separate step" pattern
- Deploy to a Proxmox VM for persistent running
