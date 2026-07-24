# Agent diagnostic-discipline guardrails — Design Spec

**Issue:** [#598](https://github.com/lmorchard/decafclaw/issues/598)
**Status:** brainstormed + approved 2026-07-23; ready to plan
**Owner:** Les

## Problem

A session (`web-lmorchard-fa5ec853`, Gemini-Pro) fell into a ~150-message
degenerate loop debugging a skill that wouldn't load. Three behavioral failure
modes, none caused by token budget (87K/300K) — this was diagnostic-discipline
collapse:

1. **No loop-breaker.** Repeated the same failed edit-and-refresh cycle dozens
   of times, guessing at causes, never switching to diagnosis.
2. **Phantom tool call.** Wrote a full plan to re-engage Claude Code but never
   emitted the `claude_code_send` call; the user had to catch it.
3. **Apology spiral.** Nearly every turn opened with "You are absolutely
   correct, my apologies…" then repeated the same failed move.

## Key finding that shapes the design

`AGENT.md` **already** carries strong, specific guidance against the apology
spiral (lines 80–99, including the exact "You're absolutely right, my apologies"
anti-example) — and Gemini-Pro sailed past it for ~150 messages. Adding more
universal prompt text is therefore not, by itself, a reliable fix for the worst
offender. The most damaging mode (the loop) gets a **mechanical backstop**;
the softer modes get **prompt refinements**.

## Decisions

- **No model-conditional prompting.** There is no per-model prompt mechanism
  today and we are not building one now. All prompt guardrails are universal.
- **Mode 1 (loop) → mechanical loop-breaker + sharpened prompt.**
- **Modes 2 & 3 → prompt-only refinements.**
- **Detector trips on either signal** (repeated identical calls OR repeated
  failures).
- **Escalation:** nudge first, hard-stop if it keeps looping.

## Component 1 — Loop-breaker mechanism (mode 1)

**Where:** `TurnRunner.run()` in `agent.py`, inside the existing
`while self.budget.consume()` tool-iteration loop. A small per-turn detector,
updated after each round of tool results. State is **per-turn** — fresh each
turn, no cross-turn persistence.

**Detector state:** a rolling list of recent tool calls as
`(tool_name, args_fingerprint, is_error)`. `args_fingerprint` = a stable hash of
the call's arguments (e.g. `hash(json.dumps(args, sort_keys=True))`).

**Trip conditions (either):**
- Same `(tool_name, args_fingerprint)` seen ≥ `repeat_threshold` times this turn, OR
- ≥ `error_threshold` of the last `error_window` tool results were errors.

**Escalation:**
1. **First trip →** inject a synthetic `system`-role message into the turn's
   message list before the next LLM iteration:
   > "You've called `<tool>` N times with the same result / hit N tool errors
   > without progress. STOP repeating it. Switch to root-cause diagnosis: read
   > the relevant logs, build a minimal repro, re-check the contract/interface.
   > Do not repeat the same call."

   Mark the detector "nudged." Turn continues.
2. **Trips again after the nudge →** hard-stop: end the turn (one final
   no-tools LLM response that summarizes what was tried and the suggested
   diagnostic next step), surfaced to the user. Reuses the existing
   end-of-turn path (the same mechanism as the `max_tool_iterations` exhaustion
   response).

**Config:** a `LoopBreakerConfig` **top-level** sub-config —
`config.loop_breaker`, alongside `config.http` / `config.terminal` (NOT nested
under `config.agent`, which would trip the doubly-nested env-var gotcha where
`load_sub_config` only reads a nested dataclass's env vars if its JSON key is
present). Fields:
- `enabled: bool = True`
- `repeat_threshold: int = 3`
- `error_threshold: int = 4`
- `error_window: int = 6`

Defaults sit slightly above the issue's "~2" so a single legitimate retry
doesn't trip the breaker. Tunable via `config.json` (`loop_breaker` key) /
`LOOP_BREAKER_*` env, resolving through one `load_sub_config` call.

**Injection mechanism:** the nudge is a synthetic message appended to the
turn's working message list before the next LLM call — the exact insertion
point (history vs. messages, role) to be confirmed against `TurnRunner` during
planning. The hard-stop reuses the existing turn-termination path.

## Component 2 — Prompt guardrails (`AGENT.md`, all universal)

**Mode 1 — diagnosis discipline (pairs with the mechanism).** Sharpen the
existing "Name the pattern on repeated errors" block (~line 103) from generic
"name it and do something different" into the explicit rule: *two failed
attempts at the same fix = stop editing, switch to diagnosis — read the
relevant logs, build a minimal repro, re-check the contract/interface.* The
prompt carries the intent so the model ideally self-corrects before the
mechanism fires; the mechanism is the backstop.

**Mode 3 — apology spiral (light touch).** The existing block (lines 80–99) is
already strong, so rather than pile on more text that already failed, add one
connecting line tying it to the loop: *"Acknowledgement is not progress. Never
open with an apology and then repeat the same failed move — that's the loop
wearing a disguise. One clause of acknowledgement at most, then a genuinely
different move or a stop."*

**Mode 2 — phantom tool call (new, prompt-only).** Add a guardrail near the
tool-use section: *"Don't report an action as done until its tool call has
actually fired in this turn. If you describe doing something (running a command,
sending to Claude Code), emit the call in the same turn — never narrate a tool
action in prose and then stop or hand back."* Prompt-only for v1: mechanically
detecting "narrated an action it didn't emit" needs fuzzy intent-vs-emitted-call
analysis not worth the false-positive risk yet.

## Testing

**Unit tests (deterministic — the mechanism's real coverage).** Exercise the
loop-breaker detector + escalation in isolation with sequences of
`(tool_name, args_fingerprint, is_error)`:
- same `(tool, args)` reaching `repeat_threshold` → trips
- `error_threshold`-of-`error_window` errors → trips
- first trip → nudge emitted; trips again after nudge → hard-stop signal
- below thresholds → no trip; boundary (threshold−1 vs threshold)
- `enabled=False` → never trips
- per-turn reset (fresh state each turn)

**One eval (LLM behavior).** A deliberately-unloadable skill fixture; the user
asks the agent to use it. Bounded, rigorous assertions per project eval
conventions: `max_tool_calls` cap + `expect_tool_count_by_name` capping the
repeated edit tool (the agent *cannot* thrash the editor beyond ~N calls — the
breaker enforces it), with `setup.reflection_enabled: false` since we assert
tool-count discipline. Validates the combined prompt + mechanism end-to-end
against a real model.

**Deliberately skipped:** dedicated evals for modes 2 & 3. The apology spiral is
a stochastic, model-specific register (Gemini-Pro-worst; the eval model may
differ) and the phantom-call guardrail is prompt-only — an assertion on either
would be flaky and low-signal. Conscious, documented departure from "every
behavior-affecting change gets an eval"; the loop-breaker eval carries the load.

## Out of scope / deferred

- Model-conditional prompting.
- Mechanical detection of the phantom-tool-call mode (mode 2 stays prompt-only).
- Cross-turn loop detection (the postmortem loop was autonomous within-turn
  iteration; per-turn state covers it).

## Docs to update in the implementation PR

- `docs/` page covering agent behavior / guardrails (or `AGENT.md` is the
  source; note the loop-breaker in the relevant behavior doc).
- `CLAUDE.md` — one line under agent behavior if a new config block + mechanism
  warrant it; add `LoopBreakerConfig` to config docs.
