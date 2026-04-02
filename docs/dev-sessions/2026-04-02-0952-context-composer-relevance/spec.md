# Context Composer Phase 2: Relevance Scoring & A-MEM Concepts — Spec

## Related issue

GitHub issue #182 — Context composer (phases 4-6)

## Goal

Enhance the context composer's retrieval and selection pipeline with relevance scoring, wiki-link graph expansion, and structured vault page frontmatter. This builds on the Phase 1 extraction/refactor (PR #195) to make context selection intentional rather than ad-hoc.

Inspired by research from A-MEM (Zettelkasten-inspired agentic memory), Generative Agents (three-factor relevance scoring), and the context rot findings (lean context > big context).

## Background

### Current state

- Memory context retrieval (`memory_context.py`) uses embedding cosine similarity with a fixed threshold and fixed token budget (500 tokens)
- No relevance scoring beyond raw similarity
- Vault pages are plain markdown with no structured metadata
- Wiki-links (`[[page]]`) exist in pages but aren't used for retrieval
- The composer (Phase 1) assembles context but doesn't score or rank sources
- Dream process distills journal → pages every 3 hours
- Garden process does structural maintenance weekly

### Research inputs

- **A-MEM** — Zettelkasten-inspired: each memory gets keywords, tags, summary, and dynamic links. New info refines existing memories. Graph traversal at retrieval time follows links beyond pure vector search.
- **Generative Agents** — Three-factor scoring: `score = w1*recency + w2*importance + w3*similarity`. All normalized to [0,1].
- **Context Rot** (Chroma) — Every token added degrades retrieval quality. Lean, relevant context beats dumping everything in.

## Design

### 1. Vault page frontmatter

Pages support optional YAML frontmatter (Jekyll/Obsidian-compatible):

```markdown
---
summary: "One-line description of the page's purpose"
keywords: [term1, term2, term3]
tags: [category1, category2]
importance: 0.5
---

# Page Title
...content...
```

**Fields:**
- `summary` — one-line description. Replaces the informal `tl;dr` convention. Used in composite embeddings.
- `keywords` — salient terms ordered by importance. Enriches embedding search.
- `tags` — broad categorical labels. Compatible with Obsidian tags.
- `importance` — float [0, 1], default 0.5 (neutral). Adjusted by garden process in Phase B.

**Rules:**
- Frontmatter is optional — pages without it work fine (defaults applied).
- LLM-generated when pages are created/updated (Phase B dream integration). 
- Human-editable — manually set values are respected; LLM fills gaps but doesn't overwrite explicit values.
- Parsing on read, preservation on write. Vault tools (`vault_write`, `vault_read`) handle frontmatter transparently.

### 2. Composite embeddings

The embedding index stores a composite document per page:

```
{summary}\n{keywords joined by ', '}\n{tags joined by ', '}\n{content}
```

This enriches semantic search with structured metadata. Requires reindex when frontmatter is added or updated (`make reindex` already handles this).

Pages without frontmatter embed as content-only (current behavior).

### 3. Wiki-link graph expansion

When `memory_context.py` retrieves top-k results via embedding search, it expands the candidate set by following `[[wiki-links]]` from the top hits:

- **One hop only** — if hit A links to page B, page B is added to the candidate pool.
- **Linked pages are marked** — `{"source_type": "graph_expansion", "linked_from": "PageName"}` so the composer can see provenance.
- **No cap on expansion** — all linked pages enter the candidate pool, but they compete on score like everything else. The composer's budget allocation handles the rest.
- **Link parsing** — extract `[[PageName]]` and `[[PageName|display]]` from page content. Resolve against vault root.
- **Scoring graph-expanded pages** — linked pages don't have their own cosine similarity from embedding search. They inherit a discounted similarity from the parent hit (e.g., `parent_similarity * 0.7`). Their recency and importance use their own metadata as normal.

This happens in `memory_context.py` — the composer just sees a bigger, annotated candidate pool.

### 4. Relevance scoring

The composer scores all retrieval candidates using three factors, each normalized to [0, 1]:

**`score = w_similarity * similarity + w_recency * recency + w_importance * importance`**

**Factors:**
- **Similarity** — cosine similarity from embedding search (already normalized [0, 1]).
- **Recency** — exponential decay based on page modification time or journal entry timestamp. More recent = higher score. Decay rate configurable.
- **Importance** — from frontmatter field (default 0.5 if absent). Neutral until dream/garden adjusts it in Phase B.

**Default weights** (configurable in `config.json`):
- `w_similarity: 0.5` — similarity dominates
- `w_recency: 0.3` — recency is secondary
- `w_importance: 0.2` — importance has less influence until actively tuned

**Where it lives:**
- `memory_context.py` retrieves candidates with raw metadata (similarity, timestamp, importance from frontmatter).
- The composer applies the scoring formula and makes selection/budget decisions.

### 5. Budget allocation

The composer uses a priority-tier model:

1. **Fixed costs** (always included, not negotiable):
   - System prompt
   - Conversation history
   - Tool definitions (active + deferred list)
   - Explicitly referenced vault pages (`@[[Page]]` mentions and open web UI page) — user intent, not scored

2. **Scored candidates** (compete for remaining budget):
   - Memory/journal entries from retrieval
   - Vault pages from retrieval (not explicitly referenced)
   - Graph-expanded pages (linked from retrieval hits)

After fixed costs are reserved, remaining token budget goes to scored candidates. Candidates are ranked by composite score; the composer fills from the top until the budget is exhausted.

The fixed token budget for memory context (currently 500 tokens) is replaced by this dynamic allocation.

### 6. Configuration

New config fields in `MemoryContextConfig` (or a new `RelevanceConfig`):

```python
@dataclass
class RelevanceConfig:
    w_similarity: float = 0.5
    w_recency: float = 0.3
    w_importance: float = 0.2
    recency_decay_rate: float = 0.99  # per-hour decay
    graph_expansion_enabled: bool = True
```

## Success criteria

1. **Vault pages with frontmatter** — parse on read, preserve on write, composite embedding on index
2. **Graph expansion** — retrieval follows one hop of wiki-links, linked pages in candidate pool
3. **Relevance scoring** — candidates scored by recency + importance + similarity with configurable weights
4. **Budget allocation** — fixed costs reserved, scored candidates fill remainder
5. **All existing tests pass** — no regressions
6. **Configurable** — weights, decay rate, graph expansion toggle in config

## Out of scope (Phase B — follow-up issue)

- Dream process generates/updates frontmatter on pages it creates/revises
- Dream process suggests new wiki-links (A-MEM "strengthen" operation)
- Garden process adjusts importance scores across all pages (retrieval frequency, link count, reference frequency)
- Garden process validates/repairs frontmatter consistency
- Backfill frontmatter on existing pages
- Reindex existing pages with composite embeddings
- Micro-evolution on journal append (lighter than dream, more responsive)

## Relationship to other issues

- #182 — Context composer parent issue (this is phases 4-6)
- #196 — Wiki/memory → vault naming cleanup
- #175 — Vault unification
