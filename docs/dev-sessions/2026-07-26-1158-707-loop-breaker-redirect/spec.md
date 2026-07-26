# Loop-breaker recovery redirect Spec

**Goal:** Make the loop-breaker redirect a thrashing agent into a *different
action* rather than only terminating it. Today it stops the agent reliably and
never gives it a round in which changing course can pay off.

**Source:** [#707](https://github.com/lmorchard/decafclaw/issues/707).
Builds on [#598](https://github.com/lmorchard/decafclaw/issues/598) (the
mechanism), [#675](https://github.com/lmorchard/decafclaw/issues/675) (archive
de-duplication) and [#680](https://github.com/lmorchard/decafclaw/issues/680)
(nudge attribution).

## Current state

Three independent defects, in descending order of impact.

### 1. The detector latches, so the nudge can never be obeyed

`LoopBreaker._counts` (`loop_breaker.py:42`) is cumulative and never decays,
and `_tripped_reason()` tests `count >= repeat_threshold` — a *standing
condition*, not an event. `verdict()` escalates one-way off that condition:

- Round N: third identical call → trip → `NUDGE`, `_nudged = True`
- Round N+1: agent obeys the nudge and issues a **completely different** call →
  `record()` → `_tripped_reason()` still sees the old count ≥ 3 → trip →
  `STOP`

So the turn is hard-stopped on the round immediately after the nudge **whatever
the agent does**. There is no round in which compliance is rewarded, which is
the direct cause of the reported symptom ("it stops the agent but doesn't
prompt it to do anything different"). The same applies to the error signal:
`_recent_errors` is a rolling window, so a window still holding
`error_threshold` old errors keeps tripping even after the agent stops
generating new ones.

The existing tests cannot catch this. `test_repeat_threshold_trips_nudge_then_stop`
(`tests/test_loop_breaker.py:16`) feeds the *same* fingerprint four times, so
"latched condition" and "correct escalation" produce identical output. The
discriminating test — nudge, then record a *different* call, expect `NONE` —
does not exist. The bug lives in the gap between two individually-tested
components.

### 2. The evidence needed for a specific redirect is discarded at record time

`_extract_call_signatures` (`agent.py:124-146`) returns
`(tool_name, fingerprint, is_error)`. Arguments are only ever *hashed*
(`fingerprint()`), and the tool-result body is reduced to a single
`is_error` bool. `_counts` entries are `[tool_name, count]` lists.

Consequently `last_signal()` can only produce prose like "called `edit` 3×
with the same args", and both the nudge (`agent.py:790-801`) and the hard stop
(`agent.py:1089-1097`) fall back to the same generic sentence: *"read the
relevant logs, build a minimal repro, and re-check the contract."* That is
advice the model can acknowledge without acting on — it names no log, no file,
no error.

### 3. The hard stop re-delivers everything that just happened

`_finalize_with_note` (`agent.py:1099-1118`) joins every accumulated preamble
into the delivered text:

```python
accumulated = "\n\n".join(self.accumulated_text_parts)
delivered = accumulated + note if accumulated else note.strip()
```

But every transport already rendered those preambles live, as they were
emitted. `agent.py:685` publishes `text_before_tools` for each one, and:

| transport | handler | effect |
|---|---|---|
| Mattermost | `mattermost.py:477` → `on_text_complete` (`mattermost_display.py:96-111`) | posts each preamble as a real post via `client.send` |
| Web UI | `websocket.py:681` | renders in-stream |
| Terminal | `chunk` (`interactive_terminal.py:72`) | streams in-place |

So a long thrash ends by repeating the whole turn back at the user — worst
exactly when the transcript most needs to be readable.

[#675](https://github.com/lmorchard/decafclaw/issues/675) fixed only half of
this: it stopped *archiving* the join (only `note` is persisted) and kept
*delivering* it, justified in `docs/loop-breaker.md` as "so the turn reads as a
whole." `test_loop_break_does_not_duplicate_accumulated_text`
(`tests/test_agent_loop_breaker.py:233`) inspects only `read_archive(...)` and
never asserts on `result.text`, which is why the delivery half survived.
`_finalize_max_iterations` shares the helper, so it has the same bug.

## Desired end state

A three-rung ladder where each rung requires a **fresh** offense, so obeying a
rung ends the escalation:

1. **Trip 1 → `NUDGE`.** Unchanged in kind: short, user-role, ephemeral,
   self-identifying as automated (#680). Now grounded in the real call/error.
2. **Trip 2 → `REDIRECT`.** A diagnosis contract: names the offending tool,
   its actual arguments and the actual repeated error text; forbids that
   specific call for the remainder of the turn; requires a root-cause
   hypothesis, the evidence that would confirm *or refute* it, and one
   read-only action to fetch that evidence.

   "Forbids" here is **prose in the redirect text, not a mechanical block** —
   the tool remains in the tool list (see *What we're NOT doing*). Enforcement
   comes from the ladder: issuing the forbidden call again is precisely the
   fresh offense that advances rung 2 → rung 3, so non-compliance is
   self-punishing without needing a tool taxonomy.
3. **Trip 3+ → `STOP`.** A handoff worth reading: what was tried, what the
   error actually was, what the agent needs in order to proceed.

Reaching rung 3 now means the agent re-offended twice *after* being told twice
— a far stronger claim than today's "one round elapsed." An agent that
complies at rung 1 or 2 sees no further loop-breaker output at all.

Plus: the hard stop (and the iteration-limit finalizer) deliver only their
note.

## Design decisions

### Escalate on fresh offenses, not standing conditions

Each signal records a watermark of where it stood at the last trip and
re-trips only when it advances past that mark.

- **Repeat signal:** per-fingerprint `last_tripped_count`. Re-trip only when
  that fingerprint's `count` exceeds it — i.e. the agent repeated the *same*
  call again after being told not to.
- **Error signal:** a monotonic `_total_errors` counter alongside the existing
  rolling window, plus `_errors_at_last_trip`. Re-trip only when new errors
  have landed since the last trip.

This is the load-bearing change; §2 and §3 are not worth building without it.
It is deliberately scoped beyond the literal report because the reported
symptom is a *consequence* of it.

### Retain the evidence, bounded

Introduce a `CallSignature` NamedTuple in `loop_breaker.py` carrying
`tool_name`, `fingerprint`, `is_error`, `args`, `error_text`. The module stays
pure/deterministic with no agent or LLM imports, so the record contract lives
next to the detector that consumes it.

`_counts` entries become a small dataclass (`_Offender`: `tool_name`, `count`,
`args`, `error_text`, `last_tripped_count`) rather than the current
`[name, count]` list — per the "new runtime state goes on the dataclass"
convention in CLAUDE.md.

Args and error text are truncated at **module constants**, not config keys
(`_MAX_ARG_CHARS = 400`, `_MAX_ERROR_CHARS = 300`). There is no reason to tune
them per-agent, and every knob is surface area.

### `LoopBreakerConfig` is untouched

No new config. The rung count is fixed at three; truncation caps are
constants. Adding `redirect_enabled` would invite a configuration in which the
ladder has a hole in the middle.

### Escalation state becomes a counter

`_nudged: bool` → `_trips: int`; `LoopVerdict` gains `REDIRECT`. `verdict()`
keeps its existing "call exactly once per recorded round" contract and its
documented state mutation.

### Delivery de-duplication

`_finalize_with_note` returns `ToolResult(text=note.strip())` — the
accumulated join is dropped from delivery as well as from the archive. The
docstring and `docs/loop-breaker.md` both need their "so the turn reads as a
whole" justification removed, since it is now false.

## Patterns to follow

- **Bug fix = test first** (CLAUDE.md). The latching bug gets a failing test
  before the fix: `record` a tripping fingerprint, take the `NUDGE`, then
  `record` a *different* fingerprint and assert `verdict() is LoopVerdict.NONE`.
  It fails against current `main`.
- **No deprecated code for test compatibility.** Changing `record()`'s tuple
  shape to `CallSignature` breaks the positional 3-tuples in
  `tests/test_loop_breaker.py` — rewrite those tests to the new path in the
  same commit rather than accepting both shapes.
- **Ephemeral nudge/redirect.** Both are appended to `self.messages` only,
  never `self.history`, never archived — the #598 reasoning (restore_history
  would resurrect a `user`-role diagnostic into every later turn) applies
  unchanged to the new rung.
- **Automated-sender disclaimer** (#680) on the redirect too, verbatim in
  spirit: user-role for directive weight, explicit non-authorship so the model
  does not confabulate agreement with a correction the user never made.
- **Docs in the same PR.** `docs/loop-breaker.md` covers escalation, trip
  conditions, config table and files list; all four sections change.

## What we're NOT doing

- **Tool-gated diagnosis** (mechanically withholding mutating tools for a
  round). "Mutating" is not a concept this codebase has —
  `tools/tool_registry.py` classifies by *priority*, not side-effect, and MCP
  and skill tools are opaque from outside. Withholding the wrong tool strands
  an agent that legitimately needed the write. Rejected on false-positive risk,
  not effort.
- **A diagnosis child-agent rung** — fork an isolated agent seeded with the
  thrash record and recent messages, have it return a root-cause hypothesis,
  inject that as the redirect content. **Deferred, not rejected.** The argument
  for it is strong: the stuck agent's context is itself the problem, and a
  fresh reader sees what it cannot. The pre-compaction memory sweep
  (`compaction.memory_sweep_enabled`) is the precedent to copy — isolated,
  restricted tools, fail-open. Deferred on cost: an LLM call on every trip,
  including on false positives. Revisit if grounded prompting proves
  insufficient in practice; it slots in cleanly as a fourth rung or as a
  replacement for rung 2's content.
- **Telemetry for loop_breaker events** — that is
  [#645](https://github.com/lmorchard/decafclaw/issues/645), already open.
  This PR keeps the existing `ctx.publish("loop_breaker", ...)` calls and adds
  one for the redirect action, so #645 has all three actions to subscribe to.
- **`_run_grace_turn`'s attribution exposure** —
  [#696](https://github.com/lmorchard/decafclaw/issues/696), separate.
- **Cross-turn loop detection.** The breaker stays per-turn.

## Open questions

- **Non-streaming terminal preambles.** With `llm.streaming = false`, the
  terminal transport handles `chunk` and `message_complete` but its
  subscription list (`interactive_terminal.py:72-134`) shows no
  `text_before_tools` handler. If preambles are genuinely not rendered there,
  dropping the join from delivery loses them in that one configuration. This
  must be **verified during implementation, not assumed**. If it is a real
  gap it is pre-existing, and the fix is to have the terminal subscribe to
  `text_before_tools` — not to keep re-delivering the join on every transport
  to paper over one.
- **Eval headroom.** `evals/diagnostic_discipline.yaml` bounds the fixture at
  `max_tool_calls: 15` / `max_tool_errors: 15`. A ladder that now permits a
  compliant recovery round may need slightly more headroom, or may need
  *less* because compliance ends the loop earlier. Check the numbers rather
  than guessing; add a case asserting the redirect rung fires.
