# User Memory

## Overview

Give the agent persistent memory across conversations using a directory
of daily markdown files per user, searchable via substring grep. The agent
can save observations, preferences, and facts, then recall them in future
conversations.

## Goals

- Agent remembers things about users across restarts and conversations
- Human-readable and editable — just markdown files
- Zero infrastructure (no database, no vector store)
- Tools are general-purpose — read user/channel/thread from context, not config
- Clear upgrade path to linking, RAG, and multi-user

## Data Layout

```
{DATA_HOME}/workspace/{agent_id}/memories/{user_id}/{year}/{date}.md
```

- `DATA_HOME` — configurable via env var, default `./data`
- `agent_id` — configurable via env var, default `decafclaw`
- `user_id` — read from context (populated by the channel layer)
- Year directory prevents flat directories of 365+ files
- One file per day, append-only

### Config additions

```
DATA_HOME=./data                 # base data directory
AGENT_ID=decafclaw               # agent identity
AGENT_USER_ID=lmorchard          # single configured user (for now)
```

`AGENT_USER_ID` is a temporary convenience. The Mattermost layer maps all
messages to this user. Future work: multi-user mapping from channel user IDs
to agent user IDs.

## Entry Format

```markdown
## 2026-03-13 22:45

- **channel:** Meta-Decafclaw (3abxtztu9t81ff7r3z4donjcua)
- **thread:** og3ye9rh
- **tags:** preference, communication

Les prefers concise answers and doesn't like summaries of what was just done.
```

- Timestamp: `YYYY-MM-DD HH:MM`
- Channel/thread metadata: pulled from context automatically
- Tags: free-form strings, multiple allowed, comma-separated
- Content: free-form text

## Context Changes

The forked request context must carry channel/thread/user metadata so
memory tools can access it without the tools knowing about Mattermost.

The Mattermost layer populates these on the forked context:
- `ctx.user_id` — the agent-level user ID (from config for now)
- `ctx.channel_name` — human-readable channel name (if available)
- `ctx.channel_id` — Mattermost channel ID
- `ctx.thread_id` — Mattermost thread root ID (or empty)

The interactive (terminal) layer should also populate sensible defaults.

## Tools

### `memory_save(tags: list[str], content: str)`

Appends a memory entry to the current day's file for the user from context.

- `tags` — list of free-form tag strings
- `content` — the memory text
- User ID, channel, thread, timestamp are pulled from context automatically
- Creates the directory structure and file if they don't exist
- Returns a confirmation message

### `memory_search(query: str, context_lines: int = 3)`

Searches across all memory files for the user from context using substring
matching.

- `query` — substring to search for (case-insensitive)
- `context_lines` — number of lines of surrounding context (like grep -C)
- Searches the user's entire memory directory
- Returns matching entries with context
- Pure Python implementation (no shelling out to grep)

### `memory_recent(n: int = 5)`

Returns the last N memory entries for the user from context.

- `n` — number of recent entries to return (default 5)
- Reads from the most recent daily files, walking backwards
- Returns entries in reverse chronological order

## System Prompt Changes

Add to the system prompt:

> You have a persistent memory system. At the start of each conversation,
> use `memory_search` or `memory_recent` to recall relevant context about
> the user. When you learn something worth remembering — a preference,
> a fact, project context — use `memory_save` to store it for future
> conversations.

## Workspace Directory

This session establishes the workspace directory pattern:

```
./data/                          # DATA_HOME
  workspace/
    decafclaw/                   # AGENT_ID
      memories/
        lmorchard/               # user_id (from context)
          2026/
            2026-03-13.md
```

The workspace sandbox (restricting file tools to this directory) is
deferred to a future session.

## Scope

### In scope

- Config: `DATA_HOME`, `AGENT_ID`, `AGENT_USER_ID`
- Workspace directory structure
- Context changes: user_id, channel_name, channel_id, thread_id on forked context
- Memory tools: `memory_save`, `memory_search`, `memory_recent`
- System prompt update to encourage memory use
- Wire tools into the tool registry

### Out of scope (future)

- `related_to` / `supersedes` entry linking
- RAG / vector embedding search
- Multi-user mapping (channel user → agent user)
- Multi-agent workspaces
- Workspace filesystem sandbox enforcement
- Memory pruning or archival

## Comparison to production memory systems

This is a deliberately minimal first implementation. Here's how it
compares to what production agent frameworks offer, to guide future work.

**What this approach does well:**
- Human-readable and debuggable — most systems use opaque databases
- Metadata-rich entries (channel, thread, tags) vs. typical key-value stores
- Agent controls its own memory (decides what to save, not a framework)
- No vendor lock-in, no infrastructure, no embedding model costs
- Works with any LLM
- A human can directly curate the memory files

**Known gaps vs. production systems (mem0, Letta/MemGPT, Zep):**
- **No semantic search** — substring grep misses "prefers brief responses"
  when searching for "concise." Production systems use vector embeddings.
- **No automatic memory management** — MemGPT-style agents actively promote,
  consolidate, and forget memories. We rely on the agent to call `memory_save`.
- **No importance/decay scoring** — all memories have equal weight regardless
  of age or relevance. Systems like Zep score by recency + relevance.
- **No contradiction resolution** — conflicting memories both persist. Smarter
  systems detect and resolve conflicts (the `supersedes` linking would help).
- **Single retrieval strategy** — no blending of recency + relevance + importance.
  We have separate tools for recency and keyword search.

**Upgrade path:**
- Semantic search: same file format, add a vector indexer alongside grep
- Auto-management: agent loop could prompt for memory consolidation periodically
- Importance scoring: add a score field to entries, weight search results
- Contradiction resolution: `supersedes` linking + search that respects it
