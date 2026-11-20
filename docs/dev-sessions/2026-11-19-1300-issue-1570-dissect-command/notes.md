# issue-1570-dissect-command

## Resume here

- Issue: https://github.com/lmorchard/decafclaw/issues/1570
- Worktree: /Users/lmorchard/devel/mine/decafclaw/.claude/worktrees/issue-1570-dissect-command
- Branch: issue-1570-dissect-command
- Base: origin/main at 1bb40282405b6a793aa3bc5aae05af2718a0310c

## Log

- Scoped the issue to three triggers over one fork primitive: `/dissect`
  (web side-effect command), the errored tool card **Dissect** button (REST),
  and the agent's `conversation_fork` tool. Web-only because the fork opens as
  a tab and delegated `--child-` conversations are read-only.
- Extraction is pure (`dissect.py`): last `[error` tool result or a named
  `tool_call_id`, with the triggering prompt, assistant turn, arguments, and
  exact error, bounded to keep the brief small. Falls back to the latest
  exchange when no tool failed.
- Orchestration (`web/dissection.py`): top-level indexed conversation with
  `forked_from`, inherited active model, seeded first turn, manual title, and a
  durable `dissect_notice` record plus live event for the parent card.
- UI: message-store handles live/archived `dissect_notice`; chat-message
  renders the card with a new-tab link; tool-message shows a Dissect button on
  `[error` results and opens the returned tab synchronously.
- Limits: Mattermost has no `/dissect` link/tab path; this delivery is web-only
  and the tool refuses non-web transports.
- Refactor: inverted the hierarchy so `/fork` (`/branch`) and
  `conversation_fork` are the general primitive. `fork.py` slices the last one
  or two typed exchanges and builds a code-agnostic brief; `dissect.py` is the
  failure preset layered on it. `web/forking.py` owns both templates, the
  `fork_notice` parent record, and `POST /api/conversations/{id}/fork`.
  `/dissect` and the tool-card button call the same fork API with
  `template="dissect"`. Empty `tool_call_id` now only inspects the immediate
  latest turn, so older errors cannot hijack a new fork; a dissect request with
  no latest error falls back to a general fork.
