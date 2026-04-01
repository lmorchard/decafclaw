# Plan: Tabstack Tools for DecafClaw

Four steps, building on the working agent from the previous session.

---

## Step 1: Refactor tools into a package + add config

**Context:** tools.py is a single file with 3 tools. Adding 5 more would
make it unwieldy. Restructure first, then add new tools.

**What to do:**
- Convert `src/decafclaw/tools.py` → `src/decafclaw/tools/__init__.py`
  - Move shell, read_file, web_fetch into `tools/core.py`
  - `__init__.py` re-exports TOOLS, TOOL_DEFINITIONS, execute_tool
  - Verify existing tools still work after the refactor
- Add `TABSTACK_API_KEY` and `TABSTACK_API_URL` to config.py
  (URL defaults to production, overridable for dev/stage)
- Add both to `.env.example`
- `uv add tabstack`

**After this step:** Same behavior as before, but tools are in a
package ready for new additions. Tabstack SDK is installed.

---

## Step 2: Add Tabstack tools

**Context:** Tools package exists, SDK installed. Add the five
Tabstack tool functions and their OpenAI schemas.

**What to do:**
- Create `tools/tabstack.py` with 5 functions:
  - `tool_tabstack_extract_markdown(url)` — call SDK, return content
  - `tool_tabstack_extract_json(url, json_schema)` — call SDK, return JSON
  - `tool_tabstack_generate(url, json_schema, instructions)` — call SDK, return JSON
  - `tool_tabstack_automate(task, url=None)` — iterate SSE stream, log progress, return final answer
  - `tool_tabstack_research(query, mode="balanced")` — iterate SSE stream, log progress, return final answer
- Hand-write the 5 TOOL_DEFINITIONS entries with descriptions that
  help the LLM choose correctly:
  - extract_markdown: "Read a web page or PDF as clean, readable
    Markdown. Better than raw HTTP fetch for getting article content."
  - web_fetch (existing): "Fetch raw HTML from a URL. Use when you
    need the original markup, not cleaned content."
  - Etc. — descriptions should differentiate clearly
- Register all in tools/__init__.py
- Initialize Tabstack client once from config (not per-call)
- Test in interactive mode: "summarize https://example.com"

**After this step:** All 8 tools available. Agent can browse the web,
extract data, do research. Testable in terminal mode.

---

## Step 3: Mattermost UX — placeholder + typing indicator

**Context:** Tools work but Mattermost users see silence during
processing. Add immediate feedback.

**What to do:**
- Add to MattermostClient:
  - `send_placeholder(channel_id, root_id)` → posts "Thinking..."
    and returns the post ID
  - `edit_message(post_id, message)` → PUTs to update the post
  - `send_typing(channel_id)` → POST typing indicator
- Update `__init__.py` (_run_mattermost):
  - On message received: immediately send placeholder
  - Run agent loop
  - Edit placeholder with final response (instead of sending new post)
- Add typing indicator calls in execute_tool or agent loop when
  a tool starts executing
- Test on Mattermost: should see "Thinking..." immediately, then
  it gets replaced with the answer

**After this step:** Responsive UX on Mattermost. Users get instant
feedback and can see the bot is working.

---

## Step 4: Test and tune

**Context:** Everything works. Verify end-to-end and tune descriptions.

**What to do:**
- Test each acceptance criterion from the spec:
  - "Summarize this article" → extract_markdown
  - "Extract prices from this page" → extract_json
  - "Research quantum computing" → research
  - "List files" → shell (still works)
  - web_fetch vs extract_markdown — does the LLM choose well?
- Check tool descriptions — if the LLM consistently picks the wrong
  tool, adjust the description
- Verify progress logging for automate/research
- Check total line count (target: under ~700)
- Commit and update session notes

**After this step:** Polished, tested agent with 8 tools and good
Mattermost UX.

---

## File layout after completion

```
src/decafclaw/
├── __init__.py         (entry point, Mattermost runner)
├── agent.py            (the loop)
├── config.py           (env var loading)
├── llm.py              (LLM HTTP client)
├── mattermost.py       (WebSocket + REST + placeholder + typing)
└── tools/
    ├── __init__.py     (registry, TOOL_DEFINITIONS, execute_tool)
    ├── core.py         (shell, read_file, web_fetch)
    └── tabstack.py     (5 Tabstack tools)
```
