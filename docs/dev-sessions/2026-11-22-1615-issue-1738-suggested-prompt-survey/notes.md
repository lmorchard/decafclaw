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
