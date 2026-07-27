# Pre-agent scripts + unified response sentinels — Implementation Plan

**Goal:** Let a scheduled task precompute data in plain Python before the turn,
let it suppress delivery when there is nothing to say, and collapse three
response-sentinel matching rules into one start-anchored helper.

**Approach:** Three vertical slices in dependency order. Slice 1 lands the shared
sentinel helper (independently valuable — it removes a false-positive class that
already forced a workaround in `agent.py`). Slice 2 uses that helper for
`[SILENT]`. Slice 3 adds `pre_script`, orthogonal to both. Docs ride with the
slice that changes the behavior they describe.

**Tech stack:** Python 3.13, pytest (`-n auto`), `asyncio.create_subprocess_exec`,
`dataclasses`, `yaml`.

---

## Spec clarification resolved here (read this first)

The spec lists "changing `build_task_preamble`'s wording" as a non-goal, but
`[SILENT]` cannot fire unless something tells the agent to emit it. Those aren't
in conflict once the reading is pinned:

**`[SILENT]` is opt-in per task.** The *task author* writes the instruction in
their own schedule body ("if nothing changed, reply with `[SILENT]` and nothing
else"). `build_task_preamble` is not touched, so no existing task's behavior
changes and the global `HEARTBEAT_OK` wording stays as #362 left it.

Why this reading: the spec ships "the mechanism only" and defers the newsletter
migration, and the non-goal exists specifically to avoid perturbing the preamble.
A global instruction would also be wrong on its own terms — it would tell *every*
scheduled task to consider suppressing itself, which is a policy change, not a
mechanism.

Consequence for docs: `docs/schedules.md` must state that `[SILENT]` does nothing
unless the task body asks for it. Phase 2 covers that. If you want it global
instead, that is one sentence in `polling.py` plus reversing this decision —
raise it before Phase 2 rather than after.

---

## Phase 1: One start-anchored sentinel helper

Collapses `is_heartbeat_ok` and `is_background_wake_ok` onto a shared
start-anchored matcher. Standalone value: `HEARTBEAT_OK` stops matching
mid-response, which is the false-positive class `agent.py:1220-1235` currently
works around by ordering.

**Files:**
- Modify: `src/decafclaw/heartbeat.py` — add `response_starts_with_sentinel`;
  both existing predicates delegate to it.
- Modify: `src/decafclaw/agent.py` — comment only, at the `Note comes FIRST`
  block (~1220-1235): record that start-anchoring now also prevents this, so the
  ordering is belt-and-braces rather than the sole defence. **No code change.**
- Test: `tests/test_heartbeat.py` — rewrite one test, add the shared-helper cases.

**Key changes:**
- `response_starts_with_sentinel(response: str | None, sentinel: str) -> bool` — new.
- `is_heartbeat_ok(response)` / `is_background_wake_ok(response)` — bodies become
  one-line delegations; names and signatures unchanged, so all four call sites
  keep working (`heartbeat.py:182`, `schedules.py:516`,
  `conversation_manager.py:1541`, plus tests).

```python
_SENTINEL_SCAN_CHARS = 300


def response_starts_with_sentinel(response: str | None, sentinel: str) -> bool:
    """True when `response` begins with `sentinel`, ignoring leading whitespace.

    Start-anchored on purpose. The original `HEARTBEAT_OK` check matched the
    sentinel anywhere in the first 300 characters, so a response that merely
    *mentioned* it counted — and that already forced unrelated code to work
    around it (`agent.py` orders the loop-breaker note first so a preamble
    mentioning the sentinel can't bury the alert). `BACKGROUND_WAKE_OK` was
    written start-anchored to avoid exactly this; this is that rule, shared.

    Safe to tighten because `polling.py:build_task_preamble` — the only producer
    of these instructions — already asks for the marker first: "respond with
    HEARTBEAT_OK" (heartbeat) and "begin your summary with HEARTBEAT_OK on its
    own line" (scheduled, worded that way in #362).
    """
    if not response:
        return False
    return response[:_SENTINEL_SCAN_CHARS].lstrip().lower().startswith(
        sentinel.lower()
    )


def is_heartbeat_ok(response: str | None) -> bool:
    """Check if a response indicates nothing to report."""
    return response_starts_with_sentinel(response, "HEARTBEAT_OK")


def is_background_wake_ok(response: str | None) -> bool:
    """True when a wake turn's result isn't worth surfacing to the user."""
    return response_starts_with_sentinel(response, "BACKGROUND_WAKE_OK")
```

**Tests — one rewrite, because it asserts the behavior being removed:**

`test_is_heartbeat_ok_case_insensitive` currently asserts
`is_heartbeat_ok("Everything is fine. heartbeat_ok") is True` — a mid-response
match. Per CLAUDE.md ("no deprecated code for test compatibility") it is rewritten
in this commit rather than accommodated:

```python
def test_is_heartbeat_ok_case_insensitive():
    assert is_heartbeat_ok("heartbeat_ok") is True
    assert is_heartbeat_ok("Heartbeat_OK — nothing to report") is True


def test_is_heartbeat_ok_ignores_mid_response_mention():
    """Tightened in #450: a response that merely mentions the sentinel is not
    a quiet cycle. This is the class that forced agent.py's note ordering."""
    assert is_heartbeat_ok("Everything is fine. heartbeat_ok") is False
    assert is_heartbeat_ok("I could say HEARTBEAT_OK but things changed") is False


def test_is_heartbeat_ok_allows_leading_whitespace():
    assert is_heartbeat_ok("  \n HEARTBEAT_OK") is True


def test_sentinel_helper_is_shared():
    """Both predicates must go through the same matcher, or they drift again."""
    from decafclaw.heartbeat import response_starts_with_sentinel
    assert response_starts_with_sentinel("FOO_OK trailing", "foo_ok") is True
    assert response_starts_with_sentinel("prefix FOO_OK", "FOO_OK") is False
    assert response_starts_with_sentinel("x" * 300 + "FOO_OK", "FOO_OK") is False
    assert response_starts_with_sentinel(None, "FOO_OK") is False
    assert response_starts_with_sentinel("", "FOO_OK") is False
```

Untouched and expected to still pass: `test_is_heartbeat_ok_present`,
`test_is_heartbeat_ok_beyond_300_chars`, `test_is_heartbeat_ok_not_present`, both
`is_background_wake_ok` tests, `test_schedules.py::test_heartbeat_ok_detected`
(fixture is `"HEARTBEAT_OK — nothing to report."` — leading, so unaffected), and
`test_background_wake_integration.py`.

**Verification — automated:**
- [x] `test_is_heartbeat_ok_ignores_mid_response_mention` fails before the change
      (proves the test discriminates) — **4 failed, 6 passed** pre-implementation;
      the mid-response and word-boundary assertions both failed as `assert True is False`
- [x] `.venv/bin/python -m pytest tests/test_heartbeat.py tests/test_schedules.py tests/test_background_wake_integration.py -q` passes — **89 passed**
- [x] `make test` passes — **3586 passed, 2 skipped** (baseline was 3581; +5 new)
- [x] `make check` passes — **All checks passed!**, pyright **0 errors, 0 warnings**

**Adaptation (plan was incomplete here).** The plan specified `startswith`, which
would have *lost* a property the existing code had: `_BACKGROUND_WAKE_OK_RE` used
`\b` "so BACKGROUND_WAKE_OKAY doesn't match." A plain `startswith` matches
`HEARTBEAT_OKAY`. The helper therefore compiles a cached regex and applies `\b`
only when the sentinel ends in a word character — it cannot be applied to
`[SILENT]`, since `\b` after `]` would demand a following word char and
`"[SILENT]"` alone would fail. Two extra tests cover this
(`test_is_heartbeat_ok_requires_a_word_boundary`,
`test_sentinel_helper_word_boundary_only_for_word_endings`).

**Verification — manual:**
- [x] Confirm no call site relied on substring matching: re-read
      `heartbeat.py:211`, `schedules.py:516`, `conversation_manager.py:1541` —
      **all three are plain boolean consumers.** heartbeat: `ok` → an OK/ALERT log
      line + returned dict. schedules: `ok` → notification title/priority.
      conversation_manager: `suppress` for WAKE turns. None inspects position or
      relies on substring semantics.

---

## Phase 2: `[SILENT]` suppresses scheduled delivery

A scheduled response beginning with `[SILENT]` emits no notification, so no
channel delivers. The archive is untouched.

**Files:**
- Modify: `src/decafclaw/schedules.py` — sentinel check before
  `_notify_task_complete` (~513-521); one `log.info`.
- Modify: `docs/schedules.md` — document `[SILENT]`, including that it is inert
  unless the task body instructs it.
- Test: `tests/test_schedules.py`.

**Key changes:** in `run_schedule_task`, replacing the unconditional notify:

```python
result_text = (await future) or "(no response)"
from .heartbeat import is_heartbeat_ok, response_starts_with_sentinel
ok = is_heartbeat_ok(result_text)
# A suppressed cycle must stay distinguishable from a crashed one in the log —
# the skipped notification is otherwise its only trace.
if response_starts_with_sentinel(result_text, SILENT_SENTINEL):
    log.info("Scheduled task %r returned %s — suppressing notification",
             task.name, SILENT_SENTINEL)
else:
    await _notify_task_complete(
        config, event_bus, task.name, result_text, ok, conv_id,
    )
```

- `SILENT_SENTINEL = "[SILENT]"` — module constant in `schedules.py`.
- The returned dict is unchanged, so callers still see `response` and `is_ok`.
  Suppression governs *delivery*, not the return value.
- The `except` branch's `_notify_task_complete` is **not** gated — a failure
  always notifies, whatever the response said.

**Tests:**
```python
async def test_silent_suppresses_notification(self, config):
    """[SILENT] skips delivery; the turn still returns its text."""
    # fake_run returns "[SILENT] nothing changed"
    # assert notify not called, and result["response"] is intact

async def test_silent_must_lead(self, config):
    """A mid-response mention is not suppression."""
    # fake_run returns "Checked feeds. Not [SILENT] though."
    # assert notify WAS called

async def test_error_notifies_even_if_response_was_silent(self, config):
    """The except branch is ungated."""
```
Patch target: `decafclaw.notifications.notify` (what `_notify_task_complete`
calls), asserted by call count rather than by reading the inbox.

**Verification — automated:**
- [x] `test_silent_suppresses_notification` fails before the change — **1 failed,
      2 passed**: `assert 1 == 0` on `mock_notify.call_count` (the two
      must-still-notify guards passed already, as expected)
- [x] `.venv/bin/python -m pytest tests/test_schedules.py -q` passes — **50 passed**
- [x] `make test` passes — **3589 passed, 2 skipped** (+3 from Phase 1's 3586)
- [x] `make check` passes — deferred to the Phase 3 run; Phase 2 touches no JS
      and pyright ran clean on the same tree at Phase 1

**Verification — manual:**
- [ ] Read the new `docs/schedules.md` section: is it clear that `[SILENT]` does
      nothing until a task body asks for it?

---

## Phase 3: `pre_script` frontmatter, execution, and injection

A schedule declaring `pre_script:` runs it with the project interpreter before
the turn and injects stdout into the prompt.

**Files:**
- Modify: `src/decafclaw/config_types.py` — `PreScriptConfig`.
- Modify: `src/decafclaw/config.py` — resolve and attach (mirror `loop_breaker`
  at `config.py:195,489-490,585`).
- Modify: `src/decafclaw/schedules.py` — `ScheduleTask.pre_script`; parse;
  serialize; overlay patch key; `_run_pre_script`; prompt injection.
- Modify: `docs/schedules.md`, `docs/config.md`.
- Test: `tests/test_schedules.py`.

**Key changes:**

```python
# config_types.py — beside LoopBreakerConfig
@dataclass
class PreScriptConfig:
    """Pre-agent scripts for scheduled tasks (#450).

    A scheduled task may precompute data in plain Python before its turn
    starts; stdout is injected into the prompt. The timeout is deliberately
    NOT charged against `agent.max_tool_iterations` — no LLM iteration has
    happened yet, so a slow fetch must not shrink the reasoning budget.
    """
    enabled: bool = True
    timeout_sec: int = 60
```

```python
# schedules.py
_PRE_SCRIPT_MAX_CHARS = 8000   # constant, not config — see spec


async def _run_pre_script(config, task: ScheduleTask) -> str:
    """Run a task's pre_script and return the text to inject.

    Fail-open with disclosure: a missing file, non-zero exit, or timeout yields
    an error string rather than aborting the turn, so the agent can report "the
    fetch failed" instead of the run vanishing. Returns "" when there is
    nothing to inject.
    """
    if not task.pre_script or not config.pre_script.enabled:
        return ""
    script = _resolve_pre_script_path(config, task)
    if script is None:
        return f"[pre_script error: {task.pre_script!r} is outside the allowed roots]"
    if not script.is_file():
        return f"[pre_script error: {task.pre_script!r} not found]"
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, str(script),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            cwd=str(config.workspace_path),
            env={**os.environ,
                 "DECAFCLAW_AGENT_ID": config.agent.id,
                 "DECAFCLAW_ROUTINE_NAME": task.name,
                 "DECAFCLAW_WORKSPACE": str(config.workspace_path)},
        )
        out, err = await asyncio.wait_for(
            proc.communicate(), timeout=config.pre_script.timeout_sec)
    except TimeoutError:
        proc.kill()
        return (f"[pre_script error: timed out after "
                f"{config.pre_script.timeout_sec}s]")
    except OSError as exc:
        return f"[pre_script error: {type(exc).__name__}: {exc}]"
    if err:
        # stderr is diagnostics, not interface — logged, never injected.
        log.warning("pre_script %r stderr: %s", task.name,
                    err.decode(errors="replace")[:500])
    if proc.returncode != 0:
        return f"[pre_script error: exited {proc.returncode}]"
    return out.decode(errors="replace")[:_PRE_SCRIPT_MAX_CHARS]
```

Path resolution mirrors `_resolve_safe` (`tools/workspace_tools.py:50`) and
accepts the two roots the issue names:

```python
def _resolve_pre_script_path(config, task: ScheduleTask) -> Path | None:
    """Resolve pre_script against workspace/ then data/{agent_id}/.
    Returns None if it escapes both (containment via is_relative_to)."""
    for root in (config.workspace_path, config.agent_path):
        root = root.resolve()
        candidate = (root / task.pre_script).resolve()
        if candidate.is_relative_to(root):
            return candidate
    return None
```

Injection, replacing the assembly at `schedules.py:497-500`:

```python
preamble = build_task_preamble("scheduled task", task.name)
body = substitute_body(task.body, skill_dir=skill_dir)
loaded_skills = _render_required_skill_bodies(config, required_skills)
pre_output = await _run_pre_script(config, task)
pre_block = (
    f"<pre_script_output>\n{pre_output.rstrip()}\n</pre_script_output>\n\n"
    if pre_output else ""
)
prompt = (preamble
          + (f"{loaded_skills}\n\n" if loaded_skills else "")
          + pre_block
          + body)
```

Round-trip plumbing — all three are required, or the overlay path (which rewrites
the whole file) silently drops the field:
- `ScheduleTask`: `pre_script: str = ""`
- `parse_schedule_file`: `pre_script=str(meta.get("pre_script", ""))`
- `serialize_to_markdown`: `if task.pre_script: fm["pre_script"] = task.pre_script`
- `write_overlay`: accept `pre_script` in the patch dict

**Tests:**
```python
async def test_pre_script_output_reaches_the_prompt(self, config, tmp_path):
    """Fixture script echoes JSON; assert the captured prompt contains the
    delimited block and the payload."""

async def test_pre_script_failure_is_disclosed_not_fatal(self):
    """sys.exit(3) → prompt carries '[pre_script error: exited 3]' and the
    turn still runs."""

async def test_pre_script_timeout_is_disclosed(self):
    """timeout_sec=1 against a sleeping script → error text, turn still runs."""

async def test_pre_script_missing_file_is_disclosed(self):

async def test_pre_script_path_escape_rejected(self):
    """pre_script: '../../etc/passwd' → error, no execution."""

def test_pre_script_round_trips_through_serialize(self):
    """parse → serialize → parse preserves pre_script."""

def test_write_overlay_preserves_pre_script(self, config):
    """The overlay rewrite must not drop it."""

async def test_no_pre_script_leaves_prompt_unchanged(self):
    """Absent key → no block, byte-identical to today's assembly."""
```
The prompt is captured by asserting on `enqueue_turn`'s `prompt` kwarg (patch
`manager.enqueue_turn`), which is how the existing schedule tests reach it.

**Verification — automated:**
- [x] `test_pre_script_output_reaches_the_prompt` fails before the change —
      **7 failed, 1 passed** pre-implementation
- [x] `.venv/bin/python -m pytest tests/test_schedules.py -q` passes — **8 passed**
      for `-k pre_script`; **61 passed** for the file
- [x] `make test` passes — **3599 passed, 2 skipped** (+10 from Phase 2's 3589)
- [x] `make check` passes — **All checks passed!**, pyright **0 errors**. First run
      exited 2 on a ruff import-sort error (`PreScriptConfig` inserted beside
      `LoopBreakerConfig` rather than alphabetically); fixed with `ruff --fix`
- [x] `make config` still resolves — prints `pre_script.enabled = true`,
      `pre_script.timeout_sec = 60.0`
- [!] `pytest --durations=25` — no pre_script test in the top 25. **Partly false as
      written:** `test_pre_script_timeout_is_disclosed` IS the slowest test in the
      file at **0.11s**. Not a real failure — the plan assumed a 1s timeout, but
      `timeout_sec` was declared `float` so the test uses `0.05`, making the
      timeout path ~0.11s rather than ~1s. The intent (no fixed multi-second
      waits) holds; the assertion was written against a design that changed.

**Verification — manual:**
- [ ] Write a real 3-line script under `workspace/scripts/`, point a disabled
      schedule at it, run the task once by hand, and read the assembled prompt in
      the archive to confirm the block reads well to a model.

---

## Phase 4: Eval coverage

`<pre_script_output>` is new LLM-visible prompt content, so CLAUDE.md requires a
case. No `tool_choice` case is needed — no tool description changed.

**Files:**
- Modify: `evals/` — one case in an existing scheduled/routine theme, or a new
  `evals/pre-script.yaml` if none fits (decided by reading the file list at
  execution time).

**Key changes:** a case whose fixture pre-script emits three items, asserting the
response references them without any fetch tool being called — i.e. the agent used
the injected block instead of re-fetching:

```yaml
- name: "uses pre_script output instead of re-fetching"
  setup:
    config_overrides:
      reflection.enabled: false   # required with expect_no_tool
  expect:
    max_tool_calls: 4
    expect_no_tool: [http_get]
```

**Verification — automated:**
- [!] The eval passes on a real model run — **NOT DONE, and not doable as
      planned.** The eval harness cannot reach this code path: `eval/runner.py`
      never calls `run_schedule_task`, never sets `task_mode`, and
      `_KNOWN_SETUP_KEYS` (`runner.py:491`) has no schedule fixture — it runs
      interactive-style turns only. A `pre_script` case would need a new harness
      capability (a schedule fixture that executes a task through the scheduled
      path), which is a bigger change than #450 and not a config knob the generic
      `config_overrides` mechanism can reach.

      No eval was written or run. Deciding this **before** spending real model
      calls on a case that structurally cannot exercise the feature.

      Coverage today is the 8 unit tests from Phase 3, which do assert the
      LLM-visible artifact directly: the presence of `<pre_script_output>`, the
      payload inside it, and its position *before* the task body. What they can't
      assert is the behavioral question an eval would answer — whether a real
      model uses the injected data instead of re-fetching.
- [!] `make eval-history` shows no regression — **not applicable**, no eval run.

**Verification — manual:**
- [!] Confirm `expect_no_tool` is paired with `reflection.enabled: false` —
      **not applicable**, no eval case exists to configure.

---

## Plan self-review

**Spec coverage.** Pre-scripts → Phase 3. `[SILENT]` → Phase 2. Sentinel
unification → Phase 1. Fail-open disclosure → Phase 3 `_run_pre_script`. Timeout
not charged to iterations → Phase 3 `PreScriptConfig` docstring + separate key.
stdout-only → Phase 3 (stderr logged). Archive unaffected → Phase 2 (no archive
code touched). Suppression log line → Phase 2. 8000-char cap → Phase 3 constant.
Docs → Phases 2 and 3. Eval → Phase 4. The three spec non-goals that could leak
(preamble wording, `agent.py` reorder, newsletter migration) are each named as
untouched.

**Placeholder scan.** No TBD/TODO. Every symbol referenced later is defined here:
`response_starts_with_sentinel`, `SILENT_SENTINEL`, `PreScriptConfig`,
`_PRE_SCRIPT_MAX_CHARS`, `_run_pre_script`, `_resolve_pre_script_path`,
`ScheduleTask.pre_script`. The one deliberate deferral — which eval file the
Phase 4 case lands in — is a decision made by reading the directory, not missing
design.

**Type consistency.** `response_starts_with_sentinel(response, sentinel)` keeps
that argument order at all three call sites. `is_heartbeat_ok` /
`is_background_wake_ok` keep their names and signatures, so the four existing
callers are untouched. `pre_script` is the field name in `ScheduleTask`, the
frontmatter key, and the overlay patch key — one spelling throughout.

**Known risk.** Phase 1 changes behavior that one existing test relies on,
rewritten in the same commit. If an *un-tested* consumer depended on substring
matching, Phase 1 surfaces it as a `make test` failure rather than silently —
which is why Phase 1 runs the full suite, not only the three named files.
