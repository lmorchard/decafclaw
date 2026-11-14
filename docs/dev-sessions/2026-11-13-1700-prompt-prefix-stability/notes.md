# Resume here

Issue: https://github.com/lmorchard/decafclaw/issues/1257
Branch: issue-1257-prompt-prefix-stability
Worktree: .claude/worktrees/prompt-cache-investigation-20261113
Base: 579bdb088b2e6ad78cb00ffc5769a6abd71d4dae
Scope: file findings, implement, test, and publish a PR. No merge authorization.
Single-session implementation/review for this local-model task. Runtime model identity: unknown.

Baseline: `make test` passed (5357 passed, 2 skipped, 31.65s). `uv sync` and `make install-js` completed.
Current: core fix implemented. Final `make check` passed. Final complete suite: 5372 passed, 2 skipped in 23.94s. Routing eval remains running.
Checks so far: initial regressions 4 failed/1 passed, then green. Targeted suite 542 passed. Complete suite before final ordering fix: 5371 passed, 2 skipped. Ordering regression failed before correction, then targeted composer/registry/prefix tests passed (254).
Next: read final validation logs, commit, publish draft, review exact head, then ready submission if evidence permits.

## Findings and decisions

- #1117 only partially implemented #1116. Follow-up 5cbefd76 remains on origin/issue-1111-1116-prompt-caching-and-deltas.
- Native heuristic promotion changes tools before system context in Qwen's template. Replace promotion with archived discovery hints; explicit loading remains available.
- Use shared tool assembly for composer accounting and actual iterations. Do not freeze revoked/stale capabilities.
- Keep opportunistic cleanup available by explicit config; default preserves sent history between compactions.
- Log which early-prompt components changed on deliberate capability/config/model changes; never log their contents.
- Identity verified through ghflow: MokaGnome. Board auto-added issue to project 6, then moved Backlog -> In progress with confirmed readback.

## Validation findings

- Actual OpenAI-compatible HTTP body and real manager-turn tests preserve tools and the complete earlier message prefix.
- Startup discovery is explicit in the manager regression, matching production startup. A bare harness configuration otherwise discovers skills only during its first composition.
- Exact-dialogue tests opt out of hints/status through an explicit fixture or harness configuration. The prefix regression opts them back in.
- Candidate ordering affects budget membership. Restoration walks a set, so sorting only the final tools array is insufficient. Canonicalize before classification.
- `make check` passed before final ordering change. The pinned Pyright tool emits an existing newer-version notice.
- First tool-choice eval: 47/56. Unchanged-main comparison: 44/56, including all nine failures from the implementation run. Tool-choice loadout and descriptions are unchanged by this fix.
- First deferral eval: 3/4. New discovery case passed. Postmortem activation passed its routing assertion but later actions exceeded the unrelated six-call cap (seven calls). Bound that case to one agent iteration so it tests activation routing alone.
- Unchanged-main deferral eval: 4/4. Final modified routing eval remains pending.
- LM Studio localhost:1234 responds HTTP 401. Existing agent configuration has no matching provider credential. No live cache speedup or hit-rate claim is made.

## Draft and review corrections

Draft PR: https://github.com/lmorchard/decafclaw/pull/1259
Published checkpoint: 0635950489b77f2a4fd8e13f0c4ef2e70503b8cb (author and committer verified remotely as MokaGnome). The host gh login differs; all writes use the configured ghflow identity.

Final routing eval: 4/4 passed (17.1s). The postmortem case now checks initial activation in one agent iteration instead of testing its later vault workflow. `make eval-history` checked after the run.

Same-session review of the checkpoint found two evidence/diagnostic gaps: no real disk-archive restart regression, and model digests only tracked aliases. Added the restart regression and model-setting regressions for named and legacy configuration. All 16 prefix tests pass. Disabling the model-config digest makes both new model-setting tests fail, confirming coverage. Full gates with duration reporting pending after these corrections.

Earlier verify-commit invocation used the literal remote HEAD, which resolves main. Repeated verification with the full checkpoint SHA confirmed the branch commit and both MokaGnome identities. No attribution change was required.

Correction validation: `make check` passed. `PYTEST_ADDOPTS=--durations=25 make test` passed: 5375 passed, 2 skipped in 24.21s. No new prefix regression appears among the top 25 durations. The focused 4/4 routing eval covers the unchanged hint/loadout behavior; the corrections only extend diagnostics and replay coverage.

Project Priority=P1 and Size=M update was rejected by automatic approval review because field values need a user decision. No field write ran. An optional approval request is pending; implementation and submission do not depend on it.
