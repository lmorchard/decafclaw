# Self-improving Vault Implementation Plan (#197)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the vault self-improving — dream generates page frontmatter, garden tunes `importance` from real signals — and make retrieval quality measurable.

**Architecture:** Six phases on branch `197-vault-evolution`, one commit each. A structured `vault_update_frontmatter` tool is the shared write primitive (enforces respect-manual-values in code); a deterministic `vault_recompute_importance` computes importance from retrieval-frequency telemetry + a persistent backlink index. Measurement is a fail-open retrieval-event telemetry stream plus an LLM-judge eval.

**Tech Stack:** Python 3.13, `uv`, pytest (xdist), EventBus pub/sub, sqlite-vec embeddings, YAML frontmatter, prompt-driven skills (dream/garden).

## Global Constraints

- Python deps via `uv` / `uv run` inside the worktree; never bare `python` (resolves to the main clone's editable install).
- New runtime state goes on dataclasses; never `setattr`/`getattr` undeclared fields. Config additions: dataclass default → `config.json` → env, wired in `config.py` via `load_sub_config`.
- Telemetry and all new event subscribers are **fail-open** — never propagate an exception into a turn (`except Exception as exc: log.debug(...)`). No bare `except: pass`.
- Files on disk, human-readable, crash-recoverable (JSONL / JSON / markdown).
- Tools receive `ctx` first, even if unused. Tool errors return `ToolResult(text="[error: ...]")`.
- `make check` + `make test` green before every phase commit. Suite currently ends with 2 known forkpty warnings (#638) — not ours; keep `PytestUnraisableExceptionWarning`-class noise at zero.
- Commit trailer: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.
- Rebase on `origin/main` before the final PR squash (main advances often).
- Update the feature's `docs/` page in the same PR; update `docs/context-composer.md` for context-assembly/tool changes.

## Cross-phase interface: the retrieval event

Phase 0 defines this; Phase 5's importance formula consumes the aggregated form.

```python
# Published on the EventBus from _compose_vault_retrieval once per interactive turn.
{
    "type": "retrieval_event",
    "conv_id": str,
    "candidates": [
        {
            "file_path": str,
            "source_type": str,          # "page" | "user" | "journal" | "graph_expansion"
            "similarity": float,
            "recency": float,
            "importance": float,
            "composite_score": float,
            "included": bool,            # survived score threshold AND budget trim
            "drop_reason": str | None,   # None | "score" | "budget"
        },
        ...
    ],
}
```

One JSONL record per event at `{workspace}/telemetry/retrieval.jsonl`.

---

## Phase 0 — Retrieval telemetry stream

**Files:**
- Create: `src/decafclaw/retrieval_telemetry.py`
- Create: `tests/test_retrieval_telemetry.py`
- Modify: `src/decafclaw/context_composer.py` (`_compose_vault_retrieval`, emit event)
- Modify: `src/decafclaw/config_types.py` (`TelemetryConfig` — add `retrieval_path`)
- Modify: `src/decafclaw/runner.py` (subscribe the telemetry handler on the bus)
- Modify: `Makefile` (add `retrieval-report`), `pyproject.toml` (console script)

**Interfaces:**
- Produces: `make_retrieval_telemetry_subscriber(config) -> Callable[[dict], Awaitable[None]]`; `build_report(config) -> str`; `_retrieval_path(config) -> Path`.
- Consumes: the `retrieval_event` schema above.

- [ ] **Step 1: Write the failing test for the subscriber**

```python
# tests/test_retrieval_telemetry.py
import json
import pytest
from decafclaw.retrieval_telemetry import (
    make_retrieval_telemetry_subscriber, _retrieval_path, record_from_event,
)

def _event():
    return {
        "type": "retrieval_event", "conv_id": "c1",
        "candidates": [
            {"file_path": "pages/a.md", "source_type": "page", "similarity": 0.9,
             "recency": 0.8, "importance": 0.5, "composite_score": 0.77,
             "included": True, "drop_reason": None},
            {"file_path": "pages/b.md", "source_type": "page", "similarity": 0.2,
             "recency": 0.5, "importance": 0.5, "composite_score": 0.3,
             "included": False, "drop_reason": "score"},
        ],
    }

@pytest.mark.asyncio
async def test_subscriber_writes_one_record_per_event(config):
    handler = make_retrieval_telemetry_subscriber(config)
    await handler(_event())
    await handler({"type": "tool_end"})  # ignored
    lines = _retrieval_path(config).read_text().strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["conv_id"] == "c1"
    assert len(rec["candidates"]) == 2
    assert rec["candidates"][0]["included"] is True

@pytest.mark.asyncio
async def test_subscriber_fail_open_on_bad_event(config):
    handler = make_retrieval_telemetry_subscriber(config)
    await handler({"type": "retrieval_event"})  # no candidates key — must not raise
```

- [ ] **Step 2: Run it, verify import/attribute failure**

Run: `cd .claude/worktrees/197-vault-evolution && uv run pytest tests/test_retrieval_telemetry.py -q`
Expected: FAIL (module/attr missing).

- [ ] **Step 3: Implement `retrieval_telemetry.py`** — mirror `tool_telemetry.py`.

Model exactly on `tool_telemetry.py`: `_now_iso`, `_retrieval_path(config)` → `config.workspace_path / config.telemetry.retrieval_path`, `record_from_event(event)` (timestamp + conv_id + candidates), `append_record`, `make_retrieval_telemetry_subscriber` (ignore non-`retrieval_event`, fail-open), and the reporting half (`load_records`, `aggregate`, `format_report`, `build_report`, `main`). `aggregate` computes per-page: retrieval_count (appeared as candidate), include_count, drop-by-reason; plus vault-health: frontmatter coverage %, orphan count (needs the vault — read pages, count those with/without `importance` frontmatter). Keep the report format table-shaped like `format_report` in tool_telemetry.

- [ ] **Step 4: Add `retrieval_path` to `TelemetryConfig`**

In `config_types.py`, add `retrieval_path: str = "telemetry/retrieval.jsonl"` next to `tool_usage_path`. (No env wiring change needed beyond the existing `TELEMETRY_` prefix.)

- [ ] **Step 5: Run subscriber tests to green**

Run: `uv run pytest tests/test_retrieval_telemetry.py -q`
Expected: PASS.

- [ ] **Step 6: Write the failing composer-emit test**

```python
# tests/test_context_composer.py — new async test
@pytest.mark.asyncio
async def test_retrieval_event_published_with_drop_verdicts(self, ctx, config):
    events = []
    async def cap(e):
        if e.get("type") == "retrieval_event":
            events.append(e)
    ctx.event_bus.subscribe(cap)  # match existing subscribe API in tests
    mock_results = [
        {"file_path": "pages/hit.md", "source_type": "page", "entry_text": "x",
         "similarity": 0.9, "modified_at": "", "importance": 0.9},
        {"file_path": "pages/miss.md", "source_type": "page", "entry_text": "y",
         "similarity": 0.01, "modified_at": "", "importance": 0.1},
    ]
    with patch("decafclaw.context_composer.retrieve_memory_context",
               new_callable=AsyncMock, return_value=mock_results):
        composer = ContextComposer()
        await composer._compose_vault_retrieval(
            ctx, config, "q", ComposerMode.INTERACTIVE)
    assert len(events) == 1
    paths = {c["file_path"]: c for c in events[0]["candidates"]}
    assert paths["pages/hit.md"]["included"] is True
    assert paths["pages/miss.md"]["included"] is False
    assert paths["pages/miss.md"]["drop_reason"] in ("score", "budget")
```

(Confirm the exact EventBus subscribe API from an existing composer test before finalizing — reuse whatever pattern `test_background_event_expanded_in_compose` uses.)

- [ ] **Step 7: Emit the event in `_compose_vault_retrieval`**

After `_score_candidates` returns the full scored list, capture it before the threshold/budget filters. Compute the surviving `file_path` set after trim. Build the candidates array tagging `included` + `drop_reason` (`"score"` if below `min_composite_score`, else `"budget"` if dropped by `_trim_to_token_budget`). Publish via `await ctx.publish("retrieval_event", conv_id=..., candidates=[...])` (match the `ctx.publish` signature used elsewhere — it wraps `{"type": ..., **kwargs}`). Fail-open around the emit so telemetry never breaks retrieval.

- [ ] **Step 8: Run composer test + full suite**

Run: `uv run pytest tests/test_context_composer.py -q && uv run pytest -q`
Expected: PASS (3116+ ; only the known forkpty warnings).

- [ ] **Step 9: Wire the subscriber + report target**

In `runner.py`, subscribe `make_retrieval_telemetry_subscriber(config)` where `make_tool_telemetry_subscriber` is subscribed (grep it). Add `pyproject.toml` console script `decafclaw-retrieval-report = "decafclaw.retrieval_telemetry:main"` and `Makefile` target `retrieval-report:` mirroring `tool-usage-report`.

- [ ] **Step 10: `make check` + commit**

```bash
uv run pytest tests/test_retrieval_telemetry.py tests/test_context_composer.py -q
make check
git add -A && git commit -m "feat(vault): retrieval-event telemetry stream + report (#197)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Phase 1 — `vault_update_frontmatter` tool

**Files:**
- Modify: `src/decafclaw/skills/vault/tools.py` (new tool fn + `TOOL_HANDLERS`/`TOOL_DEFINITIONS` registration)
- Modify: `tests/test_vault_tools.py`

**Interfaces:**
- Produces: `async tool_vault_update_frontmatter(ctx, page, fields: dict, overwrite: bool=False) -> ToolResult`. Registered as tool name `vault_update_frontmatter`.
- Consumes: `frontmatter.parse_frontmatter`, `frontmatter.serialize_frontmatter`, existing `_reindex_page(ctx, path)` (already in vault tools), `resolve_page`.

- [ ] **Step 1: Failing tests**

```python
class TestVaultUpdateFrontmatter:
    @pytest.mark.asyncio
    async def test_fills_absent_fields(self, ctx, agent_pages):
        p = agent_pages / "topic.md"; p.write_text("Body text.\n")
        await tool_vault_update_frontmatter(
            ctx, page="agent/pages/topic.md",
            fields={"summary": "s", "importance": 0.8})
        meta, body = parse_frontmatter(p.read_text())
        assert meta["summary"] == "s" and meta["importance"] == 0.8
        assert body.strip() == "Body text."

    @pytest.mark.asyncio
    async def test_respects_existing_value_when_not_overwrite(self, ctx, agent_pages):
        p = agent_pages / "topic.md"
        p.write_text("---\nsummary: manual\n---\nBody.\n")
        await tool_vault_update_frontmatter(
            ctx, page="agent/pages/topic.md",
            fields={"summary": "auto"}, overwrite=False)
        meta, _ = parse_frontmatter(p.read_text())
        assert meta["summary"] == "manual"

    @pytest.mark.asyncio
    async def test_overwrite_replaces(self, ctx, agent_pages):
        p = agent_pages / "topic.md"
        p.write_text("---\nimportance: 0.5\n---\nBody.\n")
        await tool_vault_update_frontmatter(
            ctx, page="agent/pages/topic.md",
            fields={"importance": 0.9}, overwrite=True)
        meta, _ = parse_frontmatter(p.read_text())
        assert meta["importance"] == 0.9

    @pytest.mark.asyncio
    async def test_clamps_importance_and_coerces_lists(self, ctx, agent_pages):
        p = agent_pages / "t.md"; p.write_text("Body.\n")
        await tool_vault_update_frontmatter(
            ctx, page="agent/pages/t.md",
            fields={"importance": 5.0, "tags": "a"})
        meta, _ = parse_frontmatter(p.read_text())
        assert meta["importance"] == 1.0 and meta["tags"] == ["a"]

    @pytest.mark.asyncio
    async def test_reindexes_after_write(self, ctx, agent_pages):
        p = agent_pages / "t.md"; p.write_text("Body.\n")
        with patch("decafclaw.skills.vault.tools._reindex_page",
                   new_callable=AsyncMock) as rx:
            await tool_vault_update_frontmatter(
                ctx, page="agent/pages/t.md", fields={"summary": "s"})
        rx.assert_awaited_once()
```

- [ ] **Step 2: Run → fail.** `uv run pytest tests/test_vault_tools.py::TestVaultUpdateFrontmatter -q`

- [ ] **Step 3: Implement the tool.** Read the page via `resolve_page`; `parse_frontmatter`; for each field in `fields`, coerce (importance→clamped float, tags/keywords→list[str], summary→str); merge respecting `overwrite`; `serialize_frontmatter`; write; `await _reindex_page(ctx, path)`; return `ToolResult(text=..., data={"changed": [...]})`. Guard unknown page → `ToolResult(text="[error: ...]")`.

- [ ] **Step 4: Register** in `TOOL_HANDLERS` + `TOOL_DEFINITIONS` (copy the shape of a neighboring vault tool; description states "merge frontmatter fields; only fills absent fields unless overwrite; reindexes"). Confirm registration names via a `tool_choice` eval later.

- [ ] **Step 5: Run → green.** Then `make check`.

- [ ] **Step 6: Commit** `feat(vault): vault_update_frontmatter merge tool (#197)`.

---

## Phase 2 — Dream frontmatter generation

**Files:**
- Modify: `src/decafclaw/skills/dream/SKILL.md`
- Create: `evals/vault-frontmatter.yaml` (behavior eval)
- Modify: `docs/skills.md` / `docs/vault.md` as needed

**Interfaces:** consumes `vault_update_frontmatter` (Phase 1). No code — prose + eval.

- [ ] **Step 1: Edit `dream/SKILL.md` Consolidate phase.** Add: after writing/revising a page body, call `vault_update_frontmatter` (overwrite=False) to fill `summary` (1 sentence), `keywords`, `tags`, and an initial `importance` (0–1; guidance: pages consolidating many journal entries or central to the graph score higher; a passing note scores lower). Do not overwrite fields the user set manually.

- [ ] **Step 2: Add `evals/vault-frontmatter.yaml`.** A case where dream-style consolidation of a seeded journal produces a page, asserting `expect_tool: vault_update_frontmatter`. Bound with `max_tool_calls` / `max_tool_errors`. Add a `tool_choice` case disambiguating `vault_update_frontmatter` from `vault_write`.

- [ ] **Step 3: Run the eval.** `uv run python -m decafclaw.eval evals/vault-frontmatter.yaml` (needs the LiteLLM proxy). Iterate SKILL.md wording until it reliably calls the tool.

- [ ] **Step 4: Commit** `feat(dream): generate page frontmatter on consolidate (#197)`.

---

## Phase 3 — Backfill CLI

**Files:**
- Create: `src/decafclaw/backfill_frontmatter.py` (CLI)
- Create: `tests/test_backfill_frontmatter.py`
- Modify: `pyproject.toml` (console script), `Makefile` (`backfill-frontmatter`)

**Interfaces:**
- Produces: `generate_fields_for_page(config, path) -> dict` (forced-tool structured-output LLM call, Sophie-style — one schema, "you MUST call this"); `run_backfill(config, *, dry_run=False, limit=None) -> list[dict]`.
- Consumes: `vault_update_frontmatter` logic (reuse the merge helper — extract a pure `merge_frontmatter(existing, fields, overwrite)` function during Phase 1 so both the tool and the CLI call it without a `ctx`).

> **Note for Phase 1 executor:** extract `merge_frontmatter(existing: dict, fields: dict, overwrite: bool) -> dict` as a pure function in `skills/vault/tools.py`; the tool wraps it. Phase 3 reuses it.

- [ ] **Step 1: Failing test** for `run_backfill` over a tmp vault with 2 pages (one already has frontmatter → skipped, one bare → filled). Patch the LLM call to return fixed fields.
- [ ] **Step 2: Run → fail.**
- [ ] **Step 3: Implement** — iterate frontmatter-less pages (via existing vault-page iteration; reuse `embeddings._iter_vault_pages`-style walk or `collect_recent_pages`), call `generate_fields_for_page`, `merge_frontmatter(overwrite=False)`, write. `--dry-run` prints planned changes; `--limit N`; log progress; resumable (skip pages that already have all four fields).
- [ ] **Step 4: Run → green;** add console script + `make backfill-frontmatter`; note in help that a `make reindex` should follow.
- [ ] **Step 5: `make check` + commit** `feat(vault): backfill-frontmatter CLI (#197)`.

---

## Phase 4 — Persistent backlink index

**Files:**
- Create: `src/decafclaw/backlinks.py`
- Create: `tests/test_backlinks.py`
- Modify: `src/decafclaw/skills/vault/tools.py` (`tool_vault_backlinks` reads the index; incremental update on write)
- Modify: `docs/vault.md`

**Interfaces:**
- Produces: `rebuild_index(config) -> dict[str, list[str]]` (page → inbound linkers); `load_index(config) -> dict[str,list[str]]`; `inbound_count(config, page) -> int`; `update_for_page(config, page)` (incremental). Stored JSON at `{workspace}/backlinks.json`.
- Consumes: existing `_WIKI_LINK_RE` / `resolve_page`.

- [ ] **Step 1: Failing tests** — build over a tmp vault of 3 pages with `[[links]]`; assert inbound map; assert incremental `update_for_page` after adding a link; assert `inbound_count`.
- [ ] **Step 2: Run → fail.**
- [ ] **Step 3: Implement** `backlinks.py` — full rebuild via one `rglob` pass (reuse the scan logic currently inside `tool_vault_backlinks`), persist JSON, incremental update on a single page's links. Fail-open on IO.
- [ ] **Step 4: Rewire `tool_vault_backlinks`** to read the index (rebuild lazily if missing). Subscribe an incremental-update handler to `vault_changed` in `runner.py` (fail-open).
- [ ] **Step 5: Run → green; `make check`; commit** `feat(vault): persistent backlink index (#197)`.

---

## Phase 5 — Deterministic importance tuning (garden)

**Files:**
- Create: `src/decafclaw/skills/garden/tools.py` (`vault_recompute_importance`) + register
- Create: `tests/test_recompute_importance.py`
- Modify: `src/decafclaw/skills/garden/SKILL.md`
- Modify: `src/decafclaw/config_types.py` (`ImportanceConfig`: `w_retrieval`, `w_inbound`, `w_reference=0.0`), wire in `config.py`
- Modify: `docs/vault.md`, `docs/config.md`

**Interfaces:**
- Produces: `compute_importance_scores(config) -> dict[str, float]` (pure, testable); `async tool_vault_recompute_importance(ctx, dry_run: bool=False) -> ToolResult`.
- Consumes: `retrieval_telemetry.aggregate` (retrieval frequency per page), `backlinks.inbound_count`, `merge_frontmatter` / `vault_update_frontmatter(overwrite=True)`.

Formula (v1, `w_reference=0`): `importance = clamp01(w_retrieval·norm(retrieval_freq) + w_inbound·norm(inbound_links))`, where `norm(x)=x/max(x across pages)` (0 when max is 0). Defaults `w_retrieval=0.6`, `w_inbound=0.4`.

- [ ] **Step 1: Failing test** for `compute_importance_scores` — patch telemetry aggregate + backlink index with fixtures; assert a frequently-retrieved, heavily-linked page scores near 1.0 and an orphan near 0.0; assert clamp + normalization; assert empty-data → all 0.0 (no divide-by-zero).
- [ ] **Step 2: Run → fail.**
- [ ] **Step 3: Implement** the pure scorer + the tool (tool writes via `overwrite=True`, `--dry-run` returns planned deltas without writing). Add `ImportanceConfig`.
- [ ] **Step 4: Edit `garden/SKILL.md`** weekly steps: call `vault_recompute_importance`, review outliers, validate frontmatter consistency, flag orphans (inbound_count 0 + low retrieval) and weak-links.
- [ ] **Step 5: Run → green; `make check`; commit** `feat(garden): deterministic importance recompute (#197)`.

---

## Measurement — retrieval-quality eval

**Files:**
- Create: `evals/retrieval-quality.yaml`
- Possibly: extend `make build-eval-fixtures` for a frontmatter-rich fixture vault.

- [ ] LLM-judge cases: seed a fixture vault (with generated frontmatter), pose representative queries, judge whether the injected memory context is relevant/sufficient. Run once as a **baseline before Phase 2's dream generation is active**, and again **after Phase 5**. Record both in `notes.md`. Pair with `make retrieval-report` before/after for the proxy delta.

---

## Self-review (done at write time)

- **Spec coverage:** dream generation (P2), garden importance tuning (P5), backfill+reindex (P3), backlink index (P4), telemetry proxy (P0), LLM-judge eval (Measurement), `vault_update_frontmatter` D1 (P1), deterministic formula D2 (P5). Micro-evolution + reference-frequency correctly excluded. ✓
- **Type consistency:** `merge_frontmatter(existing, fields, overwrite)` defined in P1, reused P3/P5; `retrieval_event` schema fixed in the cross-phase section, consumed by P0 subscriber + P5 scorer; `inbound_count` P4 → P5. ✓
- **Ordering:** P1 before P2/P3/P5 (they call the tool/merge); P0 before P5 (retrieval freq); P4 before P5 (inbound count). ✓

## Execution notes

- Subagent-driven: dispatch a fresh subagent per phase; each must `cd` into the worktree, use `uv run`, read the real target files before editing, TDD, and stop at its phase commit for review.
- After all phases: rebase on `origin/main`, self-review the full diff, update `docs/context-composer.md` (telemetry) + `docs/vault.md` + `CLAUDE.md` key-files/conventions, squash to per-phase commits (keep them) or one — decide at PR time, open PR `Closes #197`, request Copilot.
- Deploy-and-wait: importance formula weights are guesses until `retrieval.jsonl` accrues real data on the deployed agent (~a week), same posture as the instrumentation-first direction.
