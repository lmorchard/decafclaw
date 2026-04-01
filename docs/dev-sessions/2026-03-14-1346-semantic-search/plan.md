# Semantic Search — Plan

## Steps

1. Config: EMBEDDING_MODEL, EMBEDDING_URL, EMBEDDING_API_KEY
2. embeddings.py: SQLite DB, embed_text API call, index_entry, search_similar
3. Wire into memory_save: index new entries after writing markdown
4. Wire into memory_search: semantic search alongside/replacing substring
5. Index existing memories: scan markdown files and embed unindexed entries
6. Tests, lint, eval run to compare
