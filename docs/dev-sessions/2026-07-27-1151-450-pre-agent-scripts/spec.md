# Pre-agent scripts + unified response sentinels Spec

**Goal:** Let a scheduled task do its mechanical work in plain Python before the
agent turn starts, and let the agent suppress delivery when a cycle produced
nothing worth saying — so "monitor X, alert if changed" routines stop paying for
LLM round-trips to fetch data and stop emitting empty notifications.

**Source:** [#450](https://github.com/lmorchard/decafclaw/issues/450). Sentinel
unification is scope added during brainstorm (see Design decisions).

## Current state

See `research.md` for `file:line` detail. The load-bearing facts:

- A scheduled task's prompt is assembled in one expression at
  `schedules.py:497-500` — preamble + required-skill bodies + task body. There
  is no hook for anything computed beforehand, so a task that needs data must
  fetch it with tools, one LLM round-trip per step.
- Delivery funnels through `_notify_task_complete` (`schedules.py:542`), which
  emits a single inbox notification; every channel adapter subscribes to that.
  One chokepoint gates all channels.
- **Three response sentinels exist or are proposed**, with two different match
  rules. `HEARTBEAT_OK` (`heartbeat.py:115`) matches as a *substring* of the
  first 300 chars. `BACKGROUND_WAKE_OK` (`heartbeat.py:126`) is
  *start-anchored*, and its docstring says why: "requiring the sentinel at the
  start prevents mid-response mentions from accidentally" firing it. `[SILENT]`
  would be the third.
- For scheduled tasks `HEARTBEAT_OK` currently controls **only** the
  notification title and priority. It does not suppress anything, so #450's
  suppression is genuinely new behavior rather than a rename.
- The substring rule has already forced a workaround elsewhere:
  `agent.py:1220-1235` (#711) orders the loop-breaker note *before* accumulated
  preambles specifically because a preamble mentioning `HEARTBEAT_OK` would land
  in the 300-char window and silently suppress a loop-breaker alert. The comment
  ends "Don't move this."

## Desired end state

**Pre-scripts.** A schedule file may declare `pre_script: scripts/fetch.py`.
Before the turn, the runner executes it with the project interpreter, captures
stdout, and injects it into the prompt as a delimited block:

```
<pre_script_output>
{stdout, truncated at the cap}
</pre_script_output>
```

The block sits between the preamble and the task body, so the agent reads the
instructions, then the data, then the ask. Failure is **fail-open with
disclosure**: a non-zero exit, a timeout, or a missing file does not abort the
turn — the block instead carries the error text, so the agent can say "the fetch
failed" rather than the run vanishing silently.

**Suppression.** A response whose first non-whitespace token is `[SILENT]`
skips `_notify_task_complete` entirely — no inbox entry, therefore no channel
delivery. The turn is still archived, unchanged.

**Unified matching.** One helper backs all three sentinels, start-anchored:

```python
def response_starts_with_sentinel(response: str | None, sentinel: str) -> bool
```

`is_heartbeat_ok`, `is_background_wake_ok` and the new `[SILENT]` check all
delegate to it. `HEARTBEAT_OK` moves from substring to start-anchored.

## Design decisions

- **Decision:** Unify all three sentinels behind one start-anchored helper,
  rather than adding `[SILENT]` alongside two different rules.
  - **Why:** The codebase already learned the substring rule is wrong — it wrote
    the newer sentinel start-anchored on purpose, and then had to constrain
    unrelated code (`agent.py`'s note ordering) to work around the older one.
    Adding a third rule would leave three behaviors for one concept.
  - **Safe because:** `build_task_preamble` (`polling.py:60-79`) is the only
    producer of these instructions, and both branches already put the marker
    first — heartbeat: *"respond with HEARTBEAT_OK"*; scheduled: *"begin your
    summary with HEARTBEAT_OK on its own line"* (worded that way in #362 so the
    300-char scan would work). Tightening the match makes the code agree with
    the prompt. **No prompt change is needed, and none should be made.**
  - **Rejected:** reusing `HEARTBEAT_OK` for suppression — it would silently
    change what existing scheduled tasks do, and inherits the loose match.
  - **Rejected:** three independent rules — cheapest to write, worst to own.

- **Decision:** Pre-scripts run the project interpreter directly
  (`sys.executable`), never a shell.
  - **Why:** No quoting or word-splitting surface, and the frontmatter key can't
    become a second way to run arbitrary shell from a schedule (schedules already
    have `shell_patterns` for that, gated by approval).
  - **Rejected:** a generic `pre_command` string. Flexible, but reintroduces
    quoting and duplicates an existing capability.
  - **Deferred, not closed:** the key is named `pre_script` (not `pre_python`)
    so a future `pre_command` could be added beside it without a rename.

- **Decision:** Script failure is fail-open with the error surfaced in the block.
  - **Why:** Matches the project's fail-open convention for auxiliary machinery
    (memory sweep, notification producers). A monitor task that can't fetch
    should still get to say so; aborting the turn produces silence, which is the
    outcome suppression is supposed to be an explicit choice about.
  - **Rejected:** abort the turn on failure — turns a transient network blip into
    a missing run with nothing in the inbox.

- **Decision:** The script timeout does **not** count against the turn's
  iteration budget, and is its own config key.
  - **Why:** It is pre-agent — no LLM iteration has happened. Charging it to
    `max_tool_iterations` would make a slow fetch shrink the reasoning budget.
    (This confirms the issue's own answer.)

- **Decision:** stdout is captured and truncated at a module constant; stderr is
  logged, not injected.
  - **Why:** stdout is the interface; stderr is diagnostics. Injecting both
    invites a script's warnings into the model's context.

## Patterns to follow

- **Subprocess:** `asyncio.create_subprocess_exec` with an explicit timeout, as
  in `tools/shell_tools.py`. Never `shell=True`.
- **Frontmatter plumbing:** a new `ScheduleTask` field is read in
  `parse_schedule_file` (`schedules.py:59-96`) and must also round-trip through
  `serialize_to_markdown` (`schedules.py:188`) and `write_overlay`
  (`schedules.py:222`) — the overlay path rewrites the file, so a field it
  doesn't know about is silently dropped.
- **Config:** a new sub-dataclass in `config_types.py` beside
  `LoopBreakerConfig` (`config_types.py:461`), resolved the usual way.
- **Prompt delimiters:** XML-style tags matching the system-prompt section
  convention (`<soul>`, `<agent_role>`) per `docs/context-composer.md`.
- **Sandboxed paths:** resolve `pre_script` against the agent dir / workspace and
  reject escapes, mirroring `_resolve_safe` (`tools/workspace_tools.py:50`).

## What we're NOT doing

- **Migrating the newsletter skill.** The issue names it as the motivating win
  and explicitly defers it to a follow-up. This spec ships the mechanism only.
- **A generic `pre_command`.** Named above as deferred; the key name leaves room.
- **Post-scripts / hooks after the turn.** Not requested; `[SILENT]` covers the
  "nothing to say" case that motivated the pairing.
- **Changing `build_task_preamble`'s wording.** The prompts already put the
  marker first; touching them would risk the very behavior change unification is
  designed to avoid.
- **Moving `agent.py`'s note ordering** (`agent.py:1220-1235`). Unification makes
  that constraint belt-and-braces rather than the sole defence, but reordering it
  is out of scope — only its comment gets a note.
- **Reworking notification priority.** `HEARTBEAT_OK` keeps gating title and
  priority exactly as it does now; only its *match rule* changes.
- **Pre-scripts for heartbeat or interactive turns.** Scheduled tasks only.

## Open questions

- **Should `[SILENT]` also suppress the *archive*?** Default answer: **no** —
  archive unconditionally. The issue says "still archive the turn," and #362
  established that scheduled archives must retain narrative for retrospective
  consumers like `!newsletter`. Proceed on that.
- **Does suppression need a log line?** Default answer: **yes**, one `log.info`
  naming the task, so a silent cycle is distinguishable from a crashed one in
  the log. Cheap and it is the only trace a suppressed run leaves.
- **Truncation cap for stdout.** Default answer: 8000 characters, as a module
  constant, not config — same reasoning as `_MAX_ARG_CHARS` in `loop_breaker.py`.
  Revisit only if a real script exceeds it.

## Size note

The issue is boarded **P1/S**. Sentinel unification adds a refactor across
`heartbeat.py`, `conversation_manager.py` and their tests, so the realistic size
is **M**. Flagged rather than silently absorbed.
