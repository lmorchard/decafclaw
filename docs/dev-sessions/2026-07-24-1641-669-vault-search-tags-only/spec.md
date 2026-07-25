# vault_search tags-only search Spec

**Goal:** Make the tags-only `vault_search` mode — already implemented and documented — actually invokable by the agent, so a tag-scoped lookup returns results instead of a `TypeError`.

**Source:** https://github.com/lmorchard/decafclaw/issues/669

## Current state

The tags-only mode is **fully implemented** and reachable in the body (`skills/vault/tools.py:701-704`):

```python
if not query and req_tags:
    return _tag_filter_search(ctx.config, req_tags, any_tag,
                              source_type=source_type, folder=folder, days=days)
```

The docstring documents it (`:677-679`). But two interface surfaces block it:

1. **Signature** (`:670`): `query: str` has no default, so a tags-only call raises `TypeError` before the body runs.
2. **Schema** (`:1784`): `required: ['query']`, and the `query` description (`"Search text (natural language or keywords)"`) never mentions the empty-query mode — so the model has no signal it exists.

Introduced by #318/#660, which added `tags`/`any_tag` and documented the mode without relaxing `query`. See `research.md` for the verified three-surface disagreement.

## Desired end state

- `vault_search(tags=["rust"])` — no `query` — returns the pages tagged `rust`, via `_tag_filter_search`.
- The schema no longer marks `query` required, and its description states that leaving it empty filters purely by `tags`.
- A unit test covers the tags-only path, which currently has none (the docstring has been describing untested behavior).
- Existing query-based and query+tags behavior is unchanged.

## Design decisions

- **Decision:** `query: str = ""` — default to empty string, not `None`.
  - **Why:** the body already branches on `not query` and the type is `str` throughout; `None` would widen the type for no gain and risk `normalize_tag`/logging paths seeing a non-str.
  - **Rejected:** `query: str | None = None` — more churn, no behavioral difference given `not query` covers both.

- **Decision:** Drop `query` from the schema's `required` list and extend its description.
  - **Why:** the signature fix alone makes the mode *possible* but not *discoverable*. Per CLAUDE.md, tool descriptions are a control surface — the model won't use a mode nothing tells it about. Both halves are needed for the eval failure to actually stop recurring.
  - **Rejected:** signature-only fix — would leave case 41 still failing, since the model would keep passing a query.

- **Decision:** Add a `tool_choice` eval case for the tag-scoped ask.
  - **Why:** CLAUDE.md convention — "New or sharpened tool description → add a `tool_choice` case." This PR sharpens the `query` description specifically to steer routing, which is exactly the case that convention covers. `make eval-tools` is ~30s.
  - **Rejected:** relying on `evals/vault-tags.yaml` alone — that case asserts end-to-end retrieval with a query present, not the tags-only routing decision.

## Patterns to follow

- Tag-query layer: `_tag_filter_search` and `pages_with_tags` (`tags.py`, added by #318) — the fix routes to existing code, adds none.
- Schema shape: sibling optional params in the same definition (`source_type`, `days`, `folder`, `tags`, `any_tag` at `:1784`) are already absent from `required`; match them.
- Tool-choice case format: `evals/tool_choice/` existing cases.

## What we're NOT doing

- **Not touching `vault_section`** — that's #671, a separate path-resolution bug found in the same eval run.
- **Not touching `notes_append` / `vault_journal_append` routing** — that's #670.
- **Not fixing the three soft eval failures** (#18/#24/#28 in the run) — deliberately held for #650's noise-floor measurement; retuning them off one sample would destroy that signal.
- **Not refactoring `tool_vault_search`** despite its length (~90 lines, nested filters). Tempting, unrelated, and would obscure a 3-line fix.
- **Not adding tag-filter support to other vault tools** (`vault_list`, retrieval) — out of scope.
- **Not re-running the full `make eval`** as verification. Targeted unit test + `make eval-tools` only; a full run is ~11 min and belongs with #650.

## Open questions

None. The one open question from `research.md` — whether the body genuinely supports an empty query — was resolved before writing this spec by reading `:701-704`. It does, via `_tag_filter_search`. The fix is interface-only.
