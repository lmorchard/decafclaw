# Workflow batch primitives Implementation Plan

**Goal:** Add four journaled fan-out primitives (`tool_call`, `subagent`, `parallel`, `pipeline`) to the workflow replay engine, with a `/research` hero workflow exercising all four and a mid-run-restart live smoke.

**Approach:** Foundation refactor (tuple-path journal keys + sub-handles) before any new primitive. Then build up from simplest (`tool_call` — no sub-handle needed) to most complex (`pipeline` — sub-handles + stages). Hero workflow last. TDD per phase: failing live + replay test before implementation.

**Tech stack:** Python 3.12, asyncio, pytest-xdist. New code in `src/decafclaw/workflow/`. Tests in `tests/test_workflow_*.py`.

---

## Phase 1: Tuple-path journal keys

Foundation: change `Journal` keying from integer `seq` to tuple-path `seq`. Existing primitives keep working (their seqs become 1-tuples). Existing on-disk journals upgrade transparently. No new primitives in this phase.

**Files:**
- Modify: `src/decafclaw/workflow/journal.py` — change `JournalEntry.seq: tuple[int, ...]`, `Journal.entries: dict[tuple, JournalEntry]`, `Journal.get/append`, `to_dict/from_dict` with dotted-string path serialization and int→1-tuple upgrade.
- Modify: `src/decafclaw/workflow/errors.py` — `WorkflowSuspended.seq: tuple[int, ...]`, `WorkflowNonDeterministic` if it stores seq.
- Modify: `src/decafclaw/workflow/handle.py` — `_check_or_none` takes tuple; `llm_call`/`user_input` build `seq = (self._cursor,)`.
- Modify: `src/decafclaw/workflow/resume.py` — store/load tuple seq via dotted string in `action_data`; pass tuple to `journal.append`.
- Test: `tests/test_workflow_journal.py` — extend with tuple-path round-trip, dotted-string JSON, int→1-tuple upgrade. Existing tests adapt to tuple keys.
- Test: `tests/test_workflow_handle.py` — adapt existing tests to tuple-path seqs.
- Test: `tests/test_workflow_resume.py` — adapt to tuple-path seq in action_data.

**Key changes:**

```python
# journal.py
@dataclasses.dataclass
class JournalEntry:
    seq: tuple[int, ...]
    kind: str
    args_fingerprint: str
    result: Any

@dataclasses.dataclass
class Journal:
    workflow_name: str
    status: str = "running"
    entries: dict[tuple[int, ...], JournalEntry] = dataclasses.field(default_factory=dict)

    def get(self, seq: tuple[int, ...]) -> JournalEntry | None:
        return self.entries.get(seq)

    def append(self, seq: tuple[int, ...], kind: str,
               args_fingerprint: str, result: Any) -> None:
        if seq in self.entries:
            raise ValueError(f"duplicate journal append at seq={seq}")
        self.entries[seq] = JournalEntry(seq, kind, args_fingerprint, result)

    def to_dict(self) -> dict:
        return {
            "workflow_name": self.workflow_name,
            "status": self.status,
            "entries": [
                {"seq": _path_to_str(e.seq), "kind": e.kind,
                 "args_fingerprint": e.args_fingerprint, "result": e.result}
                for e in self.entries.values()
            ],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Journal":
        j = cls(workflow_name=d["workflow_name"],
                status=d.get("status", "running"))
        for entry_d in d.get("entries", []):
            seq = _path_from_any(entry_d["seq"])
            j.entries[seq] = JournalEntry(seq, entry_d["kind"],
                                          entry_d["args_fingerprint"],
                                          entry_d["result"])
        return j


def _path_to_str(path: tuple[int, ...]) -> str:
    return ".".join(str(i) for i in path)


def _path_from_any(v) -> tuple[int, ...]:
    """Accept tuple (in-memory), int (legacy flat seq), or dotted str (new on-disk)."""
    if isinstance(v, int):
        return (v,)
    if isinstance(v, str):
        return tuple(int(p) for p in v.split("."))
    if isinstance(v, (tuple, list)):
        return tuple(int(p) for p in v)
    raise TypeError(f"unrecognized journal seq: {v!r}")
```

- The contiguity check (`seq != len(entries)`) is dropped. Tuple paths can land out of order (parallel thunks finish in any order); duplicate-key check replaces it.
- `WorkflowSuspended.seq: tuple[int, ...]` — `resume.py` serializes the path as a dotted string for `action_data`, deserializes back to tuple via `_path_from_any`.
- `WorkflowHandle.llm_call/user_input` build `seq = (self._cursor,)` and pass tuple to `_check_or_none`/`journal.append`.

**Verification — automated:**
- [ ] `make lint` passes
- [ ] `make test` passes (existing workflow tests pass with tuple-path seqs)
- [ ] `make check` passes
- [ ] `pytest tests/test_workflow_journal.py -v` — new tests for tuple round-trip, dotted-string JSON, int→1-tuple legacy upgrade

**Verification — manual:**
- [ ] Inspect a freshly-generated `workflow.json` from a `/interview` run: confirm seqs serialize as dotted strings like `"0"`, `"1"`, etc.
- [ ] If a legacy `workflow.json` exists in `data/lorchard/workspace/conversations/`, load it via `load_journal` — entries upgrade cleanly.

---

## Phase 2: Sub-handle factory

Add a sub-handle pattern to `WorkflowHandle` so primitives like `parallel`/`pipeline` can dispatch work to thunks under their own key namespace.

**Files:**
- Modify: `src/decafclaw/workflow/handle.py` — add `_key_prefix: tuple[int, ...] = ()` to `WorkflowHandle`; add `_make_subhandle(idx: int) -> WorkflowHandle`; refactor `llm_call`/`user_input` to use `self._key_prefix + (self._cursor,)`.
- Test: `tests/test_workflow_handle.py` — sub-handle key composition (parent at `(5,)`, sub-handle at `(5, 0)`, grand-sub at `(5, 0, 2)`).

**Key changes:**

```python
class WorkflowHandle:
    def __init__(self, ctx, journal, *, llm_caller=None,
                 model: str = "vertex-gemini-flash",
                 _key_prefix: tuple[int, ...] = ()):
        self.ctx = ctx
        self.journal = journal
        self._cursor = 0
        self._llm_caller = llm_caller or _default_llm_call
        self._model = model
        self._key_prefix = _key_prefix

    def _next_seq(self) -> tuple[int, ...]:
        seq = self._key_prefix + (self._cursor,)
        self._cursor += 1
        return seq

    def _make_subhandle(self, idx: int) -> "WorkflowHandle":
        """Create a sub-handle with extended key prefix, fresh cursor.

        Used by parallel/pipeline to give each thunk/item its own key
        namespace. Sub-handles share ctx, journal, llm_caller, model.
        """
        sub_prefix = self._key_prefix + (self._cursor, idx)
        return WorkflowHandle(self.ctx, self.journal,
                              llm_caller=self._llm_caller,
                              model=self._model,
                              _key_prefix=sub_prefix)

    async def llm_call(self, *, prompt, schema, system="", tool_name="submit",
                       model=None):
        seq = self._next_seq()
        fp = fingerprint("llm_call",
                         {"prompt": prompt, "schema": schema, "system": system})
        cached, hit = self._check_or_none(seq, "llm_call", fp)
        if hit:
            return cached
        result = await self._llm_caller(
            self.ctx, system=system, user_msg=prompt, schema=schema,
            tool_name=tool_name, model=model or self._model)
        self.journal.append(seq, "llm_call", fp, result)
        save_journal(self.ctx.config, self.ctx.conv_id, self.journal)
        return result

    async def user_input(self, prompt, *, choices=None):
        seq = self._next_seq()
        fp = fingerprint("user_input", {"prompt": prompt, "choices": choices})
        cached, hit = self._check_or_none(seq, "user_input", fp)
        if hit:
            return cached
        raise WorkflowSuspended(seq=seq, args_fingerprint=fp, prompt=prompt,
                                choices=choices)
```

- `_make_subhandle(idx)` builds prefix `self._key_prefix + (self._cursor, idx)`. The parent's `self._cursor` is NOT advanced here — parallel/pipeline call this for each thunk/item, then later record their own outer entry which DOES advance the cursor (see Phase 5 / 6).
- Sub-handles inherit ctx, journal, llm_caller, model. Each gets a fresh `_cursor = 0`.

**Verification — automated:**
- [ ] `make lint` passes
- [ ] `make test` passes (existing tests adapted to new `_next_seq` indirection)
- [ ] `make check` passes
- [ ] `pytest tests/test_workflow_handle.py -v` — new tests cover sub-handle key composition (single level + nested)

**Verification — manual:**
- [ ] Re-run `/interview` end-to-end (or its test) and confirm journal entries still come out at `(0,), (1,), (2,)…`.

---

## Phase 3: `wf.tool_call`

Direct journaled tool dispatch. Gated by parent ctx's `allowed_tools`. Result serialized as JSON-friendly dict, media stripped.

**Files:**
- Modify: `src/decafclaw/workflow/handle.py` — add `WorkflowHandle.tool_call(name, **args)`.
- Test: `tests/test_workflow_tool_call.py` (new) — live path, replay path, allowed_tools gate rejection, ToolResult-with-media is stripped to text+data.

**Key changes:**

```python
async def tool_call(self, name: str, **args) -> dict:
    """Invoke a tool by name. Gated by ctx.allowed_tools.

    Returns a dict with at least {"text": str, "data": Any|None}. Media
    attachments on the underlying ToolResult are stripped — the journal
    captures only the JSON-serializable surface.
    """
    seq = self._next_seq()
    allowed = self.ctx.allowed_tools or set()
    if name not in allowed:
        raise WorkflowToolNotAllowed(
            f"tool {name!r} not in ctx.allowed_tools for workflow")
    fp = fingerprint("tool_call", {"name": name, "args": args})
    cached, hit = self._check_or_none(seq, "tool_call", fp)
    if hit:
        return cached
    from decafclaw.tools import execute_tool  # noqa: PLC0415 — break import cycle
    result = await execute_tool(self.ctx, name, args)
    serialized = {"text": result.text, "data": result.data}
    self.journal.append(seq, "tool_call", fp, serialized)
    save_journal(self.ctx.config, self.ctx.conv_id, self.journal)
    return serialized
```

- New exception in `errors.py`: `class WorkflowToolNotAllowed(Exception): pass`. Caught by the engine in `except Exception` (terminal error).
- The `execute_tool` import is function-level — same justification as the `archive.append_message` lazy import in `resume.py` (avoids workflow → tools → workflow cycles).
- Tests use a fake tool registered into `ctx.allowed_tools` and dispatched via `execute_tool`; the test-side registration uses the existing test pattern from `tests/test_workflow_resume.py`.

**Verification — automated:**
- [ ] `make lint` passes
- [ ] `make test` passes
- [ ] `make check` passes
- [ ] `pytest tests/test_workflow_tool_call.py -v` — covers live, replay, allowed_tools rejection, media-stripping

**Verification — manual:**
- [ ] Construct a minimal orchestrator that calls `await wf.tool_call("vault_read", path="foo.md")` against a real workspace, confirm the journal records `kind: tool_call` and the result text.

---

## Phase 4: `wf.subagent`

Dispatch a child agent loop via `delegate._run_child_turn` directly. Optional `schema=` for forced structured output.

**Files:**
- Modify: `src/decafclaw/tools/delegate.py` — promote `_run_child_turn` to module-public `run_child_turn` (or expose via a re-export); no behavior change.
- Modify: `src/decafclaw/workflow/handle.py` — add `WorkflowHandle.subagent(prompt, *, schema=None, allowed_tools=None, allow_vault_retrieval=False, allow_vault_read=False, model=None)`.
- Test: `tests/test_workflow_subagent.py` (new) — live path (mock `run_child_turn`), replay path, schema path returns structured dict, allowed_tools override threaded through.

**Key changes:**

```python
# handle.py
async def subagent(
    self, prompt: str, *,
    schema: dict | None = None,
    allowed_tools: list[str] | None = None,
    allow_vault_retrieval: bool = False,
    allow_vault_read: bool = False,
    model: str | None = None,
) -> dict | str:
    """Dispatch a child agent loop as a journaled boundary crossing.

    Returns the child's final text, OR the schema-validated structured
    object if `schema` is given. The child agent inherits delegate's
    standard restrictions (no vault writes, no further delegations) plus
    any explicit allowed_tools override.
    """
    seq = self._next_seq()
    fp = fingerprint("subagent", {
        "prompt": prompt,
        "schema": schema,
        "allowed_tools": sorted(allowed_tools) if allowed_tools else None,
        "allow_vault_retrieval": allow_vault_retrieval,
        "allow_vault_read": allow_vault_read,
    })
    cached, hit = self._check_or_none(seq, "subagent", fp)
    if hit:
        return cached
    from decafclaw.tools.delegate import run_child_turn  # noqa: PLC0415
    text, data = await run_child_turn(
        self.ctx,
        task=prompt,
        return_schema=schema,
        allowed_tools=allowed_tools,
        allow_vault_retrieval=allow_vault_retrieval,
        allow_vault_read=allow_vault_read,
        model=model,
    )
    result = data if schema is not None else text
    self.journal.append(seq, "subagent", fp, result)
    save_journal(self.ctx.config, self.ctx.conv_id, self.journal)
    return result
```

- `delegate.run_child_turn` may need to return `(text, structured_data)` for the schema branch — Phase 4's first edit to `delegate.py` aligns the return signature for both `tool_delegate_task` (which currently wraps it) and this new caller.
- `schema=None` → returns text; `schema` is present → returns the parsed-and-validated structured dict (the same value `tool_delegate_task` returns in its `data` field today).
- Fingerprint sorts `allowed_tools` to canonicalize (order shouldn't break replay).

**Verification — automated:**
- [ ] `make lint` passes
- [ ] `make test` passes (existing delegate tests adapt to the unified `run_child_turn` return shape)
- [ ] `make check` passes
- [ ] `pytest tests/test_workflow_subagent.py -v` — covers live (text + schema), replay, allowed_tools threading

**Verification — manual:**
- [ ] Inspect the journal of an orchestrator that called `wf.subagent("explain", schema={...})` — the recorded `result` should be the structured dict.

---

## Phase 5: `wf.parallel`

Fan out N thunks concurrently via sub-handles. Propagate errors. Cooperative cancel via `ctx.cancelled`. Outer entry caches assembled result.

**Files:**
- Modify: `src/decafclaw/workflow/handle.py` — add `WorkflowHandle.parallel(thunks)`.
- Test: `tests/test_workflow_parallel.py` (new) — live path (3 thunks each doing 2 llm_calls), replay (all cached), mid-fan-out resume (some thunks completed, others not, on second run the engine fills in remainder), error propagation, cooperative cancel.

**Key changes:**

```python
async def parallel(
    self, thunks: list[Callable[["WorkflowHandle"], Awaitable[Any]]]
) -> list[Any]:
    """Run N thunks concurrently under sub-handles. Returns results in
    thunk-index order. Errors propagate — wrap individual thunks in
    try/except for tolerant collection.
    """
    import asyncio  # already imported; explicit for clarity
    seq = self._next_seq()
    fp = fingerprint("parallel", {"count": len(thunks)})
    cached, hit = self._check_or_none(seq, "parallel", fp)
    if hit:
        return cached
    sub_handles = [self._make_subhandle_at(seq, idx) for idx in range(len(thunks))]
    tasks = [asyncio.create_task(thunks[i](sub_handles[i]))
             for i in range(len(thunks))]
    # Cancellation watcher: if outer ctx is cancelled, cancel in-flight tasks.
    cancel_watcher = asyncio.create_task(self.ctx.cancelled.wait())
    try:
        done, pending = await asyncio.wait(
            tasks + [cancel_watcher],
            return_when=asyncio.FIRST_EXCEPTION)
        if cancel_watcher in done and not cancel_watcher.cancelled():
            for t in tasks:
                if not t.done():
                    t.cancel()
            # Let cancellations settle (best-effort).
            await asyncio.gather(*tasks, return_exceptions=True)
            raise asyncio.CancelledError()
        # If any task raised, gather all to surface the first exception.
        # Outstanding tasks are cancelled to avoid leaks.
        for t in pending:
            if t is not cancel_watcher:
                t.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        results = [t.result() for t in tasks]  # raises on the first exception
    finally:
        if not cancel_watcher.done():
            cancel_watcher.cancel()
    self.journal.append(seq, "parallel", fp, results)
    save_journal(self.ctx.config, self.ctx.conv_id, self.journal)
    return results
```

- `_make_subhandle_at(outer_seq, idx)` is a variant of `_make_subhandle` that takes the outer seq explicitly (so the parent's cursor isn't double-advanced). Add this helper alongside `_make_subhandle`:

```python
def _make_subhandle_at(self, outer_seq: tuple[int, ...], idx: int) -> "WorkflowHandle":
    sub_prefix = outer_seq + (idx,)
    return WorkflowHandle(self.ctx, self.journal,
                          llm_caller=self._llm_caller,
                          model=self._model,
                          _key_prefix=sub_prefix)
```

(`_make_subhandle` from Phase 2 can be removed once Phase 5 lands — `_make_subhandle_at` is the only call site. Defer that cleanup until end of phase.)

- Fingerprint includes only `count`, not the thunks themselves (callables aren't serializable). Determinism is the orchestrator author's responsibility — same control flow path → same number of thunks → same fingerprint. The child entries provide the actual replay safety per-thunk.
- Mid-fan-out resume works because the outer entry is missing — the thunks re-dispatch, each sub-handle's calls hit the partial journal, completed thunks fast-path through their sub-entries, in-progress ones resume from their next-uncached call.
- Error propagation: `tasks[i].result()` raises if thunk i raised; that bubbles up out of `parallel`. Outstanding tasks are cancelled.

**Verification — automated:**
- [ ] `make lint` passes
- [ ] `make test` passes
- [ ] `make check` passes
- [ ] `pytest tests/test_workflow_parallel.py -v` — covers concurrent live (mock llm_caller with per-thunk delays), full replay (all cached), mid-fan-out resume, error propagation, cancellation
- [ ] `pytest --durations=25` — confirm no parallel test lands in top 25 by wall time (the cancellation test in particular shouldn't `asyncio.sleep`)

**Verification — manual:**
- [ ] Inspect the journal from a 3-thunk parallel run: keys `(0, 0, 0)`, `(0, 0, 1)`, `(0, 1, 0)`, `(0, 2, 0)`, `(0,)` (outer last).

---

## Phase 6: `wf.pipeline`

Fan out N items through M stages, no barrier. Stage signature `async def stage(prev, item, idx, sub)`. Sub-handles per item; stages share that sub-handle's cursor sequentially. Propagate errors.

**Files:**
- Modify: `src/decafclaw/workflow/handle.py` — add `WorkflowHandle.pipeline(items, *stages)`.
- Test: `tests/test_workflow_pipeline.py` (new) — live path (3 items × 2 stages), replay path, mid-pipeline resume (item 0 finished stage 2, item 1 still in stage 1), error propagation (item 1 stage 2 raises → out), and a nested test (`pipeline` items where stage 2 uses `sub.parallel`).

**Key changes:**

```python
async def pipeline(
    self,
    items: list,
    *stages: Callable[[Any, Any, int, "WorkflowHandle"], Awaitable[Any]],
) -> list:
    """Per-item run of stage1 → stage2 → … → stageN. No barrier between
    stages. Returns the final-stage result per item, in item-index order.

    Each stage receives `(prev, item, idx, sub)` where `sub` is the
    per-item sub-handle stages must use for journaled calls.
    """
    import asyncio
    seq = self._next_seq()
    fp = fingerprint("pipeline", {
        "items": items,
        "stage_count": len(stages),
    })
    cached, hit = self._check_or_none(seq, "pipeline", fp)
    if hit:
        return cached

    async def _run_one(item, idx):
        sub = self._make_subhandle_at(seq, idx)
        # Stages share `sub`'s cursor; each stage's journaled calls
        # advance it via `sub.llm_call(...)`, `sub.tool_call(...)`, etc.
        prev = item
        for stage in stages:
            prev = await stage(prev, item, idx, sub)
        return prev

    tasks = [asyncio.create_task(_run_one(items[i], i)) for i in range(len(items))]
    cancel_watcher = asyncio.create_task(self.ctx.cancelled.wait())
    try:
        done, pending = await asyncio.wait(
            tasks + [cancel_watcher],
            return_when=asyncio.FIRST_EXCEPTION)
        if cancel_watcher in done and not cancel_watcher.cancelled():
            for t in tasks:
                if not t.done():
                    t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise asyncio.CancelledError()
        for t in pending:
            if t is not cancel_watcher:
                t.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        results = [t.result() for t in tasks]
    finally:
        if not cancel_watcher.done():
            cancel_watcher.cancel()
    self.journal.append(seq, "pipeline", fp, results)
    save_journal(self.ctx.config, self.ctx.conv_id, self.journal)
    return results
```

- Items list IS fingerprinted (`items` plus `stage_count`). Items must be JSON-serializable for the fingerprint to compute.
- Stages aren't fingerprinted (they're code, not data). Re-arranging stages between runs is a replay-fragility the author owns — same posture as the `prompt` text in `llm_call`.
- Cancellation/error semantics mirror `parallel` exactly. Worth factoring into a shared `_run_concurrent_tasks` helper if both phases land; defer the refactor decision to end of Phase 6.

**Verification — automated:**
- [ ] `make lint` passes
- [ ] `make test` passes
- [ ] `make check` passes
- [ ] `pytest tests/test_workflow_pipeline.py -v` — covers live, full replay, mid-pipeline resume (different items at different stages), error propagation, nested with parallel
- [ ] `pytest --durations=25` — confirm pipeline tests don't `asyncio.sleep`

**Verification — manual:**
- [ ] Inspect the journal from a 3-item × 2-stage pipeline: keys `(0, 0, 0)`, `(0, 0, 1)`, `(0, 1, 0)`, `(0, 1, 1)`, `(0, 2, 0)`, `(0, 2, 1)`, `(0,)`.

---

## Phase 7: `/research` hero workflow

Build the second hero workflow that exercises all four primitives end-to-end. User-invocable as `/research <topic>` (web UI) / `!research <topic>` (Mattermost).

**Files:**
- Create: `src/decafclaw/workflow/workflows/research.py` — the orchestrator. Uses `wf.user_input`, `wf.llm_call`, `wf.parallel`, `wf.tool_call`, `wf.pipeline`, `wf.subagent`.
- Modify: `src/decafclaw/workflow/workflows/__init__.py` — register the new workflow.
- Modify: `src/decafclaw/commands.py` (or wherever workflow commands are wired — verify in execute) — register `/research` user-invocable command that dispatches to the workflow.
- Test: `tests/test_workflow_research.py` (new) — unit test the orchestrator with mocked `wf` primitives (live + replay).

**Key changes:**

```python
# workflows/research.py
from ..registry import workflow

_CLARIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "queries": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 3,
            "maxItems": 6,
        },
    },
    "required": ["queries"],
}

_SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "key_points": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["title", "key_points"],
}

_REPORT_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "body": {"type": "string"},
    },
    "required": ["title", "body"],
}


@workflow("research")
async def research(wf):
    topic = await wf.user_input("What topic should I research?")
    scope = await wf.user_input(
        "Any specific angle, audience, or constraint?")

    plan = await wf.llm_call(
        prompt=f"Topic: {topic}\nScope: {scope}\n\n"
               "Generate 3-6 focused search queries for this research.",
        schema=_CLARIFY_SCHEMA,
        system="You plan focused research sweeps.")
    queries = plan["queries"]

    # Fan out fetches via parallel. Each thunk receives a sub-handle.
    async def _fetch(query):
        async def _thunk(sub):
            return await sub.tool_call("http_fetch_search", query=query)
        return _thunk

    fetches = await wf.parallel([_fetch(q) for q in queries])

    # Pipeline each fetched result through extract → summarize.
    # Stages receive (prev, item, idx, sub) — sub is the per-item sub-handle.
    async def _extract(prev, item, idx, sub):
        return item_to_text(prev)  # plain Python, no journaling

    async def _summarize(prev, item, idx, sub):
        return await sub.llm_call(
            prompt=f"Summarize:\n\n{prev[:8000]}",
            schema=_SUMMARY_SCHEMA,
            system="You write tight summaries.")

    summaries = await wf.pipeline(fetches, _extract, _summarize)

    # Synthesize via subagent (multi-turn reasoning over the summaries).
    report = await wf.subagent(
        prompt=f"Topic: {topic}\nScope: {scope}\n\nSummaries:\n"
               + "\n\n".join(str(s) for s in summaries)
               + "\n\nSynthesize a report.",
        schema=_REPORT_SCHEMA,
    )

    return report
```

- `item_to_text` is a plain helper that extracts text from an `http_fetch_search` result — defined inline in `research.py`, not journaled.
- `http_fetch_search` is the tool name; verify the actual tool exists in `tools/http_tools.py` during execute. If it doesn't, fall back to `tabstack_research` (per the Tabstack-via-tabstack skill).
- Command registration: workflows are user-invokable via `/research <topic>`. Look at how `interview` is registered (currently bare workflow registry, no slash command) — `/research` may need a small wiring step in `commands.py` to call `enqueue_turn(kind=TurnKind.WORKFLOW, metadata={"workflow_name": "research"}, prompt=arguments)`.

**Verification — automated:**
- [ ] `make lint` passes
- [ ] `make test` passes
- [ ] `make check` passes
- [ ] `pytest tests/test_workflow_research.py -v` — unit-level walk with mocked primitives + replay

**Verification — manual:**
- [ ] `/research` appears in the slash-command list in the web UI.
- [ ] Manually trigger `/research foo` (no live LLM yet — just confirm the user_input prompt appears).

---

## Phase 8: Live smoke + docs update

Walk `/research` end-to-end on `vertex-gemini-flash`, restart the server mid-run, confirm resume. Update `docs/workflows.md` to replace the "What is not in v1" entries for these primitives with proper contract documentation.

**Files:**
- Modify: `docs/workflows.md` — replace the `subagent` / `parallel` / `pipeline` / `tool_call` bullet under "What is not in v1" with new sections under the existing "Primitives" / "Replay semantics" structure. One section per primitive: signature, fingerprint shape, replay behavior, error/cancel semantics.
- Create: `docs/dev-sessions/2026-06-10-1732-574-workflow-batch-primitives/smoke.md` — capture the smoke walk's transcript (commands, journal-file inspection, restart timing). Sidecar to `notes.md`, not a permanent doc.
- Modify: `docs/dev-sessions/2026-06-10-1732-574-workflow-batch-primitives/notes.md` — append per-phase notes, surprises, and a session retro.

**Key changes:**

The smoke walk per `reference_workflow_smoke_pattern`:
1. Start a local web-only server in the worktree: `MATTERMOST_ENABLED=false HTTP_PORT=18892 make run` (or background equivalent).
2. Drive a conversation with `decafclaw-client`: `/research artificial reefs in the Mediterranean`.
3. Answer the two `user_input` rounds.
4. Mid-way through the `wf.parallel` fetches OR mid-`wf.pipeline`, `kill -INT` the server.
5. Restart server, reload the conversation, confirm the workflow resumes from the journal — no duplicated fetches, no duplicated summaries.
6. Confirm the final report lands.
7. Inspect `workflow.json` on disk: tuple-path keys present (e.g., `"0.1.0"`), `parallel`/`pipeline` outer entries present, status `done`.

`docs/workflows.md` outline of the new sections (one each):

```markdown
### `wf.tool_call(name, **args) -> dict`

[Description, fingerprint shape, error/replay semantics, allowed_tools gate.]

### `wf.subagent(prompt, *, schema=None, ...) -> dict | str`

[Description, fingerprint shape, schema behavior, child agent restrictions.]

### `wf.parallel(thunks) -> list`

[Description, sub-handle keying, error propagation, cancellation, mid-fan-out resume semantics.]

### `wf.pipeline(items, *stages) -> list`

[Description, stage signature, sub-handle keying, error propagation, cancellation, mid-pipeline resume semantics.]
```

**Verification — automated:**
- [ ] `make lint` passes (docs change is markdown, no impact)
- [ ] `make test` passes
- [ ] `make check` passes

**Verification — manual:**
- [ ] Live smoke walk completes: `/research` runs on Flash, mid-run restart resumes, final report lands.
- [ ] `workflow.json` inspection confirms tuple-path keys + outer entries for parallel/pipeline.
- [ ] `docs/workflows.md` reads coherently: the 4 new sections match the implementation; "What is not in v1" no longer mentions these primitives.
- [ ] No stale references to "deferred" / "not in v1" for these primitives anywhere in `docs/`.
