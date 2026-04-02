# Context Composer Phase 2: Relevance Scoring & A-MEM Concepts — Plan

## Overview

Build on the Phase 1 ContextComposer to add frontmatter parsing, composite embeddings, wiki-link graph expansion, relevance scoring, and dynamic budget allocation. 7 steps, each building on the previous.

The key files we're modifying:
- `config_types.py` — new `RelevanceConfig` dataclass
- `config.py` — wire `RelevanceConfig` into `Config`
- `embeddings.py` — composite embedding text, frontmatter-aware indexing
- `memory_context.py` — graph expansion, return richer metadata
- `context_composer.py` — relevance scoring, budget allocation
- `skills/vault/tools.py` — frontmatter-aware read/write

New files:
- `src/decafclaw/frontmatter.py` — YAML frontmatter parse/serialize utilities

---

## Step 1: Frontmatter parsing utilities

Create a `frontmatter.py` module with pure functions for parsing and serializing YAML frontmatter. No integration yet — just the utilities and tests.

### Prompt

```
Create `src/decafclaw/frontmatter.py` with utilities for YAML frontmatter
(Jekyll/Obsidian-compatible). These are pure functions, no dependencies on
the rest of the codebase.

1. `parse_frontmatter(text: str) -> tuple[dict, str]`:
   - Splits markdown text into (frontmatter_dict, body_content)
   - Frontmatter is YAML between opening `---\n` and closing `\n---\n`
   - Must be at the very start of the file
   - Returns ({}, text) if no frontmatter found
   - Use `yaml.safe_load` for parsing
   - Handle edge cases: empty frontmatter, malformed YAML (log warning, return empty dict)

2. `serialize_frontmatter(metadata: dict, body: str) -> str`:
   - Combines a metadata dict and body text into frontmatter + markdown
   - Uses `yaml.dump` with `default_flow_style=False`
   - Omits frontmatter block entirely if metadata is empty
   - Preserves body content exactly (no trailing newline changes)

3. `get_frontmatter_field(metadata: dict, field: str, default=None)`:
   - Type-safe getter. For `importance`, clamp to [0, 1] float.
   - For `keywords` and `tags`, ensure list of strings.
   - For `summary`, ensure string.

4. `build_composite_text(metadata: dict, body: str) -> str`:
   - Builds the composite text for embedding indexing:
     `{summary}\n{keywords joined by ', '}\n{tags joined by ', '}\n{body}`
   - If no frontmatter fields, returns body as-is (backward compatible)

Add `pyyaml` to project dependencies if not already present.

Write tests in `tests/test_frontmatter.py`:
- parse_frontmatter with valid frontmatter
- parse_frontmatter with no frontmatter (plain markdown)
- parse_frontmatter with empty frontmatter block
- parse_frontmatter with malformed YAML (returns empty dict, body intact)
- serialize_frontmatter round-trips with parse_frontmatter
- serialize_frontmatter with empty dict omits frontmatter block
- get_frontmatter_field clamps importance to [0, 1]
- get_frontmatter_field returns list for keywords/tags
- build_composite_text with full frontmatter
- build_composite_text with no frontmatter (returns body)

Run `make check && make test`.
```

---

## Step 2: RelevanceConfig and configuration

Add `RelevanceConfig` dataclass and wire it into the config system.

### Prompt

```
Step 2: Add RelevanceConfig to the configuration system.

In `config_types.py`, add:

```python
@dataclass
class RelevanceConfig:
    w_similarity: float = 0.5
    w_recency: float = 0.3
    w_importance: float = 0.2
    recency_decay_rate: float = 0.99  # per-hour exponential decay
    graph_expansion_enabled: bool = True
    graph_expansion_similarity_discount: float = 0.7
```

In `config.py`, add `relevance: RelevanceConfig` to the `Config` dataclass,
following the existing pattern for sub-configs. Wire it into the config
loading (env vars, config.json, defaults).

Write tests:
- Test default values
- Test config loading with custom relevance values
- Test that env var overrides work (e.g. RELEVANCE_W_SIMILARITY=0.8)

Run `make check && make test`.
```

---

## Step 3: Frontmatter-aware vault tools and embeddings

Integrate frontmatter parsing into vault read/write and the embedding index.

### Prompt

```
Step 3: Integrate frontmatter into vault tools and embedding index.

In `skills/vault/tools.py`:
- `tool_vault_write`: when writing a page, use `build_composite_text()`
  for the embedding index instead of raw content. Parse the content with
  `parse_frontmatter()` to extract metadata, then build the composite.
  The full content (including frontmatter) is written to disk as-is.
- `tool_vault_read`: no changes needed — reads the raw file including
  frontmatter. The agent sees the full page.

In `embeddings.py`:
- `_iter_vault_pages()`: when yielding entries for reindex, parse
  frontmatter from each page and use `build_composite_text()` for the
  entry_text field. This enriches the embedding with metadata.
- `index_entry()`: no changes — callers are responsible for passing
  composite text when appropriate.

Import from the new `frontmatter.py` module.

Write tests:
- Test that vault_write indexes composite text (mock index_entry, verify
  the text passed includes frontmatter fields)
- Test that _iter_vault_pages yields composite text for pages with
  frontmatter
- Test that pages without frontmatter yield plain content (backward compat)

Run `make check && make test`.
```

---

## Step 4: Enrich retrieval results with metadata

Modify `memory_context.py` and `embeddings.py` to return richer metadata
with each retrieval result: file modification time and frontmatter fields.

### Prompt

```
Step 4: Return richer metadata from retrieval results.

The composer needs timestamp and importance to score candidates. Currently
`search_similar_sync` returns {entry_text, file_path, similarity, source_type}.
We need to add metadata without breaking existing callers.

Important design choice: do NOT read vault files inside search_similar_sync.
That function returns many results (over-fetches 3x) before threshold
filtering. File I/O for all of them would be wasteful. Instead:

In `embeddings.py`:
- `search_similar_sync()`: add `created_at` from the DB to each result
  dict. This is already in the query — just include it in the result.
  No file I/O needed.

In `memory_context.py`:
- Add a helper `_enrich_results(config, results: list[dict]) -> list[dict]`:
  - Called AFTER threshold filtering and max_results trimming (on the
    small, final candidate set)
  - For each result with a file_path, resolve against vault_root
  - Read file modification time via os.path.getmtime → `modified_at` (ISO)
  - For page/user source types: parse frontmatter, extract importance
  - For journal entries: importance = 0.5 (default)
  - If file not found, fall back to created_at and default importance
  - Fail-open: any error on a single result logs warning, uses defaults
- Call `_enrich_results()` in `retrieve_memory_context()` after filtering

This keeps the hot path (DB query) fast and only does file I/O on the
small set of candidates that will actually be scored.

Write tests:
- Test _enrich_results adds modified_at and importance
- Test _enrich_results defaults importance to 0.5 without frontmatter
- Test _enrich_results reads importance from frontmatter
- Test _enrich_results is fail-open (missing file → defaults)
- Test search_similar_sync includes created_at in results
- Test retrieve_memory_context results have enriched metadata

Run `make check && make test`.
```

---

## Step 5: Wiki-link graph expansion

Add one-hop wiki-link graph expansion to `memory_context.py`. When
embedding search returns top hits, follow `[[wiki-links]]` from those
pages to expand the candidate pool.

### Prompt

```
Step 5: Add wiki-link graph expansion to memory context retrieval.

In `memory_context.py`, add a function:

`_expand_graph_links(config, results: list[dict], max_hops: int = 1) -> list[dict]`:
  - For each result in the top hits, read the page content from the vault
    (using file_path to resolve against vault_root)
  - Parse `[[PageName]]` and `[[PageName|display]]` links from the content
    (reuse the regex pattern from agent.py or frontmatter.py)
  - For each linked page:
    - Skip if already in results (by file_path)
    - Read the page, parse frontmatter for importance
    - Get file modification time for recency
    - Assign discounted similarity: parent_result["similarity"] * discount_factor
    - Add to results with source_type="graph_expansion" and
      linked_from=parent_page_name
  - Return the expanded results list (originals + linked pages)

In `retrieve_memory_context()`:
  - After the initial search and filtering, call `_expand_graph_links()`
    if graph expansion is enabled in config
  - Pass config.relevance.graph_expansion_enabled and
    config.relevance.graph_expansion_similarity_discount
  - The expanded results are returned to the composer for scoring

Add a wiki-link regex: `r'\[\[([^\]|]+)(?:\|[^\]]+)?\]\]'` — extracts
the page name, handles `[[target|display]]` format.

Write tests:
- Test _expand_graph_links finds and resolves wiki-links
- Test _expand_graph_links skips already-present results (dedup)
- Test linked pages get discounted similarity
- Test linked pages have source_type="graph_expansion"
- Test graph expansion is skipped when disabled in config
- Test wiki-link regex handles both [[Page]] and [[Page|display]] formats

Run `make check && make test`.
```

---

## Step 6: Relevance scoring in the composer

Add three-factor relevance scoring to the composer's memory context
handling. Replace the fixed token budget with scored ranking.

### Prompt

```
Step 6: Add relevance scoring to the ContextComposer.

In `context_composer.py`, add a method:

`_score_candidates(self, candidates: list[dict], config) -> list[dict]`:
  - For each candidate, compute a composite score:
    score = w_similarity * similarity + w_recency * recency + w_importance * importance
  - Similarity: already in candidate["similarity"], normalized [0, 1]
  - Recency: exponential decay based on candidate["modified_at"] timestamp.
    `recency = decay_rate ^ hours_since_modification`. Clamp to [0, 1].
    If modified_at is missing, use 0.5 (neutral).
  - Importance: from candidate["importance"], default 0.5. Already [0, 1].
  - Weights from config.relevance (w_similarity, w_recency, w_importance)
  - Add "composite_score" to each candidate dict
  - Sort by composite_score descending
  - Return sorted candidates

Modify `_compose_memory_context()`:
  - After retrieving candidates (which now include graph-expanded results),
    call `_score_candidates()` to rank them
  - Instead of using the fixed max_tokens budget from MemoryContextConfig,
    the composer will use dynamic budget allocation (next step). For now,
    still use max_tokens but select candidates by score instead of by
    similarity order.
  - Update the SourceEntry details to include scoring info:
    details={"top_score": ..., "min_score": ..., "candidates_considered": ...}

Write tests:
- Test _score_candidates produces correct composite scores
- Test candidates are sorted by score descending
- Test recency decay: recent items score higher than old items
- Test importance influences score
- Test missing modified_at defaults to 0.5 recency
- Test _compose_memory_context uses scored ordering

Run `make check && make test`.
```

---

## Step 7: Dynamic budget allocation

Replace fixed memory token budget with dynamic allocation in the composer.
Fixed costs are reserved first, scored candidates fill the remainder.

### Prompt

```
Step 7: Implement dynamic budget allocation in the composer.

Modify `compose()` in `context_composer.py`:

The current flow assembles all sources independently. The new flow:

1. Compute fixed costs first (unchanged from current code):
   - System prompt tokens (from _compose_system_prompt)
   - Tool tokens (from _compose_tools)
   - Wiki context for explicit @[[Page]] references (from _compose_wiki_context —
     these are fixed costs, always included)
   - History tokens estimate (from existing history, before new messages)

2. Calculate remaining budget:
   `remaining = _get_context_window_size(config) - fixed_costs - response_reserve`
   where response_reserve is a configurable margin (e.g. 4096 tokens) for
   the model's response. Use compaction_max_tokens as the budget ceiling
   if context_window_size is not set.

3. Score and select memory candidates:
   - Retrieve candidates via _compose_memory_context (which now includes
     graph-expanded results)
   - Score via _score_candidates
   - Fill from the top until remaining budget is exhausted
   - Candidates that don't fit are counted as items_truncated

4. The MemoryContextConfig.max_tokens field becomes a fallback: if
   context_window_size is 0 and compaction_max_tokens is the default,
   fall back to the fixed budget. This preserves backward compatibility
   for deployments that haven't configured window sizes.

Update the SourceEntry for memory to reflect:
- items_included: candidates that made the cut
- items_truncated: candidates scored but excluded by budget
- tokens_estimated: actual tokens of included candidates
- details: scoring stats, budget info

Write tests:
- Test that fixed costs are reserved before memory candidates
- Test that scored candidates fill remaining budget in score order
- Test that candidates exceeding budget are truncated
- Test backward compatibility: when context_window_size is 0, falls back
  to max_tokens fixed budget
- Test explicit @[[Page]] references are not scored (always included as
  fixed costs)

Run `make check && make test`.

After this step, update CLAUDE.md:
- Add frontmatter.py to key files list
- Update memory context convention to mention relevance scoring
- Add relevance scoring convention note
- Update context composer convention to mention budget allocation

Update docs/context-composer.md with the new scoring and budget model.
Create docs/relevance-scoring.md documenting the formula, weights, and
graph expansion.
Update docs/index.md with the new doc page.
```

---

## Summary of changes per step

| Step | New/Modified Files | Tests |
|------|-------------------|-------|
| 1 | `frontmatter.py` (new) | `test_frontmatter.py` (new) |
| 2 | `config_types.py`, `config.py` | `test_config.py` |
| 3 | `skills/vault/tools.py`, `embeddings.py` | `test_vault_tools.py`, `test_embeddings.py` |
| 4 | `embeddings.py`, `memory_context.py` | `test_embeddings.py`, `test_memory_context.py` |
| 5 | `memory_context.py` | `test_memory_context.py` |
| 6 | `context_composer.py` | `test_context_composer.py` |
| 7 | `context_composer.py`, `CLAUDE.md`, `docs/` | `test_context_composer.py` |

## Risk notes

- **Step 3 changes embedding content** — existing indexes won't have composite text until reindex. Pages without frontmatter are backward compatible (body-only, same as before). A reindex is needed to benefit from frontmatter enrichment.
- **Step 4 adds I/O per candidate** — reading frontmatter from files, but only on the post-filtered candidate set (typically 5-10 files), not the full search results. Acceptable cost.
- **Step 5 graph expansion** — could significantly increase candidate count. The budget allocation in step 7 prevents this from bloating context, but the retrieval I/O increases.
- **Step 7 is the riskiest** — changes how memory budget works. Backward compatibility fallback is critical for deployments without `context_window_size` configured.
- **YAML dependency** — `pyyaml` already in project dependencies (6.0.3). No action needed.
