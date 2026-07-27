# Notes — #450

## Outcome

Phases 1–3 implemented and green. Phase 4 (eval) **not done** — structurally
infeasible, see below. `make test` **3599 passed, 2 skipped** (baseline 3581, so
+18 tests). `make check` clean.

## Phase 4 is not doable as planned, and that was worth finding before spending

The plan called for an eval case asserting a model uses injected `pre_script`
output instead of re-fetching. The harness can't express it: `eval/runner.py`
never calls `run_schedule_task`, never sets `task_mode`, and `_KNOWN_SETUP_KEYS`
(`runner.py:491`) has no schedule fixture. Evals run interactive-style turns only.

A case would need a new harness capability — a schedule fixture that drives a task
through the scheduled path — which is larger than #450 and is *not* something the
generic `config_overrides` mechanism reaches (CLAUDE.md explicitly warns against
adding bespoke `setup.*` keys, but this would be a new capability, not a knob).

I checked this before writing the case rather than after, so no model calls were
spent on something that couldn't have exercised the feature.

**What covers it instead:** the 8 Phase 3 unit tests assert the LLM-visible
artifact directly — the block's presence, its payload, and its position before the
task body. What they can't answer is the behavioral question (does a real model
actually use the data rather than re-fetch). That gap is real and unclosed.

**Candidate follow-up issue:** teach the eval harness to run a scheduled task
(schedule fixture + `run_schedule_task` path). Would unlock eval coverage for
`pre_script`, `[SILENT]`, `required-skills`, and per-task tool restrictions — none
of which are eval-reachable today.

## Two plan assertions turned out wrong

Both marked `[!]` in `plan.md` rather than force-ticked:

1. **`startswith` was the wrong implementation.** The plan specified it, but the
   existing `_BACKGROUND_WAKE_OK_RE` carried `\b` "so BACKGROUND_WAKE_OKAY doesn't
   match" — `startswith` silently loses that. The shared matcher compiles a cached
   regex and applies the boundary only when the sentinel ends in a word character;
   it can't be applied to `[SILENT]`, since `\b` after `]` would demand a
   following word char and `"[SILENT]"` alone would fail. Caught by writing a
   word-boundary test that the plan didn't have.
2. **The durations assertion was written against a design that changed.**
   `test_pre_script_timeout_is_disclosed` *is* the slowest test in its file — but
   at 0.11s, because `timeout_sec` ended up `float` and the test uses `0.05`. The
   plan assumed a 1s timeout. Intent (no fixed multi-second waits) holds.

## Interaction worth knowing

`[SILENT]` and `HEARTBEAT_OK` are independent sentinels. A quiet cycle that
replies `[SILENT]` yields `is_ok=False` in `run_schedule_task`'s return dict,
because `is_ok` is specifically the `HEARTBEAT_OK` measure. Harmless today — the
notification that would have used `is_ok` for its title and priority is the exact
thing being suppressed. It would matter if someone later made suppression
conditional; if so, decide then whether `[SILENT]` should imply `ok`.

## End-to-end dry run

Real script, real subprocess, real assembly (mocked LLM only):

```
You are running a scheduled task: "feed-watch".
... preamble ...

<pre_script_output>
{
  "routine": "feed-watch",
  "new_items": [ {...}, {...} ]
}
</pre_script_output>

Summarize any new items above in one line each.
If there are no new items, reply with [SILENT] and nothing else.
```

`notifications sent: 0` (suppressed), response preserved in the return dict,
`DECAFCLAW_ROUTINE_NAME` visibly reached the script.

## Scope held

Newsletter migration, a generic `pre_command`, post-turn hooks, `build_task_preamble`
wording, and `agent.py`'s note ordering were all named as out of scope in the spec
and none were touched. The `agent.py` change is comment-only.

## Size

Boarded P1/**S**; actual is **M** — sentinel unification touched `heartbeat.py`,
`agent.py` (comment), `schedules.py`, `config.py`, `config_types.py`, two doc
pages, and two test files. Flagged at spec time, not discovered late.
