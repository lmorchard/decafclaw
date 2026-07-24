# Diagnostic-Discipline Guardrails Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Break autonomous within-turn thrash loops with a mechanical loop-breaker (nudge → hard-stop), and sharpen `AGENT.md` prompt guardrails against the apology spiral, phantom tool calls, and repeated-edit thrashing.

**Architecture:** A per-turn `LoopBreaker` (new `loop_breaker.py`) records each iteration's tool-call signatures + error flags; its `verdict()` escalates NONE → NUDGE → STOP. `TurnRunner._handle_tool_calls` feeds it after every `execute_tool_calls` and acts on the verdict (inject a diagnostic system message, or finalize the turn). Config lives in a top-level `config.loop_breaker`. Prompt guardrails are universal edits to `AGENT.md`.

**Tech Stack:** Python (stdlib `hashlib`/`json` for fingerprints), existing `TurnRunner` loop in `agent.py`, `load_sub_config` config pattern, the YAML eval framework.

## Global Constraints

- **No model-conditional prompting.** All `AGENT.md` guardrails are universal.
- **Per-turn state only.** The `LoopBreaker` resets every turn; no cross-turn persistence.
- **Trip on either signal:** same `(tool_name, args_fingerprint)` ≥ `repeat_threshold`, OR ≥ `error_threshold` of the last `error_window` results are errors.
- **Escalation:** first trip → inject one diagnostic `system` nudge; next trip after nudging → hard-stop the turn with a summary.
- **Config defaults (verbatim):** `enabled=True`, `repeat_threshold=3`, `error_threshold=4`, `error_window=6`. Top-level `config.loop_breaker` (NOT nested under `config.agent`).
- **Config style:** new runtime state on the dataclass; wire via `load_sub_config`; never hand-enumerate fields.
- **Errors are marked** by tool-result message `content` starting with `"[error"` (see `tool_execution.py:356`, `ToolResult(text="[error: ...]")`).
- **Mode 2 (phantom call) is prompt-only.** No mechanical detection.
- Commit after each task; `make lint && make test` before committing; `make check` clean.

---

## File Structure

**New:**
- `src/decafclaw/loop_breaker.py` — `LoopBreaker` class + `LoopVerdict` enum. Pure, per-turn detector + escalation state machine. No agent/LLM imports.
- `tests/test_loop_breaker.py` — deterministic unit tests for the detector + escalation.
- `evals/fixtures/broken_skill/SKILL.md` — a deliberately-unloadable skill fixture (bad frontmatter / missing required field) used by the eval.
- `evals/diagnostic_discipline.yaml` — one eval case asserting the breaker caps edit-thrashing.

**Modified:**
- `src/decafclaw/config_types.py` — add `LoopBreakerConfig` dataclass.
- `src/decafclaw/config.py` — `loop_breaker` field on `Config` + `load_sub_config` wiring.
- `src/decafclaw/agent.py` — instantiate `LoopBreaker` per turn; in `_handle_tool_calls`, extract signatures + feed the breaker + act on verdict; add `_finalize_loop_break`.
- `src/decafclaw/prompts/AGENT.md` — sharpen mode-1 diagnosis block, add mode-3 connector line, add mode-2 phantom-call guardrail.
- `docs/agent-behavior.md` (or nearest existing behavior doc) + `CLAUDE.md` — document the loop-breaker + config.
- `docs/dev-sessions/2026-07-23-1506-598-diagnostic-guardrails/notes.md` — session notes.

---

## Task 1: `LoopBreakerConfig`

**Files:**
- Modify: `src/decafclaw/config_types.py`
- Modify: `src/decafclaw/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `config.loop_breaker: LoopBreakerConfig` with `enabled: bool`, `repeat_threshold: int`, `error_threshold: int`, `error_window: int`.

- [ ] **Step 1: Write failing tests** in `tests/test_config.py`:

```python
def test_loop_breaker_config_defaults():
    from decafclaw.config import load_config
    cfg = load_config()
    assert cfg.loop_breaker.enabled is True
    assert cfg.loop_breaker.repeat_threshold == 3
    assert cfg.loop_breaker.error_threshold == 4
    assert cfg.loop_breaker.error_window == 6


def test_loop_breaker_config_env_override(monkeypatch):
    from decafclaw.config import load_config
    monkeypatch.setenv("LOOP_BREAKER_ENABLED", "false")
    monkeypatch.setenv("LOOP_BREAKER_REPEAT_THRESHOLD", "2")
    cfg = load_config()
    assert cfg.loop_breaker.enabled is False
    assert cfg.loop_breaker.repeat_threshold == 2
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/test_config.py -k loop_breaker -v`
Expected: FAIL — `AttributeError: 'Config' object has no attribute 'loop_breaker'`.

- [ ] **Step 3: Add the dataclass** in `config_types.py` (near `HttpConfig`):

```python
@dataclass
class LoopBreakerConfig:
    enabled: bool = True
    repeat_threshold: int = 3      # same (tool, args) N times → trip
    error_threshold: int = 4       # this many errors...
    error_window: int = 6          # ...within the last N tool results → trip
```

- [ ] **Step 4: Wire into `config.py`.** Add `LoopBreakerConfig` to the `from .config_types import (...)` block; add the field to `Config` (near `http`):

```python
    loop_breaker: "LoopBreakerConfig" = field(default_factory=LoopBreakerConfig)
```

In `load_config`, next to the other `load_sub_config` calls:

```python
    loop_breaker = load_sub_config(LoopBreakerConfig, file_data.get("loop_breaker", {}), "LOOP_BREAKER")
```

Pass `loop_breaker=loop_breaker` into the `Config(...)` constructor.

- [ ] **Step 5: Run to verify pass**

Run: `.venv/bin/pytest tests/test_config.py -k loop_breaker -v` → PASS.

- [ ] **Step 6: Lint + commit**

```bash
make lint
git add src/decafclaw/config_types.py src/decafclaw/config.py tests/test_config.py
git commit -m "feat(loop-breaker): LoopBreakerConfig top-level config (#598)"
```

---

## Task 2: `LoopBreaker` detector + escalation (deterministic core)

**Files:**
- Create: `src/decafclaw/loop_breaker.py`
- Test: `tests/test_loop_breaker.py`

**Interfaces:**
- Produces:
  - `class LoopVerdict(enum.Enum): NONE; NUDGE; STOP`
  - `def fingerprint(tool_name: str, args) -> str` — stable hash of `(tool_name, args)`.
  - `class LoopBreaker`:
    - `__init__(self, config)` where `config` is a `LoopBreakerConfig`.
    - `record(self, calls: list[tuple[str, str, bool]]) -> None` — each tuple is `(tool_name, fingerprint, is_error)` for one iteration's calls.
    - `verdict(self) -> LoopVerdict` — evaluates trip conditions + escalation; returns NONE/NUDGE/STOP and advances internal escalation state.
    - `last_signal(self) -> str` — human-readable reason for the last non-NONE verdict (for the nudge/summary text), e.g. `"called workspace_edit 3× with the same args"` or `"4 of the last 6 tool results were errors"`.

- [ ] **Step 1: Write failing unit tests** in `tests/test_loop_breaker.py`:

```python
from decafclaw.config_types import LoopBreakerConfig
from decafclaw.loop_breaker import LoopBreaker, LoopVerdict, fingerprint


def _lb(**kw):
    cfg = LoopBreakerConfig(**kw)
    return LoopBreaker(cfg)


def test_fingerprint_stable_and_arg_sensitive():
    assert fingerprint("edit", {"a": 1, "b": 2}) == fingerprint("edit", {"b": 2, "a": 1})
    assert fingerprint("edit", {"a": 1}) != fingerprint("edit", {"a": 2})
    assert fingerprint("edit", {"a": 1}) != fingerprint("read", {"a": 1})


def test_repeat_threshold_trips_nudge_then_stop():
    lb = _lb(repeat_threshold=3, error_threshold=99, error_window=6)
    fp = fingerprint("edit", {"path": "x"})
    lb.record([("edit", fp, False)])
    assert lb.verdict() is LoopVerdict.NONE          # 1 occurrence
    lb.record([("edit", fp, False)])
    assert lb.verdict() is LoopVerdict.NONE           # 2
    lb.record([("edit", fp, False)])
    assert lb.verdict() is LoopVerdict.NUDGE           # 3 → first trip
    lb.record([("edit", fp, False)])
    assert lb.verdict() is LoopVerdict.STOP            # trips again after nudge


def test_error_window_trips():
    lb = _lb(repeat_threshold=99, error_threshold=4, error_window=6)
    # 3 distinct erroring calls, then a 4th → 4 errors in window
    for i in range(3):
        lb.record([(f"t{i}", fingerprint(f"t{i}", {}), True)])
        assert lb.verdict() is LoopVerdict.NONE
    lb.record([("t3", fingerprint("t3", {}), True)])
    assert lb.verdict() is LoopVerdict.NUDGE


def test_errors_outside_window_do_not_trip():
    lb = _lb(repeat_threshold=99, error_threshold=3, error_window=3)
    lb.record([("a", "fa", True)])
    lb.verdict()
    lb.record([("b", "fb", False)])
    lb.verdict()
    lb.record([("c", "fc", False)])
    lb.verdict()
    lb.record([("d", "fd", True)])  # window now [b?,c,d] errors=1 (a aged out)
    assert lb.verdict() is LoopVerdict.NONE


def test_disabled_never_trips():
    lb = _lb(enabled=False, repeat_threshold=1, error_threshold=1, error_window=1)
    lb.record([("edit", "fp", True)])
    assert lb.verdict() is LoopVerdict.NONE


def test_last_signal_describes_reason():
    lb = _lb(repeat_threshold=2, error_threshold=99, error_window=6)
    fp = fingerprint("edit", {"p": "x"})
    lb.record([("edit", fp, False)]); lb.verdict()
    lb.record([("edit", fp, False)])
    assert lb.verdict() is LoopVerdict.NUDGE
    assert "edit" in lb.last_signal()
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/test_loop_breaker.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'decafclaw.loop_breaker'`.

- [ ] **Step 3: Implement `loop_breaker.py`**

```python
"""Per-turn loop-breaker: detects autonomous tool-call thrash and escalates
a diagnostic nudge, then a hard stop. Pure/deterministic — no agent or LLM
imports; driven by TurnRunner. See docs/dev-sessions/.../spec.md (#598)."""

import enum
import hashlib
import json


class LoopVerdict(enum.Enum):
    NONE = "none"
    NUDGE = "nudge"
    STOP = "stop"


def fingerprint(tool_name: str, args) -> str:
    """Stable hash of a tool call's name + arguments (order-insensitive)."""
    try:
        arg_repr = json.dumps(args, sort_keys=True, default=str)
    except (TypeError, ValueError):
        arg_repr = repr(args)
    return hashlib.sha1(f"{tool_name}\x00{arg_repr}".encode()).hexdigest()


class LoopBreaker:
    def __init__(self, config):
        self._cfg = config
        self._counts: dict[str, int] = {}          # fingerprint → times seen
        self._recent_errors: list[bool] = []       # rolling is_error flags
        self._nudged = False
        self._last_signal = ""

    def record(self, calls) -> None:
        """calls: iterable of (tool_name, fingerprint, is_error) for one iteration."""
        for tool_name, fp, is_error in calls:
            self._counts[fp] = self._counts.get(fp, 0) + 1
            self._counts_name = tool_name  # last seen name, for messaging
            self._last_fp = fp
            self._recent_errors.append(bool(is_error))
        # Trim the error window.
        window = self._cfg.error_window
        if len(self._recent_errors) > window:
            self._recent_errors = self._recent_errors[-window:]

    def _tripped_reason(self) -> str | None:
        # Repeated identical call?
        top_fp, top_n = None, 0
        for fp, n in self._counts.items():
            if n > top_n:
                top_fp, top_n = fp, n
        if top_n >= self._cfg.repeat_threshold:
            return f"called the same tool {top_n}× with identical args"
        # Repeated errors in the window?
        errs = sum(self._recent_errors)
        if errs >= self._cfg.error_threshold:
            return f"{errs} of the last {len(self._recent_errors)} tool results were errors"
        return None

    def verdict(self) -> LoopVerdict:
        if not self._cfg.enabled:
            return LoopVerdict.NONE
        reason = self._tripped_reason()
        if reason is None:
            return LoopVerdict.NONE
        self._last_signal = reason
        if not self._nudged:
            self._nudged = True
            return LoopVerdict.NUDGE
        return LoopVerdict.STOP

    def last_signal(self) -> str:
        return self._last_signal
```

Note on `test_last_signal`: the reason string says "the same tool"; the test asserts `"edit" in lb.last_signal()`. Adjust the reason to include the tool name — track the name alongside the fingerprint count (e.g. store `{fp: (name, count)}`) so the message can name the tool. Update the implementation to thread the tool name into `_tripped_reason` so the assertion holds; keep the test as the contract.

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/pytest tests/test_loop_breaker.py -v`
Expected: PASS (all cases). Fix the tool-name threading if `test_last_signal_describes_reason` fails.

- [ ] **Step 5: Lint + commit**

```bash
make lint
git add src/decafclaw/loop_breaker.py tests/test_loop_breaker.py
git commit -m "feat(loop-breaker): per-turn detector + nudge/stop escalation (#598)"
```

---

## Task 3: Wire `LoopBreaker` into `TurnRunner`

**Files:**
- Modify: `src/decafclaw/agent.py` (`TurnRunner.run`, `_handle_tool_calls`, new `_finalize_loop_break`)
- Test: `tests/test_agent_loop_breaker.py`

**Interfaces:**
- Consumes: `LoopBreaker`, `LoopVerdict`, `fingerprint` (Task 2); `config.loop_breaker` (Task 1); existing `_Final`, `_Continue`, `_archive`, `execute_tool_calls`.
- Produces: loop-breaker behavior in the live turn loop.

- [ ] **Step 1: Add a pure signature-extraction helper + its test.**

In `agent.py` (module-level helper):

```python
def _extract_call_signatures(tool_calls, messages):
    """Map each tool_call to (tool_name, fingerprint, is_error) using the
    tool-result messages just appended by execute_tool_calls. Errors are
    tool-role messages whose content starts with '[error'."""
    from .loop_breaker import fingerprint
    results_by_id = {
        m.get("tool_call_id"): (m.get("content") or "")
        for m in messages if m.get("role") == "tool"
    }
    out = []
    for tc in tool_calls:
        fn = tc.get("function", {})
        name = fn.get("name", "")
        raw_args = fn.get("arguments", "")
        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
        except (TypeError, ValueError):
            args = raw_args
        content = results_by_id.get(tc.get("id"), "")
        is_error = content.lstrip().startswith("[error")
        out.append((name, fingerprint(name, args), is_error))
    return out
```

In `tests/test_agent_loop_breaker.py`:

```python
from decafclaw.agent import _extract_call_signatures


def test_extract_signatures_flags_errors():
    tool_calls = [
        {"id": "1", "function": {"name": "edit", "arguments": '{"path": "x"}'}},
        {"id": "2", "function": {"name": "read", "arguments": '{"path": "y"}'}},
    ]
    messages = [
        {"role": "tool", "tool_call_id": "1", "content": "[error: bad edit]"},
        {"role": "tool", "tool_call_id": "2", "content": "ok"},
    ]
    sigs = _extract_call_signatures(tool_calls, messages)
    assert sigs[0][0] == "edit" and sigs[0][2] is True
    assert sigs[1][0] == "read" and sigs[1][2] is False
    assert sigs[0][1] != sigs[1][1]
```

Confirm the `tc` shape (`tc["function"]["name"]`, `tc["function"]["arguments"]`) against `execute_single_tool` / `_handle_single_tool_call` in `tool_execution.py`; adjust the accessors if the shape differs (some paths use `tc["name"]`). Handle both defensively (`fn.get("name") or tc.get("name", "")`).

Run: `.venv/bin/pytest tests/test_agent_loop_breaker.py::test_extract_signatures_flags_errors -v` → PASS.

- [ ] **Step 2: Instantiate the breaker per turn.** In `TurnRunner.run`, alongside `self.budget = IterationBudget(...)` (agent.py:512):

```python
        from .loop_breaker import LoopBreaker
        self.loop_breaker = LoopBreaker(self.config.loop_breaker)
```

- [ ] **Step 3: Feed the breaker + act on the verdict** in `_handle_tool_calls`, AFTER the `execute_tool_calls` call returns and before returning `_Continue` (agent.py ~657). Add:

```python
        from .loop_breaker import LoopVerdict
        sigs = _extract_call_signatures(tool_calls, self.messages)
        self.loop_breaker.record(sigs)
        verdict = self.loop_breaker.verdict()
        if verdict is LoopVerdict.NUDGE:
            nudge = {
                "role": "system",
                "content": (
                    f"[loop-breaker] You {self.loop_breaker.last_signal()} without "
                    "progress. STOP repeating it. Switch to root-cause diagnosis: "
                    "read the relevant logs, build a minimal repro, and re-check the "
                    "contract/interface before any further edits."
                ),
            }
            self.messages.append(nudge)
            _archive(self.ctx, nudge)
            await self.ctx.publish("loop_breaker", action="nudge",
                                   reason=self.loop_breaker.last_signal())
        elif verdict is LoopVerdict.STOP:
            await self.ctx.publish("loop_breaker", action="stop",
                                   reason=self.loop_breaker.last_signal())
            return _Final(result=await self._finalize_loop_break())
```

(Place this before the existing end-turn-signal handling returns, but do not let it override a genuine end-turn signal already produced this iteration — if `end_turn_signal` is truthy, honor that first; only run the loop-breaker check on the normal `_Continue` path.)

- [ ] **Step 4: Add `_finalize_loop_break`** (mirrors `_finalize_max_iterations`, agent.py:999):

```python
    async def _finalize_loop_break(self) -> "ToolResult":
        """Loop-breaker hard-stop: end the turn with a summary of the thrash
        and a diagnostic next step, preserving any accumulated text."""
        note = (
            f"\n\n[loop-breaker] Stopped: you {self.loop_breaker.last_signal()} "
            "without progress. Next: read the relevant logs, build a minimal "
            "repro, and re-check the contract before retrying."
        )
        accumulated = "\n\n".join(self.accumulated_text_parts)
        msg = accumulated + note if accumulated else note.strip()
        final_msg = {"role": "assistant", "content": msg}
        self.history.append(final_msg)
        _archive(self.ctx, final_msg)
        from .tool_execution import ToolResult
        return ToolResult(text=msg)
```

Match the exact `ToolResult` construction + any `_maybe_compact`/finalize bookkeeping that `_finalize_max_iterations` does; reuse that path rather than reinventing it (read agent.py:999-1020 and mirror it).

- [ ] **Step 5: Integration test** — drive a turn with a scripted LLM that repeats the same failing tool call; assert nudge then stop. In `tests/test_agent_loop_breaker.py`, patch `decafclaw.agent._call_llm_with_events` to return a fixed tool-call response repeatedly and stub `execute_tool_calls` to append an `[error: ...]` tool message. Assert: (a) a `system` message containing `[loop-breaker]` is injected after `repeat_threshold` iterations, and (b) the turn ends via `_finalize_loop_break` (returns a `ToolResult` whose text contains `[loop-breaker] Stopped`) rather than running to `max_tool_iterations`. Follow the existing `TurnRunner`/`run_agent_turn` test setup in `tests/` (grep for `TurnRunner(` or `run_agent_turn(` to find the harness + fixtures to reuse); if no such harness exists, test at the `_handle_tool_calls` level by constructing a `TurnRunner` with minimal stubbed `ctx`/`config`/`messages` and calling it directly. Report DONE_WITH_CONCERNS if the end-to-end harness is heavier than a focused `_handle_tool_calls` test warrants — the pure pieces (Task 2 + Step 1 helper) already carry the deterministic coverage.

- [ ] **Step 6: Run + lint + commit**

Run: `.venv/bin/pytest tests/test_agent_loop_breaker.py -v` → PASS; then `make lint && make test`.

```bash
git add src/decafclaw/agent.py tests/test_agent_loop_breaker.py
git commit -m "feat(loop-breaker): wire detector into TurnRunner (nudge + hard-stop) (#598)"
```

---

## Task 4: Prompt guardrails (`AGENT.md`)

**Files:**
- Modify: `src/decafclaw/prompts/AGENT.md`

**Verification:** prose edits — no unit test; validated by the eval (Task 5) + `make lint` (no code change). Read the current sections first (lines 50–114) so the edits match the surrounding voice/format.

- [ ] **Step 1: Sharpen the mode-1 diagnosis block.** Replace the "Name the pattern on repeated errors" paragraph (~line 103) so it ends with the explicit rule:

> **Two strikes → diagnose, don't re-edit.** After two failed attempts at the same fix, STOP editing and switch to root-cause diagnosis: read the relevant logs, build a minimal repro, and re-check the contract/interface. Repeating a failed edit a third time is the loop — break it by changing *what you're doing*, not by trying the same thing harder.

- [ ] **Step 2: Add the mode-3 connector** to the "Don't surrender" block (~lines 87–99), as a final line:

> Acknowledgement is not progress. Never open a turn with an apology and then repeat the same failed move — that's the loop wearing a disguise. One clause of acknowledgement at most, then a genuinely different move or a stop.

- [ ] **Step 3: Add the mode-2 phantom-call guardrail** under the tool-use section (near "Only call tools that exist", ~line 51):

> **Emit the call, don't narrate it.** Don't report an action as done until its tool call has actually fired in this turn. If you describe doing something — running a command, sending to Claude Code, editing a file — emit the call in the *same* turn. Never write out a plan to act and then stop or hand back without the call. Before you say "I've done X," confirm X's tool call is present in this turn.

- [ ] **Step 4: Verify + commit**

Run: `make lint` (unchanged code; sanity). Manually re-read the three edited sections for voice consistency.

```bash
git add src/decafclaw/prompts/AGENT.md
git commit -m "docs(guardrails): sharpen AGENT.md diagnosis/apology/phantom-call guidance (#598)"
```

---

## Task 5: Eval — deliberately-unloadable skill

**Files:**
- Create: `evals/fixtures/broken_skill/SKILL.md`
- Create: `evals/diagnostic_discipline.yaml`
- (Check: read `docs/eval-loop.md` + an existing `evals/*.yaml` for the exact schema — setup fields, `expect_tool_count_by_name`, `max_tool_calls`, fixture-loading convention.)

**Verification:** `make eval` (or the targeted eval runner) for this file; assert it runs and the assertion holds. This is LLM-driven — expect ~6–10 min + token cost.

- [ ] **Step 1: Create the broken-skill fixture.** `evals/fixtures/broken_skill/SKILL.md` with frontmatter that fails validation (e.g. missing the required `description`, or malformed YAML) so activating/using it reliably errors. Match how other eval fixtures are laid out (grep `evals/` for existing fixture skills; if evals load skills from a configured `extra_skill_paths`/workspace, place it where the eval harness will discover it).

- [ ] **Step 2: Write the eval case** `evals/diagnostic_discipline.yaml`:

```yaml
- name: loop_breaker_caps_edit_thrash
  setup:
    reflection_enabled: false
    # point the agent at the broken skill fixture per the harness's
    # skill-path convention (mirror an existing eval that loads a fixture skill)
  prompt: >
    Load and use the "broken_skill" skill. If it won't load, get it working.
  max_tool_calls: 20
  max_tool_errors: 20
  expect_tool_count_by_name:
    # the breaker + prompt must stop the agent from thrashing the editor;
    # cap well below what a 150-message loop would produce
    workspace_edit: { max: 5 }
```

Use the ACTUAL edit tool name(s) the agent would thrash (grep tool definitions — likely `workspace_edit` / `workspace_replace_lines`; cap each, or the most likely one). Keep the assertion on the mechanical outcome (bounded edit count) rather than fuzzy "did it read logs", per the spec's flakiness note.

- [ ] **Step 3: Run the eval**

Run the single eval file via the project's eval runner (see `docs/eval-loop.md` / `make eval-*`). Expected: the case passes — the agent does not exceed the edit-count cap. If it fails because the agent legitimately needs >5 edits, raise the cap to a value still far below loop territory and note the reasoning.

- [ ] **Step 4: Commit**

```bash
git add evals/fixtures/broken_skill/ evals/diagnostic_discipline.yaml
git commit -m "test(guardrails): eval — loop-breaker caps edit thrash on unloadable skill (#598)"
```

---

## Task 6: Docs

**Files:**
- Modify: `CLAUDE.md` (agent-behavior section + `loop_breaker.py` in key files)
- Modify/Create: the nearest `docs/` behavior page (check `docs/index.md`; likely `docs/reflection.md` sibling or a new `docs/loop-breaker.md`)
- Create: session `notes.md`

- [ ] **Step 1: Document the mechanism.** Add a short `docs/` section: what the loop-breaker is (per-turn thrash detector), the two trip signals, nudge→stop escalation, and `config.loop_breaker` fields + `LOOP_BREAKER_*` env. Link from `docs/index.md`.

- [ ] **Step 2: Update `CLAUDE.md`.** Add `loop_breaker.py` to the key-files "Other" list with a one-line description; add one bullet under "Agent behavior" describing the per-turn loop-breaker + that guardrail prompt text lives in `AGENT.md`.

- [ ] **Step 3: Write session `notes.md`** summarizing the design decisions (no model-conditional, both signals, escalate, mode-2 prompt-only, evals-skipped-for-modes-2/3) and the final state.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md docs/ docs/dev-sessions/2026-07-23-1506-598-diagnostic-guardrails/notes.md
git commit -m "docs(loop-breaker): document mechanism + config (#598)"
```

---

## Self-Review

**Spec coverage:**
- Loop-breaker mechanism (detector, both signals, escalation, per-turn, config) — Tasks 1–3. ✓
- Prompt guardrails modes 1/2/3 (universal, no model-conditional) — Task 4. ✓
- Config `config.loop_breaker` top-level (dodges nested-env gotcha) — Task 1. ✓
- Unit tests (deterministic) — Task 2 + Task 3 Step 1. ✓
- One bounded eval; modes 2/3 evals skipped — Task 5. ✓
- Docs — Task 6. ✓

**Placeholder scan:** Two deliberate "read the existing pattern and match it" instructions remain (Task 3 `tc` shape + finalize bookkeeping; Task 5 eval schema + fixture-loading convention) — these point at concrete live code to mirror, correct given they must match existing structure exactly. Task 2 Step 3 flags a known adjustment (thread tool name into the reason string) with the test as the contract.

**Type consistency:** `LoopVerdict` (NONE/NUDGE/STOP), `LoopBreaker.record/verdict/last_signal`, `fingerprint`, `_extract_call_signatures`, `_finalize_loop_break`, `config.loop_breaker` used consistently across Tasks 1–3. Error marker `"[error"` matches `tool_execution.py`.

**Known risk:** Task 3 Step 5's end-to-end `TurnRunner` test may be heavier than the harness supports; the plan authorizes falling back to a focused `_handle_tool_calls`-level test since the deterministic coverage lives in Task 2 + the extraction helper.
