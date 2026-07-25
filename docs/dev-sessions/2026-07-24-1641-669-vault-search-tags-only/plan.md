# vault_search tags-only Implementation Plan

**Goal:** Make the already-implemented tags-only `vault_search` mode invokable and discoverable.

**Approach:** Interface-only fix. Default `query` to `""`, drop it from the schema's `required`, and state the empty-query mode in its description. The body already routes `not query and req_tags` to `_tag_filter_search` (`skills/vault/tools.py:701-704`), so no logic changes. Add the missing test coverage and a `tool_choice` case.

**Tech stack:** Python 3.13, pytest, decafclaw eval harness.

---

## Phase 1: Make the tags-only call reachable and tested

Delivers a working tags-only `vault_search` end-to-end, with the regression test that was missing.

**Files:**
- Modify: `src/decafclaw/skills/vault/tools.py` — `query: str` → `query: str = ""` (`:670`); schema `required` + `query` description (`:1784`)
- Test: add to the existing tag test file if one exists (check `tests/` for `test_vault_tags*` / `test_tags*` during execute); otherwise create `tests/test_vault_search_tags_only.py`

**Key changes:**
- `tool_vault_search(ctx, query: str = "", ...)` — default added
- Schema: remove `query` from `required`, matching sibling optional params (`source_type`, `days`, `folder`, `tags`, `any_tag`)
- `query` description gains the empty-query mode

```python
# signature (:670)
async def tool_vault_search(ctx, query: str = "", source_type: str = "",
                            days: int = 0, folder: str = "",
                            tags: list[str] | None = None,
                            any_tag: bool = False) -> str | ToolResult:
```

```python
# schema description (:1784) — the discoverability half
"query": {
    "type": "string",
    "description": (
        "Search text (natural language or keywords). Leave empty to filter "
        "purely by `tags` — skips semantic/substring search and returns every "
        "page carrying the tags."
    ),
},
```

**Test-first.** Write these before touching the source. The behavioral ones must fail with `TypeError: missing 1 required positional argument: 'query'` — not an assertion mismatch:
1. `tool_vault_search(ctx, tags=["rust"])`, omitting `query`, returns the `rust`-tagged page and not the `python`-tagged one.
2. `any_tag=True` with two tags returns pages matching either.
3. Non-regression: `query="..."` alone still searches; `query` + `tags` still post-filters.
4. Schema assertion: `query` not in `required`, and its description mentions the empty-query mode. Guards the discoverability half, which is otherwise untestable and would silently rot.

**Verification — automated:**
- [ ] New behavioral tests fail with `TypeError` before the fix
- [ ] `make test` passes after
- [ ] `make check` passes
- [ ] `uv run pytest tests/ -k "tags_only or vault_search" -q` passes

**Verification — manual:**
- [ ] Read the final `query` description as the model sees it — does it actually convey the mode? Wording is the control surface; technically-correct-but-unclear fails this phase.

---

## Phase 2: Guard the routing decision with a tool_choice eval case

Delivers the CLAUDE.md-required eval guard so the sharpened description can't silently drift back.

**Files:**
- Add a case under `evals/tool_choice/` — inspect the existing case format during execute and match it

**Key changes:**
- A tag-scoped ask ("find my vault pages tagged rust") selects `vault_search` with `tags` set. Assert via `expect_tool` / `expect_tool_args` per `docs/eval-loop.md`.

**Verification — automated:**
- [ ] `make eval-tools` passes (~30s)
- [ ] `make test` still passes (tool_choice YAML is walked by `test_every_eval_yaml_setup_validates` from #663)

**Verification — manual:**
- [ ] Case is bounded (`max_tool_calls` / `max_tool_errors`) per the CLAUDE.md eval convention

---

## Phase 3: Docs + session notes

**Files:**
- Modify: `docs/vault.md` — document the tags-only mode if it enumerates `vault_search` params (check during execute; skip if not)
- Modify: session `notes.md` — final summary

**Verification — automated:**
- [ ] `make check` passes

**Verification — manual:**
- [ ] No stale claim left anywhere that `query` is required
