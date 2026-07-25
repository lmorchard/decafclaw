# Notes — #649 unattended approval bypasses

Run driven by `agent-session express`. Tier `needs-review`, so the run completed the work and
stopped to surface the risk-gated diff before opening a PR.

## What changed

| File | Change |
|---|---|
| `src/decafclaw/context.py` | +12: `UNATTENDED_TASK_MODES` + `is_unattended` property |
| `src/decafclaw/tools/shell_tools.py` | removed the `heartbeat-admin` auto-approve; deny-before-prompt on unattended miss |
| `src/decafclaw/tools/skill_tools.py` | removed `is_heartbeat` from the confirmation condition; deny-before-prompt; refreshed the stale precedence comment |
| `docs/tools.md` | the "Approval sources" list documented the removed bypass as rule 1 |

## Evidence

Frozen at `86170b1` (re-anchored after rebase; originally `b53ac87`).

| Check | At freeze | After | Verified by |
|---|---|---|---|
| C1 unattended shell not auto-approved | fail | pass | independent verifier |
| C2 unattended shell denies without prompting | fail | pass | independent verifier |
| C3 unattended workspace skill denied | fail | pass | independent verifier |
| G1 allowlisted unattended command still runs | pass | pass | verifier |
| G2 bundled skill still activates (negative control) | pass | pass | verifier |
| G3 `always` grant still authorizes | pass | pass | verifier |
| G4 interactive still prompts | pass | pass | verifier |
| G5 `test_shell_allowlist.py` | 29 passed | 29 passed | verifier |
| G6 `test_skills.py` + `test_background_tools.py` | 130 passed | 130 passed | verifier |
| G7 `make check` | exit 0 | exit 0 | verifier |
| Full suite | 3417 + 7 new | 3425 passed, 2 skipped | verifier |

Tamper diff against the freeze: **empty** for `tests/test_unattended_approval.py`. `git diff <freeze>
-- tests/` is empty entirely — no test file changed, added, skipped, or xfailed since the freeze.
Manifest diff is the `Frozen at` line only; no CRITERION or CHECK command altered.

Suite went 3424 → 3425 across the rebase (+1 upstream test). The guard invariant is relative
("nothing lost, newly skipped, or newly failing"), so it absorbed that instead of tripping — which is
the fix for the brittle-absolute guard that tripped on the #638 run.

## Two findings from self-review, neither fixed here

**1. A child agent delegated from a heartbeat turn still stalls 60s.** `delegate.py` builds children
via `Context.for_task` with `task_mode="child_agent"`, and sets
`child_ctx.request_confirmation = parent_ctx.request_confirmation` (`delegate.py:210`). So a child of
an *unattended* turn is not `is_unattended`, falls through to a prompt, and routes it to the parent's
handler — which for a heartbeat parent is the same unanswerable path C2 exists to eliminate. It still
*denies* (no security hole), but the 60s stall survives on that route.

The issue's "What we're NOT doing" excludes `child_agent` with the rationale that a child inherits the
parent's `request_confirmation` and so "does have someone who can answer." That rationale is right for
a child of an interactive turn and **wrong for a child of a heartbeat turn** — my own reasoning was
incomplete. The honest predicate is that a child should inherit its parent's *answerability* rather
than be excluded outright.

Not fixed here: it extends past the frozen criteria and past the explicit non-goal, so changing it now
would be the implementer widening its own spec. Follow-up issue.

**2. `Context.fork()` does not propagate `task_mode`** — it constructs a fresh `Context` and applies
only explicit `**overrides` (`context.py:156-171`), so a forked context defaults to `task_mode=""`,
i.e. *interactive*. Currently harmless: `grep -rn "\.fork(" src/decafclaw --include=*.py` finds no
callers outside `pty.fork()`. `fork_for_tool_call` is safe — it uses `copy.copy(self)`, so every flat
field including `task_mode` propagates. Worth knowing because `CLAUDE.md` records that
`fork_for_tool_call` *did* once drop `task_mode` and silently disabled scheduled newsletter email for
weeks; the same slip in a security predicate would silently re-open the bypass.

## Side effect worth recording

`compaction.py:71` sets `task_mode="scheduled"` for the memory-sweep child, so those turns are now
classified unattended. Verified unreachable for both approval paths: that context sets
`tools.allowed = set(VAULT_TOOLS.keys())` and pre-approves all of them (`compaction.py:78-81`), so
neither `check_shell_approval` nor `tool_activate_skill` is reachable. The classification is also
*correct* — a memory sweep genuinely has no human — but if anyone widens that child's tool set,
deny-by-default is what they will get.

## On the denial message

The frozen check asserts the denial text contains `"was denied by user"`, so the unattended denial
reuses the existing string rather than more accurate wording. That is the frozen check constraining
the implementation, which is its job. No amendment: the pre-existing 60s-timeout path already returns
the same loose phrasing for a denial no user issued, so this is not a new inaccuracy. Worth a
follow-up across both paths.
