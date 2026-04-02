# Relevance Scoring & Graph Expansion

Memory context retrieval uses a three-factor relevance scoring system inspired by Generative Agents and A-MEM research.

## Scoring Formula

```
composite_score = w_similarity * similarity + w_recency * recency + w_importance * importance
```

All factors are normalized to [0, 1].

### Factors

| Factor | Source | Description |
|--------|--------|-------------|
| **Similarity** | Embedding cosine similarity | How semantically close the entry is to the user's message |
| **Recency** | File modification time | Exponential decay: `decay_rate ^ hours_since_modification` |
| **Importance** | Frontmatter field | Page significance, default 0.5. Adjusted by garden process. |

### Default Weights

| Weight | Default | Rationale |
|--------|---------|-----------|
| `w_similarity` | 0.5 | Similarity dominates — relevance to the query matters most |
| `w_recency` | 0.3 | Recent content is more likely to be useful |
| `w_importance` | 0.2 | Lower weight until dream/garden actively tune importance |

Configurable via `RelevanceConfig` in `config.json` or environment variables.

## Wiki-Link Graph Expansion

After embedding search returns top-k results, the system follows `[[wiki-links]]` one hop from each hit:

1. Parse `[[PageName]]` links from top hit content
2. Resolve each link against the vault
3. Add linked pages to the candidate pool with:
   - Discounted similarity: `parent_similarity * 0.7` (configurable)
   - Their own recency (file mtime) and importance (frontmatter)
   - `source_type: "graph_expansion"` and `linked_from: parent_page`
4. All candidates (original + expanded) compete on composite score

This captures conceptual relationships that pure embedding similarity might miss — if a page about "deployment" links to a page about "systemd services," both may be relevant even if only one matches the query embedding.

## Dynamic Budget Allocation

The composer uses a priority-tier model:

### Fixed Costs (always included)
- System prompt
- Conversation history
- Tool definitions
- Explicitly referenced vault pages (`@[[Page]]` mentions, open web UI page)

### Scored Candidates (compete for remaining budget)
After fixed costs are reserved, remaining tokens go to scored candidates:
- Memory/journal entries from embedding retrieval
- Vault pages from retrieval
- Graph-expanded pages

Candidates fill in composite_score order until the budget is exhausted.

The budget is derived from `context_window_size` (or `compaction_max_tokens` as fallback) minus fixed costs minus a response reserve (4096 tokens). Falls back to the fixed `max_tokens` budget if the dynamic budget is unavailable.

## Vault Page Frontmatter

Pages support optional YAML frontmatter:

```markdown
---
summary: "One-line description"
keywords: [term1, term2, term3]
tags: [category1, category2]
importance: 0.5
---

# Page Content
...
```

### Composite Embeddings

The embedding index stores a composite document:
```
{summary}
{keywords joined by ', '}
{tags joined by ', '}
{body content}
```

This enriches semantic search with structured metadata. Pages without frontmatter embed as body-only (backward compatible).

### Future: Automated Frontmatter (Phase B)

- Dream process will generate/update frontmatter when creating or revising pages
- Garden process will adjust importance scores across all pages
- See issue #197 for details

## Configuration

```json
{
  "relevance": {
    "w_similarity": 0.5,
    "w_recency": 0.3,
    "w_importance": 0.2,
    "recency_decay_rate": 0.99,
    "graph_expansion_enabled": true,
    "graph_expansion_similarity_discount": 0.7
  }
}
```

Environment variables: `RELEVANCE_W_SIMILARITY`, `RELEVANCE_W_RECENCY`, etc.
