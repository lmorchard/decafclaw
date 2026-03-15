# Memory System

DecafClaw has persistent memory stored as daily markdown files. The agent can save, search, and recall memories across conversations.

## How it works

Memories are appended to daily markdown files at:

```
data/{agent_id}/workspace/memories/{year}/{year}-{month}-{day}.md
```

Each entry includes a timestamp, channel/thread metadata, tags, and the content:

```markdown
## 2026-03-15 01:30

- **channel:** town-square (abc123)
- **thread:** def456
- **tags:** preference, food

User prefers Thai food over Chinese food.
```

## Tools

| Tool | Description |
|------|-------------|
| `memory_save` | Save a memory with tags. Indexed for semantic search if configured. |
| `memory_search` | Search memories by keyword (substring) or meaning (semantic). |
| `memory_recent` | Return the N most recent memory entries. |

### memory_save

The agent decides when to save memories based on its prompt instructions. Memories are tagged for later retrieval. If semantic search is configured, new memories are automatically embedded and indexed.

### memory_search

Two strategies, controlled by `MEMORY_SEARCH_STRATEGY`:

- **`substring`** (default) — case-insensitive substring matching across all memory files. Returns whole entries when any line matches.
- **`semantic`** — embeds the query and finds the most similar memories by cosine similarity. Falls back to substring if the embedding index is empty.

### memory_recent

Returns the last N entries (default: 5), most recent first. Useful for quick context about what the agent has been told recently.

## Search strategies

### Substring search

Simple and fast. Works well for exact keywords, tags, and names. No external API calls needed.

### Semantic search

Finds memories by meaning rather than exact keywords. Requires an embedding model to be configured (see [Semantic Search](semantic-search.md)).

Example: searching for "what food does the user like" would find a memory about "prefers Thai food" even though the words don't overlap.

## Files on disk

All memory files are plain markdown — human-readable and editable. You can:

- Read them directly to see what the agent remembers
- Edit or delete entries manually
- Run `make reindex` after manual edits to rebuild the semantic search index

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `MEMORY_SEARCH_STRATEGY` | `substring` | `substring` or `semantic` |

See [Semantic Search](semantic-search.md) for embedding configuration.
