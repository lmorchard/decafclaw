# Prune stale embeddings

Tracking issue: #305

## Problem

When a vault page is deleted or compacted away, its embedding can
linger in `embeddings.db`. Memory retrieval may then surface that
stale candidate even though the underlying content is gone.

The runtime hooks for vault writes/deletes are already in place:

- `vault_delete` calls `delete_entries(config, rel_path, source_type)`.
- `vault_write` and `vault_rename` call the same.
- `http_server.py` workspace endpoints (`PUT` / `POST` / rename)
  also call it.

What's missing:

1. **One-time cleanup.** Existing deployments accumulated stale
   rows from periods before the runtime hooks landed (or while
   bugs in those hooks went unnoticed). Without a sweep, the
   index stays polluted indefinitely.
2. **Legacy source types.** Per `memory_context.py:51`, the
   retrieval path explicitly excludes `conversation`-source
   embeddings as legacy noise (#133). Those rows still sit in
   the DB and contribute zero value.

The compaction-half of #305 (drop conversation embeddings on
compaction) is a no-op today — conversations aren't being
embedded. Building that hook now would be infra-waiting-for-use.
The one-time prune covers any legacy rows; the runtime conversation
embedding hook is a follow-up if/when conversation embedding gets
re-enabled.

## Goal

A `prune_stale_embeddings(config)` function + `make
prune-embeddings` CLI that scans the index and drops:

- Rows whose source file no longer exists at the expected vault
  path (for `page`, `user`, `journal`, `wiki` legacy).
- Rows of source types we know to be legacy / excluded from
  retrieval (`conversation`, `memory` legacy default).
- Returns counts so the operator can see what got reclaimed.

Unknown source types are kept and logged — better to leave
unknown rows alone than nuke them.

## Decisions (autonomous brainstorm)

1. **Hard delete, no soft TTL.** The retrieval path already
   filters by file existence implicitly (search results carry a
   `file_path` whose corresponding file no longer exists is
   useless). A soft TTL would add complexity for a recovery case
   that doesn't actually exist — embeddings are deterministic
   functions of source content, so worst case the operator runs
   `make reindex` to rebuild from scratch.
2. **Legacy source types: drop unconditionally.** `conversation`
   and `memory` are explicitly excluded from retrieval (#133).
   Holding their rows costs disk and slows full-table scans for
   no benefit.
3. **Unknown source types: keep + warn.** Better to be
   conservative — a workspace skill could legitimately introduce
   a new source_type. Drop only what we know is safe.
4. **No new compaction hook.** Conversation embeddings aren't
   written today. The infra would sit unused.
5. **CLI entry, not a tool.** The agent shouldn't be poking at
   the embedding index at runtime; this is operator hygiene.
   Mirror the `decafclaw-reindex` pattern.
6. **Reuse `delete_entries`.** It already deletes from both
   `memory_embeddings` and `embeddings_vec` correctly. No new
   SQL for the row-removal path.

## Architecture

### `prune_stale_embeddings(config) -> dict`

```python
def prune_stale_embeddings(config) -> dict[str, int]:
    """Scan the embedding index; drop rows whose source no longer
    exists (or whose source type is excluded from retrieval).

    Returns counts: {
        "checked": total rows scanned,
        "dropped_missing": rows whose source file is gone,
        "dropped_legacy": rows of excluded source types,
        "kept": rows still pointing at live content,
        "unknown": rows with an unrecognized source_type (kept + logged),
    }
    """
```

### Source-existence check

Per source_type:

- `page`, `user`, `wiki` (legacy `page` alias), `journal`:
  file exists at `(vault_root / file_path).resolve()`. If not,
  drop.
- `conversation`, `memory`: drop unconditionally (legacy / excluded).
- Anything else: log once per unknown type, keep the row.

### CLI entry

`prune_embeddings_cli` in `embeddings.py`, mirroring `reindex_cli`.
Registered in `pyproject.toml` as `decafclaw-prune-embeddings`.
Makefile gets a `prune-embeddings` target.

CLI prints the count breakdown:

```
Embedding prune sweep — DecafClaw vault

Scanned 4823 rows.
  kept:            4612 (live vault content)
  dropped_missing:  187 (source files gone)
  dropped_legacy:    24 (conversation / memory legacy)
  unknown:            0

Reclaimed 211 rows from /path/to/embeddings.db
```

## Out of scope

- Compaction-time hook. Conversations aren't being embedded; the
  hook is YAGNI. File a follow-up if conversation embedding is
  re-enabled.
- Soft-delete with TTL.
- Auto-prune on startup. Operator-initiated only — keeps the
  semantics predictable.
- Schema migration. Existing schema is fine.

## Acceptance criteria

- `prune_stale_embeddings` returns the documented counts dict.
- Rows whose `(vault_root / file_path)` doesn't exist get
  dropped; live ones survive.
- `conversation` / `memory` rows always drop.
- Unknown source types are kept with a single-warning log.
- `make prune-embeddings` runs cleanly against an existing DB.
- Idempotent: a second run on a clean DB drops zero rows.

## Testing

- Unit tests for `prune_stale_embeddings` against an in-tmp DB
  seeded with a mix of rows: live `page`, missing `page`, live
  `journal`, missing `journal`, `conversation` legacy, `memory`
  legacy, unknown `frobnicate`. Assert per-bucket counts.
- Existing tests in `tests/test_embeddings.py` continue to pass.
- No real-LLM CI test. Manual smoke after merge: `make
  prune-embeddings` against the live DB.

## Files touched

- `src/decafclaw/embeddings.py` — `prune_stale_embeddings`,
  `prune_embeddings_cli`.
- `pyproject.toml` — register the new CLI entry point.
- `Makefile` — `prune-embeddings` target.
- `tests/test_embeddings.py` — extend with prune tests.
- `docs/semantic-search.md` — mention the prune CLI.
- `CLAUDE.md` — no convention change; just a `make
  prune-embeddings` mention in the "Running" block.
