# issue-1514-recalled-facts-widget

## Resume here

- Issue: https://github.com/lmorchard/decafclaw/issues/1514
- Worktree: /Users/lmorchard/devel/mine/decafclaw/.claude/worktrees/issue-1514-recalled-facts-widget
- Branch: issue-1514-recalled-facts-widget
- Base: origin/main at e89a7b36cf504a52aed4e5cbaee793ba9aa3cdf1
- Implementation complete (uncommitted at last check): backend fact capture, WS `message_complete`/`conv_history`, `chat-recalled-facts` component, preference toggle (desktop control bar + mobile conversation menu), docs, tests.
- Checks: `make test` 5841 passed, 2 skipped; `make test-js` 786 passed; `make check-js`, lint, typecheck, fmt-check, check-npm-policy, check-browser-assets pass. `check-message-types` fails only because generated files are unstaged (it diffs the working tree); rerun after commit.
- Next action: commit, then draft PR publication and same-model self-review per single-session ghflow mode.

## Log

- Confirmed the legacy `<recalled_facts>` envelope is archived but hidden from the web transcript (now listed in `_HIDDEN_ROLES`); the assistant archive row carries the atomic `recalled_facts` list instead.
- `compose_pre_compose_hooks` now returns the fact lines; trimmed envelopes report only the lines the model received.
- Fault injection confirmed the new backend tests fail when fact capture or WS forwarding is disabled.
