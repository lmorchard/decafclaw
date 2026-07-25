# Research — #669 vault_search tags-only

Gathered inline (no research subagent; session runs under a no-subagent constraint).
All findings verified against the code at `78a1469`, not inferred.

## How the bug surfaced

First full `make eval` run in ~8 weeks (2026-07-24, 45/52 passed). Case
*"dream consolidation fills page frontmatter after writing"* failed:

```
Too many tool errors: 1 > 0
[error executing vault_search: tool_vault_search() missing 1 required positional
 argument: 'query'. Expected parameters: query, source_type, days, folder, tags, any_tag]
```

## The three surfaces disagree

| Surface | Location | Says |
|---|---|---|
| Docstring | `skills/vault/tools.py:674-679` | "Tag filtering has two modes: Empty `query` + non-empty `tags`: pure tag filter, bypasses …" |
| Signature | `skills/vault/tools.py:670` | `async def tool_vault_search(ctx, query: str, ...)` — **no default** |
| JSON schema | `skills/vault/tools.py:1784` | `required: ['query']`; description `"Search text (natural language or keywords)"` |

Verified schema by loading it:

```
required : ['query']
props    : ['query', 'source_type', 'days', 'folder', 'tags', 'any_tag']
```

So the "pure tag filter" mode the docstring advertises cannot be invoked
through the tool interface. Two independent barriers:

1. **Python:** `query` is a required positional — a tags-only call raises
   `TypeError` before the body runs.
2. **Schema:** the model is told `query` is required, and nothing mentions
   that empty means "filter by tags only". Even after (1) is fixed, the model
   has no signal the mode exists.

## Provenance

`tags` / `any_tag` were added by the first-class tags work (#318, merged as
#660 earlier the same day). That change documented the tags-only mode and
added the filter parameters without relaxing `query`.

## Does the body actually support an empty query?

Needs confirming during execute — the docstring claims it, but the docstring
has been describing untested behavior, so the claim itself is suspect. Read
the branch that handles `tags` before assuming an empty query short-circuits
to a pure tag filter. This is the one place where the fix could turn out to be
larger than a signature tweak.

## Related existing behavior

`pages_with_tags` / `collect_all_tags` in `tags.py` are the tag-query layer
(#318). Whether `tool_vault_search` routes an empty query to those, or falls
through to semantic search with an empty embedding, determines whether this is
a 1-line fix or needs a real branch.
