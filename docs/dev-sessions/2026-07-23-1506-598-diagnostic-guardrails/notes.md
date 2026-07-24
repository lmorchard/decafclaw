# Diagnostic-discipline guardrails — Session Notes

**Issue:** [#598](https://github.com/lmorchard/decafclaw/issues/598)
**Status:** shipped

## Trigger

A real session (`web-lmorchard-fa5ec853`, Gemini-Pro) spent ~150 messages
stuck debugging a skill that wouldn't load, at only 87K/300K token budget —
not a context problem, a diagnostic-discipline collapse. Three distinct
failure modes: repeated the same failed edit-and-refresh cycle dozens of
times (no loop-breaker), wrote out a plan to re-engage Claude Code but never
emitted the tool call (phantom tool call), and opened nearly every turn with
"You are absolutely correct, my apologies…" before repeating the same failed
move (apology spiral).

## Design decisions

- **No model-conditional prompting.** There is no per-model prompt mechanism
  today and we didn't build one for this. All `AGENT.md` guardrails are
  universal, even though the triggering session was specifically
  Gemini-Pro-worst.
- **Key finding that shaped the whole design:** `AGENT.md` already carried
  strong, specific anti-apology-spiral guidance (the exact "You're
  absolutely right, my apologies" anti-example) — and the model sailed past
  it for ~150 messages anyway. That's the evidence that more prompt text
  alone isn't a reliable fix for the worst failure mode. So the loop itself
  (mode 1) got a **mechanical backstop**; the softer modes (2 and 3) stayed
  **prompt-only** refinements.
- **Detector trips on either signal**, not just one: same `(tool_name,
  args_fingerprint)` seen `repeat_threshold` (default 3) or more times this
  turn, OR `error_threshold` (default 4) or more of the last `error_window`
  (default 6) tool results were errors. Repeats-with-varying-args-but-all-
  failing was a real pattern in the original session and neither signal
  alone would have caught both shapes of thrash.
- **Escalation: nudge, then stop** — not straight to hard-stop. First trip
  injects a diagnostic nudge and lets the turn continue (the model gets a
  chance to self-correct); only a second trip after the nudge ends the turn.
  This avoids over-triggering on a single legitimate burst of retries while
  still capping genuine loops.
- **The nudge is ephemeral — messages-only, deliberately never archived.**
  It's appended to `self.messages` (the in-memory turn list) but never
  written to `self.history` or the archive. Reasoning: archiving it would
  let `restore_history` resurrect it on a page reload or process restart — a
  `system`-role message is a real LLM role, not UI-only — permanently
  polluting every later turn's context with a diagnostic aside that only
  made sense in the moment it fired. The hard-stop's *final summary*, by
  contrast, is archived normally, because it's a genuine response the user
  should see and the agent should remember having said. Verified with a
  dedicated wiring test (end-turn precedence + non-archival of the nudge).
- **Config is a top-level `config.loop_breaker`, not nested under
  `config.agent`.** This deliberately dodges a known project gotcha:
  `load_sub_config` only recurses into a nested dataclass's env vars when
  the JSON key is present, so a doubly-nested group can silently drop env
  overrides. Top-level sidesteps it entirely, same pattern as `config.http` /
  `config.terminal`.
- **Mode 2 (phantom tool call) is prompt-only for v1**, by design — no
  mechanical detection was built. Reliably detecting "narrated an action in
  prose but didn't emit the tool call" needs fuzzy intent-vs-emitted-call
  analysis that isn't worth the false-positive risk yet. It's a straight
  `AGENT.md` addition ("Emit the call, don't narrate it").
- **Mode 3 (apology spiral) got a light-touch connector line, not a
  rewrite.** Since the existing block already failed to hold the line once,
  piling on more text seemed unlikely to help by itself; the new line ties
  it explicitly to the loop concept ("Acknowledgement is not progress...
  that's the loop wearing a disguise") so it reads as one coherent framework
  with mode 1's mechanical backstop, rather than a second unrelated rule.
- **Evals skipped for modes 2 and 3, on purpose** — a conscious, documented
  departure from the project convention that every behavior-affecting change
  gets an eval case. The apology spiral is a stochastic, model-specific
  register (the eval model may not reproduce Gemini-Pro's worst behavior)
  and the phantom-call guardrail is prompt-only; an assertion on either would
  be flaky and low-signal. The one eval that exists (mode 1, the mechanical
  loop-breaker) carries the load for this feature.
- **Eval bound: `max_tool_calls: 15` / `max_tool_errors: 15`**, both true
  upper bounds (not counts) per the eval runner's `expect` semantics. Chosen
  because a real 150-message loop would blow through either bound many times
  over, so the bound is only satisfiable if the breaker (or a clean
  first-try fix) actually caps the thrash. `expect_tool` (asserting which
  tool the model reaches for first) was tried and dropped — which path the
  model takes (`activate_skill` vs. `tool_search` vs. `skill_validate`)
  varies run to run and isn't what the eval cares about. Verified live: one
  run hit the breaker for real (`activate_skill("broken_skill")` 3× with
  identical args → `"[loop-breaker] Stopped: you called activate_skill 3x
  with the same args without progress."`); another run had the model give
  up cleanly after one `tool_search` call. Both are legitimate outcomes
  within the bound.

## Implementation notes

- `fingerprint()` hashes `tool_name` + sorted-JSON args (`sha1`), so argument
  order doesn't cause false negatives.
- `LoopBreaker` tracks `{fingerprint: [tool_name, count]}` (not just a bare
  count) so `last_signal()` can name the offending tool in both the nudge
  and the hard-stop summary — an adjustment made during implementation to
  satisfy the test asserting the tool name appears in the reason string.
- The loop-breaker check only runs on the normal "keep going" path in
  `_handle_tool_calls` — a genuine end-turn signal (widget pause,
  `EndTurnConfirm`, or `end_turn=True`) already returns earlier and takes
  precedence, confirmed by a dedicated precedence test.
- `_finalize_loop_break` mirrors the existing `max_tool_iterations`
  exhaustion path: preserves `accumulated_text_parts`, archives the final
  assistant message, and runs the same post-turn compaction check.

## Final shipped state

Nine commits on `598-diagnostic-guardrails`, in order: design spec →
implementation plan → `LoopBreakerConfig` (top-level config) → `LoopBreaker`
detector/escalation (deterministic core, `loop_breaker.py`) → `TurnRunner`
wiring (nudge + hard-stop in `agent.py`) → fix keeping the nudge ephemeral +
end-turn-precedence test → `AGENT.md` guardrail sharpening (three prose
edits: two-strikes-diagnose, acknowledgement-is-not-progress,
emit-the-call-don't-narrate-it) → the `diagnostic_discipline.yaml` eval +
fixture → a follow-up tightening the eval bound to 15/15 and fixing a
fixture-sync comment → this docs pass.

Shipped surface:

- `src/decafclaw/loop_breaker.py` — `LoopBreaker`, `LoopVerdict`,
  `fingerprint()`.
- `src/decafclaw/agent.py` — per-turn `LoopBreaker` instance on `TurnRunner`,
  `_extract_call_signatures`, verdict handling in `_handle_tool_calls`,
  `_finalize_loop_break`.
- `src/decafclaw/config_types.py` / `config.py` — `LoopBreakerConfig`,
  top-level `config.loop_breaker`, `LOOP_BREAKER_*` env.
- `src/decafclaw/prompts/AGENT.md` — three sharpened/added guardrails.
- `tests/test_loop_breaker.py`, `tests/test_agent_loop_breaker.py` —
  deterministic unit coverage.
- `evals/diagnostic_discipline.yaml` — one bounded, real-LLM eval.
- `docs/loop-breaker.md` (new), linked from `docs/index.md`; `CLAUDE.md`
  key-files + agent-behavior bullet (this pass).

No src/test changes in this docs pass — verified `make check` stays clean.
