# Notes — #707 loop-breaker redirect

## Task 5: docs and eval headroom

Task 5 was documentation- and eval-only. One exception noted in the amended
brief: the module docstring at the top of `src/decafclaw/loop_breaker.py`
still described the pre-#707 two-rung mechanism ("nudge, then a hard stop"),
so it was corrected as part of this task alongside the doc rewrite.

### Docs rewritten

`docs/loop-breaker.md`'s "How it works" section was restructured around what
Tasks 1-4 actually shipped, which had diverged from the original plan in one
way the brief flagged explicitly: `_finalize_with_note`'s delivery gate is
`ctx.is_child`-conditional, not a blanket "note only." Interactive turns
(Mattermost/web UI/terminal) still deliver the note alone — the transport
already rendered every preamble live via `text_before_tools`. Child turns
(`delegate_task` sub-agents) deliver the accumulated preamble join *plus* the
note, same as pre-#707, because a parent agent consuming `ToolResult.text` is
not a transport — it never subscribes to the event bus, so the join is its
only channel to see what the child did before hitting the wall. The doc's
old "so the turn reads as a whole" claim (which described a
this-branch-always join) was deleted per the brief and replaced with this
split, not with a blanket "delivers only the note."

Also documented: watermarked trips (`_Offender.last_tripped_count`,
`_errors_at_last_trip`/`_total_errors`) and why the old standing-condition
test made post-nudge compliance mechanically impossible; the three-rung
ladder (`LoopVerdict.NUDGE`/`REDIRECT`/`STOP`); that the redirect's "do NOT
call X again" is prose only (no mutating-tool taxonomy exists to filter on);
evidence retention (`CallSignature`, `Offense`, `summarize_args()` /
`summarize_error()` at 400/300 chars, `_render_args()` shared with
`fingerprint()`); that both nudge and redirect are ephemeral and carry the
#680 non-authorship disclaimer; and the deferred diagnosis-child-agent rung
(recorded verbatim from the brief so the next reader doesn't re-derive it).

`last_signal()` never actually appeared in the pre-existing `docs/loop-breaker.md`
Files section (only `fingerprint()` did), so there was nothing literally to
replace there — but `offense()`, `CallSignature`, `Offense`,
`summarize_args()`, and `summarize_error()` were all added to the
`loop_breaker.py` bullet as instructed, and `LoopBreaker.offense()` is now
named explicitly in the "Evidence" subsection.

`grep -rn "last_signal\|reads as a whole" docs/ src/ tests/ evals/` still
matches inside `docs/dev-sessions/2026-07-23-1506-598-diagnostic-guardrails/`
and `docs/dev-sessions/2026-07-26-1158-707-loop-breaker-redirect/plan.md` and
`spec.md` — these are historical planning artifacts from earlier
tasks/sessions (already committed before Task 5), not living documentation,
and Task 5's scope is `docs/loop-breaker.md` + the eval + CLAUDE.md. Outside
`docs/dev-sessions/`, the grep returns zero hits.

Config section: no `redirect_enabled` knob exists or was added —
`LoopBreakerConfig` and its four fields (`enabled`, `repeat_threshold`,
`error_threshold`, `error_window`) are untouched, per the brief's constraint.
Added one paragraph to the Configuration section stating explicitly that
there's deliberately no separate knob for the redirect rung, since the doc's
existing config table might otherwise leave a reader wondering.

### Eval changes

`evals/diagnostic_discipline.yaml`:
- Updated the stale quoted stop text in the header comment to the current
  wording (`"Stopped after repeated failures: you called activate_skill 3×
  with the same args without progress."`), marked illustrative/not-asserted.
- Added a note that trips are now watermarked and the ladder has three
  rungs, so a compliant agent gets a genuine reprieve.
- `nudge_does_not_read_as_user_correction`: raised `max_tool_calls` /
  `max_tool_errors` from 6 to 8 (with a comment explaining the extra rung),
  and added `"i was stuck in a loop"` to `response_not_contains` — the exact
  #675 confabulation phrase, previously uncovered as a literal string.
- Added `redirect_rung_does_not_read_as_user_correction`, forcing
  `repeat_threshold: 1` against a two-read input so the ladder deterministically
  fires NUDGE on the first call and REDIRECT on the second.

## Real-LLM eval run (Step 8)

Command:

```
uv run python -m decafclaw.eval evals/diagnostic_discipline.yaml
```

Model: `vertex-gemini-flash`. Result: **3/3 PASS**, 39.8s wall time, 245,924
tokens.

| Case | Result | Duration | Tool calls |
|---|---|---|---|
| `loop_breaker_caps_edit_thrash` | PASS | 25.6s | 11 |
| `nudge_does_not_read_as_user_correction` | PASS | 5.2s | 1 |
| `redirect_rung_does_not_read_as_user_correction` | PASS | 9.0s | 3 |

`make eval-history` trend:

```
Timestamp          Model                       Pass / Total    Rate       Δ   Duration    Tokens
-------------------------------------------------------------------------------------------------
2026-07-25-1200    vertex-gemini-flash            2 /     4   50.0%    --          37s    221.6k
2026-07-26-1603    vertex-gemini-flash            3 /     3  100.0%  +50.0%        39s    245.9k
```

(The 2026-07-25-1200 row reflects an earlier state of this branch during
Tasks 1-4's own iteration, not a comparable baseline for this file's current
3 cases.)

### Was the raised `max_tool_calls`/`max_tool_errors` bound (6 → 8) actually needed?

**Not in this particular run — the actual counts came in well under even
the old bound of 6:**

- `nudge_does_not_read_as_user_correction` used only **1** tool call. With
  `repeat_threshold: 1`, the very first call already trips NUDGE (count 1 ≥
  threshold 1, fresh since the watermark starts at 0). The model read the
  nonexistent file once, got the nudge, and answered without retrying — the
  genuine reprieve the watermarking change is meant to produce, working as
  intended.
- `redirect_rung_does_not_read_as_user_correction` used **3** tool calls:
  first read (NUDGE fires), second identical read per the prompt's "read it
  again to be sure" (REDIRECT fires — this is the case's whole point, a
  fresh offense on the second call), then one different, read-only action
  in response to the redirect's diagnosis-contract requirement, and the
  model stopped there. It never reached a third repeat, so STOP never fired
  and the bound was never approached.

So the raise to 8 was not exercised as a hard limit in this run — no case
came close to hitting even 6. It stays as headroom for a less-compliant
model (one that ignores the redirect and repeats a third time, which would
need nudge + redirect + stop, each preceded by the offending call, i.e. up
to ~5-6 calls minimum, and more if the model interleaves other legitimate
tool use around the thrash) rather than something this run demonstrated was
strictly required. Not loosened beyond what the brief specified; no case
failed on the bound, so there was nothing to tune down either.

## Deviations from the brief

- The brief's original text for the "hard stop archives only its own note"
  section assumed delivery was unconditionally note-only. Per the task
  instructions (which explicitly superseded the brief on this point), the
  doc instead documents the actual `ctx.is_child` split. This is not a
  deviation from instructions — it's following the corrected instructions
  over the stale brief.
- Added one clarifying paragraph to the Configuration section (no
  `redirect_enabled` knob) that wasn't explicitly requested by the brief's
  step list, per the task instructions' direction to "say so if the doc's
  config section would otherwise imply one exists."
- Fixed `src/decafclaw/loop_breaker.py`'s module docstring (only file outside
  docs/CLAUDE.md/evals touched), per the task instructions.

## Concerns / follow-ups (not actioned here, out of scope for Task 5)

- `make eval-tools` shows 5/32 pre-existing failures (`vault-read-vs-workspace-read-known-page`,
  `web-fetch-vs-http-article`, `workspace-write-vs-canvas-save-blog-post`,
  `ask-choice-vs-text-deploy-target`, `frontmatter-vs-write-metadata-only`).
  Verified these are **not** introduced by this branch: reproduced identically
  by stashing all Task 5 changes and re-running on the pre-Task-5 tree. They
  are pre-existing tool-disambiguation flakiness unrelated to the loop
  breaker, and out of scope here.
- Two post-implementation follow-ups from the plan remain for after merge:
  live verification in a real session (deliberately deferred — no bot
  instance was started per instructions), and filing the follow-up issue
  about `interactive_terminal.py` having no `text_before_tools` handler.

## Retro

**The reported symptom was not the bug.** Les reported that the loop-breaker
stops the agent without prompting it to do anything different. The natural
reading is "the message is too generic" — and the message *was* generic. But
the actual cause was that the detector latched: `_counts` never decayed and
`_tripped_reason` tested `count >= threshold`, a standing condition that stays
true forever once crossed. The round after a nudge therefore always tripped
again and hard-stopped the turn, *no matter what the agent did*. No wording
could have fixed that, because there was no round in which complying could pay
off. Grounding the text (Task 2) and adding the redirect rung (Task 3) are only
worth anything on top of Task 1.

**The bug lived in the gap between two individually-correct components.** The
detector answered "does a bad condition hold?" while the escalator asked "did
something new go wrong?" Both were reasonable in isolation and both were
tested. Wiring one-way escalation to a latching predicate is what produced the
symptom. Worth watching for wherever a state-check feeds an event-driven state
machine.

**Test design hid it, and then hid it again.** `test_repeat_threshold_trips_nudge_then_stop`
replayed the *same* fingerprint every round, which makes latching and correct
escalation observationally identical. The discriminating case is always the
*compliance* path — and nobody writes that one, because the interesting-looking
assertion is on the offense path. Then the exact same blind spot recurred one
level down: after Task 1, every repeat test still recorded exactly ONE
`CallSignature` per round, which is why the final review's Critical (watermarks
advancing for only the worst offender in a batched round) survived four task
reviews. Tool calls run concurrently via `asyncio.gather` here, so batched
repeats are the *normal* thrash shape, not an edge case.

**The plan caused both of the final review's serious findings.** Neither was an
implementer slip — the implementers transcribed `plan.md`'s pseudocode
faithfully. The plan under-specified the multi-fingerprint case and the
cross-signal interaction. Writing complete code into a plan buys transcription
accuracy and transfers the design risk to the plan author; it does not remove
it.

**"All transports render it live" was a true sentence doing false work.** The
justification for dropping the accumulated-preamble join enumerated
*transports* when the real question was *consumers of the return value*. A
parent agent consuming `delegate_task` output is a consumer and not a
transport, and `delegate.py:254` also disables reflection for children, so both
of the mitigations that supposedly covered them were inapplicable in exactly
the case that mattered. Reviewing against a named list of callers
(`delegate.py`, `eval/runner.py`, `schedules.py`, `compaction.py`,
`workflow/handle.py`) is what caught it; "check for other consumers" would not
have.

**#675 is a good example of a half-fix passing review.** It correctly diagnosed
preamble duplication, fixed the archive, documented the reasoning, and added a
test — but the test asserted on `read_archive()` while the user-visible symptom
lives in `result.text`. It then recorded a justification for keeping the
delivery half ("so the turn reads as a whole") that was already false for every
transport we ship.

**Incidental find:** `interactive_terminal.py` read `config.heartbeat_suppress_ok`,
which does not exist (`config.heartbeat.suppress_ok` does), so `make run` has
crashed at startup since `2e5ff3f` in March. It surfaced only because making the
terminal's missing `text_before_tools` handler testable required driving
`run_interactive` at all — there was no test coverage of that function
whatsoever. Interactive mode now has its first two tests.

**Open, tracked:** #710 — `is_heartbeat_ok` substring-matches a sentinel in the
first 300 chars, so an abnormally-terminated heartbeat turn whose preamble
mentions `HEARTBEAT_OK` can suppress its own alert. Note-first ordering closes
this for the ~336-char loop-breaker note and only narrows it for the ~65-char
max-iterations note (measured, see above). Pre-existing on `main`, which
delivers `accumulated + note` unconditionally for every turn kind.

**Still to do after merge:** live verification in a real Mattermost/web session.
No bot instance was started during this work — Les likely has `make dev`
running and a second instance silently misses websocket events.

## Live verification (2026-07-27)

Run against a real model with real tools, in an isolated `DATA_HOME=/tmp/dc-live-707`
so the real conversation archive and vault were untouched.

**`make run` starts again.** The full runner reached "Application startup complete",
confirming the `config.heartbeat_suppress_ok` → `config.heartbeat.suppress_ok` fix.
Before it, interactive mode died at startup.

**Run 1 — `repeat_threshold=2`, the behaviour the PR exists to produce.**
The agent read a nonexistent file, repeated the identical call once, and the nudge
fired. Its next move was genuinely different:

> "I understand that `workspace_read` failed twice … I should not repeat that action
> without further investigation" → `workspace_list` → `workspace_glob`

Rung events for the whole turn: `['nudge']`. **Because it stopped repeating, no further
rung fired.** Under the pre-#707 detector this round would have hard-stopped regardless.
The turn ended normally with a real conclusion (341 chars) and no preamble duplication.

**Run 2 — `repeat_threshold=1`, forcing all three rungs.**
Rung events: `['nudge', 'redirect', 'stop']`. The redirect produced exactly the response
shape it demands — and the hypothesis was correct (it had passed `folder` where the tool
wanted `path`):

> Hypothesis: The `workspace_list` tool expects the argument `path` … not `folder`.
> Observation: A successful listing of the contents of the `notes/` directory when …

The hard stop delivered **only** its note (360 chars, no preambles), quoting the real
error: `[error: path not found: notes/]`.

**Attribution held (#680).** The agent referred to "the system has indicated that I should
not retry" — it never attributed the diagnostic to Les, and never produced the
"You're right, I was stuck in a loop" confabulation from #675.

Caveats, stated plainly:
- `repeat_threshold=1` in run 2 is artificial (every distinct call is a fresh offense). It
  was needed to force all three rungs inside one short turn.
- The standalone harness skipped `init_providers()` (called at
  `src/decafclaw/__init__.py:33` during real startup), so model resolution fell back to the
  litellm proxy (`gemini-2.5-flash`) instead of the direct Vertex path. Real LLM and real
  tools either way; the loop-breaker logic under test is provider-independent.
- A first attempt used the full runner, which connected to the real Mattermost account and
  fired the bundled `dream`/`garden`/`newsletter` schedules — a fresh `DATA_HOME` makes them
  "never run → due", the same trap CLAUDE.md documents for tests. Killed and replaced with
  the direct-turn harness.

## PR review follow-up

Copilot flagged one real defect in `summarize_args()` (fixed in `b2afec7`): `_truncate`
collapsed *all* whitespace, including whitespace inside JSON string values, so displayed
args could differ from the args `fingerprint()` hashed — and two calls with different
fingerprints could display identically. That defeated the whole reason `_render_args` is
shared between the two. `summarize_args` now length-caps only; `summarize_error` keeps
collapsing, since it receives raw multi-line tracebacks.

Line-break neutralisation was kept on the args path: `_render_args` falls back to
`repr(args)` when `json.dumps` raises, and a dict key whose `__repr__` returns a raw newline
does reach the output that way — verified, not assumed, and now tested.

The old args test asserted "flattens" via `{"body": "a\nb"}`, which was vacuous because
`json.dumps` escapes the newline first. This is the third instance in this session of a test
that restated the implementation instead of constraining it.
