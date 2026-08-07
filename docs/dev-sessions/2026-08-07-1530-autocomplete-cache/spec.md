# Autocomplete performance: build and manage a quick filename cache index

**Source:** https://github.com/lmorchard/decafclaw/issues/799

## Problem
Autocomplete is still significantly too slow, even with 150ms debouncing and top-level directory pruning in the workspace walk. Walking the workspace tree on every query is inefficient for larger codebases or deeper project trees.

## Proposed Solution
Build a lightweight background indexing/cache process to maintain and manage a quick filename/resource cache index specifically optimized for fast autocomplete lookups.

## Acceptance Criteria

- CRITERION: WHEN `/api/autocomplete` is queried for workspace files, THE SYSTEM SHALL query an in-memory workspace file index cache rather than walking the filesystem directory tree on every HTTP request.
  CHECK: `uv run pytest tests/test_autocomplete.py::test_autocomplete_uses_file_index_cache`
  VERIFIED DISCRIMINATING: Ran `uv run pytest tests/test_autocomplete.py::test_autocomplete_uses_file_index_cache` at intake and got exit code 5 (no tests ran / test node missing, failed as expected).

- CRITERION: WHEN files in the workspace are modified, added, or deleted (or when the cache is invalidated), THE SYSTEM SHALL update or refresh the filename cache so autocomplete returns up-to-date workspace file paths.
  CHECK: `uv run pytest tests/test_autocomplete.py::test_autocomplete_cache_invalidation`
  VERIFIED DISCRIMINATING: Ran `uv run pytest tests/test_autocomplete.py::test_autocomplete_cache_invalidation` at intake and got exit code 5 (no tests ran / test node missing, failed as expected).

### Regression guards
- GUARD: `uv run pytest tests/test_autocomplete.py` passes all existing autocomplete endpoint tests.
- GUARD: `make check` passes on decafclaw.

## Tier: `auto-ok`
All acceptance criteria are covered by automated unit tests in `tests/test_autocomplete.py` and no risk-gated paths are modified.

## Design decisions
- **Decision:** Implement an in-memory workspace file cache with TTL / dirty checking in `http_server.py` or a workspace index helper.
  - **Why:** Keeps autocomplete responses fast and sub-millisecond without requiring external indexing daemons.
  - **Rejected:** External indexing process or database, which adds daemon management overhead.

## What we're NOT doing
- Not replacing MCP resource discovery or vault page search logic (only workspace file walking is cached).
