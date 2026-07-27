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
records the round's calls as `CallSignature`s (`tool_name`, `fingerprint`,
`is_error`, plus truncated `args_text`/`error_text` — see below) and asks
the breaker for a verdict. `fingerprint()` in `loop_breaker.py` is a stable
hash of the call's name + sorted-JSON arguments.

### Trip conditions are watermarked, not standing

Both signals are unchanged in *kind*:

- The same `(tool_name, args_fingerprint)` pair has been seen
  `repeat_threshold` or more times this turn — a genuine repeat-the-same-call
  loop, or
- `error_threshold` or more of the last `error_window` tool results were
  errors — a run of failures even if the calls themselves vary.

But each now fires only on a **fresh** offense, compared against a watermark
set the last time that signal tripped:

- The repeat signal tracks a per-fingerprint `count` and a
  `last_tripped_count` watermark (on the module-private `_Offender`
  dataclass). It trips again only once `count` has grown past
  `last_tripped_count` — i.e. the agent repeated the call *again* after
  being told to stop, not merely that the count is still at or above
  threshold.
- The error signal tracks a monotonic `_total_errors` counter (never
  trimmed) alongside `_errors_at_last_trip`. It trips again only once a
  *full* `error_threshold` worth of new errors has landed since the last trip
  (`_total_errors - _errors_at_last_trip >= error_threshold`), **and** those
  errors are dense enough to still fill the `error_window`
  (`sum(_recent_errors) >= error_threshold`). Two necessary conditions: the
  delta is the freshness half, the window is the density half — a slow
  trickle of errors interleaved with successes over many rounds isn't thrash,
  and shouldn't accumulate into a trip. On the first trip the delta condition
  is free, since a window sum at threshold implies a total at threshold.

Both watermarks are advanced for *every* signal the trip consumed, not just
the one named in the `Offense`:

- A repeat trip watermarks **all** currently-fresh fingerprints, not only the
  worst offender it quotes. Tool calls run concurrently
  (`asyncio.gather` in `tool_execution.py`), so a repeated multi-call batch —
  the canonical thrash shape — pushes several fingerprints over threshold in
  the same round. Watermarking only the worst left the others permanently
  "fresh", so they re-tripped on later rounds off counts that had stopped
  growing.
- A repeat trip also consumes the error credit (`_errors_at_last_trip`). The
  errors that piled up alongside the thrash are already paid for by the rung
  the agent just got; leaving them uncounted let the error signal fire on the
  very next round off the same failures. That mattered most for the redirect,
  which *instructs* the model to "take exactly one read-only action" — and in
  the broken environments where the breaker fires, that diagnostic read
  frequently errors. The mechanism was punishing the behavior it had just
  demanded.
- The reverse case needs no handling: when the error branch runs there are no
  fresh repeat offenders by construction, because the repeat branch is
  checked first.

This all matters because the old implementation tested a standing condition
(`count >= threshold`, forever true once crossed). That meant the very
round *after* a nudge fired always tripped again too — compliance was
mechanically impossible, because the count from before the nudge was still
sitting at or above threshold. Watermarking makes escalation event-shaped:
an agent that stops repeating the call gets a genuine reprieve, and only a
new instance of the offending pattern advances the ladder.

### Three rungs

1. **First fresh offense → nudge.** A `user`-role diagnostic message is
   appended telling the model to stop repeating the move and switch to
   root-cause diagnosis (read logs, build a minimal repro, re-check the
   contract). It is `user`-role rather than `system` because models weight
   user-role directives more heavily for mid-turn corrections (matching
   `_run_grace_turn`). The turn continues normally into the next iteration.
2. **A second fresh offense → redirect.** The nudge was ignored (or
   re-offended after being obeyed once) and the agent tripped again on the
   *same or a different* signal. The redirect is a diagnosis contract: it
   names the actual offending call/error using the retained evidence (see
   below), says not to call that tool with those arguments again this turn,
   and requires the reply to contain, in order, (1) a falsifiable
   root-cause hypothesis, (2) the one observation that would confirm or
   kill it, and (3) exactly one read-only action that fetches that
   observation — nothing else. The turn continues into the next iteration.
3. **A third fresh offense → hard stop.** The turn ends immediately with a
   short handoff (what was tried, what failed, what would unblock it),
   delivered as the turn's final response — the same termination path used
   when `max_tool_iterations` is exhausted (`_finalize_with_note`, below).

Reaching the stop therefore requires re-offending twice *after* two
warnings — nudge on the first fresh offense, redirect on the second, stop
only on the third. `LoopVerdict` has four values: `NONE`, `NUDGE`,
`REDIRECT`, `STOP`.

The loop breaker only runs on the "keep going" path — a genuine end-turn
signal (a widget pause, `EndTurnConfirm`, or `end_turn=True` from a tool)
already ends the turn earlier and takes precedence over the breaker.

### The redirect's "do NOT call X again" is prose, not a filter

The redirect never removes the offending tool from the tool list — enforcing
that mechanically would need a mutating-vs-read-only taxonomy that doesn't
exist. `tool_registry` classifies tools by priority (critical / deferred /
etc.) for context-budget purposes, not by whether a call mutates state, so
there's no existing axis to filter on without risking cutting off a
legitimate use mid-turn. Enforcement instead comes from the ladder itself:
if the model reissues the forbidden call anyway, that reissue *is* the fresh
offense that advances the rung to `STOP`. The prose is the whole mechanism.

### Evidence is retained so the redirect can be specific

The old detector hashed the args away and kept only an `is_error` bool, so
its diagnostic text could only ever be generic ("you called a tool
repeatedly"). `CallSignature` (`tool_name`, `fingerprint`, `is_error`,
`args_text`, `error_text`) now carries the call's rendered arguments and, on
error, the tool's error body, one-line via `summarize_args()` /
`summarize_error()` at `_MAX_ARG_CHARS = 400` / `_MAX_ERROR_CHARS = 300`.
The two are *not* truncated the same way: `summarize_error()` flattens all
whitespace (real tool-result bodies are multi-line tracebacks, and the
collapsed form reads fine in a single-sentence prompt), but `summarize_args()`
only length-caps and neutralizes raw line breaks — it must not otherwise
touch whitespace, because whitespace inside a JSON string argument value is
call data, not formatting, and collapsing it would let two calls that
`fingerprint()` treats as distinct (e.g. `"a  b"` vs `"a b"`) render
identically in the text shown to the model.
`LoopBreaker.record()` stores the latest values per fingerprint on
`_Offender` — `error_text` is *cleared* on a successful occurrence, so the
"the error every time" / "the failure each time" wording stays true for a
call that failed early and then started succeeding (reachable because
repeated *successful* identical calls trip the repeat signal too);
`verdict()` copies them into a frozen `Offense` dataclass
(`reason`, `tool_name`, `args_text`, `error_text`) retrievable via
`LoopBreaker.offense()`, which **always** returns an `Offense` — an
empty-field instance before any trip, never `None`, so callers need no
`None` check. Both the nudge and the redirect quote `offense()` to name the
actual failing call and its actual error instead of giving generic advice.
`_render_args()` is the single canonical-JSON renderer shared by
`fingerprint()` and `summarize_args()`, so the text shown to the model
always matches the text that was hashed and counted — there is no path
where the displayed args drift from what actually tripped the breaker. That
guarantee is exactly why `summarize_args()` may only length-cap: any
whitespace normalization on top of `_render_args()`'s output would
reintroduce drift between what's displayed and what's counted.

### Nudge and redirect are ephemeral, deliberately

Both messages are appended to the turn's in-memory `messages` list only —
**never** written to `self.history` and **never** archived. This is
intentional: archiving either would let it get restored via
`restore_history` on a page reload or process restart (a `user`-role
message is a real LLM role, not UI-only), permanently polluting the context
of every later turn with a diagnostic aside that only made sense in the
moment it fired. The hard-stop's final summary, by contrast, *is* archived
normally — it's a real assistant response the user should see and the agent
should remember saying.

Both also open by disclaiming authorship — *"Automated diagnostic from the
agent runtime — the user did not send this"* (the redirect: *"Second
automated diagnostic..., again, the user did not send this"*). That isn't
decoration. The `user` role means the model reads either message as the
human speaking: in the session behind #675 it replied *"You're right. I was
stuck in a loop"* to a correction the user never made, then carried the
fabricated exchange forward as context. Confabulated agreement is its own
failure mode — an agent that believes it was just criticized over-corrects
and abandons lines of investigation that were fine, invisibly. The
`[loop-breaker]` prefix alone wasn't enough; it reads as a label on a
human's message rather than as the sender (#680). Both rungs carry this
disclaimer for the same reason.

### The hard stop archives only its own note — and delivery splits on "did anyone watch this live"

A turn that ran many iterations has already archived each iteration's
assistant preamble as it was emitted (`_handle_tool_calls`), and every
preamble was also published live as `text_before_tools` — rendered by
Mattermost (`mattermost_display.on_text_complete`), the web UI
(`web/websocket.py`), and the terminal (`interactive_terminal.on_event`) as
the turn ran. `_finalize_with_note` (shared by the loop-breaker stop and the
iteration-limit finalizer) always **archives only the note**. What it
*delivers* as `ToolResult.text` depends on whether anyone was watching the
turn live:

- **Watched turns** deliver the note alone: interactive turns (Mattermost,
  web UI, terminal) and background wakes, which fire on the user's *real*
  conversation and therefore do have a live subscriber. The transport already
  rendered each preamble live, so re-joining them into the delivered text
  would duplicate the whole turn: invisible on a one-preamble turn, a wall of
  repeated text on a long thrash, which is exactly when the transcript most
  needs to be readable (#675). This matches the normal end-of-turn path,
  which also delivers only its final content.
- **Unwatched turns** deliver the accumulated preamble join *plus* the note,
  same as before #707, because `ToolResult.text` is their only channel:
  - a **child agent** (`ctx.is_child`, set by `delegate.py` and by
    `compaction.py`'s memory sweep). A parent agent consuming a child's
    output is not a transport — it never subscribes to the event bus, so
    `ToolResult.text` (routed back through `delegate.py`'s `run_child_turn`
    or the workflow `subagent` primitive) is the child's only channel for its
    own work. `delegate.py` also sets `skip_reflection = True` for children,
    so the reflection judge doesn't give the parent a second chance to see it
    either.
  - a **heartbeat or scheduled turn** (`ctx.task_mode`, checked against
    `agent.py`'s `_UNWATCHED_TASK_MODES`). These run on synthetic conv_ids no
    transport subscribed to, so nothing renders their `text_before_tools` —
    yet their `ToolResult.text` *is* parsed (`heartbeat.py`) and displayed to
    the user afterwards (`interactive_terminal.py`,
    `tools/heartbeat_tools.py`, `schedules.py`). The original gate tested
    `ctx.is_child` alone, which encoded "child == no live viewer" and dropped
    everything these turns accumulated before hitting the wall.

Dropping the join for any unwatched shape silently loses work no one ever
saw; keeping it for a watched shape duplicates the turn. Archiving is
identical in every case — only `note` is ever appended to `self.history` /
the archive.

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

`error_threshold` is used twice by the error signal — as the size of the
rolling `error_window` slice that must be errors (density) *and* as the
number of new errors that must have accrued since the last trip (freshness).
Widening `error_window` therefore makes the density half easier to satisfy
but does nothing to the freshness half; it is a "how spread out may these
errors be" knob, not a sensitivity knob.

There is deliberately no `redirect_enabled` (or similar) knob. The redirect
is rung 2 of one ladder driven by the same four fields above — it isn't a
separately-togglable feature, and adding a knob for just that rung would let
someone disable the middle of an escalation sequence without disabling the
rest, which doesn't correspond to any real intent.

## Files

- `src/decafclaw/loop_breaker.py` — `LoopBreaker` (`record()`, `verdict()`,
  `offense()`), `LoopVerdict`, `CallSignature`, `Offense`, `fingerprint()`,
  `summarize_args()`, `summarize_error()` — pure/deterministic, no agent or
  LLM imports
- `src/decafclaw/agent.py` — `TurnRunner._handle_tool_calls` wiring
  (`_extract_call_signatures`, nudge/redirect injection, `_finalize_loop_break`,
  `_finalize_with_note`, `_UNWATCHED_TASK_MODES`)
- `src/decafclaw/interactive_terminal.py` — the terminal's
  `text_before_tools` handler (streaming-guarded, so preambles render exactly
  once whether or not the model streams)
- `src/decafclaw/config_types.py` — `LoopBreakerConfig`
- `src/decafclaw/prompts/AGENT.md` — the diagnosis / acknowledgement /
  phantom-call prompt guardrails
- `tests/test_loop_breaker.py`, `tests/test_agent_loop_breaker.py` —
  detector unit tests + `TurnRunner` wiring tests
- `tests/test_interactive_terminal.py` — terminal preamble rendering
- `evals/diagnostic_discipline.yaml` — the bounded, real-LLM eval
