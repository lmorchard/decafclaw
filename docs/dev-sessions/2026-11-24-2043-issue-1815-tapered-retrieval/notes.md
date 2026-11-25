# issue-1815-tapered-retrieval

## Resume here

- Issue: https://github.com/lmorchard/decafclaw/issues/1815
- Worktree: /Users/lmorchard/devel/mine/decafclaw/.claude/worktrees/issue-1815-tapered-retrieval
- Branch: issue-1815-tapered-retrieval
- Base: origin/main at fe6c9e34e030b20b84a79a7863e0715b7f46a0df
- Status: implemented, tests and `make check` green (known macOS host-metrics failures only); PR pending.

## Log

- Added `RetrievalMode.TAPERED` and `vault_retrieval.tapered_top_n_detailed` / `tapered_summary_max_chars`.
- `memory_context.format_memory_tapered` renders L0 summary + L1 abstract for the top N, L0 headlines after, with a `vault_read` banner.
- L1 extraction order: `> tl;dr:` blockquote, named overview section, first paragraph. Enrichment stores `l1_excerpt` from the page body so the composite embedding text does not duplicate the summary as L1.
- Extension routes tapered mode and honors `top_n_detailed` / `headline_summary_max_chars` / `tapered_summary_max_chars` options.
- Prompt (`prompts/AGENT.md`) tells the agent that retrieval shows abstracts and to call `vault_read` for L2 bodies.
- Docs: context-composer, config, extensions.
