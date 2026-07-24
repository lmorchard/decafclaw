# Loop Breaker

The loop breaker is a per-turn, mechanical backstop against autonomous
tool-call thrash — the agent repeating the same failed move (or hitting a
run of errors) inside a single turn's tool-iteration loop, without ever
switching to diagnosis. It shipped alongside sharpened `AGENT.md`
guardrails as part of [#598](https://github.com/lmorchard/decafclaw/issues/598),
after a real session spent ~150 messages stuck editing-and-refreshing a
skill that wouldn't load.

Prompting alone wasn't a reliable fix — `AGENT.md` already carried strong
anti-apology-spiral guidance and the model sailed past it. So the worst
failure mode (the loop itself) gets a mechanical detector; the softer
failure modes (apology spirals, phantom tool calls — see below) stay
prompt-only refinements in `AGENT.md`.

## How it works

`TurnRunner` (`agent.py`) creates one `LoopBreaker` per turn — state does
not persist across turns, only within the current turn's tool-iteration
loop. After each round of tool calls, `TurnRunner._handle_tool_calls`
records the round's `(tool_name, args_fingerprint, is_error)` signatures
(`fingerprint()` in `loop_breaker.py` is a stable hash of the call's name +
sorted-JSON arguments) and asks the breaker for a verdict.

**Trip conditions (either signal fires it):**

- The same `(tool_name, args_fingerprint)` pair has been seen
  `repeat_threshold` or more times this turn — a genuine repeat-the-same-call
  loop, or
- `error_threshold` or more of the last `error_window` tool results were
  errors — a run of failures even if the calls themselves vary.

**Escalation is one-way per turn:**

1. **First trip → nudge.** A `system`-role diagnostic message is appended
   telling the model to stop repeating the move and switch to root-cause
   diagnosis (read logs, build a minimal repro, re-check the contract). The
   turn continues normally into the next iteration.
2. **Any subsequent trip → hard stop.** The turn ends immediately with a
   short summary of what was tried and the same diagnostic next step,
   delivered as the turn's final response — the same termination path used
   when `max_tool_iterations` is exhausted.

The loop breaker only runs on the "keep going" path — a genuine end-turn
signal (a widget pause, `EndTurnConfirm`, or `end_turn=True` from a tool)
already ends the turn earlier and takes precedence over the breaker.

### The nudge is ephemeral, deliberately

The nudge message is appended to the turn's in-memory `messages` list only
— it is **never** written to `self.history` and **never** archived. This is
intentional: archiving it would let it get restored via `restore_history`
on a page reload or process restart (a `system`-role message is a real LLM
role, not UI-only), permanently polluting the context of every later turn
with a diagnostic aside that only made sense in the moment it fired. The
hard-stop's final summary, by contrast, *is* archived normally — it's a
real assistant response the user should see and the agent should remember
saying.

## Prompt guardrails (`AGENT.md`)

Two related, prompt-only behaviors from the same investigation live as
plain guidance in `src/decafclaw/prompts/AGENT.md`, not in this mechanism —
there's no per-model prompt system today, so none of this is
model-conditional:

- **"Two strikes → diagnose, don't re-edit."** After two failed attempts at
  the same fix, stop editing and switch to root-cause diagnosis. This is
  the prompt-side companion to the loop breaker: the model should ideally
  self-correct before the mechanism ever needs to trip.
- **"Acknowledgement is not progress."** Opening a turn with an apology and
  then repeating the same failed move is the loop wearing a disguise — one
  clause of acknowledgement at most, then a genuinely different move or a
  stop.
- **"Emit the call, don't narrate it."** Don't report an action (running a
  command, sending to Claude Code, editing a file) as done until its tool
  call has actually fired in this turn — never narrate an action in prose
  and then stop or hand back without emitting it. This "phantom tool call"
  mode is prompt-only by design: mechanically detecting "narrated an action
  it didn't emit" needs fuzzy intent-vs-emitted-call analysis that isn't
  worth the false-positive risk yet.

There are no evals for these two prompt-only guardrails — see
[Eval Loop](eval-loop.md) and the design spec for why (stochastic,
model-specific register for the apology spiral; low-signal for a
prompt-only phantom-call check). The mechanical loop breaker is covered by
`evals/diagnostic_discipline.yaml`, which caps a deliberately-unloadable
skill fixture at `max_tool_calls: 15` / `max_tool_errors: 15` and relies on
the breaker (or a clean first-try fix) to stay inside that bound.

## Configuration

The `loop_breaker` config group (`data/{agent_id}/config.json`), top-level
alongside `http` / `terminal` — not nested under `agent`, which would trip
the doubly-nested env-var gotcha where `load_sub_config` only reads a
nested dataclass's env vars if its JSON key is present:

```json
{
  "loop_breaker": {
    "enabled": true,
    "repeat_threshold": 3,
    "error_threshold": 4,
    "error_window": 6
  }
}
```

| Field | Type | Default | Env Var |
|-------|------|---------|---------|
| `enabled` | bool | `true` | `LOOP_BREAKER_ENABLED` |
| `repeat_threshold` | int | `3` | `LOOP_BREAKER_REPEAT_THRESHOLD` |
| `error_threshold` | int | `4` | `LOOP_BREAKER_ERROR_THRESHOLD` |
| `error_window` | int | `6` | `LOOP_BREAKER_ERROR_WINDOW` |

Defaults sit slightly above "a single legitimate retry" so normal retry
behavior doesn't trip the breaker. Set `enabled: false` to disable the
mechanism entirely (the `AGENT.md` prompt guardrails still apply either
way).

## Files

- `src/decafclaw/loop_breaker.py` — `LoopBreaker`, `LoopVerdict`,
  `fingerprint()` — pure/deterministic, no agent or LLM imports
- `src/decafclaw/agent.py` — `TurnRunner._handle_tool_calls` wiring
  (`_extract_call_signatures`, nudge injection, `_finalize_loop_break`)
- `src/decafclaw/config_types.py` — `LoopBreakerConfig`
- `src/decafclaw/prompts/AGENT.md` — the diagnosis / acknowledgement /
  phantom-call prompt guardrails
- `tests/test_loop_breaker.py`, `tests/test_agent_loop_breaker.py` —
  detector unit tests + `TurnRunner` wiring tests
- `evals/diagnostic_discipline.yaml` — the bounded, real-LLM eval
