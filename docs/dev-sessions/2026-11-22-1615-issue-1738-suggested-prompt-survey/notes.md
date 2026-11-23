# issue-1738-suggested-prompt-survey

## Resume here

- Issue: https://github.com/lmorchard/decafclaw/issues/1738
- Worktree: /Users/lmorchard/devel/mine/decafclaw/.claude/worktrees/issue-1738-suggested-prompt-survey
- Branch: issue-1738-suggested-prompt-survey
- Base: origin/main at 728284fd42127b0649e87b7c28431788a3f12cee
- Next action: read the issue and start.

## Log

- 2026-11-22 16:15 — Created worktree from origin/main 728284fd. Harness implemented in `src/decafclaw/survey/`.
- 2026-11-22 16:25 — Stage 0 full local corpus (3003 archives, 320 boundaries): offered choice 63 (19.7%), recapitulated 3 (4.8% when offered), stop-like final turns 2/2999. Calibration: reuse previous user 0.266 mean, last assistant 0.094, global connected 0.004, random floor 0.010. Local fastembed backend; corpus has no provenance.
- 2026-11-22 16:26 — Dev set 200 cases at `evals/suggested_prompt/devset.jsonl` (816K). Score dry run: 400 calls, ~330k input tokens, ~$0.23 estimated for flash-vs-haiku; as-configured aux/active collapses to 218 calls (~$0.12).
- Commit c0a4edcf: harness + tests + dev set + docs.
- Next: finish score run A (as-configured), run B (aux flash vs active haiku), read 40-case spot check, write findings, `make check`, draft/ready PR.
- 2026-11-22 16:45 — User clarified the survey must run against the production corpus. Production host `decafclaw` (VM 302 on myriad), DATA_HOME=/home/lmorchard/.decafclaw, conversations under `~/.decafclaw/decafclaw/workspace/sessions` (5,748 archive files, 4,262 session dirs), vault at `~/.decafclaw/decafclaw/obsidian/main` (5,050 md), embeddings.db 25M.
- Dev-corpus pilot numbers are superseded; keep the harness. Added `--page-sample` (default 300) so Stage 0 calibrates a deterministic sample on the large corpus.
- 2026-11-22 17:05 — Production run complete (temp worktree /tmp/decafclaw-survey, removed after). Stage 0: 5,748 conversations, 266 with boundaries, 3,172 boundaries; 570 offered (18.0%), 21 recapitulated (3.7% when offered / 0.7% all); 0/279 final turns stop-like; page sample 300 (22 provenance, 278 proxy); previous-user floor 0.055, oracle 0.934.
- Score A (as configured): aux flash 0/200 exact, 1/200 Jaccard, 86 abstained (43%), direction 0.040. Active (mostly flash) 0 exact, 1 Jaccard, 78 abstained.
- Score B (flash vs haiku): flash 0 exact, 1 Jaccard, 82 abstained, direction 0.041; haiku 0 exact, 0 Jaccard, 0 abstained, 178 unrelated, direction 0.029, 4 hallucinated specifics. Cheap arm better.
- Operator spot check run A: 5 useful, 8 plausible-wrong, 7 off, 20 abstained.
- Spend: 233 + 400 calls, priced actual < $0.40; conservative <$2. Recommendation: no-go for general ghost text; harness remains for re-runs.
- Gates: `make check` EXIT=0; focused survey tests 40 passed; full `make test` has 4 pre-existing `tests/test_host_metrics.py` failures also on main.
- Privacy: generated dev sets are git-ignored; production temp worktree deleted.
- 2026-11-22 17:35 — Independent review found a recency-stratification bug (sorted by conv_id, not timestamp), spend double-count on collapsed arms, errors counted as abstention, and an empty-results crash. Fixed with regression tests. Also removed verbatim production quotes from the public memo; privacy rule now holds.
- Reran production after the fix: corrected dev set 200 cases from 3,185 eligible boundaries / 266 conversations; 155 conversations, max 5 cases each; recency buckets now Mar–Apr / Jun–Oct / Nov.
- Corrected run A: aux flash 1/200 exact, 3/200 Jaccard, 78 abstained, direction 0.056; active 1/3/71, direction 0.052. Calibration floor 0.063.
- Corrected run B: flash 1/200 exact, 2 Jaccard, 80 abstained, direction 0.041; haiku 1/200 exact, 1 Jaccard, 1 abstained, 171 unrelated, 5 hallucinated, direction 0.032.
- Corrected operator spot check: 7 useful, 9 plausible-wrong, 5 off, 19 abstained. No-go stands.
- Spend: run A 237 calls ~$0.05 priced; run B 400 calls ~$0.25 haiku; total <$0.40.
