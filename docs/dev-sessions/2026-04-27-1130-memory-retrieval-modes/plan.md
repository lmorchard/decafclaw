# Plan

See `spec.md` for architecture + decisions.

## Phase 1 — config + headlines formatter

- `VaultRetrievalConfig.mode: str = "always"` and
  `headline_summary_max_chars: int = 120` (with default).
- `format_memory_headlines(results, *, max_summary_chars)` in
  `memory_context.py`. Drop summary into the line as `file_path · summary · score`;
  fallback to truncated body when no summary frontmatter; sentinel
  empty-results behavior matches `format_memory_context`.
- Enrich existing `_enrich_results` to capture frontmatter
  `summary` alongside `importance` (one extra field).
- Tests: headlines empty, with summary, fallback to body, score
  formatting, truncation.

## Phase 2 — composer mode branching

- `_compose_vault_retrieval` reads `config.vault_retrieval.mode`.
  - `"on_demand"`: short-circuit before retrieval; return
    `(messages=[], formatted="", results=[], entry=...)` with the
    SourceEntry still emitted (so the sidecar records the mode).
  - `"headlines"`: full retrieval+score+budget pipeline, but use
    `format_memory_headlines` for rendering.
  - `"always"` / unknown: existing behavior; an unknown value
    logs a warning and falls back.
- SourceEntry `details` gains `mode` + `injection_skipped`.
- Tests: each mode branch produces the expected message shape and
  the expected sidecar details.

## Phase 3 — docs

- `docs/context-composer.md`: add a "Memory retrieval modes"
  subsection.
- `docs/semantic-search.md`: brief mention of mode-aware retrieval.
- `docs/config.md`: extend the `vault_retrieval` table.
- `CLAUDE.md`: update the context-engineering bullet.

## Phase 4 — squash, push, PR, request Copilot

`Closes #301`. Move project board entry to In review.
