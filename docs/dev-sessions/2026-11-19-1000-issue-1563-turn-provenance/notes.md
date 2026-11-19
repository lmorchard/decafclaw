# issue-1563-turn-provenance

## Resume here

- Issue: https://github.com/lmorchard/decafclaw/issues/1563
- Worktree: /Users/lmorchard/devel/mine/decafclaw/.claude/worktrees/issue-1563-turn-provenance
- Branch: issue-1563-turn-provenance
- Base: origin/main at 9072e8e8c99786db5bb0961442bc251bae18780f
- Next action: open the draft PR.

## Log

- Baseline `make test`: 5967 passed, 2 skipped.
- Backend: `ContextContribution.provenance` (source key → item dicts) merges into
  `ComposedContext.provenance`; legacy string facts become the `facts` capsule
  after the token-ceiling trim. `ComposerState.last_provenance` feeds
  `message_complete`, and the agent publishes `turn_provenance` from `_compose`.
- Extensions: recent journal, scratchpad notes, and vault retrieval now attach
  bounded provenance items; mem0 keeps its legacy string path.
- Wire: replaced the `recalled_facts` message type with `turn_provenance`;
  `message_complete` and history rows carry `provenance`. Passive prompt roles
  (`recalled_facts`, `recent_journal`, `conversation_notes`, `vault_retrieval`)
  are hidden from `conv_history`.
- Web: `turn-provenance-row` renders capsules (facts, journal, vault,
  scratchpad; unknown keys generic); assistant messages mount it under the
  role label. The brain toggle left the control bar; `Show context capsules`
  lives in the conversation menu and persists through
  `context-provenance-preference.js` (migrates the old recalled-facts key).
- Removed `chat-recalled-facts` and `recalled-facts-preference`.
- Tests: targeted Python and JS suites green; full `make test` 5972 passed,
  2 skipped; `make test-js` 811 passed; `make check-js`, `make check-tui`,
  `make lint`, `make typecheck` green.
