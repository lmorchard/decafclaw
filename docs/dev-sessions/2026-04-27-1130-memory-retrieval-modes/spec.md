# Memory retrieval modes — headlines + on-demand

Tracking issue: #301

## Problem

Vault retrieval auto-injects ~500 tokens every interactive turn
regardless of whether the user's message even needs memory. For
short turns ("thanks", "yes", clarifications) this is pure overhead.
The model never gets to *decide* whether the candidates are worth
pulling.

Anthropic's [Effective Context Engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
calls this **just-in-time context**: inject lightweight identifiers,
let the agent pull full bodies on demand via tools (`vault_read`,
`vault_search`).

## Goal

Three modes, configurable globally:

- `always` (default, back-compat) — current behavior; inject scored
  full-body candidates.
- `headlines` — inject **title + tl;dr + score** only; the agent
  calls `vault_read` to pull full bodies if a headline looks
  promising.
- `on_demand` — inject nothing from auto-retrieval; the agent drives
  all retrieval via `vault_search` / `vault_read`. Explicit
  `@[[Page]]` references still inject (those are user-driven, not
  auto-retrieval).

## Decisions (autonomous brainstorm)

1. **Snippet content per mode is fixed.** Headlines emit
   `file_path · summary · composite_score`. `summary` is pulled
   from frontmatter (already a known field); falls back to the
   first 120 chars of the body when no summary frontmatter exists.
   Length cap on the summary is configurable via
   `headline_summary_max_chars`.
2. **Global config only.** Per-conversation override deferred —
   keeps the surface tight for v1. Web UI toggle is a follow-up.
3. **`@[[Page]]` mentions inject regardless of mode.** Those are
   explicit user references, not auto-retrieval; treating them
   differently breaks the mental model.
4. **Headlines mode keeps graph expansion.** The expansion set
   contributes headlines like any other candidate. The cost is
   tiny anyway.
5. **Skip-on-low-confidence reuses `RelevanceConfig.min_composite_score`.**
   The existing post-score filter already drops candidates below
   the threshold; if the filter empties the set, nothing injects
   today either. New mode-aware code preserves that behavior.
6. **Sidecar records `mode` + `injection_skipped` flag** in the
   `memory` source's `details`. Useful for the context inspector
   to surface which mode ran.

## Architecture

### Config

`VaultRetrievalConfig` gains:

```python
mode: str = "always"  # "always" | "headlines" | "on_demand"
headline_summary_max_chars: int = 120
```

Validation: invalid mode logs a warning and falls back to
`"always"`.

### Retrieval flow

`_compose_vault_retrieval` (`context_composer.py`) branches early:

```python
mode_str = config.vault_retrieval.mode
if mode_str == "on_demand":
    # Skip auto-retrieval entirely. @[[Page]] still inject via
    # _compose_vault_references.
    return [], "", [], _empty_entry(mode="on_demand")

# always / headlines: run scoring, dedup, budget — same as today.
results = await retrieve_memory_context(...)
results = self._score_candidates(results, config)
# ... filter, trim ...

if mode_str == "headlines":
    # Render compactly: file_path · summary · score
    formatted = format_memory_headlines(
        results, max_summary_chars=config.vault_retrieval.headline_summary_max_chars,
    )
else:
    formatted = format_memory_context(results)  # full body, current behavior
```

### Headlines formatter

New in `memory_context.py`:

```python
def format_memory_headlines(
    results: list[dict],
    *,
    max_summary_chars: int = 120,
) -> str:
    """One line per candidate: file_path · summary · score.

    Summary comes from frontmatter when present, falls back to a
    truncated body excerpt. Suitable for showing the agent enough
    to decide whether to pull a full body via vault_read.
    """
```

### Diagnostics

`SourceEntry.details` for the `memory` source gains:

- `mode`: `"always"` / `"headlines"` / `"on_demand"`
- `injection_skipped`: `bool` — true when on_demand mode silently
  skipped retrieval, OR when headlines/always ran but the result
  set was empty after filtering.

### Tool descriptions

`vault_read` and `vault_search` already recommend themselves in
their descriptions. No change needed for them. Documentation calls
out the mode-aware retrieval flow.

## Out of scope

- **Per-conversation mode override** (web UI toggle). v2.
- **Embedding the headlines themselves** for recall. The headlines
  are derived from already-embedded content; re-embedding adds
  nothing.
- **Smart "auto" mode** that picks per-turn based on message
  length / ambiguity. Tempting but premature — pick a mode that
  works for your deployment and tune from there.
- **Tracking explicit `vault_read` results** in `injected_paths` so
  read pages don't re-headline. Existing `injected_paths` only
  tracks auto-injected pages; explicit reads via `vault_read`
  don't update it. Worth a follow-up; out of scope here.

## Acceptance criteria

- `mode = "always"` produces byte-identical output to current
  behavior (the entire feature is gated on a non-default value).
- `mode = "headlines"` emits a compact list with file_path,
  summary, and score; full bodies are NOT in context.
- `mode = "on_demand"` produces no auto-retrieval message at all.
  `@[[Page]]` references still work.
- Sidecar `memory` source's `details` records `mode` and
  `injection_skipped`.
- Invalid mode in config falls back to `"always"` with a warning.

## Testing

- **Unit tests** for `format_memory_headlines`: empty results,
  with/without frontmatter summary, summary truncation, score
  formatting.
- **Composer integration tests** for `_compose_vault_retrieval`:
  - `mode="always"` produces the existing full-body message.
  - `mode="headlines"` produces a headlines-only message.
  - `mode="on_demand"` produces no auto-retrieval message; the
    SourceEntry still records the mode + `injection_skipped: true`.
  - Invalid mode falls back to `"always"`.
- **No real-LLM CI test.** Manual smoke after merge.

## Files touched

- `src/decafclaw/config_types.py` — `VaultRetrievalConfig` gains
  `mode` and `headline_summary_max_chars`.
- `src/decafclaw/memory_context.py` — `format_memory_headlines` +
  enrich `summary` field during result enrichment.
- `src/decafclaw/context_composer.py` — `_compose_vault_retrieval`
  branches on mode; sidecar details.
- `tests/test_memory_context.py` — headlines formatter tests.
- `tests/test_context_composer.py` — composer mode-branch tests.
- `docs/context-composer.md` — describe the modes.
- `docs/semantic-search.md` — mention mode-aware retrieval.
- `docs/config.md` — `vault_retrieval.mode` reference.
- `CLAUDE.md` — context-engineering convention bullet update.
