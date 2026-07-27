# Loop-breaker Recovery Redirect Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the loop-breaker redirect a thrashing agent into a different action instead of only terminating it — fix the latching detector that hard-stops the agent even when it complies, add a grounded `REDIRECT` rung between nudge and stop, and stop re-delivering the whole turn on abnormal termination.

**Architecture:** `loop_breaker.py` stays pure and deterministic (no agent/LLM imports). Escalation moves from a latching predicate to per-signal watermarks, so a rung only advances on a *fresh* offense. The detector retains the offending call's arguments and error text (truncated) so `agent.py` can render redirect text that names the real failure. A three-rung ladder (`NUDGE` → `REDIRECT` → `STOP`) replaces the current two, with enforcement coming from the ladder itself rather than from any tool-list manipulation.

**Tech Stack:** Python 3.12+, stdlib only (`enum`, `dataclasses`, `typing.NamedTuple`, `hashlib`, `json`), pytest + pytest-asyncio, pytest-xdist.

**Spec:** [`spec.md`](spec.md) in this directory. **Issue:** [#707](https://github.com/lmorchard/decafclaw/issues/707).

## Global Constraints

- **`loop_breaker.py` must not import from `agent.py`, `llm/`, or any LLM path.** It is pure/deterministic by design (module docstring states this). All rendering helpers and size caps live in it; `agent.py` imports *from* it, never the reverse.
- **`LoopBreakerConfig` gains no new fields.** Rung count is fixed at three. Truncation caps are module constants: `_MAX_ARG_CHARS = 400`, `_MAX_ERROR_CHARS = 300`.
- **No compatibility shims for tests.** Per CLAUDE.md "No deprecated code for test compatibility" — when a signature changes, rewrite the affected tests in the same commit. Do not accept both old and new shapes.
- **Nudge and redirect messages are ephemeral.** Appended to `self.messages` only — never `self.history`, never `_archive(...)`. The hard-stop note *is* archived (it is a real assistant response).
- **Nudge and redirect are `user`-role and must disclaim authorship.** Both carry an explicit "the user did not send this" statement (#680). Do not drop this to shorten the text.
- **`verdict()` keeps its "call exactly once per recorded round" contract** and remains the only method that mutates escalation state.
- **Commit after each task.** Run `make lint && make test` before each commit.
- **Docs in the same PR** (CLAUDE.md): `docs/loop-breaker.md` is the feature's source of truth and must not lag.

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `src/decafclaw/loop_breaker.py` | Pure detector: fingerprinting, evidence retention, watermark escalation, ladder | Modify — the bulk of the work |
| `src/decafclaw/agent.py` | Wiring: signature extraction, rung dispatch, message text, finalizers | Modify (`_extract_call_signatures`, `_handle_tool_calls`, `_finalize_loop_break`, `_finalize_with_note`) |
| `tests/test_loop_breaker.py` | Detector unit tests | Modify — new watermark tests, rewrite tuple call sites |
| `tests/test_agent_loop_breaker.py` | `TurnRunner` wiring tests | Modify — three-rung ladder, delivery de-dup |
| `docs/loop-breaker.md` | Feature documentation | Modify — escalation, evidence, deferred rung, files |
| `evals/diagnostic_discipline.yaml` | Real-LLM bounds | Modify — headroom + stale quoted text |
| `CLAUDE.md` | Key-files one-liner | Modify — one line |

`config_types.py` is deliberately **not** in this list.

---

### Task 1: Escalate on fresh offenses, not standing conditions

This is the headline bug and lands alone so a reviewer can see it isolated from data-structure churn. No new verdicts, no new evidence, no agent changes.

**Files:**
- Modify: `src/decafclaw/loop_breaker.py:38-96`
- Test: `tests/test_loop_breaker.py`

**Interfaces:**
- Consumes: nothing (first task).
- Produces: `LoopBreaker._counts` becomes `dict[str, _Offender]` where `_Offender` is a module-private dataclass with fields `tool_name: str`, `count: int`, `last_tripped_count: int`. `record()` and `verdict()` keep their existing signatures. `last_signal() -> str` unchanged.

- [ ] **Step 1: Write the two failing tests**

Add to `tests/test_loop_breaker.py`. These are the discriminating cases the existing suite lacks — they exercise the *compliance* path, where a latching predicate and correct escalation differ.

```python
def test_compliance_after_nudge_does_not_trip_again():
    """#707: the round after a NUDGE must not trip when the agent obeyed.

    The old detector tested a standing condition (count >= threshold) that
    stayed true forever once crossed, so this round always escalated to STOP
    regardless of what the agent did — the agent was never given a round in
    which changing course could pay off.
    """
    lb = _lb(repeat_threshold=3, error_threshold=99, error_window=6)
    fp = fingerprint("edit", {"path": "x"})
    lb.record([("edit", fp, False)])
    assert lb.verdict() is LoopVerdict.NONE
    lb.record([("edit", fp, False)])
    assert lb.verdict() is LoopVerdict.NONE
    lb.record([("edit", fp, False)])
    assert lb.verdict() is LoopVerdict.NUDGE
    # The agent obeys: a different call, not the offending one.
    lb.record([("read", fingerprint("read", {"path": "y"}), False)])
    assert lb.verdict() is LoopVerdict.NONE


def test_error_surge_does_not_retrip_without_new_errors():
    """#707: a window still holding old errors must not re-trip on its own.

    _recent_errors is a rolling window, so after 4 errors one clean call
    leaves 4 errors still in a 6-wide window — enough to satisfy the old
    standing-condition test and stop a recovering agent.
    """
    lb = _lb(repeat_threshold=99, error_threshold=4, error_window=6)
    verdict = None
    for i in range(4):
        lb.record([(f"t{i}", fingerprint(f"t{i}", {}), True)])
        verdict = lb.verdict()
    assert verdict is LoopVerdict.NUDGE
    # The agent recovers: one clean call, no new errors.
    lb.record([("ok", fingerprint("ok", {}), False)])
    assert lb.verdict() is LoopVerdict.NONE


def test_repeating_the_same_call_after_nudge_does_escalate():
    """The reprieve is conditional: re-offending still advances the ladder."""
    lb = _lb(repeat_threshold=3, error_threshold=99, error_window=6)
    fp = fingerprint("edit", {"path": "x"})
    for _ in range(2):
        lb.record([("edit", fp, False)])
        assert lb.verdict() is LoopVerdict.NONE
    lb.record([("edit", fp, False)])
    assert lb.verdict() is LoopVerdict.NUDGE
    lb.record([("edit", fp, False)])  # count 3 -> 4: a fresh offense
    assert lb.verdict() is LoopVerdict.STOP
```

- [ ] **Step 2: Run the tests to verify the first two fail**

```bash
uv run pytest tests/test_loop_breaker.py -v -p no:xdist
```

Expected: `test_compliance_after_nudge_does_not_trip_again` FAILS (`LoopVerdict.STOP is not LoopVerdict.NONE`), `test_error_surge_does_not_retrip_without_new_errors` FAILS the same way, `test_repeating_the_same_call_after_nudge_does_escalate` PASSES already (it exercises the offense path, which was never broken).

- [ ] **Step 3: Replace the `[name, count]` list with an `_Offender` dataclass**

In `loop_breaker.py`, add `import dataclasses` to the existing stdlib imports (module level, per CLAUDE.md). Insert above `class LoopBreaker`:

```python
@dataclasses.dataclass
class _Offender:
    """Per-fingerprint tally, plus a watermark of where `count` stood the last
    time this fingerprint tripped the breaker.

    The watermark is what makes escalation event-shaped instead of
    state-shaped (#707): a fingerprint that has already tripped at count N
    only trips again once it reaches N+1, i.e. once the agent has repeated
    the call *again* after being told to stop.
    """
    tool_name: str
    count: int = 0
    last_tripped_count: int = 0
```

- [ ] **Step 4: Rewrite `__init__` and `record` to use it, and add the error watermark**

Replace the `__init__` body's state block and the `record` method:

```python
    def __init__(self, config):
        self._cfg = config
        self._counts: dict[str, _Offender] = {}
        self._recent_errors: list[bool] = []  # rolling is_error flags
        self._total_errors = 0                # monotonic; never trimmed
        self._errors_at_last_trip = 0         # watermark for the error signal
        self._trips = 0
        self._last_signal = ""

    def record(self, calls) -> None:
        """Record one iteration's tool calls.

        calls: iterable of (tool_name, fingerprint, is_error).
        """
        for tool_name, fp, is_error in calls:
            entry = self._counts.get(fp)
            if entry is None:
                entry = self._counts[fp] = _Offender(tool_name=tool_name)
            entry.tool_name = tool_name
            entry.count += 1
            self._recent_errors.append(bool(is_error))
            if is_error:
                self._total_errors += 1
        # Trim to a rolling window of the last N results.
        window = self._cfg.error_window
        if len(self._recent_errors) > window:
            self._recent_errors = self._recent_errors[-window:]
```

Note `_trips` replaces `_nudged` here so the escalation change is a single edit; it still only distinguishes first-trip from later-trip in this task.

- [ ] **Step 5: Replace `_tripped_reason` with watermark-aware signal checks**

Delete `_tripped_reason` entirely and replace it plus `verdict` with:

```python
    def _fresh_repeat_offender(self) -> "_Offender | None":
        """The worst fingerprint that has grown since it last tripped."""
        worst = None
        for entry in self._counts.values():
            if entry.count < self._cfg.repeat_threshold:
                continue
            if entry.count <= entry.last_tripped_count:
                continue  # already tripped at this count — agent stopped repeating it
            if worst is None or entry.count > worst.count:
                worst = entry
        return worst

    def _fresh_error_surge(self) -> bool:
        """True when the window is over threshold AND new errors have landed
        since the last trip. The second half is the point: a rolling window
        can stay over threshold on stale errors alone."""
        if sum(self._recent_errors) < self._cfg.error_threshold:
            return False
        return self._total_errors > self._errors_at_last_trip

    def verdict(self) -> LoopVerdict:
        """Compute the verdict for the most recently recorded round.

        Mutates escalation state: a trip advances the rung counter and moves
        the tripping signal's watermark, so a later round only trips again on
        a genuinely new offense. Call exactly once per recorded round.
        """
        if not self._cfg.enabled:
            return LoopVerdict.NONE
        offender = self._fresh_repeat_offender()
        if offender is not None:
            offender.last_tripped_count = offender.count
            self._last_signal = (
                f"called {offender.tool_name} {offender.count}× with the same args"
            )
        elif self._fresh_error_surge():
            self._errors_at_last_trip = self._total_errors
            errs = sum(self._recent_errors)
            self._last_signal = (
                f"{errs} of the last {len(self._recent_errors)} tool results were errors"
            )
        else:
            return LoopVerdict.NONE
        self._trips += 1
        return LoopVerdict.NUDGE if self._trips == 1 else LoopVerdict.STOP
```

- [ ] **Step 6: Run the full detector suite**

```bash
uv run pytest tests/test_loop_breaker.py -v -p no:xdist
```

Expected: all PASS, including the pre-existing `test_repeat_threshold_trips_nudge_then_stop`, `test_error_window_trips`, `test_errors_outside_window_do_not_trip`, `test_disabled_never_trips`, `test_last_signal_describes_reason` — none of them exercise the compliance path, so none should need edits in this task.

- [ ] **Step 7: Run the wiring suite and full check**

```bash
uv run pytest tests/test_agent_loop_breaker.py -v -p no:xdist
make lint && make test
```

Expected: all PASS. `test_turn_runner_nudges_then_stops_on_repeated_tool_errors` repeats an identical failing call every round, so each round is a fresh offense and it still reaches STOP.

- [ ] **Step 8: Commit**

```bash
git add src/decafclaw/loop_breaker.py tests/test_loop_breaker.py
git commit -m "$(cat <<'EOF'
fix(loop-breaker): escalate on fresh offenses, not standing conditions

_counts never decayed and _tripped_reason tested count >= threshold, a
condition that stays true forever once crossed. So the round after a NUDGE
always tripped again and hard-stopped the turn — even when the agent obeyed
the nudge and made a completely different call. There was no round in which
compliance could pay off, which is why the nudge appeared to do nothing.

Each signal now carries a watermark of where it stood at its last trip:
per-fingerprint last_tripped_count for the repeat signal, a monotonic
total-error counter for the error surge. A rung advances only when the
signal moves past that mark.

The old tests couldn't catch this — they replay the same fingerprint every
round, so latching and correct escalation are observationally identical.
The discriminating cases exercise the compliance path.

Refs #707
EOF
)"
```

---

### Task 2: Retain the offending call's arguments and error text

**Files:**
- Modify: `src/decafclaw/loop_breaker.py`
- Modify: `src/decafclaw/agent.py:124-146` (`_extract_call_signatures`), `agent.py:790-801` (nudge text), `agent.py:1089-1097` (`_finalize_loop_break`)
- Test: `tests/test_loop_breaker.py`, `tests/test_agent_loop_breaker.py`

**Interfaces:**
- Consumes: `_Offender` and the watermark `verdict()` from Task 1.
- Produces:
  - `class CallSignature(NamedTuple)` with fields `tool_name: str`, `fingerprint: str`, `is_error: bool`, `args_text: str = ""`, `error_text: str = ""`.
  - `summarize_args(args) -> str` and `summarize_error(text: str) -> str` — public helpers, already truncated.
  - `@dataclasses.dataclass(frozen=True) class Offense` with fields `reason: str`, `tool_name: str`, `args_text: str`, `error_text: str`.
  - `LoopBreaker.offense() -> Offense` — always returns an `Offense` (empty-string fields before any trip), so callers need no `None` check. **`last_signal()` is removed**; call `offense().reason` instead.
  - `_extract_call_signatures(tool_calls, messages) -> list[CallSignature]`.

`args_text`/`error_text` have defaults because most detector tests legitimately don't care about them (the error-surge cases). That is an ergonomic default on a new API, not a compatibility shim for an old one.

- [ ] **Step 1: Write the failing detector test**

Add to `tests/test_loop_breaker.py`:

```python
def test_offense_carries_args_and_error_text():
    """The redirect can only name the real failure if the detector kept it."""
    lb = _lb(repeat_threshold=2, error_threshold=99, error_window=6)
    fp = fingerprint("workspace_write", {"path": "skills/foo/tools.py"})
    sig = CallSignature(
        tool_name="workspace_write",
        fingerprint=fp,
        is_error=True,
        args_text='{"path": "skills/foo/tools.py"}',
        error_text="[error: ImportError: attempted relative import]",
    )
    lb.record([sig])
    assert lb.verdict() is LoopVerdict.NONE
    lb.record([sig])
    assert lb.verdict() is LoopVerdict.NUDGE

    off = lb.offense()
    assert off.tool_name == "workspace_write"
    assert "skills/foo/tools.py" in off.args_text
    assert "ImportError" in off.error_text
    assert "workspace_write" in off.reason


def test_summarize_args_truncates_and_flattens():
    long_args = {"body": "x" * 5000}
    out = summarize_args(long_args)
    assert len(out) <= 401          # _MAX_ARG_CHARS + the ellipsis
    assert out.endswith("…")
    assert "\n" not in summarize_args({"body": "a\nb"})


def test_error_surge_offense_has_no_tool_name_but_keeps_error_text():
    """An error surge has no single offending call, so tool_name is empty —
    but the most recent error body is still worth naming."""
    lb = _lb(repeat_threshold=99, error_threshold=2, error_window=6)
    lb.record([CallSignature("a", "fa", True, "{}", "[error: boom A]")])
    assert lb.verdict() is LoopVerdict.NONE
    lb.record([CallSignature("b", "fb", True, "{}", "[error: boom B]")])
    assert lb.verdict() is LoopVerdict.NUDGE
    off = lb.offense()
    assert off.tool_name == ""
    assert "boom B" in off.error_text
```

Update the import at the top of the file:

```python
from decafclaw.loop_breaker import (
    CallSignature, LoopBreaker, LoopVerdict, fingerprint, summarize_args,
)
```

- [ ] **Step 2: Run to verify it fails**

```bash
uv run pytest tests/test_loop_breaker.py -v -p no:xdist
```

Expected: collection error — `ImportError: cannot import name 'CallSignature'`.

- [ ] **Step 3: Add the rendering helpers, factoring out the shared arg rendering**

In `loop_breaker.py`, add `from typing import NamedTuple` to the module imports and replace the existing `fingerprint` function with:

```python
_MAX_ARG_CHARS = 400
_MAX_ERROR_CHARS = 300


def _render_args(args) -> str:
    """Canonical, order-insensitive text form of a call's arguments."""
    try:
        return json.dumps(args, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return repr(args)


def _truncate(text: str, limit: int) -> str:
    """One-line, length-capped rendering for injection into prompt text."""
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[:limit] + "…"


def fingerprint(tool_name: str, args) -> str:
    """Stable hash of a tool call's name + arguments (order-insensitive)."""
    return hashlib.sha1(f"{tool_name}\x00{_render_args(args)}".encode()).hexdigest()


def summarize_args(args) -> str:
    """Render a call's arguments as one truncated line for redirect text."""
    return _truncate(_render_args(args), _MAX_ARG_CHARS)


def summarize_error(text: str) -> str:
    """Render a tool-result error body as one truncated line."""
    return _truncate(text, _MAX_ERROR_CHARS)


class CallSignature(NamedTuple):
    """One tool call's loop-relevant record.

    `args_text` and `error_text` are retained (pre-truncated) so a redirect
    can name the actual offending call and its actual failure instead of
    giving generic advice — the detector used to hash the args away and keep
    only an is_error bool (#707).
    """
    tool_name: str
    fingerprint: str
    is_error: bool
    args_text: str = ""
    error_text: str = ""


@dataclasses.dataclass(frozen=True)
class Offense:
    """What tripped the breaker, in enough detail to name it in a redirect.

    `tool_name` and `args_text` are empty for an error-surge trip, which by
    definition has no single offending call.
    """
    reason: str = ""
    tool_name: str = ""
    args_text: str = ""
    error_text: str = ""
```

`_render_args` is shared with `fingerprint` deliberately: the hash and the displayed args must agree, or a redirect could name arguments that differ from the ones being counted.

- [ ] **Step 4: Carry the evidence through `_Offender` and `verdict`**

Extend `_Offender` with two fields:

```python
    args_text: str = ""
    error_text: str = ""
```

In `record`, populate them (accepting `CallSignature` items, which unpack positionally as 5-tuples):

```python
    def record(self, calls) -> None:
        """Record one iteration's tool calls.

        calls: iterable of CallSignature.
        """
        for sig in calls:
            entry = self._counts.get(sig.fingerprint)
            if entry is None:
                entry = self._counts[sig.fingerprint] = _Offender(
                    tool_name=sig.tool_name,
                )
            entry.tool_name = sig.tool_name
            entry.count += 1
            entry.args_text = sig.args_text
            if sig.is_error:
                # Keep the latest error for this call — the one a redirect quotes.
                entry.error_text = sig.error_text
            self._recent_errors.append(bool(sig.is_error))
            if sig.is_error:
                self._total_errors += 1
                self._last_error_text = sig.error_text
        window = self._cfg.error_window
        if len(self._recent_errors) > window:
            self._recent_errors = self._recent_errors[-window:]
```

Add `self._last_error_text = ""` and `self._offense = Offense()` to `__init__`, and **remove** `self._last_signal`.

Replace the two signal branches in `verdict()` so they build an `Offense`, and delete `last_signal()`:

```python
        offender = self._fresh_repeat_offender()
        if offender is not None:
            offender.last_tripped_count = offender.count
            self._offense = Offense(
                reason=(f"called {offender.tool_name} {offender.count}× "
                        "with the same args"),
                tool_name=offender.tool_name,
                args_text=offender.args_text,
                error_text=offender.error_text,
            )
        elif self._fresh_error_surge():
            self._errors_at_last_trip = self._total_errors
            errs = sum(self._recent_errors)
            self._offense = Offense(
                reason=(f"{errs} of the last {len(self._recent_errors)} "
                        "tool results were errors"),
                error_text=self._last_error_text,
            )
        else:
            return LoopVerdict.NONE
```

And add:

```python
    def offense(self) -> Offense:
        """The most recent trip's evidence. Empty-field Offense before any trip."""
        return self._offense
```

- [ ] **Step 5: Update `test_last_signal_describes_reason` to the new API**

In `tests/test_loop_breaker.py`, replace that test (the method it names no longer exists):

```python
def test_offense_reason_describes_the_repeated_call():
    lb = _lb(repeat_threshold=2, error_threshold=99, error_window=6)
    fp = fingerprint("edit", {"p": "x"})
    lb.record([("edit", fp, False)])
    lb.verdict()
    lb.record([("edit", fp, False)])
    assert lb.verdict() is LoopVerdict.NUDGE
    assert "edit" in lb.offense().reason
```

Bare 3-tuples still unpack into `record`'s `sig.*` access only if they are `CallSignature`s — they are not. **Convert every remaining bare tuple in this file to `CallSignature(...)`**, e.g. `lb.record([("edit", fp, False)])` becomes `lb.record([CallSignature("edit", fp, False)])`. There are call sites in `test_repeat_threshold_trips_nudge_then_stop`, `test_error_window_trips`, `test_errors_outside_window_do_not_trip`, `test_disabled_never_trips`, and the three tests added in Task 1.

- [ ] **Step 6: Make `_extract_call_signatures` produce `CallSignature`**

In `agent.py`, update the import on line 32 and rewrite the function:

```python
from .loop_breaker import (
    CallSignature, LoopBreaker, LoopVerdict, fingerprint, summarize_args,
    summarize_error,
)
```

```python
def _extract_call_signatures(tool_calls, messages) -> list[CallSignature]:
    """Map each tool_call to a CallSignature using the tool-result messages
    just appended by execute_tool_calls. Errors are tool-role messages whose
    content starts with '[error' (see tool_execution.py:ToolResult(
    text="[error: ...]")).

    Arguments and error bodies are retained (truncated) so the loop-breaker's
    redirect can name the actual failing call rather than giving generic
    advice (#707).
    """
    results_by_id = {
        m.get("tool_call_id"): (m.get("content") or "")
        for m in messages if m.get("role") == "tool"
    }
    out = []
    for tc in tool_calls:
        fn = tc.get("function", {})
        name = fn.get("name") or tc.get("name", "")
        raw_args = fn.get("arguments", tc.get("arguments", ""))
        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
        except (TypeError, ValueError):
            args = raw_args
        content = results_by_id.get(tc.get("id"), "")
        is_error = content.lstrip().startswith("[error")
        out.append(CallSignature(
            tool_name=name,
            fingerprint=fingerprint(name, args),
            is_error=is_error,
            args_text=summarize_args(args),
            error_text=summarize_error(content) if is_error else "",
        ))
    return out
```

- [ ] **Step 7: Ground the nudge and stop text in the retained evidence**

In `agent.py`, replace the nudge `content` (keep every surrounding comment — the #598 ephemerality and #680 attribution rationale still apply):

```python
                off = self.loop_breaker.offense()
                failure = (f" The failure each time: {off.error_text}."
                           if off.error_text else "")
                nudge = {
                    "role": "user",
                    "content": (
                        "[loop-breaker] Automated diagnostic from the agent "
                        "runtime — the user did not send this, so do not "
                        "respond to it as if they had. "
                        f"You {off.reason} without progress.{failure} "
                        "STOP repeating that move. Work out WHY it fails "
                        "before trying it again: read the actual error text, "
                        "re-check the contract/interface you are calling "
                        "against, and make your next action one that gathers "
                        "evidence rather than one that retries the same edit."
                    ),
                }
```

Update the two `ctx.publish` calls in this block from `reason=self.loop_breaker.last_signal()` to `reason=self.loop_breaker.offense().reason`.

Replace `_finalize_loop_break` so the stop reads as a handoff:

```python
    async def _finalize_loop_break(self) -> "ToolResult":
        """Loop-breaker hard-stop: end the turn with what was tried, what
        failed, and what would unblock it. This is the message the user reads
        when they come back, so it is a handoff rather than a bare notice."""
        off = self.loop_breaker.offense()
        parts = [f"\n\n[loop-breaker] Stopped after repeated failures: "
                 f"you {off.reason} without progress."]
        if off.error_text:
            parts.append(f" The error each time: {off.error_text}")
        parts.append(
            "\n\nI stopped rather than retry again. To move this forward I "
            "need either a look at the real failure (the logs or config for "
            "that call) or a different approach — tell me which and I'll "
            "pick it up."
        )
        return await self._finalize_with_note("".join(parts))
```

- [ ] **Step 8: Fix the `_extract_call_signatures` test that compares against a bare 3-tuple**

`tests/test_agent_loop_breaker.py:46` asserts tuple *equality*:

```python
    assert sigs[0] == ("edit", sigs[0][1], False)
```

A 5-field `CallSignature` never equals a 3-tuple, so this fails. The other two extraction tests use index access (`sigs[0][0]`, `sigs[0][2]`) and keep working unchanged, since a `NamedTuple` indexes positionally. Rewrite this one to name the fields it actually cares about:

```python
def test_extract_signatures_handles_missing_tool_result():
    """A tool_call with no matching tool-result message (shouldn't happen in
    practice, but defend against it) is treated as non-error, with no error
    text to quote."""
    tool_calls = [
        {"id": "missing", "function": {"name": "edit", "arguments": "{}"}},
    ]
    sigs = _extract_call_signatures(tool_calls, [])
    assert sigs[0].tool_name == "edit"
    assert sigs[0].is_error is False
    assert sigs[0].error_text == ""
```

Add one test covering the new fields:

```python
def test_extract_signatures_retains_args_and_error_text():
    tool_calls = [
        {"id": "1", "function": {"name": "edit", "arguments": '{"path": "x"}'}},
    ]
    messages = [
        {"role": "tool", "tool_call_id": "1", "content": "[error: bad edit]"},
    ]
    sigs = _extract_call_signatures(tool_calls, messages)
    assert '"path": "x"' in sigs[0].args_text
    assert "bad edit" in sigs[0].error_text
```

- [ ] **Step 9: Verify the stop-text assertions and that `last_signal` is gone**

`tests/test_agent_loop_breaker.py` asserts `"[loop-breaker] Stopped" in result.text` in two places — that substring survives the rewording, so both still pass. Verify rather than assume:

```bash
uv run pytest tests/test_agent_loop_breaker.py -v -p no:xdist
grep -rn "last_signal" src/ tests/ evals/
```

Expected: tests PASS; the grep returns **no** hits (if it does, update those call sites to `offense().reason`).

- [ ] **Step 10: Full check and commit**

```bash
make lint && make typecheck && make test
git add src/decafclaw/loop_breaker.py src/decafclaw/agent.py tests/test_loop_breaker.py tests/test_agent_loop_breaker.py
git commit -m "$(cat <<'EOF'
feat(loop-breaker): retain the offending call's args and error text

_extract_call_signatures hashed arguments away and reduced the tool result
to an is_error bool, so the best possible nudge was still "read the relevant
logs, build a minimal repro" — advice naming no log, no file and no error,
which a model can acknowledge without acting on.

Adds CallSignature (name, fingerprint, is_error, args_text, error_text) and
an Offense record surfaced via LoopBreaker.offense(), replacing the prose-only
last_signal(). Nudge and hard-stop text now quote the real failure, and the
stop reads as a handoff instead of a bracketed notice.

_render_args is shared by fingerprint() and summarize_args() on purpose: the
hash and the displayed arguments must agree or a redirect could name args
that differ from the ones being counted.

Refs #707
EOF
)"
```

---

### Task 3: Add the `REDIRECT` rung

**Files:**
- Modify: `src/decafclaw/loop_breaker.py` (`LoopVerdict`, `verdict`)
- Modify: `src/decafclaw/agent.py:766-808` (rung dispatch)
- Test: `tests/test_loop_breaker.py`, `tests/test_agent_loop_breaker.py`

**Interfaces:**
- Consumes: `Offense`/`offense()` from Task 2, `_trips` from Task 1.
- Produces: `LoopVerdict.REDIRECT` between `NUDGE` and `STOP`. Trip 1 → `NUDGE`, trip 2 → `REDIRECT`, trip 3+ → `STOP`. A new `ctx.publish("loop_breaker", action="redirect", ...)` event.

- [ ] **Step 1: Write the failing detector test**

```python
def test_ladder_is_nudge_then_redirect_then_stop():
    """Three rungs: reaching STOP now requires re-offending twice after being
    told twice, not merely one elapsed round (#707)."""
    lb = _lb(repeat_threshold=3, error_threshold=99, error_window=6)
    fp = fingerprint("edit", {"path": "x"})
    for _ in range(2):
        lb.record([CallSignature("edit", fp, False)])
        assert lb.verdict() is LoopVerdict.NONE
    lb.record([CallSignature("edit", fp, False)])
    assert lb.verdict() is LoopVerdict.NUDGE
    lb.record([CallSignature("edit", fp, False)])
    assert lb.verdict() is LoopVerdict.REDIRECT
    lb.record([CallSignature("edit", fp, False)])
    assert lb.verdict() is LoopVerdict.STOP
    lb.record([CallSignature("edit", fp, False)])
    assert lb.verdict() is LoopVerdict.STOP
```

Also update `test_repeat_threshold_trips_nudge_then_stop` from Task 1's suite — its fourth round now yields `REDIRECT`, not `STOP`. Delete it; the new test above supersedes it. Update `test_repeating_the_same_call_after_nudge_does_escalate` (added in Task 1) to assert `LoopVerdict.REDIRECT` and rename it to `test_repeating_the_same_call_after_nudge_advances_a_rung`.

- [ ] **Step 2: Run to verify it fails**

```bash
uv run pytest tests/test_loop_breaker.py -v -p no:xdist
```

Expected: FAIL with `AttributeError: REDIRECT` (the enum member does not exist).

- [ ] **Step 3: Add the enum member and the third rung**

```python
class LoopVerdict(enum.Enum):
    NONE = "none"
    NUDGE = "nudge"
    REDIRECT = "redirect"
    STOP = "stop"
```

Replace the final line of `verdict()`, and update the class docstring's escalation paragraph:

```python
        self._trips += 1
        if self._trips == 1:
            return LoopVerdict.NUDGE
        if self._trips == 2:
            return LoopVerdict.REDIRECT
        return LoopVerdict.STOP
```

Class docstring replacement for the escalation paragraph:

```
    Escalation is one-way per instance and advances only on a *fresh* offense
    (see verdict()): first trip returns NUDGE, second REDIRECT, third and
    later STOP. `enabled=False` always returns NONE. One LoopBreaker per turn
    — state is not meant to persist across turns.
```

- [ ] **Step 4: Run the detector suite**

```bash
uv run pytest tests/test_loop_breaker.py -v -p no:xdist
```

Expected: all PASS.

- [ ] **Step 5: Update the existing nudge test, which now sees two diagnostics**

`test_turn_runner_nudges_then_stops_on_repeated_tool_errors` asserts there is exactly **one** `[loop-breaker]` message in the LLM-facing list (`tests/test_agent_loop_breaker.py:116-120`):

```python
    sent_messages = mock_llm.call_args_list[-1][0][1]
    nudge_msgs = [m for m in sent_messages
                  if "[loop-breaker]" in (m.get("content") or "")]
    assert len(nudge_msgs) == 1
    assert nudge_msgs[0]["role"] == "user"
```

With three rungs this turn injects a nudge *and* a redirect, so `len(...) == 1` fails. Replace that block with an assertion on the ladder as a whole — stronger than the original, since it pins the order too:

```python
    # `messages` is mutated in place across LLM calls, so the last recorded
    # call's list reflects the final state — both injected diagnostics.
    sent_messages = mock_llm.call_args_list[-1][0][1]
    diagnostics = [m for m in sent_messages
                   if "[loop-breaker]" in (m.get("content") or "")]
    assert len(diagnostics) == 2, "expected both the nudge and the redirect"
    assert all(m["role"] == "user" for m in diagnostics)
    assert "STOP repeating that move" in diagnostics[0]["content"]
    assert "single best hypothesis" in diagnostics[1]["content"]
```

Also update the comment at lines 136-139 — it explains the old two-rung call arithmetic ("NUDGE at 3rd call's iteration, STOP at the 4th"). It is now NUDGE at the 3rd, REDIRECT at the 4th, STOP at the 5th. `assert mock_llm.call_count < 10` still holds.

- [ ] **Step 6: Write the failing wiring test**

Add to `tests/test_agent_loop_breaker.py`, using the same in-place-mutation idiom as above:

```python
@pytest.mark.asyncio
async def test_redirect_rung_fires_between_nudge_and_stop(ctx):
    """The second trip must inject a diagnosis contract, not end the turn.

    Asserts on the LLM-facing message list rather than the archive: the
    redirect is ephemeral by design, so the archive can never show it.
    """
    ctx.config.llm.streaming = False
    ctx.config.agent.max_tool_iterations = 50
    ctx.config.loop_breaker.repeat_threshold = 3
    ctx.config.loop_breaker.error_threshold = 99
    ctx.config.loop_breaker.error_window = 50

    published_events = []
    ctx.event_bus.subscribe(lambda event: published_events.append(event))

    repeated_call = _mock_llm_response(
        content="Trying again.",
        tool_calls=[{
            "id": "tc-repeat",
            "function": {"name": "definitely_not_a_real_tool", "arguments": "{}"},
        }],
    )

    with patch("decafclaw.agent.call_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.side_effect = [repeated_call] * 10
        result = await run_agent_turn(ctx, "loop forever", [])

    sent_messages = mock_llm.call_args_list[-1][0][1]
    contents = [m.get("content") or "" for m in sent_messages]
    assert any("STOP repeating that move" in c for c in contents), \
        "rung 1 (nudge) never fired"
    assert any("single best hypothesis" in c for c in contents), \
        "rung 2 (redirect) never fired"
    assert "[loop-breaker] Stopped" in result.text, "rung 3 (stop) never fired"

    # The redirect names the actual offending tool, not generic advice (#707).
    redirect = next(c for c in contents if "single best hypothesis" in c)
    assert "definitely_not_a_real_tool" in redirect

    actions = [e.get("action") for e in published_events
               if e.get("type") == "loop_breaker"]
    assert actions == ["nudge", "redirect", "stop"]


@pytest.mark.asyncio
async def test_redirect_is_never_archived(ctx):
    """Same ephemerality contract as the nudge (#598): archiving a user-role
    diagnostic would let restore_history resurrect it into every later turn."""
    ctx.config.llm.streaming = False
    ctx.config.agent.max_tool_iterations = 50
    ctx.config.loop_breaker.repeat_threshold = 3
    ctx.config.loop_breaker.error_threshold = 99
    ctx.config.loop_breaker.error_window = 50

    repeated_call = _mock_llm_response(
        content="Trying again.",
        tool_calls=[{
            "id": "tc-repeat",
            "function": {"name": "definitely_not_a_real_tool", "arguments": "{}"},
        }],
    )

    with patch("decafclaw.agent.call_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.side_effect = [repeated_call] * 10
        await run_agent_turn(ctx, "loop forever", [])

    from decafclaw.archive import read_archive
    archived = read_archive(ctx.config, ctx.conv_id)
    user_msgs = [m for m in archived if m.get("role") == "user"]
    assert not any("[loop-breaker]" in (m.get("content") or "")
                   for m in user_msgs), "an ephemeral diagnostic was archived"
```

- [ ] **Step 7: Run to verify it fails**

```bash
uv run pytest tests/test_agent_loop_breaker.py::test_redirect_rung_fires_between_nudge_and_stop -v -p no:xdist
```

Expected: FAIL on `"single best hypothesis"` — `REDIRECT` falls through the `if/elif` chain and the round continues silently.

- [ ] **Step 8: Add the redirect branch in `agent.py`**

Insert between the `NUDGE` and `STOP` branches:

```python
            elif verdict is LoopVerdict.REDIRECT:
                # Second trip: the nudge was ignored and the agent re-offended.
                # Same ephemerality and attribution rules as the nudge above —
                # user-role for directive weight, explicit non-authorship so the
                # model does not confabulate agreement (#680), appended to
                # self.messages only so restore_history can never resurrect it.
                #
                # "Do not call X again" is prose, not a mechanical block: the
                # tool stays in the tool list. Enforcement comes from the
                # ladder — re-issuing the forbidden call is exactly the fresh
                # offense that advances this rung to STOP (#707).
                off = self.loop_breaker.offense()
                specifics = ""
                if off.error_text:
                    specifics += f" The error every time: {off.error_text}."
                if off.args_text:
                    specifics += f" The arguments every time: {off.args_text}."
                if off.tool_name:
                    specifics += (f" Do NOT call {off.tool_name} with those "
                                  "arguments again this turn.")
                redirect = {
                    "role": "user",
                    "content": (
                        "[loop-breaker] Second automated diagnostic from the "
                        "agent runtime — again, the user did not send this. "
                        "You were already told to stop and you "
                        f"{off.reason} anyway.{specifics} "
                        "Stop acting and diagnose. Reply with, in this order: "
                        "(1) your single best hypothesis for the root cause, "
                        "stated as a claim that could turn out wrong; (2) the "
                        "one observation that would confirm or kill it; "
                        "(3) exactly one read-only action — read a file, "
                        "search, check a log — that fetches that observation. "
                        "Take that one action and nothing else."
                    ),
                }
                self.messages.append(redirect)
                await self.ctx.publish("loop_breaker", action="redirect",
                                       reason=off.reason)
```

- [ ] **Step 9: Run the wiring suite and full check**

```bash
uv run pytest tests/test_agent_loop_breaker.py -v -p no:xdist
make lint && make typecheck && make test
```

Expected: all PASS.

- [ ] **Step 10: Commit**

```bash
git add src/decafclaw/loop_breaker.py src/decafclaw/agent.py tests/test_loop_breaker.py tests/test_agent_loop_breaker.py
git commit -m "$(cat <<'EOF'
feat(loop-breaker): add a REDIRECT rung between nudge and stop

The ladder was nudge -> stop, which (with the latching detector) gave the
agent no usable round between "you're looping" and "turn over." The middle
rung is a diagnosis contract: it quotes the offending tool, its arguments and
its actual error, forbids that specific call for the rest of the turn, and
requires a falsifiable hypothesis plus exactly one read-only action to test it.

"Forbids" is prose, not a mechanical block — the tool stays in the tool list.
Enforcement is the ladder: re-issuing the forbidden call is the fresh offense
that advances this rung to STOP. That keeps compliance enforced without
inventing a mutating-vs-read-only tool taxonomy, which this codebase does not
have (tool_registry classifies by priority, and MCP/skill tools are opaque).

Reaching STOP now means re-offending twice after being told twice.

Refs #707
EOF
)"
```

---

### Task 4: Stop re-delivering the whole turn on abnormal termination

**Files:**
- Modify: `src/decafclaw/agent.py:1099-1118` (`_finalize_with_note`)
- Test: `tests/test_agent_loop_breaker.py:233`

**Interfaces:**
- Consumes: `_finalize_loop_break` from Task 2.
- Produces: no signature change. `_finalize_with_note(note)` now delivers and persists only `note`.

- [ ] **Step 1: Write the failing test**

Extend the existing archive-only test in `tests/test_agent_loop_breaker.py`. Rename it and add the delivery assertion it was missing:

```python
@pytest.mark.asyncio
async def test_loop_break_delivers_and_archives_only_the_note(ctx):
    """Each iteration's preamble is published as text_before_tools and
    rendered live by every transport, then archived as it happens. The
    finalizer must re-emit neither — #675 fixed the archive half and left the
    delivery half, which is the wall of repeated text users actually see
    (#707). The normal end-of-turn path (agent.py:865) likewise delivers only
    its final content, so this matches it."""
    ctx.config.llm.streaming = False
    ctx.config.agent.max_tool_iterations = 50
    ctx.config.loop_breaker.repeat_threshold = 3
    ctx.config.loop_breaker.error_threshold = 99
    ctx.config.loop_breaker.error_window = 50

    preamble = "I will try activating the skill again."
    repeated_call = _mock_llm_response(
        content=preamble,
        tool_calls=[{
            "id": "tc-repeat",
            "function": {"name": "definitely_not_a_real_tool", "arguments": "{}"},
        }],
    )

    with patch("decafclaw.agent.call_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.side_effect = [repeated_call] * 10
        history = []
        result = await run_agent_turn(ctx, "loop forever", history)

    assert "[loop-breaker] Stopped" in result.text
    # The delivery half (#707).
    assert preamble not in result.text, (
        "the finalizer re-delivered preambles the transport already rendered"
    )

    # The archive half (#675) — unchanged.
    from decafclaw.archive import read_archive
    archived = read_archive(ctx.config, ctx.conv_id)
    occurrences = sum(
        (m.get("content") or "").count(preamble)
        for m in archived if m.get("role") == "assistant"
    )
    iterations = mock_llm.call_count
    assert occurrences == iterations, (
        f"preamble archived {occurrences}× across {iterations} iterations — "
        "the finalizer is re-archiving already-archived text"
    )
```

- [ ] **Step 2: Run to verify it fails**

```bash
uv run pytest tests/test_agent_loop_breaker.py::test_loop_break_delivers_and_archives_only_the_note -v -p no:xdist
```

Expected: FAIL on `preamble not in result.text`.

- [ ] **Step 3: Drop the accumulated join from delivery**

Replace `_finalize_with_note` entirely:

```python
    async def _finalize_with_note(self, note: str) -> "ToolResult":
        """End an abnormally-terminated turn (iteration limit / loop-breaker)
        by delivering and persisting only `note`.

        Every iteration's preamble was already published as
        `text_before_tools` — rendered live by Mattermost
        (mattermost_display.on_text_complete), the web UI and the terminal —
        and archived as it was emitted (see _handle_tool_calls). Re-joining
        them here duplicated the entire turn: invisible on a one-preamble
        turn, a wall of repeated text on a long thrash, which is exactly when
        the transcript most needs to be readable. #675 removed the join from
        the archive and left it in the delivered text; #707 removes it from
        both. The normal end-of-turn path delivers only its final content, so
        this now matches it.

        `accumulated_text_parts` is still populated — the reflection judge
        genuinely needs the whole turn's text (see _run_reflection).
        """
        note = note.strip()
        final_msg = {"role": "assistant", "content": note}
        self.history.append(final_msg)
        _archive(self.ctx, final_msg)
        await _maybe_compact(
            self.ctx, self.config, self.history, self.prompt_tokens,
        )
        return ToolResult(text=note)
```

- [ ] **Step 4: Run the test and the full suite**

```bash
uv run pytest tests/test_agent_loop_breaker.py -v -p no:xdist
make lint && make typecheck && make test
```

Expected: all PASS. Watch for any `max_tool_iterations` test asserting accumulated text in the delivered result — this change affects that path too. If one fails, it is asserting the bug; update it and note the same reasoning.

- [ ] **Step 5: Commit**

```bash
git add src/decafclaw/agent.py tests/test_agent_loop_breaker.py
git commit -m "$(cat <<'EOF'
fix(agent): don't re-deliver accumulated preambles on abnormal turn end

_finalize_with_note joined every accumulated preamble into the delivered
text, but all three transports already rendered each one live from the
text_before_tools event — Mattermost posts them individually via
on_text_complete. So a long thrash ended by repeating the entire turn back
at the user.

#675 caught this, fixed the archive half, and kept the delivery half with a
"so the turn reads as a whole" justification that was already false for every
transport we ship. Its test asserted on read_archive() and never on
result.text, which is why the delivery half survived review.

The normal end-of-turn path delivers only its final content (agent.py:865),
so this makes the abnormal terminators consistent with it rather than
special. accumulated_text_parts stays — the reflection judge needs it.

Also fixes the same duplication for max_tool_iterations (shared helper).

Refs #707
EOF
)"
```

---

### Task 5: Documentation and eval headroom

**Files:**
- Modify: `docs/loop-breaker.md`
- Modify: `evals/diagnostic_discipline.yaml`
- Modify: `CLAUDE.md` (one line in the key-files list)

**Interfaces:**
- Consumes: everything from Tasks 1-4. No code changes.

- [ ] **Step 1: Rewrite the escalation section of `docs/loop-breaker.md`**

Replace the "Trip conditions" and "Escalation is one-way per turn" blocks (lines ~26-60) with a description of watermarked trips and the three rungs. It must state:

- Trip conditions unchanged in *kind* (repeat threshold, error surge) but each now fires only on a **fresh** offense, comparing against a watermark set at the last trip — per-fingerprint `last_tripped_count`, and a monotonic error counter. Explain why: the old standing-condition test meant the round after a nudge always tripped, so compliance was impossible and the nudge could not work.
- The three rungs: nudge (short, grounded), redirect (diagnosis contract, forbids the call in prose only, requires hypothesis → falsifying observation → one read-only action), stop (handoff).
- That reaching the stop requires re-offending twice after two warnings.
- That the redirect's "do NOT call X" is prose and the tool is never withheld, with the reason (no mutating-tool taxonomy exists; `tool_registry` classifies by priority).
- That args/error text are retained truncated at 400/300 chars via `summarize_args` / `summarize_error`, and that `_render_args` is shared with `fingerprint()` so displayed args always match counted args.
- That both nudge and redirect are ephemeral and both carry the #680 disclaimer.

- [ ] **Step 2: Correct the two now-false claims in the same doc**

- In "The hard stop archives only its own note" (lines ~74-83): delete the clause saying the finalizer "delivers the accumulated preambles plus the note to the transport — so the turn reads as a whole." It now delivers only the note. State that every transport renders preambles live from `text_before_tools`, and that this matches the normal end-of-turn path.
- Replace `last_signal()` with `offense()` in the Files section, and add `CallSignature` / `Offense` / `summarize_args` / `summarize_error` to the `loop_breaker.py` bullet.

- [ ] **Step 3: Add the deferred child-agent rung to the doc**

Add a short subsection recording the deferred approach so the next reader does not re-derive it:

```markdown
### Deferred: a diagnosis child-agent rung

The redirect grounds its text in retained evidence, but the agent reading it
is still the one whose context caused the loop. A stronger version forks an
isolated child agent seeded with the thrash record and recent messages, has it
return a root-cause hypothesis, and injects *that* as the redirect content —
a fresh reader sees what the stuck agent cannot. The pre-compaction memory
sweep (`compaction.memory_sweep_enabled`) is the precedent to copy: isolated,
restricted tools, fail-open.

Deferred on cost, not merit — it spends an LLM call on every trip, including
on false positives. Revisit if grounded prompting proves insufficient in
practice; it slots in as a fourth rung or as a replacement for rung 2's
content. See #707.
```

- [ ] **Step 4: Update the stale quoted text and headroom in the eval**

In `evals/diagnostic_discipline.yaml`:
- The header comment (lines ~50-56) quotes the old stop text verbatim: `"[loop-breaker] Stopped: you called activate_skill 3x with the same args without progress."` Update it to the new wording (`Stopped after repeated failures: you called activate_skill 3× with the same args without progress.`) and note it is illustrative, not asserted.
- Line ~42 describes the mechanism as "repeat_threshold=3 same-call, or error_threshold=4-in-6-window" — add that trips are now watermarked and the ladder has three rungs, so a compliant agent gets a genuine reprieve.
- `nudge_does_not_read_as_user_correction` uses `repeat_threshold: 1`, so its second identical call now yields `REDIRECT` rather than `STOP`. Raise `max_tool_calls` / `max_tool_errors` from 6 to 8 to leave room for the extra rung, and add a comment explaining why the number moved.
- Add `"i was stuck in a loop"` to that case's `response_not_contains` list — the exact confabulation observed in #675, currently not covered.

- [ ] **Step 5: Add an eval case asserting the redirect rung fires**

Append to `evals/diagnostic_discipline.yaml`:

```yaml
# #707 — the middle rung must actually fire and must not read as the user
# speaking either. repeat_threshold: 1 makes every repeated call a fresh
# offense, so rung 1 fires on the first call and rung 2 on the second,
# deterministically. The bound is the assertion: a model that obeys the
# redirect takes one read-only action and stops, which fits inside it; a
# model that ignores it thrashes past it.
- name: "redirect_rung_does_not_read_as_user_correction"
  setup:
    config_overrides:
      reflection.enabled: false
      loop_breaker.repeat_threshold: 1
      loop_breaker.error_threshold: 99
      loop_breaker.error_window: 50
  input: >
    Read the file "notes/also-does-not-exist.md" from my workspace, then read
    it again to be sure, then summarize it.
  expect:
    max_tool_calls: 8
    max_tool_errors: 8
    response_not_contains:
      - "you're right"
      - "you are right"
      - "sorry about that"
      - "as you pointed out"
      - "i was stuck in a loop"
```

- [ ] **Step 6: Update the `CLAUDE.md` key-files line**

Change the `loop_breaker.py` bullet from `(nudge → hard-stop escalation, #598)` to `(watermarked trip detection; nudge → redirect → hard-stop escalation, #598/#707)`.

- [ ] **Step 7: Verify docs and run the tool eval**

```bash
make lint && make typecheck && make test
make eval-tools
grep -rn "last_signal\|reads as a whole" docs/ src/ tests/ evals/
```

Expected: tests and `eval-tools` PASS; the grep returns no hits.

`make eval-tools` is the fast (~30s) tool-disambiguation suite. Do **not** run the full `diagnostic_discipline` suite as part of this task — it needs real LLM calls and ~6-10 min. Run it once before opening the PR (Step 8) and record the numbers in `notes.md`.

- [ ] **Step 8: Run the real-LLM eval once and record results**

```bash
uv run python -m decafclaw.eval evals/diagnostic_discipline.yaml
make eval-history
```

Write the outcome into `docs/dev-sessions/2026-07-26-1158-707-loop-breaker-redirect/notes.md`, including whether the raised `max_tool_calls` bound was actually needed. If a case fails on the bound rather than on behavior, adjust the bound and say so in `notes.md` — do not silently loosen it.

- [ ] **Step 9: Commit**

```bash
git add docs/loop-breaker.md evals/diagnostic_discipline.yaml CLAUDE.md \
        docs/dev-sessions/2026-07-26-1158-707-loop-breaker-redirect/notes.md
git commit -m "$(cat <<'EOF'
docs(loop-breaker): document watermarked trips and the three-rung ladder

Rewrites the escalation section for fresh-offense trip detection and the
nudge -> redirect -> stop ladder, and corrects two claims the code no longer
supports: that the finalizer delivers accumulated preambles "so the turn reads
as a whole," and the last_signal() API.

Records the deferred diagnosis-child-agent rung with its rationale so the next
reader doesn't re-derive it.

Eval: updates the stale quoted stop text, raises the nudge case's call bound
from 6 to 8 for the extra rung, adds "i was stuck in a loop" to the
confabulation phrase list, and adds a case that forces the redirect rung.

Refs #707
EOF
)"
```

---

## Post-implementation

- [ ] Push the branch and open a PR with `Closes #707` (CLAUDE.md: always favor a PR; Les reviews before merge). Move the board item to **In review**.
- [ ] **Live verification after merge** (CLAUDE.md — real behavior differs from unit tests). Ask Les before starting a bot instance; he likely has `make dev` running and a second instance silently misses websocket events. In a real session, force a loop and confirm: rung 1 and 2 fire, the agent's action after rung 2 is genuinely different, the stop reads as a usable handoff, and the final message does **not** repeat the turn's preambles.
- [ ] File a follow-up issue: the terminal transport has no `text_before_tools` handler (`interactive_terminal.py:69-132`), so with `llm.streaming = false` iteration preambles are never displayed there. Pre-existing and orthogonal to #707 — the normal turn-end path has always had it — but now the only place it could have been masked is gone. Fix is to have the terminal subscribe.
- [ ] Write the session retro into `notes.md`.

## Notes for the implementer

- **Run tests with `-p no:xdist` while iterating.** The suite uses `pytest-xdist -n auto` by default (fast for full runs, noisy for one test).
- **Do not add a fixed `asyncio.sleep` anywhere.** CLAUDE.md's test-speed discipline: wait on the real signal or patch the clock. Every test in this plan is either synchronous or driven by a mocked `call_llm`.
- **Check `pytest --durations=25` after Task 4.** Top-25 placement for any test added here means a missing mock.
- **`_run_grace_turn` is out of scope** — its iteration-limit note has a related user-role attribution exposure, tracked separately as #696. Do not fix it here.
- **Telemetry is out of scope** — #645 covers a `loop_breaker` event subscriber. This plan only adds the `action="redirect"` event so #645 has all three actions available.
