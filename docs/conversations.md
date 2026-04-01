# Conversation Archive & Compaction

DecafClaw persists all conversations to disk and compacts them when they grow too long for the LLM's context window.

## Conversation Archive

Every message in every conversation is appended to a JSONL file:

```
data/{agent_id}/workspace/conversations/{conv_id}.jsonl
```

Each line is a JSON object with `role`, `content`, optional `tool_calls`/`tool_call_id`, and a `timestamp`.

### Conversation IDs

- **Mattermost threads**: `conv_id` = `root_id` (the thread's root post)
- **Mattermost top-level**: `conv_id` = `channel_id`
- **Interactive mode**: `conv_id` = `"interactive"`

### Resume on restart

When DecafClaw restarts, it reads the archive for any conversation that receives a new message. The full history is replayed into memory, so the agent picks up where it left off.

## Compaction

When the conversation grows too large (exceeding the token budget), the agent automatically compacts older messages into a summary.

### How it works

1. The agent loop checks `prompt_tokens` after each LLM call
2. If tokens exceed `COMPACTION_MAX_TOKENS`, compaction triggers
3. The compaction LLM reads the **archive** (source of truth) and produces a summary
4. In-memory history is replaced with: `[summary message] + [recent turns]`
5. The archive is not modified — it remains the complete record

### Configuration

Compaction is configured via the `compaction` section in `config.json` or environment variables:

```json
{
  "compaction": {
    "max_tokens": 100000,
    "preserve_turns": 5,
    "model": "",
    "url": "",
    "api_key": "",
    "llm_max_tokens": 0
  }
}
```

| config.json key | Env variable | Default | Description |
|----------------|----------|---------|-------------|
| `max_tokens` | `COMPACTION_MAX_TOKENS` | `100000` | Trigger compaction when prompt exceeds this |
| `preserve_turns` | `COMPACTION_PRESERVE_TURNS` | `5` | Keep this many recent turns intact |
| `url` | `COMPACTION_LLM_URL` | Falls back to `LLM_URL` | LLM endpoint for compaction |
| `model` | `COMPACTION_LLM_MODEL` | Falls back to `LLM_MODEL` | Model for compaction |
| `api_key` | `COMPACTION_LLM_API_KEY` | Falls back to `LLM_API_KEY` | API key for compaction |
| `llm_max_tokens` | `COMPACTION_LLM_MAX_TOKENS` | `0` (use `max_tokens`) | Compaction LLM's context budget |

Empty `url`, `model`, and `api_key` fields fall back to the main LLM config. Env vars take precedence over config.json.

### Custom compaction prompt

Place a `COMPACTION.md` file at `data/{agent_id}/COMPACTION.md` to customize the summarization instructions. If absent, a built-in default is used that preserves key facts, decisions, user preferences, tool results, and open questions.

### Tools

- **`conversation_compact`** — manually trigger compaction without waiting for the token budget to be exceeded
- **`conversation_search`** — search past conversations using semantic search (across all archived conversations, not just the current one)

## Web UI Conversations

Web conversations are managed through a REST API. The web UI sidebar shows conversations organized into folders.

### Conversation Folders

Users can organize conversations into nested folders. Folder structure is tracked in a per-user JSON index file — conversation archive files stay in place (no filesystem moves).

**Index file:** `data/{agent_id}/web/users/{username}/conversation_folders.json`

```json
{
  "folders": ["projects", "projects/bot-redesign", "research"],
  "assignments": {
    "web-les-abc123": "projects/bot-redesign",
    "web-les-def456": "research"
  }
}
```

- Conversations not in the index default to the top level
- Folder names starting with `_` are reserved for virtual folders
- Archiving preserves folder assignment; unarchiving restores it
- Renaming a folder to an existing name merges them

### Virtual Folders

The sidebar shows two virtual folders at the top level:

- **Archived** — archived conversations, preserving their folder structure
- **System** — system conversations grouped into sub-folders by type (heartbeat, schedule, delegated)

Virtual folders are navigable but not renamable or deletable.

### REST API

All conversation management uses REST (WebSocket is only for real-time chat streaming):

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/conversations?folder=` | List conversations + subfolders in a folder |
| `GET` | `/api/conversations/archived?folder=` | List archived conversations by folder |
| `GET` | `/api/conversations/system?folder=` | List system conversations by type |
| `POST` | `/api/conversations` | Create conversation (optional: folder, effort) |
| `PATCH` | `/api/conversations/{id}` | Rename and/or move to a folder |
| `POST` | `/api/conversations/{id}/archive` | Archive a conversation |
| `POST` | `/api/conversations/{id}/unarchive` | Unarchive a conversation |
| `POST` | `/api/conversations/folders` | Create a folder |
| `DELETE` | `/api/conversations/folders/{path}` | Delete an empty folder |
| `PUT` | `/api/conversations/folders/{path}` | Rename/move a folder (merges on collision) |

## Files on disk

```
data/{agent_id}/workspace/
  conversations/
    {conv_id}.jsonl          # Append-only archive per conversation
  embeddings.db              # Semantic search index (includes conversation messages)
data/{agent_id}/web/
  users/{username}/
    conversation_folders.json # Per-user folder index
```

All files are human-readable (JSON/JSONL) and crash-recoverable (append-only writes, atomic folder index updates).
