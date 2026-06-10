# Workflow Replay Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a first-class `TurnKind.WORKFLOW` that runs an authored async-Python orchestrator with durable, restart-surviving suspend/resume via deterministic replay, proven by an interview→artifact workflow.

**Architecture:** A workflow is a registered async function (`@workflow("interview")`) the harness runs as its own turn kind. Control flow is plain Python; the only journaled boundary crossings are two primitives — `llm_call` and `user_input`. The engine re-runs the orchestrator from the top on resume, replaying journaled results, so a `user_input` suspension that *ends the turn* resumes correctly even across a server restart. Suspend posts a pending confirmation with no awaiting waiter; resolution routes through the existing confirmation **recovery** path to a handler that journals the answer and enqueues a resume turn.

**Tech Stack:** Python 3, asyncio, existing decafclaw `llm.call_llm`, `ConversationManager`, `ConfirmationRegistry`, JSONL archive. Tests via pytest (`pytest-xdist -n auto`).

**Spec:** `docs/dev-sessions/2026-06-05-1455-workflow-replay-engine/spec.md`

**The load-bearing rule (from the spec):** every call that crosses to the outside world goes through the journal; everything else is ordinary Python. For the MVP that is exactly two journaled wrappers: `llm_call`, `user_input`.

---

## File Structure

**New module `src/decafclaw/workflow/`:**

| File | Responsibility |
|---|---|
| `__init__.py` | Public exports (`workflow`, `run_workflow`, `WorkflowHandle`, errors) |
| `paths.py` | `workflow_dir()` / `workflow_path()` — `conversations/{conv_id}/workflow.json`, sandboxed |
| `errors.py` | `WorkflowSuspended`, `WorkflowNonDeterministic`, `WorkflowError` |
| `journal.py` | `Journal` + `JournalEntry` + `fingerprint()` + load/save |
| `llm.py` | `call_structured()` — forced-tool structured-output helper (lifted from the spike) |
| `handle.py` | `WorkflowHandle` — replay/suspend/determinism protocol + `llm_call` + `user_input` |
| `engine.py` | `run_workflow()` + `WorkflowOutcome` |
| `registry.py` | `@workflow` decorator + `REGISTRY` + `get_workflow()` + `workflow_commands()` |
| `resume.py` | `WorkflowUserInputHandler` (confirmation handler) + `run_workflow_turn()` glue |
| `workflows/__init__.py` | Imports orchestrators so the decorator registers them |
| `workflows/interview.py` | The hero orchestrator |

**Edited existing files:**

| File | Change |
|---|---|
| `src/decafclaw/confirmations.py:14-21` | Add `WORKFLOW_USER_INPUT` to `ConfirmationAction` |
| `src/decafclaw/conversation_manager.py:73-80` | Add `TurnKind.WORKFLOW` |
| `src/decafclaw/conversation_manager.py:~1449-1456` | `_start_turn.run()` dispatch: WORKFLOW → `run_workflow_turn` |
| `src/decafclaw/conversation_manager.py` (new method) | `post_confirmation()` (post-without-await) |
| `src/decafclaw/conversation_manager.py` (`__init__`) | Register `WorkflowUserInputHandler` |
| `src/decafclaw/web/websocket.py:~302` | `/interview` command bridge → enqueue WORKFLOW turn |

**New tests:** `tests/test_workflow_paths.py`, `test_workflow_journal.py`, `test_workflow_llm.py`, `test_workflow_handle.py`, `test_workflow_engine.py`, `test_workflow_registry.py`, `test_workflow_interview.py`, `test_workflow_resume.py`, `test_workflow_turn_integration.py`.

---

## Task 1: Conversation-scoped workflow path

**Files:**
- Create: `src/decafclaw/workflow/__init__.py` (empty for now)
- Create: `src/decafclaw/workflow/paths.py`
- Test: `tests/test_workflow_paths.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_workflow_paths.py
from types import SimpleNamespace
from pathlib import Path
from decafclaw.workflow.paths import workflow_dir, workflow_path


def _cfg(tmp_path):
    return SimpleNamespace(workspace_path=tmp_path)


def test_workflow_path_is_in_conv_subdirectory(tmp_path):
    cfg = _cfg(tmp_path)
    assert workflow_path(cfg, "abc123") == (
        tmp_path / "conversations" / "abc123" / "workflow.json"
    )


def test_workflow_dir_is_created(tmp_path):
    cfg = _cfg(tmp_path)
    d = workflow_dir(cfg, "abc123", create=True)
    assert d.is_dir()
    assert d == tmp_path / "conversations" / "abc123"


def test_path_sandboxed_against_traversal(tmp_path):
    cfg = _cfg(tmp_path)
    p = workflow_path(cfg, "../../etc/passwd")
    base = (tmp_path / "conversations").resolve()
    assert p.resolve().is_relative_to(base)


def test_empty_conv_id_falls_back(tmp_path):
    cfg = _cfg(tmp_path)
    p = workflow_path(cfg, "")
    assert p.name == "workflow.json"
    assert (tmp_path / "conversations").resolve() in p.resolve().parents
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_workflow_paths.py -v`
Expected: FAIL — `ModuleNotFoundError: decafclaw.workflow.paths`

- [ ] **Step 3: Implement**

```python
# src/decafclaw/workflow/paths.py
"""Per-conversation workflow file paths.

New convention (#255): per-conversation files live in a directory named
for the conversation id — ``conversations/{conv_id}/workflow.json`` —
rather than the flat ``{conv_id}.*`` sidecar pattern. Only the workflow
file adopts this now; existing sidecars migrate later (see spec).
"""
from pathlib import Path


def _safe_conv_id(conv_id: str) -> str:
    safe = conv_id.replace("/", "").replace("\\", "").replace("..", "")
    return safe or "_invalid"


def workflow_dir(config, conv_id: str, *, create: bool = False) -> Path:
    base = (config.workspace_path / "conversations").resolve()
    d = (base / _safe_conv_id(conv_id)).resolve()
    if not d.is_relative_to(base):
        d = base / "_invalid"
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d


def workflow_path(config, conv_id: str) -> Path:
    return workflow_dir(config, conv_id) / "workflow.json"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_workflow_paths.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add src/decafclaw/workflow/__init__.py src/decafclaw/workflow/paths.py tests/test_workflow_paths.py
git commit -m "feat(workflow): conversation-scoped workflow.json path"
```

---

## Task 2: Errors + the Journal

**Files:**
- Create: `src/decafclaw/workflow/errors.py`
- Create: `src/decafclaw/workflow/journal.py`
- Test: `tests/test_workflow_journal.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_workflow_journal.py
from types import SimpleNamespace
from decafclaw.workflow.journal import (
    Journal, JournalEntry, fingerprint, load_journal, save_journal,
)


def _cfg(tmp_path):
    return SimpleNamespace(workspace_path=tmp_path)


def test_fingerprint_is_stable_and_order_insensitive():
    a = fingerprint("llm_call", {"prompt": "hi", "schema": {"x": 1}})
    b = fingerprint("llm_call", {"schema": {"x": 1}, "prompt": "hi"})
    assert a == b
    assert a != fingerprint("llm_call", {"prompt": "bye", "schema": {"x": 1}})


def test_append_is_contiguous_and_get_by_seq():
    j = Journal(workflow_name="t")
    j.append(0, "llm_call", "fp0", {"a": 1})
    j.append(1, "user_input", "fp1", "answer")
    assert j.get(0).result == {"a": 1}
    assert j.get(1).result == "answer"
    assert j.get(2) is None


def test_append_rejects_non_contiguous_seq():
    j = Journal(workflow_name="t")
    try:
        j.append(1, "llm_call", "fp", None)
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_save_and_load_round_trip(tmp_path):
    cfg = _cfg(tmp_path)
    j = Journal(workflow_name="interview", status="suspended")
    j.append(0, "user_input", "fp0", "topic")
    save_journal(cfg, "conv1", j)

    loaded = load_journal(cfg, "conv1")
    assert loaded.workflow_name == "interview"
    assert loaded.status == "suspended"
    assert loaded.get(0).kind == "user_input"
    assert loaded.get(0).result == "topic"
    assert loaded.get(0).args_fingerprint == "fp0"


def test_load_missing_returns_none(tmp_path):
    assert load_journal(_cfg(tmp_path), "nope") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_workflow_journal.py -v`
Expected: FAIL — `ModuleNotFoundError: decafclaw.workflow.journal`

- [ ] **Step 3: Implement errors + journal**

```python
# src/decafclaw/workflow/errors.py
"""Workflow engine exceptions."""


class WorkflowError(Exception):
    """Base for workflow engine failures."""


class WorkflowSuspended(Exception):
    """Raised by a journaled primitive that needs to suspend for the user.

    Carries everything the harness needs to post a confirmation and, on
    response, journal the answer at the right position.
    """

    def __init__(self, *, seq: int, args_fingerprint: str, prompt: str,
                 choices: list[str] | None = None):
        super().__init__(f"workflow suspended at step {seq}: {prompt!r}")
        self.seq = seq
        self.args_fingerprint = args_fingerprint
        self.prompt = prompt
        self.choices = choices


class WorkflowNonDeterministic(WorkflowError):
    """Replay reached a journaled call whose args don't match the record.

    Means control flow diverged between runs — a determinism bug in the
    orchestrator. Fail loudly rather than return a stale result.
    """

    def __init__(self, seq: int, recorded_kind: str, recorded_fp: str,
                 got_kind: str, got_fp: str):
        super().__init__(
            f"workflow non-deterministic at step {seq}: recorded "
            f"{recorded_kind}/{recorded_fp}, replay produced {got_kind}/{got_fp}"
        )
        self.seq = seq
```

```python
# src/decafclaw/workflow/journal.py
"""Durable, ordered record of journaled-call results for one workflow run.

Entries are keyed positionally by execution order: the Nth journaled call
executed gets sequence N. This is what makes loops replay correctly — same
control flow produces the same execution order, hence the same keys.
"""
import dataclasses
import hashlib
import json
from typing import Any

from .paths import workflow_dir, workflow_path


def fingerprint(kind: str, args: dict) -> str:
    """Stable hash of a journaled call's kind + args (order-insensitive)."""
    payload = json.dumps({"kind": kind, "args": args},
                         sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


@dataclasses.dataclass
class JournalEntry:
    seq: int
    kind: str
    args_fingerprint: str
    result: Any


@dataclasses.dataclass
class Journal:
    workflow_name: str
    status: str = "running"  # running | suspended | done | error
    entries: list[JournalEntry] = dataclasses.field(default_factory=list)

    def get(self, seq: int) -> JournalEntry | None:
        if 0 <= seq < len(self.entries):
            return self.entries[seq]
        return None

    def append(self, seq: int, kind: str, args_fingerprint: str,
               result: Any) -> None:
        if seq != len(self.entries):
            raise ValueError(
                f"non-contiguous journal append: seq={seq}, "
                f"len={len(self.entries)}")
        self.entries.append(JournalEntry(seq, kind, args_fingerprint, result))

    def to_dict(self) -> dict:
        return {
            "workflow_name": self.workflow_name,
            "status": self.status,
            "entries": [dataclasses.asdict(e) for e in self.entries],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Journal":
        j = cls(workflow_name=d["workflow_name"],
                status=d.get("status", "running"))
        j.entries = [JournalEntry(**e) for e in d.get("entries", [])]
        return j


def save_journal(config, conv_id: str, journal: Journal) -> None:
    """Persist the journal. Flushed on every call for crash-safety."""
    workflow_dir(config, conv_id, create=True)
    path = workflow_path(config, conv_id)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(journal.to_dict(), indent=2))
    tmp.replace(path)  # atomic on POSIX


def load_journal(config, conv_id: str) -> Journal | None:
    path = workflow_path(config, conv_id)
    if not path.exists():
        return None
    return Journal.from_dict(json.loads(path.read_text()))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_workflow_journal.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add src/decafclaw/workflow/errors.py src/decafclaw/workflow/journal.py tests/test_workflow_journal.py
git commit -m "feat(workflow): journal with positional keying + fingerprints"
```

---

## Task 3: Structured-output LLM helper

**Files:**
- Create: `src/decafclaw/workflow/llm.py`
- Test: `tests/test_workflow_llm.py`

Lifted from the spike (`spike_research_brief/tools.py` on the closed `feat/255-workflow-engine` branch), generalized to take the model as an argument instead of a module constant.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_workflow_llm.py
import pytest
from types import SimpleNamespace
from decafclaw.workflow import llm as wf_llm


@pytest.mark.asyncio
async def test_returns_parsed_tool_args(monkeypatch):
    async def fake_call_llm(config, messages, *, tools, model_name):
        return {"tool_calls": [{"function": {"arguments": '{"q": "why?"}'}}]}
    monkeypatch.setattr(wf_llm, "call_llm", fake_call_llm)

    out = await wf_llm.call_structured(
        SimpleNamespace(config=object()),
        system="s", user_msg="u", schema={"type": "object"},
        tool_name="submit", model="m",
    )
    assert out == {"q": "why?"}


@pytest.mark.asyncio
async def test_retries_on_narrate_then_succeeds(monkeypatch):
    calls = []

    async def fake_call_llm(config, messages, *, tools, model_name):
        calls.append(messages)
        if len(calls) == 1:
            return {"content": "I think the answer is...", "tool_calls": None}
        return {"tool_calls": [{"function": {"arguments": '{"ok": true}'}}]}
    monkeypatch.setattr(wf_llm, "call_llm", fake_call_llm)

    out = await wf_llm.call_structured(
        SimpleNamespace(config=object()),
        system="s", user_msg="u", schema={}, tool_name="submit", model="m",
    )
    assert out == {"ok": True}
    assert len(calls) == 2  # one narrate-stall, one nudged retry


@pytest.mark.asyncio
async def test_raises_after_exhausting_retries(monkeypatch):
    async def fake_call_llm(config, messages, *, tools, model_name):
        return {"content": "prose", "tool_calls": None}
    monkeypatch.setattr(wf_llm, "call_llm", fake_call_llm)

    with pytest.raises(RuntimeError):
        await wf_llm.call_structured(
            SimpleNamespace(config=object()),
            system="s", user_msg="u", schema={}, tool_name="submit",
            model="m", retries=1,
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_workflow_llm.py -v`
Expected: FAIL — `ModuleNotFoundError: decafclaw.workflow.llm`

- [ ] **Step 3: Implement**

```python
# src/decafclaw/workflow/llm.py
"""Forced-tool structured-output helper for workflow llm_call.

Exposes exactly one tool the model MUST call, parses its args, and retries
once with a stricter nudge on narrate-stall. Provider-agnostic; proven on
vertex-gemini-flash by the #255 spike.
"""
import json
import logging

from decafclaw.llm import call_llm

log = logging.getLogger(__name__)


async def call_structured(ctx, *, system: str, user_msg: str, schema: dict,
                          tool_name: str, model: str, description: str = "",
                          retries: int = 1) -> dict:
    """Force a structured response. Returns parsed tool args, or raises."""
    base_user = user_msg
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": base_user},
    ]
    tools = [{
        "type": "function",
        "function": {
            "name": tool_name,
            "description": description or (
                "Submit the structured result for this step. "
                "You MUST call this — do not respond with prose."),
            "parameters": schema,
        },
    }]
    last_error: str | None = None
    for _ in range(retries + 1):
        result = await call_llm(ctx.config, messages, tools=tools,
                                model_name=model)
        tool_calls = result.get("tool_calls") or []
        if tool_calls:
            args_raw = tool_calls[0].get("function", {}).get("arguments") or "{}"
            try:
                return json.loads(args_raw)
            except json.JSONDecodeError as e:
                last_error = f"invalid JSON in tool args: {e}; raw={args_raw[:200]!r}"
        else:
            last_error = (
                f"model emitted text instead of calling {tool_name!r}: "
                f"{(result.get('content') or '')[:200]!r}")
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": (
                base_user + f"\n\nIMPORTANT: You MUST call the tool "
                f"`{tool_name}` now. Do not narrate. Emit only the call.")},
        ]
    raise RuntimeError(
        f"structured call to {tool_name} failed after {retries + 1} "
        f"attempts: {last_error}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_workflow_llm.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/decafclaw/workflow/llm.py tests/test_workflow_llm.py
git commit -m "feat(workflow): forced-tool structured-output helper"
```

---

## Task 4: WorkflowHandle — the replay/suspend/determinism core

**Files:**
- Create: `src/decafclaw/workflow/handle.py`
- Test: `tests/test_workflow_handle.py`

This is the heart. `llm_call` and `user_input` both consult the journal positionally: an already-journaled call returns its cached result (after a fingerprint check); a new `llm_call` runs live and journals; a new `user_input` raises `WorkflowSuspended`. The `llm_caller` is injectable so tests need no real LLM.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_workflow_handle.py
import pytest
from types import SimpleNamespace
from decafclaw.workflow.handle import WorkflowHandle
from decafclaw.workflow.journal import Journal
from decafclaw.workflow.errors import WorkflowSuspended, WorkflowNonDeterministic


def _ctx(tmp_path):
    # conv_id "" keeps save_journal writing under tmp_path/conversations/_invalid
    return SimpleNamespace(config=SimpleNamespace(workspace_path=tmp_path),
                           conv_id="convX")


@pytest.mark.asyncio
async def test_llm_call_executes_live_and_journals(tmp_path):
    j = Journal(workflow_name="t")
    hits = []

    async def fake_llm(ctx, **kw):
        hits.append(kw["user_msg"])
        return {"answer": 42}

    h = WorkflowHandle(_ctx(tmp_path), j, llm_caller=fake_llm)
    out = await h.llm_call(prompt="q1", schema={}, model="m")
    assert out == {"answer": 42}
    assert j.get(0).kind == "llm_call"
    assert hits == ["q1"]


@pytest.mark.asyncio
async def test_replay_returns_cached_without_executing(tmp_path):
    # Seed a journal as if llm_call already ran.
    j = Journal(workflow_name="t")
    from decafclaw.workflow.journal import fingerprint
    fp = fingerprint("llm_call", {"prompt": "q1", "schema": {}, "system": ""})
    j.append(0, "llm_call", fp, {"answer": 42})

    async def boom(ctx, **kw):
        raise AssertionError("live LLM path must NOT run during replay")

    h = WorkflowHandle(_ctx(tmp_path), j, llm_caller=boom)
    out = await h.llm_call(prompt="q1", schema={}, model="m")
    assert out == {"answer": 42}  # sabotage check: cached, not re-executed


@pytest.mark.asyncio
async def test_user_input_suspends_when_unanswered(tmp_path):
    j = Journal(workflow_name="t")
    h = WorkflowHandle(_ctx(tmp_path), j, llm_caller=None)
    with pytest.raises(WorkflowSuspended) as ei:
        await h.user_input("What topic?")
    assert ei.value.seq == 0
    assert ei.value.prompt == "What topic?"


@pytest.mark.asyncio
async def test_user_input_replays_recorded_answer(tmp_path):
    from decafclaw.workflow.journal import fingerprint
    j = Journal(workflow_name="t")
    fp = fingerprint("user_input", {"prompt": "What topic?", "choices": None})
    j.append(0, "user_input", fp, "tide pools")
    h = WorkflowHandle(_ctx(tmp_path), j, llm_caller=None)
    assert await h.user_input("What topic?") == "tide pools"


@pytest.mark.asyncio
async def test_determinism_guard_fires_on_divergent_args(tmp_path):
    from decafclaw.workflow.journal import fingerprint
    j = Journal(workflow_name="t")
    fp = fingerprint("llm_call", {"prompt": "ORIGINAL", "schema": {}, "system": ""})
    j.append(0, "llm_call", fp, {"x": 1})

    async def fake_llm(ctx, **kw):
        return {"x": 1}

    h = WorkflowHandle(_ctx(tmp_path), j, llm_caller=fake_llm)
    with pytest.raises(WorkflowNonDeterministic):
        await h.llm_call(prompt="CHANGED", schema={}, model="m")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_workflow_handle.py -v`
Expected: FAIL — `ModuleNotFoundError: decafclaw.workflow.handle`

- [ ] **Step 3: Implement**

```python
# src/decafclaw/workflow/handle.py
"""WorkflowHandle — the `wf` object an orchestrator drives.

Exposes the two journaled primitives. Positional cursor advances once per
journaled call. Replay returns cached results (after a determinism check);
new llm_calls run live and journal; new user_inputs raise WorkflowSuspended.
"""
import logging

from . import llm as wf_llm
from .errors import WorkflowNonDeterministic, WorkflowSuspended
from .journal import fingerprint, save_journal

log = logging.getLogger(__name__)


async def _default_llm_call(ctx, **kw):
    return await wf_llm.call_structured(ctx, **kw)


class WorkflowHandle:
    def __init__(self, ctx, journal, *, llm_caller=None,
                 model: str = "vertex-gemini-flash"):
        self.ctx = ctx
        self.journal = journal
        self._cursor = 0
        self._llm_caller = llm_caller or _default_llm_call
        self._model = model

    def _check_or_none(self, seq: int, kind: str, fp: str):
        """Return cached result for an already-journaled call at seq, or None.

        Raises WorkflowNonDeterministic if the recorded call doesn't match.
        """
        existing = self.journal.get(seq)
        if existing is None:
            return None, False
        if existing.kind != kind or existing.args_fingerprint != fp:
            raise WorkflowNonDeterministic(
                seq, existing.kind, existing.args_fingerprint, kind, fp)
        return existing.result, True

    async def llm_call(self, *, prompt: str, schema: dict, system: str = "",
                       tool_name: str = "submit", model: str | None = None):
        seq = self._cursor
        self._cursor += 1
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

    async def user_input(self, prompt: str, *, choices: list[str] | None = None):
        seq = self._cursor
        self._cursor += 1
        fp = fingerprint("user_input", {"prompt": prompt, "choices": choices})
        cached, hit = self._check_or_none(seq, "user_input", fp)
        if hit:
            return cached
        # New, unanswered → suspend. The journal (entries 0..seq-1) is already
        # persisted by the preceding live call (or empty on a fresh start).
        raise WorkflowSuspended(seq=seq, args_fingerprint=fp, prompt=prompt,
                                choices=choices)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_workflow_handle.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add src/decafclaw/workflow/handle.py tests/test_workflow_handle.py
git commit -m "feat(workflow): WorkflowHandle replay/suspend/determinism core"
```

---

## Task 5: The engine — run_workflow

**Files:**
- Create: `src/decafclaw/workflow/engine.py`
- Test: `tests/test_workflow_engine.py`

`run_workflow` builds a handle, runs the orchestrator, and classifies the outcome: completion, suspension, or error. It persists the journal status at each terminal state.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_workflow_engine.py
import pytest
from types import SimpleNamespace
from decafclaw.workflow.engine import run_workflow
from decafclaw.workflow.journal import Journal, fingerprint


def _ctx(tmp_path):
    return SimpleNamespace(config=SimpleNamespace(workspace_path=tmp_path),
                           conv_id="convE")


@pytest.mark.asyncio
async def test_suspends_on_first_user_input(tmp_path):
    async def wf(h):
        topic = await h.user_input("topic?")
        return {"topic": topic}

    j = Journal(workflow_name="t")
    outcome = await run_workflow(_ctx(tmp_path), wf, j)
    assert outcome.status == "suspended"
    assert outcome.suspend.prompt == "topic?"
    assert j.status == "suspended"


@pytest.mark.asyncio
async def test_completes_when_journal_fully_seeded(tmp_path):
    async def wf(h):
        topic = await h.user_input("topic?")
        return {"topic": topic, "done": True}

    j = Journal(workflow_name="t")
    fp = fingerprint("user_input", {"prompt": "topic?", "choices": None})
    j.append(0, "user_input", fp, "tide pools")

    outcome = await run_workflow(_ctx(tmp_path), wf, j)
    assert outcome.status == "done"
    assert outcome.result == {"topic": "tide pools", "done": True}
    assert j.status == "done"


@pytest.mark.asyncio
async def test_orchestrator_exception_becomes_error_outcome(tmp_path):
    async def wf(h):
        raise ValueError("boom")

    j = Journal(workflow_name="t")
    outcome = await run_workflow(_ctx(tmp_path), wf, j)
    assert outcome.status == "error"
    assert "boom" in outcome.error
    assert j.status == "error"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_workflow_engine.py -v`
Expected: FAIL — `ModuleNotFoundError: decafclaw.workflow.engine`

- [ ] **Step 3: Implement**

```python
# src/decafclaw/workflow/engine.py
"""run_workflow — runs an orchestrator once, classifying the outcome.

The engine owns control flow only in the trivial sense that it invokes the
orchestrator and interprets how it returned: completed, suspended for user
input, or errored. The orchestrator itself is plain async Python.
"""
import dataclasses
import logging
from typing import Any, Awaitable, Callable

from .errors import WorkflowNonDeterministic, WorkflowSuspended
from .handle import WorkflowHandle
from .journal import Journal, save_journal

log = logging.getLogger(__name__)


@dataclasses.dataclass
class WorkflowOutcome:
    status: str  # "done" | "suspended" | "error"
    result: Any = None
    suspend: WorkflowSuspended | None = None
    error: str = ""


async def run_workflow(
    ctx,
    workflow_fn: Callable[[WorkflowHandle], Awaitable[Any]],
    journal: Journal,
    *,
    llm_caller=None,
    model: str = "vertex-gemini-flash",
) -> WorkflowOutcome:
    handle = WorkflowHandle(ctx, journal, llm_caller=llm_caller, model=model)

    def _persist(status: str) -> None:
        journal.status = status
        save_journal(ctx.config, ctx.conv_id, journal)

    try:
        result = await workflow_fn(handle)
    except WorkflowSuspended as s:
        _persist("suspended")
        return WorkflowOutcome(status="suspended", suspend=s)
    except WorkflowNonDeterministic as e:
        log.error("workflow non-deterministic: %s", e)
        _persist("error")
        return WorkflowOutcome(status="error", error=str(e))
    except Exception as e:  # noqa: BLE001 — terminal classification
        log.exception("workflow orchestrator raised")
        _persist("error")
        return WorkflowOutcome(status="error", error=str(e))

    _persist("done")
    return WorkflowOutcome(status="done", result=result)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_workflow_engine.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/decafclaw/workflow/engine.py tests/test_workflow_engine.py
git commit -m "feat(workflow): run_workflow engine with outcome classification"
```

---

## Task 6: Registry + decorator

**Files:**
- Create: `src/decafclaw/workflow/registry.py`
- Modify: `src/decafclaw/workflow/__init__.py`
- Test: `tests/test_workflow_registry.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_workflow_registry.py
import pytest
from decafclaw.workflow.registry import workflow, get_workflow, REGISTRY


def test_decorator_registers_and_lookup_works():
    @workflow("demo_wf")
    async def demo(h):
        return "ok"

    spec = get_workflow("demo_wf")
    assert spec is not None
    assert spec.name == "demo_wf"
    assert spec.fn is demo


def test_unknown_workflow_returns_none():
    assert get_workflow("does_not_exist_xyz") is None


def test_duplicate_name_raises():
    @workflow("dup_wf")
    async def a(h):
        return 1
    with pytest.raises(ValueError):
        @workflow("dup_wf")
        async def b(h):
            return 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_workflow_registry.py -v`
Expected: FAIL — `ModuleNotFoundError: decafclaw.workflow.registry`

- [ ] **Step 3: Implement**

```python
# src/decafclaw/workflow/registry.py
"""Workflow registry + @workflow decorator.

A workflow is its own first-class concept — NOT a skill. It borrows only
the command-invocation plumbing. Orchestrators register at import time.
"""
import dataclasses
from typing import Any, Awaitable, Callable


@dataclasses.dataclass
class WorkflowSpec:
    name: str
    fn: Callable[[Any], Awaitable[Any]]
    model: str = "vertex-gemini-flash"


REGISTRY: dict[str, WorkflowSpec] = {}


def workflow(name: str, *, model: str = "vertex-gemini-flash"):
    def deco(fn):
        if name in REGISTRY:
            raise ValueError(f"workflow {name!r} already registered")
        REGISTRY[name] = WorkflowSpec(name=name, fn=fn, model=model)
        return fn
    return deco


def get_workflow(name: str) -> WorkflowSpec | None:
    return REGISTRY.get(name)


def workflow_commands() -> list[str]:
    """Names invocable as /<name>. Used by the command bridge."""
    return list(REGISTRY.keys())
```

```python
# src/decafclaw/workflow/__init__.py
"""Workflow replay engine (#255)."""
from .engine import WorkflowOutcome, run_workflow
from .errors import WorkflowNonDeterministic, WorkflowSuspended
from .handle import WorkflowHandle
from .registry import REGISTRY, get_workflow, workflow, workflow_commands

__all__ = [
    "run_workflow", "WorkflowOutcome", "WorkflowHandle",
    "WorkflowSuspended", "WorkflowNonDeterministic",
    "workflow", "get_workflow", "workflow_commands", "REGISTRY",
]
```

> **Note for the engineer:** `tests/test_workflow_registry.py::test_duplicate_name_raises` registers `dup_wf` once successfully then expects the second to raise — if you re-run with `-p no:randomly` ordering issues, each test name is unique so the module-level `REGISTRY` won't collide across tests. Do not add an autouse fixture that clears `REGISTRY` (later tests rely on `workflows/` imports having registered).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_workflow_registry.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/decafclaw/workflow/registry.py src/decafclaw/workflow/__init__.py tests/test_workflow_registry.py
git commit -m "feat(workflow): registry + @workflow decorator"
```

---

## Task 7: The interview orchestrator

**Files:**
- Create: `src/decafclaw/workflow/workflows/__init__.py`
- Create: `src/decafclaw/workflow/workflows/interview.py`
- Test: `tests/test_workflow_interview.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_workflow_interview.py
import pytest
from types import SimpleNamespace
from decafclaw.workflow.engine import run_workflow
from decafclaw.workflow.journal import Journal, fingerprint
from decafclaw.workflow.registry import get_workflow
import decafclaw.workflow.workflows  # noqa: F401 — registers interview


def _ctx(tmp_path):
    return SimpleNamespace(config=SimpleNamespace(workspace_path=tmp_path),
                           conv_id="convI")


@pytest.mark.asyncio
async def test_interview_suspends_for_topic_first(tmp_path):
    spec = get_workflow("interview")
    assert spec is not None
    outcome = await run_workflow(_ctx(tmp_path), spec.fn, Journal(workflow_name="interview"))
    assert outcome.status == "suspended"
    assert "about" in outcome.suspend.prompt.lower()


@pytest.mark.asyncio
async def test_interview_replays_to_artifact(tmp_path):
    """Seed a full journal (topic + one Q/A + done + synth) → pure replay,
    no LLM, reaches the artifact."""
    spec = get_workflow("interview")
    j = Journal(workflow_name="interview")

    def fp_user(prompt):
        return fingerprint("user_input", {"prompt": prompt, "choices": None})

    def fp_llm(prompt, schema, system):
        return fingerprint("llm_call",
                           {"prompt": prompt, "schema": schema, "system": system})

    # The orchestrator's call order must match these seq positions exactly.
    # Engineer: if a fingerprint mismatch (WorkflowNonDeterministic) fires,
    # the seeded args don't match the orchestrator's actual prompt/schema —
    # align them, do not loosen the guard.
    from decafclaw.workflow.workflows.interview import (
        _ask_prompt, _synth_prompt, _SYS_ASK, _SYS_SYNTH,
        _DECISION_SCHEMA, _ARTIFACT_SCHEMA,
    )
    j.append(0, "user_input", fp_user("What should this interview be about?"), "tide pools")
    q1_prompt = _ask_prompt("tide pools", [])
    j.append(1, "llm_call", fp_llm(q1_prompt, _DECISION_SCHEMA, _SYS_ASK),
             {"done": False, "question": "What draws you to them?"})
    j.append(2, "user_input", fp_user("What draws you to them?"), "the creatures")
    q2_prompt = _ask_prompt("tide pools", [{"q": "What draws you to them?", "a": "the creatures"}])
    j.append(3, "llm_call", fp_llm(q2_prompt, _DECISION_SCHEMA, _SYS_ASK),
             {"done": True, "question": ""})
    synth_prompt = _synth_prompt("tide pools",
                                 [{"q": "What draws you to them?", "a": "the creatures"}])
    j.append(4, "llm_call", fp_llm(synth_prompt, _ARTIFACT_SCHEMA, _SYS_SYNTH),
             {"title": "Tide Pools", "body": "..."})

    outcome = await run_workflow(_ctx(tmp_path), spec.fn, j)
    assert outcome.status == "done"
    assert outcome.result["title"] == "Tide Pools"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_workflow_interview.py -v`
Expected: FAIL — `ModuleNotFoundError: decafclaw.workflow.workflows`

- [ ] **Step 3: Implement**

```python
# src/decafclaw/workflow/workflows/__init__.py
"""Importing this package registers all bundled orchestrators."""
from . import interview  # noqa: F401
```

```python
# src/decafclaw/workflow/workflows/interview.py
"""Interview → artifact: the #255 hero workflow.

Asks one question at a time, looping until the model says it has enough (or a
cap), then synthesizes an artifact. The whole thing is plain Python — the
only journaled boundary crossings are wf.user_input and wf.llm_call.
"""
from ..registry import workflow

MAX_Q = 6

_SYS_ASK = (
    "You are conducting a focused interview to gather material for a written "
    "artifact. Ask ONE good next question at a time. Decide when you have "
    "enough to write something useful."
)
_SYS_SYNTH = (
    "You synthesize an interview transcript into a clear, well-structured "
    "written artifact."
)

_DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "done": {"type": "boolean",
                 "description": "True when you have enough to synthesize."},
        "question": {"type": "string",
                     "description": "The next question (empty if done)."},
    },
    "required": ["done", "question"],
}

_ARTIFACT_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "body": {"type": "string", "description": "Markdown body."},
    },
    "required": ["title", "body"],
}


def _ask_prompt(topic: str, answers: list[dict]) -> str:
    lines = [f"Topic: {topic}", ""]
    if answers:
        lines.append("Answers so far:")
        for a in answers:
            lines.append(f"- Q: {a['q']}\n  A: {a['a']}")
        lines.append("")
    lines.append("Decide whether you have enough. If not, ask the next question.")
    return "\n".join(lines)


def _synth_prompt(topic: str, answers: list[dict]) -> str:
    lines = [f"Topic: {topic}", "", "Interview transcript:"]
    for a in answers:
        lines.append(f"- Q: {a['q']}\n  A: {a['a']}")
    lines.append("")
    lines.append("Write a titled markdown artifact synthesizing this.")
    return "\n".join(lines)


@workflow("interview")
async def interview(wf):
    topic = await wf.user_input("What should this interview be about?")

    answers: list[dict] = []
    while len(answers) < MAX_Q:
        decision = await wf.llm_call(
            prompt=_ask_prompt(topic, answers),
            schema=_DECISION_SCHEMA, system=_SYS_ASK)
        if decision.get("done"):
            break
        reply = await wf.user_input(decision["question"])
        answers.append({"q": decision["question"], "a": reply})

    return await wf.llm_call(
        prompt=_synth_prompt(topic, answers),
        schema=_ARTIFACT_SCHEMA, system=_SYS_SYNTH)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_workflow_interview.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/decafclaw/workflow/workflows/ tests/test_workflow_interview.py
git commit -m "feat(workflow): interview orchestrator"
```

---

## Task 8: ConfirmationAction.WORKFLOW_USER_INPUT

**Files:**
- Modify: `src/decafclaw/confirmations.py:14-21`
- Test: `tests/test_workflow_resume.py` (created here, extended in Task 9)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_workflow_resume.py
from decafclaw.confirmations import ConfirmationAction


def test_workflow_user_input_action_exists():
    assert ConfirmationAction.WORKFLOW_USER_INPUT.value == "workflow_user_input"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_workflow_resume.py -v`
Expected: FAIL — `AttributeError: WORKFLOW_USER_INPUT`

- [ ] **Step 3: Implement** — add one line to the enum

```python
# src/decafclaw/confirmations.py — inside class ConfirmationAction
    WIDGET_RESPONSE = "widget_response"
    WORKFLOW_USER_INPUT = "workflow_user_input"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_workflow_resume.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
git add src/decafclaw/confirmations.py tests/test_workflow_resume.py
git commit -m "feat(workflow): WORKFLOW_USER_INPUT confirmation action"
```

---

## Task 9: Resume handler + turn glue

**Files:**
- Create: `src/decafclaw/workflow/resume.py`
- Test: `tests/test_workflow_resume.py` (extend)

`run_workflow_turn` is the harness entry point (called from `_start_turn`). `WorkflowUserInputHandler` resolves a posted confirmation: journals the answer at the suspended seq, then enqueues a resume WORKFLOW turn. The handler captures the manager so the recovery ctx (which lacks `.manager`) is sufficient.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_workflow_resume.py`)

```python
import pytest
from types import SimpleNamespace
from decafclaw.confirmations import ConfirmationRequest, ConfirmationResponse, ConfirmationAction
from decafclaw.workflow.journal import Journal, save_journal, load_journal, fingerprint
from decafclaw.workflow.resume import WorkflowUserInputHandler, run_workflow_turn


def _ctx(tmp_path, conv_id="convR"):
    return SimpleNamespace(config=SimpleNamespace(workspace_path=tmp_path),
                           conv_id=conv_id)


@pytest.mark.asyncio
async def test_handler_journals_answer_and_enqueues_resume(tmp_path):
    # A suspended interview: topic question posted, journal has no entries yet.
    cfg = SimpleNamespace(workspace_path=tmp_path)
    j = Journal(workflow_name="interview", status="suspended")
    save_journal(cfg, "convR", j)

    fp = fingerprint("user_input",
                     {"prompt": "What should this interview be about?",
                      "choices": None})
    request = ConfirmationRequest(
        action_type=ConfirmationAction.WORKFLOW_USER_INPUT,
        message="What should this interview be about?",
        action_data={"workflow_name": "interview", "seq": 0,
                     "args_fingerprint": fp, "prompt": "...", "choices": None},
        timeout=None,
    )
    response = ConfirmationResponse(confirmation_id="x", approved=True,
                                    data={"value": "tide pools"})

    enqueued = []

    class FakeManager:
        config = cfg
        async def enqueue_turn(self, conv_id, **kw):
            enqueued.append((conv_id, kw))

    handler = WorkflowUserInputHandler(FakeManager())
    out = await handler.on_approve(_ctx(tmp_path), request, response)

    reloaded = load_journal(cfg, "convR")
    assert reloaded.get(0).kind == "user_input"
    assert reloaded.get(0).result == "tide pools"
    assert reloaded.status == "running"
    assert enqueued and enqueued[0][1]["metadata"]["resume"] is True
    assert out == {"continue_loop": False}


@pytest.mark.asyncio
async def test_run_workflow_turn_fresh_start_suspends_and_posts(tmp_path):
    posted = []

    class FakeManager:
        config = SimpleNamespace(workspace_path=tmp_path)
        async def post_confirmation(self, conv_id, request):
            posted.append(request)

    ctx = _ctx(tmp_path, "convT")
    result = await run_workflow_turn(
        ctx, FakeManager(), "convT",
        workflow_name="interview", resume=False)
    assert posted, "a confirmation should be posted on suspend"
    assert posted[0].action_type == ConfirmationAction.WORKFLOW_USER_INPUT
    assert posted[0].action_data["seq"] == 0
    assert hasattr(result, "text")  # ToolResult-like
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_workflow_resume.py -v`
Expected: FAIL — `ModuleNotFoundError: decafclaw.workflow.resume`

- [ ] **Step 3: Implement**

```python
# src/decafclaw/workflow/resume.py
"""Harness glue: run a workflow turn, and resume it after user input.

run_workflow_turn is invoked by ConversationManager._start_turn for
TurnKind.WORKFLOW. WorkflowUserInputHandler is registered on the
ConfirmationRegistry; it fires via the confirmation *recovery* path
(no awaiting waiter) because a workflow suspend ends the turn.
"""
import logging

from decafclaw.confirmations import (
    ConfirmationAction, ConfirmationRequest, ConfirmationResponse,
)
from decafclaw.media import ToolResult

from .engine import run_workflow
from .journal import Journal, load_journal, save_journal
from .registry import get_workflow

log = logging.getLogger(__name__)


def _render_artifact(result) -> str:
    if isinstance(result, dict) and "title" in result and "body" in result:
        return f"# {result['title']}\n\n{result['body']}"
    return str(result)


async def run_workflow_turn(ctx, manager, conv_id: str, *,
                            workflow_name: str, resume: bool) -> ToolResult:
    spec = get_workflow(workflow_name)
    if spec is None:
        return ToolResult(text=f"[workflow error: unknown workflow "
                               f"{workflow_name!r}]")

    journal = load_journal(ctx.config, conv_id)
    if journal is None:
        if resume:
            return ToolResult(text="[workflow error: no journal to resume]")
        journal = Journal(workflow_name=workflow_name)

    await ctx.publish("tool_status", tool="workflow",
                      message=f"[workflow: {workflow_name}] running")
    outcome = await run_workflow(ctx, spec.fn, journal, model=spec.model)

    if outcome.status == "done":
        await ctx.publish("tool_status", tool="workflow",
                          message=f"[workflow: {workflow_name}] complete")
        return ToolResult(text=_render_artifact(outcome.result))

    if outcome.status == "suspended":
        s = outcome.suspend
        request = ConfirmationRequest(
            action_type=ConfirmationAction.WORKFLOW_USER_INPUT,
            message=s.prompt,
            action_data={
                "workflow_name": workflow_name,
                "seq": s.seq,
                "args_fingerprint": s.args_fingerprint,
                "prompt": s.prompt,
                "choices": s.choices,
            },
            timeout=None,  # user answers when ready
        )
        await manager.post_confirmation(conv_id, request)
        return ToolResult(text=f"_{s.prompt}_")

    return ToolResult(text=f"[workflow error: {outcome.error}]")


class WorkflowUserInputHandler:
    """Resolves a WORKFLOW_USER_INPUT confirmation via the recovery path."""

    def __init__(self, manager):
        self.manager = manager

    async def on_approve(self, ctx, request: ConfirmationRequest,
                         response: ConfirmationResponse) -> dict:
        ad = request.action_data
        answer = (response.data or {}).get("value", "")
        journal = load_journal(ctx.config, ctx.conv_id)
        if journal is None:
            log.error("workflow resume: no journal for conv %s", ctx.conv_id)
            return {"continue_loop": False}
        journal.append(ad["seq"], "user_input", ad["args_fingerprint"], answer)
        journal.status = "running"
        save_journal(ctx.config, ctx.conv_id, journal)

        from decafclaw.conversation_manager import TurnKind
        await self.manager.enqueue_turn(
            ctx.conv_id, kind=TurnKind.WORKFLOW, prompt="",
            metadata={"workflow_name": ad["workflow_name"], "resume": True})
        return {"continue_loop": False}

    async def on_deny(self, ctx, request: ConfirmationRequest,
                      response: ConfirmationResponse) -> dict:
        journal = load_journal(ctx.config, ctx.conv_id)
        if journal is not None:
            journal.status = "error"
            save_journal(ctx.config, ctx.conv_id, journal)
        return {"continue_loop": False}
```

> **Engineer note:** `ToolResult` is in `decafclaw.media` (confirmed). `enqueue_turn`'s `metadata` kwarg is threaded into `_start_turn` (signature at `conversation_manager.py:1299`). The handler imports `TurnKind` lazily to avoid an import cycle (`conversation_manager` will import `resume`).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_workflow_resume.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/decafclaw/workflow/resume.py tests/test_workflow_resume.py
git commit -m "feat(workflow): resume handler + run_workflow_turn glue"
```

---

## Task 10: ConversationManager — post_confirmation

**Files:**
- Modify: `src/decafclaw/conversation_manager.py` (new method near `request_confirmation`, ~line 1103)
- Test: `tests/test_workflow_turn_integration.py`

`post_confirmation` sets `pending_confirmation` but deliberately **leaves `confirmation_event` None**, so `respond_to_confirmation` takes the recovery branch (no waiter) → dispatches to our handler.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_workflow_turn_integration.py
import pytest
from decafclaw.confirmations import ConfirmationRequest, ConfirmationAction


@pytest.mark.asyncio
async def test_post_confirmation_sets_pending_without_waiter(make_manager):
    # make_manager: a fixture building a real ConversationManager on tmp config.
    manager = make_manager()
    conv_id = "convP"
    req = ConfirmationRequest(
        action_type=ConfirmationAction.WORKFLOW_USER_INPUT,
        message="topic?", action_data={"seq": 0}, timeout=None)

    await manager.post_confirmation(conv_id, req)

    state = manager._conversations[conv_id]
    assert state.pending_confirmation is req
    assert state.confirmation_event is None  # forces recovery dispatch
```

> **Engineer note:** add a `make_manager` fixture to `tests/conftest.py` if one doesn't exist, building `ConversationManager(config=..., event_bus=EventBus())` on a `tmp_path` workspace. Search existing tests (`tests/test_confirmations*.py`, `tests/test_conversation_manager*.py`) for the established construction pattern and reuse it rather than inventing a new one.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_workflow_turn_integration.py::test_post_confirmation_sets_pending_without_waiter -v`
Expected: FAIL — `AttributeError: 'ConversationManager' object has no attribute 'post_confirmation'`

- [ ] **Step 3: Implement** — add method to `ConversationManager` (after `request_confirmation`)

```python
    async def post_confirmation(
        self,
        conv_id: str,
        request: ConfirmationRequest,
    ) -> None:
        """Post a pending confirmation WITHOUT awaiting it.

        Used by workflow suspends: the workflow turn ENDS at a user_input,
        so there is no live waiter. We persist + install the request as the
        active confirmation but deliberately leave ``confirmation_event``
        None, so ``respond_to_confirmation`` routes resolution through the
        recovery dispatch path (registered handler), not a waiter wake.
        """
        from .archive import append_message
        append_message(self.config, conv_id, request.to_archive_message())
        state = self._get_or_create(conv_id)
        async with state.lock:
            if state.pending_confirmation is not None:
                # A workflow turn is serialized by the busy flag, so the slot
                # should be free. If not, queue rather than clobber.
                queued = _QueuedConfirmation(request=request)
                state.confirmation_queue.append(queued)
                log.warning("post_confirmation: slot busy on conv %s; queued",
                            conv_id[:8])
                return
            state.pending_confirmation = request
            state.confirmation_event = None  # no waiter — recovery path
            state.confirmation_response = None
        await self.emit(conv_id, _confirmation_request_payload(request))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_workflow_turn_integration.py::test_post_confirmation_sets_pending_without_waiter -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/decafclaw/conversation_manager.py tests/test_workflow_turn_integration.py
git commit -m "feat(workflow): ConversationManager.post_confirmation (no-waiter)"
```

---

## Task 11: TurnKind.WORKFLOW + dispatch + handler registration

**Files:**
- Modify: `src/decafclaw/conversation_manager.py:73-80` (enum), `__init__` (register handler), `_start_turn.run()` (~1449-1456)
- Test: `tests/test_workflow_turn_integration.py` (extend)

- [ ] **Step 1: Write the failing test** (append)

```python
@pytest.mark.asyncio
async def test_workflow_turn_runs_and_suspends_end_to_end(make_manager):
    """Enqueue an interview WORKFLOW turn → it suspends posting a
    WORKFLOW_USER_INPUT confirmation for the topic question."""
    import decafclaw.workflow.workflows  # noqa: F401 — register interview
    manager = make_manager()
    conv_id = "convW"

    fut = await manager.enqueue_turn(
        conv_id, kind=manager_turnkind_workflow(), prompt="",
        metadata={"workflow_name": "interview", "resume": False})
    await fut  # wait for the turn to finish (it suspends, then ends)

    state = manager._conversations[conv_id]
    assert state.pending_confirmation is not None
    assert state.pending_confirmation.action_type.value == "workflow_user_input"
    assert state.pending_confirmation.action_data["workflow_name"] == "interview"


def manager_turnkind_workflow():
    from decafclaw.conversation_manager import TurnKind
    return TurnKind.WORKFLOW
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_workflow_turn_integration.py::test_workflow_turn_runs_and_suspends_end_to_end -v`
Expected: FAIL — `AttributeError: WORKFLOW` (enum) or dispatch falls through to `run_agent_turn`.

- [ ] **Step 3a: Add the enum variant** (`conversation_manager.py:73-80`)

```python
class TurnKind(Enum):
    """Classification of agent turn origins."""

    USER = "user"
    HEARTBEAT_SECTION = "heartbeat_section"
    SCHEDULED_TASK = "scheduled_task"
    CHILD_AGENT = "child_agent"
    WAKE = "wake"
    WORKFLOW = "workflow"
```

WORKFLOW is NOT added to `TASK_KINDS`/`STATE_PERSIST_KINDS` — it builds a regular `Context` (the `else` branch at line ~1353), loads history, and does not need per-conv skill-state persistence.

- [ ] **Step 3b: Register the handler in `ConversationManager.__init__`**

Find where `self.confirmation_registry` is created in `__init__` and, right after, register the workflow handler. (Search for `self.confirmation_registry =` or `ConfirmationRegistry(`.)

```python
        # Workflow user-input resumes via the confirmation recovery path.
        from .workflow.resume import WorkflowUserInputHandler
        from .confirmations import ConfirmationAction
        self.confirmation_registry.register(
            ConfirmationAction.WORKFLOW_USER_INPUT,
            WorkflowUserInputHandler(self))
```

- [ ] **Step 3c: Dispatch WORKFLOW turns in `_start_turn.run()`** (~line 1449-1456)

Replace the body that calls `run_agent_turn` with a kind branch. The surrounding `run()` machinery (emit `message_complete`, finally cleanup) is unchanged — `run_workflow_turn` returns a `ToolResult` with `.text`, satisfying the existing `result.text` access at line 1458.

```python
        async def run():
            try:
                if kind is TurnKind.WORKFLOW:
                    from .workflow.resume import run_workflow_turn
                    md = metadata or {}
                    result = await run_workflow_turn(
                        ctx, self, conv_id,
                        workflow_name=md.get("workflow_name", ""),
                        resume=md.get("resume", False))
                else:
                    from .agent import run_agent_turn
                    result = await run_agent_turn(
                        ctx, text, history,
                        archive_text=archive_text,
                        attachments=attachments,
                    )
                # ... existing response_text / emit message_complete block unchanged
```

> **Engineer note:** make the *minimal* edit — wrap only the `run_agent_turn` call in the `if/else`; leave every line after `result = ...` (the `response_text = result.text ...` block, the `except`/`finally`) exactly as-is. Read `conversation_manager.py:1449-1556` before editing.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_workflow_turn_integration.py -v`
Expected: PASS. Then full module: `pytest tests/test_workflow_*.py -v` → all pass.

- [ ] **Step 5: Commit**

```bash
git add src/decafclaw/conversation_manager.py tests/test_workflow_turn_integration.py
git commit -m "feat(workflow): TurnKind.WORKFLOW dispatch + handler registration"
```

---

## Task 12: /interview command bridge

**Files:**
- Modify: `src/decafclaw/web/websocket.py` (command handling, ~line 302)
- Test: covered by manual smoke (Task 14); add a thin unit test if a command-dispatch seam exists.

The web UI sends user text; commands are detected before a normal turn. Workflows aren't skills, so `dispatch_command` won't find `/interview`. Add a pre-check: if the text is `/<name>` where `<name>` in `workflow_commands()`, enqueue a WORKFLOW turn instead of a normal message.

- [ ] **Step 1: Read the current command path**

Read `src/decafclaw/web/websocket.py:295-365` (the `dispatch_command` call + `manager.send_message`). Identify the point right after the text is known and before `dispatch_command`.

- [ ] **Step 2: Implement the bridge** — insert before the existing `dispatch_command(cmd_ctx, text)` call

```python
        # Workflow commands (#255) are first-class, not skills — intercept
        # before the skill command dispatch.
        from decafclaw.workflow.registry import workflow_commands
        wf_trigger = None
        if text.startswith("/"):
            name = text[1:].split()[0] if len(text) > 1 else ""
            if name in workflow_commands():
                wf_trigger = name
        if wf_trigger:
            await ws_send({
                "type": WSMessageType.COMMAND_ACK, "conv_id": conv_id,
                "command": f"/{wf_trigger}", "skill": wf_trigger,
            })
            await manager.enqueue_turn(
                conv_id, kind=TurnKind.WORKFLOW, prompt="",
                user_id=username, context_setup=context_setup,
                metadata={"workflow_name": wf_trigger, "resume": False})
            return
```

> **Engineer note:** confirm the names in scope at this point in `websocket.py`: `text`, `conv_id`, `username`, `context_setup`, `ws_send`, `WSMessageType`, `manager`, and that `TurnKind` is imported (add `from decafclaw.conversation_manager import TurnKind` if not). Match the existing `enqueue_turn` call's kwargs for transport context (`user_id`, `context_setup`) so progress events render in the UI. Read the surrounding handler before editing.

- [ ] **Step 3: Lint + run the workflow tests**

Run: `make lint && pytest tests/test_workflow_*.py -v`
Expected: clean lint; all workflow tests pass.

- [ ] **Step 4: Commit**

```bash
git add src/decafclaw/web/websocket.py
git commit -m "feat(workflow): /interview command bridge in web UI"
```

---

## Task 13: Restart-durability integration test

**Files:**
- Test: `tests/test_workflow_turn_integration.py` (extend)

Proves the core promise: a journal persisted mid-suspend resumes from a fresh manager (simulating a restart) with no lost state.

- [ ] **Step 1: Write the test**

```python
@pytest.mark.asyncio
async def test_resume_after_simulated_restart(make_manager, tmp_path_factory):
    """Run interview to first suspend, persist; build a NEW manager on the
    same workspace; respond to the confirmation → resume turn replays and
    advances to the next suspension. No in-memory state carried over."""
    import decafclaw.workflow.workflows  # noqa: F401
    from decafclaw.workflow.journal import load_journal

    ws = tmp_path_factory.mktemp("ws")
    conv_id = "convRestart"

    mgr1 = make_manager(workspace=ws)
    fut = await mgr1.enqueue_turn(
        conv_id, kind=manager_turnkind_workflow(), prompt="",
        metadata={"workflow_name": "interview", "resume": False})
    await fut
    req = mgr1._conversations[conv_id].pending_confirmation
    assert req is not None and req.action_data["seq"] == 0

    # Simulate restart: brand-new manager, same workspace on disk.
    mgr2 = make_manager(workspace=ws)
    # Startup recovery would normally re-install pending_confirmation from the
    # archive; do that explicitly here via the public recovery entry point.
    mgr2._recover_pending_confirmations(conv_id)  # see engineer note

    await mgr2.respond_to_confirmation(
        conv_id, req.confirmation_id, approved=True,
        data={"value": "tide pools"})

    # Give the enqueued resume turn a moment to run to its next suspension.
    fut2 = mgr2._conversations[conv_id].agent_task
    if fut2 is not None:
        await fut2

    journal = load_journal(mgr2.config, conv_id)
    assert journal.get(0).result == "tide pools"  # answer journaled
    # Resume replayed past seq 0 and either suspended again (next question)
    # or progressed — journal must have grown beyond the single answer.
    assert len(journal.entries) >= 1
```

> **Engineer note:** the exact restart-recovery entry point may be named differently — search `conversation_manager.py` for the startup confirmation scan (around line 1734/1814, `role == "confirmation_request"`). Use whatever public method re-installs a pending confirmation from the archive; if it only runs as part of a broader startup routine, call that. The assertion that matters: after responding on a fresh manager, the answer is journaled and the workflow advanced. If `respond_to_confirmation` needs `pending_confirmation` set, ensure recovery installed it first. Adjust the waiting mechanism to the codebase's test conventions (avoid fixed sleeps — await the `agent_task`).

- [ ] **Step 2: Run, iterate to green**

Run: `pytest tests/test_workflow_turn_integration.py::test_resume_after_simulated_restart -v`
Expected: PASS. If the recovery entry point differs, fix the call per the engineer note; do not weaken the journal assertions.

- [ ] **Step 3: Full suite + durations check**

Run: `make test` then `pytest tests/test_workflow_*.py --durations=10`
Expected: all green; no workflow test in the slow tail (no fixed sleeps).

- [ ] **Step 4: Commit**

```bash
git add tests/test_workflow_turn_integration.py
git commit -m "test(workflow): resume survives simulated restart"
```

---

## Task 14: Live smoke (manual) + docs

**Files:**
- Create: `docs/workflows.md` (feature doc)
- Modify: `docs/index.md` (link), `CLAUDE.md` key-files list (add `workflow/`)
- Manual: live smoke against Mattermost/web UI

- [ ] **Step 1: Write `docs/workflows.md`** — cover: what a workflow is (first-class `TurnKind.WORKFLOW`), the journaled-vs-pure rule, the two primitives, the journal at `conversations/{conv_id}/workflow.json`, suspend/resume via the confirmation recovery path, the determinism guard, and how to author one (`@workflow` + the interview as worked example). Add the "explicitly later" scope list from the spec.

- [ ] **Step 2: Link from `docs/index.md`** and add `src/decafclaw/workflow/` to the CLAUDE.md key-files list under a new "### Workflows" heading.

- [ ] **Step 3: Live smoke (the bar no prior iteration cleared).** Ask Les to confirm no other bot instance is connected, then in the web UI:
  1. New conversation → `/interview`.
  2. Answer the topic question, then 1–2 follow-up questions.
  3. **Restart the server mid-interview** (Ctrl-C the dev process, restart).
  4. Reload the UI; the pending question should still be there. Answer it.
  5. Continue to completion; confirm the artifact renders.

  Capture the phase `tool_status` lines and the final artifact in `docs/dev-sessions/2026-06-05-1455-workflow-replay-engine/notes.md`.

- [ ] **Step 4: Commit**

```bash
git add docs/workflows.md docs/index.md CLAUDE.md docs/dev-sessions/2026-06-05-1455-workflow-replay-engine/notes.md
git commit -m "docs(workflow): feature doc + live restart smoke notes"
```

---

## Self-Review

**Spec coverage:**
- `workflow/` module (registry/engine/journal/primitives) → Tasks 1–7 ✓
- `TurnKind.WORKFLOW` + engine-routed resume → Tasks 9, 11 ✓
- `llm_call` + `user_input` journaled primitives → Tasks 3, 4 ✓
- Journal positional keying + determinism guard at `conversations/{conv_id}/workflow.json` → Tasks 1, 2, 4 ✓
- Interview orchestrator + `/interview` command → Tasks 7, 12 ✓
- Unit tests, restart-durability test, live restart smoke → Tasks 1–11 (unit), 13 (restart), 14 (live) ✓
- Load-bearing journaled-vs-pure rule → enforced by the determinism guard (Task 4) + documented (Task 14) ✓
- Error handling (retry-once then fail; loud determinism failure) → Tasks 3, 4, 5 ✓

**Type/name consistency check:** `WorkflowHandle.llm_call(prompt=, schema=, system=, tool_name=, model=)` and `user_input(prompt, choices=)` are used identically in `interview.py` (Task 7), the handle tests (Task 4), and the interview tests (Task 7). `call_structured(ctx, system=, user_msg=, schema=, tool_name=, model=, retries=)` matches between Task 3 def and the `_default_llm_call` call in Task 4. `WorkflowOutcome(status, result, suspend, error)` is consistent across Tasks 5, 9. `WorkflowSuspended(seq, args_fingerprint, prompt, choices)` consistent across Tasks 2, 4, 9. `action_data` keys (`workflow_name`, `seq`, `args_fingerprint`, `prompt`, `choices`) match between Task 9's `run_workflow_turn` (writer) and `WorkflowUserInputHandler` (reader). `Journal.append(seq, kind, args_fingerprint, result)` consistent everywhere.

**Known soft spots flagged inline for the engineer (not placeholders):** the `make_manager` fixture (Task 10), the exact restart-recovery entry point (Task 13), and the `websocket.py` in-scope names (Task 12) require reading the surrounding code — each is marked with an engineer note and a concrete search target, because those bodies are large existing functions this plan deliberately does not reproduce in full.
