# feat/981-self-update-command

## Resume here

- Issue: https://github.com/lmorchard/decafclaw/issues/981
- Worktree: /home/lmorchard/.decafclaw/decafclaw/workspace/decafclaw/.claude/worktrees/feat/981-self-update-command
- Branch: feat/981-self-update-command
- Base: origin/main at d365f76b6bb5fc74845a9a924011c6b23744ae7a
- Status: Implementation and testing complete, ready for PR submission.

## Decisions & Verification

- Implemented human-only `/update` and `!update` command logic in `decafclaw.self_update`.
- Intercepted `/update` in `_handle_send` before turn creation (no agent turn, no archive write).
- Added guards: refuses when active turns are running across conversations, aborts when working tree is dirty or branch has diverged, fetches and shows incoming commits.
- Confirmation step (`/update confirm`) triggers `git pull --ff-only`, `uv sync`, and `systemctl --user restart decafclaw`. If `uv sync` fails, reports error and does not restart.
- Added import boundary test ensuring `tools/` and `skills/` cannot import `self_update`.
- Tests pass: unit tests (`tests/test_self_update.py`), websocket command tests (`tests/web/test_update_command.py`), frontend tests (`conversation-store.test.js`).
