# Behavioral Eval Suites + Per-Turn Context Diagnostics — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close #528 (behavioral eval suites grouped by failure-mode axis + aggregate scorecard) and #531 (per-turn context diagnostics in eval result bundles) in one arc.

**Architecture:** A new pure-function module `src/decafclaw/eval/diagnostics.py` holds all deterministic logic (axis parsing, per-axis aggregation, file-read/-cited detection, diagnostics-block assembly). `runner.py`/`__main__.py` wire it in with a few lines each. The per-turn diagnostics **read the existing context sidecar** (`conversations/{conv_id}/context.json`, written on every turn-exit by `agent.py:_write_diagnostics` → `ContextComposer.build_diagnostics`) — no recomputation, no duplication. Seven new adversarial `evals/*.yaml` suites carry an axis tag; the aggregate groups pass-rates by that tag.

**Tech Stack:** Python 3.12, pytest (xdist `-n auto`), PyYAML, dataclasses. LLM-visible suites validated with real model runs via `make eval`.

## Global Constraints

- **Worktree:** all work in `.claude/worktrees/528-531-eval-behavioral-diagnostics/` (branch `528-531-eval-behavioral-diagnostics`, port `HTTP_PORT=18894`). Always `uv run <cmd>` — bare `python` hits the main clone's editable install. Subagents start with their own CWD; every implementer step must `cd` into the worktree and verify branch.
- **Canonical axes** (exact strings, verbatim): `retrieval`, `routing`, `answer_quality`, `workflow_discipline`. Untagged cases bucket as `untagged`.
- **Read-tool arg map** (exact): `{"vault_read": "page", "workspace_read": "path"}`.
- **Strict validation:** an unknown axis in `tests:` raises `ValueError` (consistent with #663's strict `setup.*` stance) — never silently drop.
- **Eval assertion discipline:** every case bounded by `max_tool_calls` + `max_tool_errors`; `expect_no_tool`/tight-`max_tool_calls` cases set `setup.config_overrides: {reflection.enabled: false}`.
- **Fail-open diagnostics:** a missing/`None` sidecar must degrade to derived-only fields, never raise.
- **Verify before commit:** `uv run make check` + `uv run make test` green before every commit. Baseline is clean (3338 passed, 2 skipped, zero warnings) — keep it that way.
- **Commit trailer:** `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.
- **No field enumeration** when copying dataclasses (CLAUDE.md) — not directly relevant here but keep in mind for any Config touch.

---

## File Structure

- **Create** `src/decafclaw/eval/diagnostics.py` — all pure logic:
  - `CANONICAL_AXES: frozenset[str]`
  - `READ_TOOL_ARGS: dict[str, str]`
  - `parse_axes(case: dict) -> list[str]`
  - `aggregate_by_axis(test_results: list[dict], cases: list[dict]) -> dict`
  - `detect_files_read(tool_calls: list[tuple[str, dict]]) -> list[str]`
  - `detect_files_cited(response: str, known_paths: list[str]) -> list[str]`
  - `build_turn_diagnostics(sidecar: dict | None, tool_calls: list[tuple[str, dict]], response: str) -> dict`
- **Create** `tests/test_eval_diagnostics.py` — unit tests for the module.
- **Modify** `src/decafclaw/eval/runner.py` — import `read_context_sidecar` + the diagnostics helpers; attach a `diagnostics` block per-turn + top-level in `run_test`; compute `by_axis` in `run_eval`; print the scorecard + `--verbose` diagnostics summary.
- **Modify** `src/decafclaw/eval/__main__.py` — nothing required (bundle already serializes `results` dict). Verify only.
- **Create** `evals/vault_answering.yaml`, `evals/tool_routing.yaml`, `evals/source_grounding.yaml`, `evals/context_pressure.yaml`, `evals/clarification.yaml`, `evals/abort_recovery.yaml`, `evals/over_ceremony.yaml`.
- **Modify** `docs/eval-loop.md` — axis tagging, scorecard, diagnostics block, `--verbose`.

---

## Task 1: diagnostics module scaffold + `parse_axes`

**Files:**
- Create: `src/decafclaw/eval/diagnostics.py`
- Test: `tests/test_eval_diagnostics.py`

**Interfaces:**
- Produces: `CANONICAL_AXES: frozenset[str]`; `parse_axes(case: dict) -> list[str]` — returns the case's axis tags (from top-level `tests:` key: absent→`[]`, str→`[str]`, list→list), validating each against `CANONICAL_AXES` and raising `ValueError` on any unknown value.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_eval_diagnostics.py
"""Unit tests for the deterministic eval-diagnostics helpers (#528, #531)."""

import pytest

from decafclaw.eval.diagnostics import CANONICAL_AXES, parse_axes


def test_parse_axes_absent_returns_empty():
    assert parse_axes({"name": "x"}) == []


def test_parse_axes_string_form():
    assert parse_axes({"tests": "retrieval"}) == ["retrieval"]


def test_parse_axes_list_form():
    assert parse_axes({"tests": ["routing", "answer_quality"]}) == ["routing", "answer_quality"]


def test_parse_axes_unknown_raises():
    with pytest.raises(ValueError, match="unknown axis"):
        parse_axes({"tests": "smartness"})


def test_parse_axes_unknown_in_list_raises():
    with pytest.raises(ValueError, match="unknown axis"):
        parse_axes({"tests": ["retrieval", "bogus"]})


def test_canonical_axes_exact_set():
    assert CANONICAL_AXES == frozenset(
        {"retrieval", "routing", "answer_quality", "workflow_discipline"}
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd .claude/worktrees/528-531-eval-behavioral-diagnostics && uv run pytest tests/test_eval_diagnostics.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'decafclaw.eval.diagnostics'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/decafclaw/eval/diagnostics.py
"""Deterministic helpers for eval axis tagging + per-turn context diagnostics.

Pure functions — no LLM calls, no I/O. `runner.py` wires these into the eval
result bundle. Per-turn diagnostics reuse the context sidecar written by
`agent.py:_write_diagnostics` (see #531); axis aggregation powers the
failure-mode scorecard (see #528).
"""

from __future__ import annotations

CANONICAL_AXES: frozenset[str] = frozenset(
    {"retrieval", "routing", "answer_quality", "workflow_discipline"}
)

# Read-shaped tool calls whose named arg identifies the file/page read.
READ_TOOL_ARGS: dict[str, str] = {"vault_read": "page", "workspace_read": "path"}


def parse_axes(case: dict) -> list[str]:
    """Return the axis tags declared on an eval case's top-level ``tests:`` key.

    Absent → ``[]``. A bare string → a one-element list. A list passes through.
    Every value must be in :data:`CANONICAL_AXES`; an unknown value raises
    ``ValueError`` rather than silently vanishing from the scorecard (mirrors
    #663's strict ``setup.*`` handling).
    """
    raw = case.get("tests")
    if raw is None:
        return []
    axes = [raw] if isinstance(raw, str) else list(raw)
    for axis in axes:
        if axis not in CANONICAL_AXES:
            raise ValueError(
                f"unknown axis {axis!r} in tests: — must be one of "
                f"{sorted(CANONICAL_AXES)}"
            )
    return axes
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_eval_diagnostics.py -q`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
git add src/decafclaw/eval/diagnostics.py tests/test_eval_diagnostics.py
git commit -m "feat(eval): axis-tag parsing for behavioral suites (#528)"
```

---

## Task 2: `aggregate_by_axis` + wire into `run_eval` + scorecard print + docs

**Files:**
- Modify: `src/decafclaw/eval/diagnostics.py`
- Modify: `src/decafclaw/eval/runner.py` (in `run_eval`, after the summary loop ~line 900)
- Modify: `docs/eval-loop.md`
- Test: `tests/test_eval_diagnostics.py`

**Interfaces:**
- Consumes: `parse_axes` (Task 1).
- Produces: `aggregate_by_axis(test_results: list[dict], cases: list[dict]) -> dict` — maps each axis (plus `untagged`) to `{"total": int, "passed": int, "failed": int, "pass_rate": float}`. `test_results[i]` pairs with `cases[i]` by index; a result's `status` is `"pass"`/`"fail"`. Multi-axis cases count once toward each axis. Also sets `results["summary"]["by_axis"]` in `run_eval`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_eval_diagnostics.py
from decafclaw.eval.diagnostics import aggregate_by_axis


def test_aggregate_single_axis_and_untagged():
    cases = [
        {"name": "a", "tests": "retrieval"},
        {"name": "b", "tests": "retrieval"},
        {"name": "c"},  # untagged
    ]
    results = [
        {"name": "a", "status": "pass"},
        {"name": "b", "status": "fail"},
        {"name": "c", "status": "pass"},
    ]
    agg = aggregate_by_axis(results, cases)
    assert agg["retrieval"] == {"total": 2, "passed": 1, "failed": 1, "pass_rate": 0.5}
    assert agg["untagged"] == {"total": 1, "passed": 1, "failed": 0, "pass_rate": 1.0}


def test_aggregate_multi_axis_double_counts():
    cases = [{"name": "a", "tests": ["routing", "answer_quality"]}]
    results = [{"name": "a", "status": "pass"}]
    agg = aggregate_by_axis(results, cases)
    assert agg["routing"]["total"] == 1
    assert agg["answer_quality"]["total"] == 1


def test_aggregate_empty_axis_absent():
    agg = aggregate_by_axis([{"name": "a", "status": "pass"}], [{"name": "a"}])
    assert "retrieval" not in agg  # only axes actually present appear
    assert agg["untagged"]["passed"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_eval_diagnostics.py -q`
Expected: FAIL — `ImportError: cannot import name 'aggregate_by_axis'`.

- [ ] **Step 3: Write minimal implementation**

Add to `src/decafclaw/eval/diagnostics.py`:

```python
def aggregate_by_axis(test_results: list[dict], cases: list[dict]) -> dict:
    """Pass-rate per failure-mode axis for the #528 scorecard.

    ``test_results[i]`` pairs with ``cases[i]`` by index. A case's axes come
    from :func:`parse_axes`; an untagged case counts toward ``"untagged"``. A
    multi-axis case counts once toward each of its axes (so summed axis totals
    may exceed the test count). Only axes that actually appear are emitted.
    """
    buckets: dict[str, dict] = {}
    for result, case in zip(test_results, cases):
        axes = parse_axes(case) or ["untagged"]
        passed = result.get("status") == "pass"
        for axis in axes:
            b = buckets.setdefault(
                axis, {"total": 0, "passed": 0, "failed": 0, "pass_rate": 0.0}
            )
            b["total"] += 1
            b["passed"] += 1 if passed else 0
            b["failed"] += 0 if passed else 1
    for b in buckets.values():
        b["pass_rate"] = round(b["passed"] / b["total"], 4) if b["total"] else 0.0
    return buckets
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_eval_diagnostics.py -q`
Expected: PASS (9 passed).

- [ ] **Step 5: Wire into `run_eval` + print the scorecard**

In `src/decafclaw/eval/runner.py`, add to the imports block (top of file, with the other `..` imports):

```python
from .diagnostics import aggregate_by_axis, build_turn_diagnostics
```

(Note: `build_turn_diagnostics` is used in Task 5; importing it now is harmless.)

In `run_eval`, immediately before `results["summary"]["duration_sec"] = round(...)` (~line 900), add:

```python
    # Per-axis failure-mode scorecard (#528). Cases align with results by index.
    results["summary"]["by_axis"] = aggregate_by_axis(results["tests"], yaml_data)
```

Then, after `results["summary"]["duration_sec"] = round(...)` and before `return results, ...`, add the console print:

```python
    by_axis = results["summary"]["by_axis"]
    if by_axis:
        print("\nBy axis (failure-mode scorecard):")
        for axis in sorted(by_axis):
            b = by_axis[axis]
            print(f"  {axis:<22} {b['passed']}/{b['total']}  "
                  f"({b['pass_rate'] * 100:.0f}%)")
```

- [ ] **Step 6: Run full eval-harness unit tests**

Run: `uv run pytest tests/test_eval_diagnostics.py tests/test_eval_setup_overrides.py -q`
Expected: PASS.

- [ ] **Step 7: Update docs**

In `docs/eval-loop.md`, add a section "Axis tagging & the failure-mode scorecard" documenting: the top-level `tests:` key (string or list), the four canonical axes, the `untagged` bucket, unknown → error, that `results.json` `summary.by_axis` carries per-axis pass rates and `make eval` prints the scorecard, and that multi-axis cases double-count. Note the deferred per-axis history trend as a follow-up.

- [ ] **Step 8: Commit**

```bash
git add src/decafclaw/eval/diagnostics.py tests/test_eval_diagnostics.py \
        src/decafclaw/eval/runner.py docs/eval-loop.md
git commit -m "feat(eval): aggregate pass-rate by failure-mode axis (#528)"
```

---

## Task 3: `detect_files_read` + `detect_files_cited`

**Files:**
- Modify: `src/decafclaw/eval/diagnostics.py`
- Test: `tests/test_eval_diagnostics.py`

**Interfaces:**
- Produces:
  - `detect_files_read(tool_calls: list[tuple[str, dict]]) -> list[str]` — from `(name, args)` tuples (as returned by `runner._collect_tool_calls`), extract the file/page identifier for each read-shaped call per `READ_TOOL_ARGS`. Order-preserving, deduped, drops empty.
  - `detect_files_cited(response: str, known_paths: list[str]) -> list[str]` — heuristic: any `known_path` whose full path, basename, or stem (case-insensitive) appears as a substring of `response`, PLUS every `[[PageName]]` wiki-mention in `response`. Order: matched known_paths (input order) then wiki-mentions not already present. Deduped.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_eval_diagnostics.py
from decafclaw.eval.diagnostics import detect_files_cited, detect_files_read


def test_detect_files_read_maps_args():
    calls = [
        ("vault_read", {"page": "agent/pages/DecafClaw"}),
        ("workspace_read", {"path": "notes/todo.md"}),
        ("conversation_search", {"query": "x"}),  # not a read tool
        ("vault_read", {"page": "agent/pages/DecafClaw"}),  # dup
    ]
    assert detect_files_read(calls) == ["agent/pages/DecafClaw", "notes/todo.md"]


def test_detect_files_read_drops_empty():
    assert detect_files_read([("vault_read", {})]) == []


def test_detect_files_cited_path_and_wiki():
    resp = "See [[Escalation Runbook]]. I read the escalation-runbook page."
    known = ["agent/pages/escalation-runbook.md", "agent/pages/oncall-rotation.md"]
    cited = detect_files_cited(resp, known)
    assert "agent/pages/escalation-runbook.md" in cited  # stem 'escalation-runbook' matched
    assert "Escalation Runbook" in cited                 # wiki-mention
    assert "agent/pages/oncall-rotation.md" not in cited  # never mentioned


def test_detect_files_cited_no_false_positive_on_unrelated():
    resp = "I don't have anything on that topic."
    assert detect_files_cited(resp, ["agent/pages/escalation-runbook.md"]) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_eval_diagnostics.py -q`
Expected: FAIL — `ImportError: cannot import name 'detect_files_read'`.

- [ ] **Step 3: Write minimal implementation**

Add to `src/decafclaw/eval/diagnostics.py`:

```python
import re
from pathlib import PurePosixPath

_WIKI_RE = re.compile(r"\[\[([^\]]+)\]\]")


def detect_files_read(tool_calls: list[tuple[str, dict]]) -> list[str]:
    """File/page identifiers read this turn, from read-shaped tool calls.

    ``tool_calls`` is the ``(name, parsed_args)`` list from
    ``runner._collect_tool_calls``. Order-preserving, deduped, empties dropped.
    """
    out: list[str] = []
    for name, args in tool_calls:
        arg_key = READ_TOOL_ARGS.get(name)
        if not arg_key:
            continue
        value = args.get(arg_key)
        if value and value not in out:
            out.append(value)
    return out


def detect_files_cited(response: str, known_paths: list[str]) -> list[str]:
    """Heuristic: which known files/pages the final response cites.

    A ``known_path`` counts as cited if its full path, basename, or stem
    (case-insensitive) is a substring of ``response``. Additionally, every
    ``[[PageName]]`` wiki-mention in the response is included. Documented as a
    heuristic — substring matching can over- or under-count (#531).
    """
    lower = response.lower()
    cited: list[str] = []
    for path in known_paths:
        p = PurePosixPath(path)
        needles = {path.lower(), p.name.lower(), p.stem.lower()}
        if any(n and n in lower for n in needles):
            if path not in cited:
                cited.append(path)
    for m in _WIKI_RE.findall(response):
        name = m.strip()
        if name and name not in cited:
            cited.append(name)
    return cited
```

(Move the `import re` / `from pathlib import ...` to the top of the module with the other imports.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_eval_diagnostics.py -q`
Expected: PASS (13 passed).

- [ ] **Step 5: Commit**

```bash
git add src/decafclaw/eval/diagnostics.py tests/test_eval_diagnostics.py
git commit -m "feat(eval): file-read + file-cited detection for diagnostics (#531)"
```

---

## Task 4: `build_turn_diagnostics`

**Files:**
- Modify: `src/decafclaw/eval/diagnostics.py`
- Test: `tests/test_eval_diagnostics.py`

**Interfaces:**
- Consumes: `detect_files_read`, `detect_files_cited` (Task 3).
- Produces: `build_turn_diagnostics(sidecar: dict | None, tool_calls: list[tuple[str, dict]], response: str) -> dict` — merges sidecar-sourced fields (tokens by section, active/deferred tool counts, retrieved candidates, totals) with derived fields (files read, files cited, tool-call names+count). `sidecar=None` degrades to derived-only fields (sidecar fields default: `tokens_by_section={}`, `active_tools`/`deferred_tools`/totals `None`, `retrieved_candidates=[]`). The returned dict is the `diagnostics` block attached to each eval turn.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_eval_diagnostics.py
from decafclaw.eval.diagnostics import build_turn_diagnostics


def _sidecar():
    return {
        "total_tokens_estimated": 12000,
        "total_tokens_actual": 11800,
        "context_window_size": 1000000,
        "compaction_threshold": 150000,
        "sources": [
            {"source": "system", "tokens_estimated": 3000, "items_included": 1,
             "items_truncated": 0, "details": {}},
            {"source": "tools", "tokens_estimated": 2000, "items_included": 8,
             "items_truncated": 40, "details": {}},
            {"source": "retrieved_context", "tokens_estimated": 1500,
             "items_included": 3, "items_truncated": 0, "details": {}},
        ],
        "memory_candidates": [
            {"file_path": "agent/pages/escalation-runbook.md",
             "composite_score": 0.82, "similarity": 0.79, "recency": 0.5,
             "importance": 0.9},
        ],
    }


def test_build_turn_diagnostics_full():
    calls = [("vault_read", {"page": "agent/pages/escalation-runbook.md"})]
    resp = "Per the escalation-runbook, Priya is paged first."
    d = build_turn_diagnostics(_sidecar(), calls, resp)
    assert d["tokens_by_section"] == {"system": 3000, "tools": 2000,
                                      "retrieved_context": 1500}
    assert d["active_tools"] == 8
    assert d["deferred_tools"] == 40
    assert d["total_tokens_estimated"] == 12000
    assert d["files_read"] == ["agent/pages/escalation-runbook.md"]
    assert "agent/pages/escalation-runbook.md" in d["files_cited"]
    assert d["tool_calls"] == {"names": ["vault_read"], "count": 1}
    assert d["retrieved_candidates"][0]["file_path"] == "agent/pages/escalation-runbook.md"


def test_build_turn_diagnostics_none_sidecar_degrades():
    calls = [("workspace_read", {"path": "notes/x.md"})]
    d = build_turn_diagnostics(None, calls, "read notes/x.md")
    assert d["tokens_by_section"] == {}
    assert d["active_tools"] is None
    assert d["deferred_tools"] is None
    assert d["total_tokens_estimated"] is None
    assert d["retrieved_candidates"] == []
    assert d["files_read"] == ["notes/x.md"]
    assert d["tool_calls"] == {"names": ["workspace_read"], "count": 1}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_eval_diagnostics.py -q`
Expected: FAIL — `ImportError: cannot import name 'build_turn_diagnostics'`.

- [ ] **Step 3: Write minimal implementation**

Add to `src/decafclaw/eval/diagnostics.py`:

```python
def build_turn_diagnostics(
    sidecar: dict | None,
    tool_calls: list[tuple[str, dict]],
    response: str,
) -> dict:
    """Assemble the per-turn ``diagnostics`` block for an eval result (#531).

    Sidecar-sourced fields (token split, tool counts, retrieved candidates,
    totals) reuse the context sidecar written on turn-exit — no recompute.
    Derived fields (files read/cited, tool-call names+count) come from the
    turn's history slice. A ``None`` sidecar (missing file) degrades to the
    derived fields only; never raises.
    """
    sidecar = sidecar or {}
    sources = sidecar.get("sources") or []
    tokens_by_section = {s.get("source", ""): s.get("tokens_estimated", 0)
                         for s in sources}
    tools_src = next((s for s in sources if s.get("source") == "tools"), None)

    candidates = [
        {
            "file_path": c.get("file_path", ""),
            "composite_score": c.get("composite_score"),
            "similarity": c.get("similarity"),
            "recency": c.get("recency"),
            "importance": c.get("importance"),
        }
        for c in (sidecar.get("memory_candidates") or [])
    ]

    files_read = detect_files_read(tool_calls)
    candidate_paths = [c["file_path"] for c in candidates if c["file_path"]]
    files_cited = detect_files_cited(response, files_read + candidate_paths)

    return {
        "tokens_by_section": tokens_by_section,
        "total_tokens_estimated": sidecar.get("total_tokens_estimated"),
        "total_tokens_actual": sidecar.get("total_tokens_actual"),
        "context_window_size": sidecar.get("context_window_size"),
        "compaction_threshold": sidecar.get("compaction_threshold"),
        "active_tools": tools_src.get("items_included") if tools_src else None,
        "deferred_tools": tools_src.get("items_truncated") if tools_src else None,
        "retrieved_candidates": candidates,
        "files_read": files_read,
        "files_cited": files_cited,
        "tool_calls": {"names": [n for n, _ in tool_calls], "count": len(tool_calls)},
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_eval_diagnostics.py -q`
Expected: PASS (15 passed).

- [ ] **Step 5: Commit**

```bash
git add src/decafclaw/eval/diagnostics.py tests/test_eval_diagnostics.py
git commit -m "feat(eval): assemble per-turn diagnostics block from sidecar + derived (#531)"
```

---

## Task 5: wire diagnostics into `run_test` + `--verbose` console + docs

**Files:**
- Modify: `src/decafclaw/eval/runner.py` (`run_test` ~lines 736-807; `run_eval` verbose block ~line 887)
- Modify: `docs/eval-loop.md`
- Test: `tests/test_eval_diagnostics.py`

**Interfaces:**
- Consumes: `build_turn_diagnostics` (Task 4); `read_context_sidecar` from `context_composer`; `_collect_tool_calls` (existing in runner).
- Produces: each entry in `result["turns"]` (and each single-turn `all_responses` entry) gains a `"diagnostics"` key; `run_test`'s top-level `result` gains a `"diagnostics"` key = the last turn's block. `--verbose` prints a compact per-test diagnostics summary.

- [ ] **Step 1: Write the failing test** (wiring test with monkeypatched turn + sidecar)

```python
# append to tests/test_eval_diagnostics.py
import json as _json

import pytest as _pytest

from decafclaw.config import Config
from decafclaw.eval import runner as eval_runner
from decafclaw.eval.runner import _build_test_config, run_test


class _FakeResult:
    def __init__(self, text):
        self.text = text


@_pytest.mark.asyncio
async def test_run_test_attaches_diagnostics(tmp_path, monkeypatch):
    # Fake the LLM turn: append a synthetic vault_read call to history, no model.
    async def _fake_turn(ctx, turn_input, history):
        history.append({
            "role": "assistant", "content": "",
            "tool_calls": [{"id": "c0", "function": {
                "name": "vault_read",
                "arguments": _json.dumps({"page": "agent/pages/foo"})}}],
        })
        history.append({"role": "tool", "tool_call_id": "c0", "content": "ok"})
        return _FakeResult("I read agent/pages/foo and here is the answer.")

    monkeypatch.setattr(eval_runner, "run_agent_turn", _fake_turn)
    monkeypatch.setattr(
        eval_runner, "read_context_sidecar",
        lambda config, conv_id: {
            "total_tokens_estimated": 100, "sources": [
                {"source": "tools", "tokens_estimated": 10,
                 "items_included": 5, "items_truncated": 20, "details": {}}],
            "memory_candidates": []},
    )

    cfg = _build_test_config(Config(), {"setup": {}}, str(tmp_path))
    result = await run_test(cfg, {"name": "t", "input": "hi", "expect": {}})

    diag = result["diagnostics"]
    assert diag["files_read"] == ["agent/pages/foo"]
    assert "agent/pages/foo" in diag["files_cited"]
    assert diag["active_tools"] == 5
    assert diag["deferred_tools"] == 20
    assert diag["tool_calls"]["count"] == 1
```

Note: `pyproject.toml` sets `asyncio_mode = "auto"`, so the `@pytest.mark.asyncio` marker is optional (harmless to keep — existing tests keep it). No `pytest-asyncio` event-loop fixture needed.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_eval_diagnostics.py::test_run_test_attaches_diagnostics -q`
Expected: FAIL — `KeyError: 'diagnostics'` (or AttributeError on `read_context_sidecar` if not yet imported into runner).

- [ ] **Step 3: Implement the wiring**

In `src/decafclaw/eval/runner.py` imports, add:

```python
from ..context_composer import read_context_sidecar
```

In `run_test`, inside the turn loop, replace the `all_responses.append({...})` block that follows `tool_calls = _count_tool_calls(history) - pre_turn_tool_calls` (~line 743) so it also builds and attaches diagnostics:

```python
        # Per-turn counts (delta from pre-turn snapshot)
        tool_calls = _count_tool_calls(history) - pre_turn_tool_calls
        turn_slice = history[pre_turn_history_len:]
        sidecar = read_context_sidecar(config, ctx.conv_id)
        diagnostics = build_turn_diagnostics(
            sidecar, _collect_tool_calls(turn_slice), response,
        )
        all_responses.append({
            "turn": turn_idx + 1,
            "input": turn["input"],
            "response": response,
            "duration_sec": round(duration, 1),
            "tool_calls": tool_calls,
            "diagnostics": diagnostics,
        })
```

(The existing assertion block below recomputes `turn_slice`/`tool_names`; leave it — it's cheap and keeps the diff local. Do not remove the later `turn_slice` assignment.)

Then, where the top-level `result` dict is built (~line 790), add the top-level diagnostics after it:

```python
    result = {
        "name": test_case["name"],
        "status": "pass" if overall_passed else "fail",
        "duration_sec": round(total_duration, 1),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "tool_calls": total_tool_calls,
        "response": final_response,
        "failure_reason": failure_reason,
    }
    if all_responses:
        result["diagnostics"] = all_responses[-1]["diagnostics"]
```

- [ ] **Step 4: Add the `--verbose` console summary**

In `run_eval`, in the `if verbose and result.get("response"):` block (~line 887), extend it to also print diagnostics:

```python
        if verbose and result.get("response"):
            print(f"         Response: {result['response'][:200]}")
            diag = result.get("diagnostics")
            if diag:
                tbs = diag.get("tokens_by_section") or {}
                tok = "  ".join(f"{k}={v}" for k, v in tbs.items())
                print(f"         Tokens: {tok}"
                      f"  (active={diag.get('active_tools')}, "
                      f"deferred={diag.get('deferred_tools')})")
                cands = diag.get("retrieved_candidates") or []
                top = ", ".join(f"{c['file_path']}:{c.get('composite_score')}"
                                for c in cands[:3])
                if top:
                    print(f"         Candidates: {top}")
                print(f"         Read: {diag.get('files_read')}  "
                      f"Cited: {diag.get('files_cited')}  "
                      f"Tools: {diag.get('tool_calls', {}).get('names')}")
```

- [ ] **Step 5: Run the wiring test + full eval unit suite**

Run: `uv run pytest tests/test_eval_diagnostics.py -q`
Expected: PASS (16 passed).

- [ ] **Step 6: Update docs**

In `docs/eval-loop.md`, document the per-turn `diagnostics` block: its shape (list the keys), that it reuses the context sidecar (link `docs/context-composer.md#diagnostics-sidecar`), the four diagnosis modes (retrieval/routing/answer/bloat) it enables, the `files_cited` heuristic (substring + `[[wiki]]`), the absence of per-tool durations (deferred), and the `--verbose` summary.

- [ ] **Step 7: Commit**

```bash
git add src/decafclaw/eval/runner.py docs/eval-loop.md tests/test_eval_diagnostics.py
git commit -m "feat(eval): attach per-turn context diagnostics to result bundles (#531)"
```

---

## Tasks 6–12: behavioral eval suites

**Shared authoring notes (apply to every suite task):**
- Each case gets a top-level `tests:` axis tag (per the table below) plus `name`, `input`/`turns`, `expect`.
- Bound every case with `max_tool_calls` + `max_tool_errors: 0` (raise the error bound only with a comment if a case legitimately expects a tool error).
- Any `expect_no_tool` / tight-`max_tool_calls` case adds `setup.config_overrides: {reflection.enabled: false}`.
- Seed vault pages via `setup.workspace_files` under `vault/agent/pages/<name>.md` (optionally with frontmatter, mirroring `evals/retrieval-quality.yaml`); seed prior turns via `setup.conversation_history`; seed journal via `setup.memories`.
- **Validation is the real test:** run `uv run python -m decafclaw.eval evals/<suite>.yaml --verbose` against the configured model. Tune assertions until the suite passes reliably (the `--verbose` diagnostics now show you *why* a case fails — retrieval vs routing vs answer). A case that can't be made to pass honestly gets dropped with a note in `notes.md`, not a weakened assertion.
- Target 4–5 cases per suite (≈30 total). If validation cost bites, ship a suite with a documented minimum of 2 and note the shortfall.
- Commit each suite in its own commit: `test(eval): <suite> behavioral suite (#528)`.

| Task | Suite file | `tests:` axis | Adversarial focus |
|---|---|---|---|
| 6 | `vault_answering.yaml` | `retrieval` | right page vs similar-but-wrong distractor; admit absence |
| 7 | `tool_routing.yaml` | `routing` | vault vs workspace vs conversation_search |
| 8 | `source_grounding.yaml` | `answer_quality` | cite the file or hedge; no fabrication |
| 9 | `context_pressure.yaml` | `answer_quality` | answer survives noisy history + big tool output |
| 10 | `clarification.yaml` | `workflow_discipline` | ask one Q only when ambiguity matters |
| 11 | `abort_recovery.yaml` | `workflow_discipline` | no stale-intent resurrection after cancel/error |
| 12 | `over_ceremony.yaml` | `workflow_discipline` | simple ask → no checklist/project escalation |

### Task 6: `vault_answering.yaml` (retrieval)

**Files:** Create `evals/vault_answering.yaml`.

- [ ] **Step 1: Author the suite.** Starter cases (tune during validation):

```yaml
# Behavioral suite — RETRIEVAL axis (#528). Adversarial: a similar-but-wrong
# distractor page sits next to the right one; a third case has NO matching page
# so the agent must admit absence rather than answer from the distractor.

- name: "retrieves the right page past a similar-but-wrong distractor"
  tests: retrieval
  setup:
    workspace_files:
      "vault/agent/pages/staging-deploy.md": |
        # Staging Deploy
        Staging deploys run automatically on every merge to `develop`.
      "vault/agent/pages/production-deploy.md": |
        # Production Deploy
        Production deploys are manual: run `./scripts/deploy.sh prod` after a
        release tag is cut. Requires the on-call lead's approval.
  input: "How do we deploy to production?"
  expect:
    response_contains_all: ["deploy.sh", "approval"]
    response_contains: "manual"
    max_tool_calls: 4
    max_tool_errors: 0

- name: "admits absence when no vault page covers the question"
  tests: retrieval
  setup:
    config_overrides: {reflection.enabled: false}
    workspace_files:
      "vault/agent/pages/staging-deploy.md": |
        # Staging Deploy
        Staging deploys run automatically on every merge to `develop`.
  input: "What's our data-retention policy for customer PII?"
  expect:
    # Must NOT fabricate a policy; hedge/deny instead. A LIST on
    # `response_contains` is ANY-match (see runner.py:364) — there is no
    # separate `response_contains_any` assertion.
    response_contains: ["don't have", "not find", "nothing", "couldn't find", "no information"]
    max_tool_calls: 4
    max_tool_errors: 0
```

Add 2–3 more retrieval cases in the same shape (e.g. distractor filenames that share a stem; a page reachable only by semantic match). **Assertion note:** `response_contains` accepts a string, a list (ANY-match), or a `re:`-prefixed regex; `response_contains_all` is AND-match. There is **no** `response_contains_any` — use a list on `response_contains`.

- [ ] **Step 2: Validate with a real run.**

Run: `uv run python -m decafclaw.eval evals/vault_answering.yaml --verbose`
Expected: all cases PASS; the by-axis scorecard shows `retrieval N/N (100%)`. Tune until reliable.

- [ ] **Step 3: Commit.**

```bash
git add evals/vault_answering.yaml
git commit -m "test(eval): vault_answering behavioral suite (#528)"
```

### Task 7: `tool_routing.yaml` (routing)

**Files:** Create `evals/tool_routing.yaml`.

- [ ] **Step 1: Author the suite.** Each case seeds data reachable by exactly ONE surface and asserts the right tool via `expect_tool` + `expect_no_tool`. Starter:

```yaml
# Behavioral suite — ROUTING axis (#528). Each case makes exactly one surface
# the correct source; asserts the agent routes there and not to look-alikes.

- name: "routes to conversation_search for 'what did I say' history queries"
  tests: routing
  setup:
    config_overrides: {reflection.enabled: false}
    conversation_history:
      - role: user
        content: "Remember: our staging DB password rotates every 30 days."
      - role: assistant
        content: "Noted — staging DB password rotates every 30 days."
  input: "What did I tell you earlier about the staging DB password rotation?"
  expect:
    expect_tool: conversation_search
    expect_no_tool: vault_search
    response_contains: "30 days"
    max_tool_calls: 4
    max_tool_errors: 0

- name: "routes to workspace_read for a concrete workspace file path"
  tests: routing
  setup:
    config_overrides: {reflection.enabled: false}
    workspace_files:
      "notes/release-checklist.md": |
        # Release Checklist
        1. Bump version. 2. Tag. 3. Run deploy.sh.
  input: "Read notes/release-checklist.md and tell me step 3."
  expect:
    expect_tool: workspace_read
    response_contains: "deploy.sh"
    max_tool_calls: 4
    max_tool_errors: 0
```

Add 2–3 more (e.g. a vault-knowledge question → `vault_search`/`vault_read`, not `workspace_read`).

- [ ] **Step 2: Validate.** `uv run python -m decafclaw.eval evals/tool_routing.yaml --verbose` → all PASS, `routing N/N`.
- [ ] **Step 3: Commit.** `test(eval): tool_routing behavioral suite (#528)`

### Task 8: `source_grounding.yaml` (answer_quality)

**Files:** Create `evals/source_grounding.yaml`.

- [ ] **Step 1: Author.** Cases where the right answer is present in a seeded source and a plausible-but-absent detail is asked; assert the answer cites the seeded fact and does NOT fabricate the absent detail. Starter:

```yaml
# Behavioral suite — ANSWER_QUALITY axis (#528). Answers must ground in a
# seeded source or hedge; no fabricated specifics.

- name: "grounds the answer in the seeded page, no invented figures"
  tests: answer_quality
  setup:
    workspace_files:
      "vault/agent/pages/sla.md": |
        # Support SLA
        P1 incidents: first response within 30 minutes. P2: within 4 hours.
  input: "What's our first-response SLA for P1 incidents?"
  expect:
    response_contains: "30 minutes"
    max_tool_calls: 4
    max_tool_errors: 0

- name: "hedges instead of fabricating an unseeded SLA tier"
  tests: answer_quality
  setup:
    config_overrides: {reflection.enabled: false}
    workspace_files:
      "vault/agent/pages/sla.md": |
        # Support SLA
        P1 incidents: first response within 30 minutes. P2: within 4 hours.
  input: "What's our first-response SLA for P3 incidents?"
  expect:
    # P3 is not in the page — must not invent a number. List = ANY-match.
    response_contains: ["don't", "not ", "isn't", "doesn't", "no P3"]
    max_tool_calls: 4
    max_tool_errors: 0
```

Add 2–3 more.

- [ ] **Step 2: Validate.** `uv run python -m decafclaw.eval evals/source_grounding.yaml --verbose` → all PASS.
- [ ] **Step 3: Commit.** `test(eval): source_grounding behavioral suite (#528)`

### Task 9: `context_pressure.yaml` (answer_quality)

**Files:** Create `evals/context_pressure.yaml`.

- [ ] **Step 1: Author.** Seed a long, noisy `conversation_history` with one buried load-bearing fact; ask about that fact; assert it's recovered. Starter:

```yaml
# Behavioral suite — ANSWER_QUALITY under CONTEXT PRESSURE (#528). A single
# load-bearing fact is buried in noisy history; the agent must still surface it.

- name: "recovers a buried fact from noisy history"
  tests: answer_quality
  setup:
    conversation_history:
      - {role: user, content: "Random chit-chat about the weather."}
      - {role: assistant, content: "It does look like rain."}
      - {role: user, content: "By the way the prod API key lives in Vault path secret/prod/api."}
      - {role: assistant, content: "Got it — secret/prod/api."}
      - {role: user, content: "Anyway, back to the weekend plans."}
      - {role: assistant, content: "Sounds fun."}
  input: "Where does the prod API key live again?"
  expect:
    response_contains: "secret/prod/api"
    max_tool_calls: 5
    max_tool_errors: 0
```

Add 2–3 more (bigger history; a large seeded workspace file the agent must read past).

- [ ] **Step 2: Validate.** `uv run python -m decafclaw.eval evals/context_pressure.yaml --verbose` → all PASS.
- [ ] **Step 3: Commit.** `test(eval): context_pressure behavioral suite (#528)`

### Task 10: `clarification.yaml` (workflow_discipline)

**Files:** Create `evals/clarification.yaml`.

- [ ] **Step 1: Author.** One case where the ask is genuinely ambiguous (agent SHOULD ask exactly one clarifying question, take no action tools) and one where it's clear (agent should proceed, not stall on a question). Starter:

```yaml
# Behavioral suite — WORKFLOW_DISCIPLINE / clarification (#528). Ask one useful
# question only when ambiguity actually blocks progress; otherwise proceed.

- name: "asks one clarifying question on a genuinely ambiguous ask"
  tests: workflow_discipline
  setup:
    config_overrides: {reflection.enabled: false}
  input: "Delete it."
  expect:
    response_contains: "?"
    expect_no_tool: workspace_delete
    max_tool_calls: 1
    max_tool_errors: 0

- name: "proceeds without over-asking on a clear request"
  tests: workflow_discipline
  setup:
    config_overrides: {reflection.enabled: false}
    workspace_files:
      "notes/scratch.md": "throwaway"
  input: "Read notes/scratch.md and tell me what it says."
  expect:
    expect_tool: workspace_read
    response_contains: "throwaway"
    max_tool_calls: 3
    max_tool_errors: 0
```

(`workspace_delete` is the confirmed delete-tool name.) Add 1–2 more.

- [ ] **Step 2: Validate.** `uv run python -m decafclaw.eval evals/clarification.yaml --verbose` → all PASS.
- [ ] **Step 3: Commit.** `test(eval): clarification behavioral suite (#528)`

### Task 11: `abort_recovery.yaml` (workflow_discipline)

**Files:** Create `evals/abort_recovery.yaml`.

- [ ] **Step 1: Author.** Seed a `conversation_history` that ends in a cancelled turn (a `cancel_marker` row carrying the **exact** production `CANCEL_MARKER_TEXT`), then send an unrelated new user message; assert the agent does the NEW thing and does not resurrect the cancelled intent. Starter:

```yaml
# Behavioral suite — WORKFLOW_DISCIPLINE / abort-recovery (#528, builds on #517).
# After a cancelled turn, the next message must not re-fulfill the abandoned intent.
# The cancel_marker content is the verbatim CANCEL_MARKER_TEXT from
# conversation_manager.py:37 — the exact "turn closed" signal the LLM sees.

- name: "does not resurrect a cancelled intent"
  tests: workflow_discipline
  setup:
    config_overrides: {reflection.enabled: false}
    conversation_history:
      - {role: user, content: "Search the web for the cheapest flights to Tokyo in March."}
      - {role: assistant, content: "Searching for flights to Tokyo..."}
      - {role: cancel_marker, content: "[User cancelled this turn. Do not retry the cancelled request unless they explicitly ask for it again.]"}
  input: "What's 17 times 23?"
  expect:
    # Primary signal: it answers the NEW question. The web tools (tabstack_*,
    # web_fetch) live behind the tabstack skill and are NOT loaded here, so an
    # `expect_no_tool` on them is weak defense-in-depth at best — the real proof
    # is the correct arithmetic answer plus a tight call budget (no flailing).
    response_contains: "391"
    max_tool_calls: 2
    max_tool_errors: 0
```

Facts (verified): `CANCEL_MARKER_TEXT` is exactly
`"[User cancelled this turn. Do not retry the cancelled request unless they explicitly ask for it again.]"`.
Web tools are the deferred tabstack-skill tools (`tabstack_research`, `web_fetch`, …), not a `web_search` tool — don't assert `expect_no_tool: web_search` (phantom name → trivially passes). Add 1–2 more cases: an **exception-marker** variant (grep `conversation_manager.py` for the #517 error-abort marker text and seed a row with that content), and a cancelled multi-step task followed by a small unrelated ask.

- [ ] **Step 2: Validate.** `uv run python -m decafclaw.eval evals/abort_recovery.yaml --verbose` → all PASS.
- [ ] **Step 3: Commit.** `test(eval): abort_recovery behavioral suite (#528)`

### Task 12: `over_ceremony.yaml` (workflow_discipline)

**Files:** Create `evals/over_ceremony.yaml`.

- [ ] **Step 1: Author.** A trivial one-shot ask that should NOT trigger checklist/project escalation. Starter:

```yaml
# Behavioral suite — WORKFLOW_DISCIPLINE / over-ceremony (#528). A simple ask
# must not spin up a checklist or project structure.

- name: "simple ask does not create a checklist"
  tests: workflow_discipline
  setup:
    config_overrides: {reflection.enabled: false}
  input: "What's the capital of France?"
  expect:
    response_contains: "Paris"
    expect_no_tool: checklist_create
    max_tool_calls: 1
    max_tool_errors: 0

- name: "small two-step ask stays inline, no project escalation"
  tests: workflow_discipline
  setup:
    config_overrides: {reflection.enabled: false}
    workspace_files:
      "notes/a.md": "alpha"
      "notes/b.md": "beta"
  input: "Read notes/a.md and notes/b.md and tell me both contents."
  expect:
    response_contains_all: ["alpha", "beta"]
    expect_no_tool: checklist_create
    max_tool_calls: 4
    max_tool_errors: 0
```

Add 1–2 more. Confirm the checklist/project escalation tool names to assert `expect_no_tool` against (`checklist_create`, and the project skill's activation via `activate_skill` — consider `expect_no_tool: activate_skill` where appropriate).

- [ ] **Step 2: Validate.** `uv run python -m decafclaw.eval evals/over_ceremony.yaml --verbose` → all PASS.
- [ ] **Step 3: Commit.** `test(eval): over_ceremony behavioral suite (#528)`

---

## Task 13: docs consolidation, full-suite verification, retro

**Files:**
- Modify: `docs/eval-loop.md` (final pass), `docs/index.md` (if a new doc section warrants a link — likely not)
- Create: `docs/dev-sessions/2026-07-24-1618-eval-behavioral-diagnostics/notes.md`

- [ ] **Step 1: Docs accuracy pass.** Re-read the axis-tagging and diagnostics sections of `docs/eval-loop.md` end-to-end; ensure the `tests:` key, canonical axes, `by_axis` shape, scorecard output, diagnostics block keys, `files_cited` heuristic, and deferred items (per-tool durations, per-axis history) are all documented and internally consistent. Cross-link `docs/context-composer.md#diagnostics-sidecar`.

- [ ] **Step 2: Full deterministic suite green.**

Run: `uv run make check && uv run make test`
Expected: `make check` clean; `make test` all pass, zero warnings, no new `--durations` outliers (`uv run pytest --durations=25` if anything looks slow).

- [ ] **Step 3: Full behavioral eval sanity run + scorecard.**

Run: `uv run python -m decafclaw.eval evals/vault_answering.yaml evals/tool_routing.yaml evals/source_grounding.yaml evals/context_pressure.yaml evals/clarification.yaml evals/abort_recovery.yaml evals/over_ceremony.yaml`
Expected: the run prints the by-axis scorecard; capture the pass rates into `notes.md`. Confirm `evals/results/<ts>/results.json` contains `summary.by_axis` and per-turn `diagnostics` blocks.

- [ ] **Step 4: Write `notes.md`.** Session summary: what shipped, final case counts per suite (and any suite that fell short of the 4–5 target with why), the scorecard snapshot, deferred follow-ups (per-tool durations, per-axis history trend, retagging existing suites), and any assertion that had to be loosened during tuning (with justification — no silently weakened assertions).

- [ ] **Step 5: Commit.**

```bash
git add docs/eval-loop.md docs/dev-sessions/2026-07-24-1618-eval-behavioral-diagnostics/notes.md
git commit -m "docs(eval): behavioral suites + diagnostics — docs + session notes (#528, #531)"
```

---

## Self-Review

**Spec coverage:**
- A1 axis metadata → Task 1 (`parse_axes`, strict validation). ✓
- A2 seven suites → Tasks 6–12, each axis-tagged per the mapping table. ✓
- A3 aggregate-by-axis + scorecard → Task 2. ✓
- B1 diagnostics block (sidecar-sourced + derived) → Tasks 3–5. ✓
- B2 `--verbose` console → Task 5 Step 4. ✓
- Reuse sidecar, no duplication → Task 4/5 read `read_context_sidecar`, never re-run `build_diagnostics`. ✓
- Unit tests for deterministic helpers → Tasks 1–5. ✓
- Docs same PR → Tasks 2, 5, 13. ✓
- Deferred (durations, per-axis history, retagging) → documented in Task 13 notes + docs. ✓

**Placeholder scan:** suite tasks intentionally carry starter YAML + a real-run validate-and-tune step rather than pre-baked passing assertions, because LLM pass/fail can't be predetermined; this is honest, not a placeholder. All code steps show complete code. No "TBD"/"handle edge cases".

**Type consistency:** `build_turn_diagnostics(sidecar, tool_calls, response)` signature is identical across Task 4 (def), Task 5 (call). `aggregate_by_axis(test_results, cases)` identical across Task 2 def + wiring. `detect_files_read`/`detect_files_cited` signatures match Task 3 → Task 4 usage. `parse_axes(case)` consistent Task 1 → Task 2.

**Verified facts baked in:** `asyncio_mode = "auto"`; `workspace_delete` (delete tool); `checklist_create` (checklist tool); web tools are deferred tabstack-skill tools (`tabstack_research`/`web_fetch`), no `web_search`; `CANCEL_MARKER_TEXT` verbatim; `response_contains` list = ANY-match (no `response_contains_any`). **Still confirm at authoring time:** the project-skill escalation surface to assert `expect_no_tool` against in `over_ceremony` (likely `activate_skill`), and the #517 exception-abort marker text for the `abort_recovery` second case.
