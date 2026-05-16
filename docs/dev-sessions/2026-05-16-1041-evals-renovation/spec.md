# Evals renovation — spec

Renovate the eval system so it's actually productively useful: real coverage of the always-loaded vault skill, real tool-name assertions on every test that targets a specific tool, real bounds on every test, and the harness pieces that let us seed and assert the things we couldn't before.

## Why now

The 2026-04-24 eval-coverage audit (`docs/dev-sessions/2026-04-24-0941-eval-coverage/evals-audit.md`) catalogued the work and split it into 16 spin-out issues. Three weeks later, **one** of those issues has landed (#349 → PR #429 — the load-bearing `expect_tool` assertions). The remaining 13 open `evals`-labelled issues are mostly P2/S now that the audit narrowed scope, but they haven't been picked up.

The 2026-05-16 re-audit (`audit.md` in this dir) found that things have gotten worse, not better, in the meantime:

- Pass rate dropped from 86.2% to 80.0% (`vertex-gemini-flash`).
- The PR #429 smoke test (`expect_tool: vault_journal_append`) now fails — the agent reaches for `notes_append` instead, a critical-priority tool added after the smoke test was written. Real tool-description disambiguation problem.
- `memory-semantic.yaml` is still silently broken; the one test in it that used to pass-for-the-wrong-reason now fails outright (its `allowed_tools` blocks `tool_search`).

## Goals

1. Replace the silently-broken `memory-semantic.yaml` with a vault-skill eval file that forces real tool use.
2. Tighten existing memory evals (bounds + AND/OR semantics + stale names) so they catch regressions.
3. Fix the bit-rotted PR #429 smoke test by disambiguating `notes_append` from `vault_journal_append` — either via tool-description tightening, smoke-test prompt change, or both — and add a `tool_choice` case for the pair.
4. Build out tool-selection coverage for the high-value tool families (vault, workspace, shell, conversation, delegate, tool deferral) using the new `expect_tool` / `expect_no_tool` / `expect_tool_count_by_name` assertions.
5. Add the harness pieces that unblock realistic assertions: post-turn workspace state, conversation history seed, `response_contains_all`, judge-prompt coverage for non-`response_contains` failures.
6. Add pass-rate trend tracking so future regressions show up over time.

## Non-goals

- **Matrix runner (#350).** Demoted to P3. Single-model coverage is plenty until the rest of the suite stops being thin.
- **Scheduled/heartbeat mode simulation, cancel probes, effort-level probes, Claude Code sandbox** — all P3 and deferred per the original audit; nothing has changed there.
- **Fixing the underlying behavior gaps the evals surface.** When a new eval exposes a real bug (e.g. agent doesn't reach for memory on an under-specified prompt), file the bug and move on — don't sink this session into chasing fixes.
- **`expect_tool_args` (H3).** Brittle. Defer until a real test demands it.

## Approach

Single dev session, multiple PRs. Four cohesive PRs in dependency order:

- **PR-A — Harness polish.** #354 (`response_contains_all` + judge prompt fix) + #352 (post-turn workspace assertions) + #353 (`setup.conversation_history` seed). Three tiny lifts; all unblock or strengthen later PRs.
- **PR-B — Vault + memory cleanup (the P1).** #339 (vault evals replacing `memory-semantic.yaml`) + #348 (tighten `memory.yaml` + `memory-multi-turn.yaml`). Same files, shared review context.
- **PR-C — Tool-selection coverage sweep.** #430 (tool-deferral evals using the new assertions) + #344 (deferral context budget) + #340 (workspace tools) + #341 (shell tools) + #343 (delegate decision) + #342 (conversation post-compaction recall — uses PR-A's seed). Each is a small YAML file; PR them in one batch with one review pass.
- **PR-D — Pass-rate trend tracking.** #351 only. Quality-of-life; lets us detect future regressions across runs.

## Validation gates

Each PR:

- `make lint` + `make check` + `make test` clean.
- `make eval` against the default model finishes with no regressions vs. the pre-renovation baseline (recorded in `audit.md`).
- Where new evals are added, they pass against the default model.

End of session:

- Full suite passing against the default model.
- `evals/history.jsonl` exists with at least the renovation's run logged.
- All 13 in-scope issues either closed or explicitly deferred with a reason.

## Sequencing rationale

- **PR-A first** because #352 and #353 are referenced by tests in PR-B and PR-C. Doing them upfront avoids retroactive test rewrites.
- **PR-B before PR-C** because `memory-semantic.yaml` being silently broken is the single worst signal in the suite — fix it before adding more files.
- **PR-D last** because trend tracking is only useful with a stabilized baseline.

## Baseline (2026-05-16 re-audit)

`vertex-gemini-flash`: **24/30 pass (80.0%)**, 370s wall, 1.05M tokens. Six failures: one outright-broken file (`memory-semantic.yaml`'s remaining tool-using test), four LLM-behavior gaps (memory triggering, vault/notes disambiguation, project-skill plan-tool registry confusion), one stale test name. Detail in `audit.md`.

Renovation success metric: **≥ 90% pass rate after PR-B + PR-C land**, with the remaining failures either filed as known LLM-behavior issues or fixed-in-this-session if scope-appropriate.

## Risk / open questions

- **The PR #429 smoke test is bit-rotted.** `notes_append` competes hard with `vault_journal_append`. PR-B has to decide: tighten the tool descriptions (best leverage, riskier), change the smoke prompt (cheap, narrow), or change `expect_no_tool` to include `notes_append` (cheap, narrowest). Default to "fix descriptions first, then re-evaluate" — that's where #17's lessons say the value is.
- **PR-C is the largest blob.** ~6 issues' worth of new YAML. If review fatigue is real, split into PR-C1 (vault-adjacent: deferral + workspace) and PR-C2 (the rest). Decide at PR-C kickoff.
- **Real behavior gaps will surface.** The 2026-05-16 baseline already has 4 such gaps. File each as an issue if it's not already, but don't sink the renovation into chasing fixes — except for F1 (vault/notes disambiguation), which is directly in PR-B's path.
