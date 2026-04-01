# User Memory — Plan

## Architecture Overview

The dependency order:

1. **Config** — add `DATA_HOME`, `AGENT_ID`, `AGENT_USER_ID`
2. **Context enrichment** — Mattermost and terminal layers populate user/channel/thread on forked context
3. **Memory module** — pure Python memory read/write operations (no tool wiring yet)
4. **Memory tools** — tool functions wrapping the memory module, using ctx
5. **Wire into registry + system prompt** — register tools, update prompt
6. **Smoke test** — verify end-to-end

Each step builds on the previous and ends with working code.

---

## Step 1: Config additions

Add workspace/memory config to `Config` and `load_config`.

### Prompt

```
Add three new config fields to src/decafclaw/config.py:

- data_home: str = "./data"         # base data directory
- agent_id: str = "decafclaw"       # agent identity
- agent_user_id: str = ""           # single configured user (temporary)

Wire them in load_config:
- data_home=os.getenv("DATA_HOME", Config.data_home)
- agent_id=os.getenv("AGENT_ID", Config.agent_id)
- agent_user_id=os.getenv("AGENT_USER_ID", Config.agent_user_id)

Add a helper property to Config:
- workspace_path -> returns Path(self.data_home) / "workspace" / self.agent_id

No other changes.
```

---

## Step 2: Context enrichment

Populate user/channel/thread metadata on the forked request context so
memory tools can access it.

### Prompt

```
Update the context fork calls to include user/channel/thread metadata.

File: src/decafclaw/mattermost.py — in _process_conversation:
Currently: req_ctx = app_ctx.fork()
Change to: req_ctx = app_ctx.fork(
    user_id=app_ctx.config.agent_user_id,
    channel_id=channel_id,
    channel_name="",  # we don't have the name easily, empty for now
    thread_id=root_id or "",
)

File: src/decafclaw/agent.py — in run_interactive:
The interactive context (ctx) should also have sensible defaults.
Before the main loop, set:
    ctx.user_id = getattr(ctx, "user_id", None) or ctx.config.agent_user_id
    ctx.channel_id = getattr(ctx, "channel_id", "") or "interactive"
    ctx.channel_name = getattr(ctx, "channel_name", "") or "interactive"
    ctx.thread_id = getattr(ctx, "thread_id", "") or ""

This ensures memory tools always find these attributes on ctx regardless
of which mode the agent is running in.
```

---

## Step 3: Memory module

Create the core memory operations as a standalone module. No tools yet —
just functions that read and write memory files.

### Prompt

```
Create a new file src/decafclaw/memory.py with pure Python memory operations.

The module needs:
- A helper to compute the memory directory path:
  def memory_dir(config, user_id) -> Path:
      return Path(config.data_home) / "workspace" / config.agent_id / "memories" / user_id

- def save_entry(config, user_id, channel_name, channel_id, thread_id,
                 tags: list[str], content: str) -> str:
    Appends a markdown entry to today's file.
    - Compute path: memory_dir(config, user_id) / str(now.year) / f"{now:%Y-%m-%d}.md"
    - Create directories with parents=True, exist_ok=True
    - Format the entry as markdown:
      ## YYYY-MM-DD HH:MM

      - **channel:** {channel_name} ({channel_id})
      - **thread:** {thread_id}
      - **tags:** tag1, tag2

      {content}
    - Append to the file (open with "a")
    - Return a confirmation like "Saved memory tagged [preference, communication]"

- def search_entries(config, user_id, query: str, context_lines: int = 3) -> str:
    Search all memory files for a user using case-insensitive substring matching.
    - Walk memory_dir(config, user_id) for all .md files
    - Read each file, split into lines
    - Find lines containing the query (case-insensitive)
    - For each match, include context_lines before and after
    - Deduplicate overlapping context windows
    - Return the combined results as a string, prefixed with filename
    - If no matches: return "No memories found matching '{query}'"

- def recent_entries(config, user_id, n: int = 5) -> str:
    Return the last N memory entries.
    - Walk memory_dir(config, user_id) for all .md files
    - Sort files by name descending (YYYY-MM-DD.md sorts naturally)
    - Read files from most recent, split on "## " to find entries
    - Collect entries in reverse chronological order until we have n
    - Return as a string
    - If no entries: return "No memories found"

Use pathlib.Path throughout. Use logging for debug output.
Import datetime for timestamps.
```

---

## Step 4: Memory tools

Create tool functions that wrap the memory module, pulling context
from ctx.

### Prompt

```
Create a new file src/decafclaw/tools/memory_tools.py with tool functions.

Each tool function takes ctx as its first parameter and delegates to
the memory module.

- def tool_memory_save(ctx, tags: list[str], content: str) -> str:
    Pull user_id, channel_name, channel_id, thread_id from ctx.
    Call memory.save_entry(ctx.config, user_id, channel_name,
        channel_id, thread_id, tags, content).
    Return the result.

- def tool_memory_search(ctx, query: str, context_lines: int = 3) -> str:
    Pull user_id from ctx.
    Call memory.search_entries(ctx.config, user_id, query, context_lines).
    Return the result.

- def tool_memory_recent(ctx, n: int = 5) -> str:
    Pull user_id from ctx.
    Call memory.recent_entries(ctx.config, user_id, n).
    Return the result.

Add the registries:

MEMORY_TOOLS = {
    "memory_save": tool_memory_save,
    "memory_search": tool_memory_search,
    "memory_recent": tool_memory_recent,
}

MEMORY_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "memory_save",
            "description": "Save a memory about the user for future conversations. Use this when you learn a preference, fact, project context, or anything worth remembering. Memories persist across restarts.",
            "parameters": {
                "type": "object",
                "properties": {
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Free-form tags to categorize this memory (e.g., 'preference', 'project', 'fact')",
                    },
                    "content": {
                        "type": "string",
                        "description": "The memory content to save",
                    },
                },
                "required": ["tags", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_search",
            "description": "Search your memories about the user. Use this when the user references a preference, prior conversation, or fact you don't have in your current context. Returns matching entries with surrounding context.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Text to search for (case-insensitive substring match)",
                    },
                    "context_lines": {
                        "type": "integer",
                        "description": "Number of surrounding lines to include (default 3)",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_recent",
            "description": "Recall your most recent memories about the user. Use this at the start of a conversation to refresh your context about who you're talking to and what you've discussed before.",
            "parameters": {
                "type": "object",
                "properties": {
                    "n": {
                        "type": "integer",
                        "description": "Number of recent memories to return (default 5)",
                    },
                },
                "required": [],
            },
        },
    },
]

All tool functions are sync (they do file I/O, which is fine in to_thread).
Import from decafclaw.memory for the actual operations.
```

---

## Step 5: Wire into tool registry and update system prompt

Register memory tools and update the system prompt.

### Prompt

```
Wire memory tools into the tool registry and update the system prompt.

File: src/decafclaw/tools/__init__.py
- Import MEMORY_TOOLS and MEMORY_TOOL_DEFINITIONS from .memory_tools
- Add to combined registry:
  TOOLS = {**CORE_TOOLS, **TABSTACK_TOOLS, **MEMORY_TOOLS}
  TOOL_DEFINITIONS = CORE_TOOL_DEFINITIONS + TABSTACK_TOOL_DEFINITIONS + MEMORY_TOOL_DEFINITIONS

File: src/decafclaw/config.py
- Update the default system_prompt to include memory instructions.
  Append to the existing default:
  "\n\nYou have a persistent memory system. At the start of each conversation, "
  "use memory_search or memory_recent to recall relevant context about the user. "
  "When you learn something worth remembering — a preference, a fact, project "
  "context — use memory_save to store it for future conversations."

Run make lint && make test to verify everything compiles and imports.
```

---

## Step 6: Smoke test and cleanup

Verify end-to-end and clean up.

### Prompt

```
Review all changes for consistency and verify:

1. make lint && make test pass.
2. Verify the memory file operations work:
   - Write a quick Python snippet that creates a context with the right
     attributes and calls save_entry, then search_entries, then recent_entries.
   - Check the created file on disk looks correct.
3. Check that ./data/ is in .gitignore (agent workspace data should not
   be committed).
4. Add ./data/ to .gitignore if not already there.
5. Verify the tool definitions have correct parameter schemas.
6. Check that the Mattermost fork populates all needed context fields.
7. Check that interactive mode populates all needed context fields.
```

---

## Implementation Order Summary

| Step | Files Changed | What It Does |
|------|--------------|--------------|
| 1 | `config.py` | Add DATA_HOME, AGENT_ID, AGENT_USER_ID |
| 2 | `mattermost.py`, `agent.py` | Populate user/channel/thread on context |
| 3 | `memory.py` (new) | Core memory read/write operations |
| 4 | `tools/memory_tools.py` (new) | Tool wrappers around memory module |
| 5 | `tools/__init__.py`, `config.py` | Wire into registry, update system prompt |
| 6 | `.gitignore`, all | Smoke test and cleanup |

## Risk Notes

- **File I/O in tools** — memory tools do sync file I/O, which is fine since
  `execute_tool` wraps sync tools in `asyncio.to_thread`. No blocking the event loop.
- **No user_id in context** — if `AGENT_USER_ID` is empty and no user_id is set on
  context, the tools should fail gracefully with a clear error rather than writing
  to an empty path.
- **Concurrent writes** — two conversations could append to the same daily file
  simultaneously. For append-only markdown this is low-risk (appends are generally
  atomic on Linux for small writes), but worth noting.
