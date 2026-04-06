# Proactive Vault Retrieval

Automatically surfaces relevant vault entries (pages, journal, user notes, conversation snippets) before each agent turn — without the agent needing to explicitly search.

## How it works

Before each turn, the agent:

1. Embeds the user's current message
2. Runs semantic search across all indexed content (vault pages, journal, conversation)
3. Filters results by similarity threshold
4. Injects matching entries as a context message before the user's message

The LLM sees this context alongside the conversation and can use it naturally. The context message is archived for auditability and persists in the conversation history.

## Configuration

All settings live under the `vault_retrieval` section in `config.json`:

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `enabled` | bool | `true` | Enable/disable the feature |
| `similarity_threshold` | float | `0.3` | Minimum similarity score to include a result |
| `max_results` | int | `5` | Maximum number of entries to inject |
| `max_tokens` | int | `500` | Token budget for injected context |
| `show_in_ui` | bool | `true` | Show retrieval indicator in chat UI |

Environment variable prefix: `VAULT_RETRIEVAL_` (e.g., `VAULT_RETRIEVAL_ENABLED=false`).

### Example config.json

```json
{
  "vault_retrieval": {
    "enabled": true,
    "similarity_threshold": 0.4,
    "max_results": 3,
    "max_tokens": 300,
    "show_in_ui": true
  }
}
```

## Requirements

- An embedding model must be configured (`embedding.model`). If not set, the feature silently does nothing.
- The embedding index should be populated (run `make reindex` if starting fresh).

## Skip conditions

Vault retrieval is skipped for non-interactive turns:

- Heartbeat cycles
- Scheduled tasks
- Delegated subtasks (child agents)
- Any turn with `skip_vault_retrieval` set on the context

Only interactive conversations (Mattermost, web UI, terminal) trigger retrieval.

## UI indicator

When `show_in_ui` is true:

- **Web UI**: An expandable block shows the full retrieved context with source types and relevance scores. Visible both live and on conversation reload.
- **Mattermost**: A concise summary post shows the count of retrieved items by source type (e.g., "🧠 Retrieved 1 page, 3 conversation"). Full text is not shown since Mattermost posts can't collapse.

Note: `show_in_ui` gates the live progress event. The `vault_retrieval` message is always archived for auditability and will appear on web UI history reload regardless of this setting. To fully suppress, disable the feature with `enabled: false`.

## Source priority

Wiki entries receive a 1.2x similarity boost (configured in the embeddings layer), so curated vault pages naturally rank above raw entries at equal semantic distance.

## Disabling

Set `vault_retrieval.enabled` to `false` in config, or set `VAULT_RETRIEVAL_ENABLED=false` in the environment.

## Related

- [Semantic Search](semantic-search.md) — The underlying embedding index
