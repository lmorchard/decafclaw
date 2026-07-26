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
