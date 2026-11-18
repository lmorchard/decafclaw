# issue-1507-epilogue-slot-preempt-hints

## Resume here

- Issue: https://github.com/lmorchard/decafclaw/issues/1507
- Worktree: /Users/lmorchard/devel/mine/decafclaw/.claude/worktrees/issue-1507-epilogue-slot-preempt-hints
- Branch: issue-1507-epilogue-slot-preempt-hints
- Base: origin/main at 989880898ed0fb2279a6d471750f3dd424ad40da
- Next action: review the draft PR, then mark ready / follow up.

## Log

### What changed

- `ContextSlot.EPILOGUE` added in `src/decafclaw/extensions/__init__.py`.
- `context_pipeline.compose_pre_compose_hooks()` returns a fifth bucket
  `(head, delta, tail, epilogue, sources)`; `mount_context_contribution()`
  accepts and fills the epilogue list.
- `context_composer._compose_impl()` mounts epilogue messages after
  `llm_history` (which ends with the user message), remaps internal roles,
  archives them, and appends them to the caller's `history` in place.
- `src/decafclaw/extensions/preempt_hints.py` (new, default-enabled) owns the
  matcher helpers moved from the deleted `context_preempt.py` and emits up to
  two `EPILOGUE` contributions: `<preempt_tool_hint>` and
  `<preempt_skill_hint>`.
- Composer drops its inline preempt stages and private wrappers.
- Docs: extensions slots + new section, composer slot table/steps, preemptive
  tool-search entry point, config default-entry list.
- Tests: new `tests/test_preempt_hints_extension.py` (ordering, archive
  replay, disable, fail-open, formatting); `test_context_contributions.py`
  gains an EPILOGUE ordering/replay test; matcher tests retargeted to the
  extension module; registry default-set expectations include `preempt_hints`.

### Decisions

- Hints keep the internal `skill_hint` archive role (not `system`, as the
  issue sketch suggested). `skill_hint` remaps to `user`, stays at its
  archived position, is hidden in the UI, and is skipped by compaction
  flattening. A trailing `system` message would be re-folded into
  `systemInstruction` by Vertex and break the #1116/#1117 prefix guarantee.
- The tool hint renders after tool classification, reading
  `ctx.tools.deferred_pool`. Keyword matches have been hint-only since #1257,
  so moving the matcher behind `_compose_tools` changes no classification.
- Hints are no longer part of the composer's `fixed_tokens`; like other
  TAIL/EPILOGUE extension contributions they are counted in total sources
  and the status line, not subtracted from the dynamic retrieval budget.
- The `[Context: ...]` status line remains core. Its value depends on the
  final sum of every source, including the PRE_COMPOSE contributions that
  arrive concurrently, so a hook cannot compute it. Documented in
  `docs/extensions.md`.

### Evidence

- `make test`: 5775 passed, 2 skipped.
- `make check`: green (ruff, pyright, message types, JS/TS, browser assets).
- `make test-js`: 764 passed; `make check-tui` / `make test-tui`: green.
- Targeted: new extension tests + composer/context-contribution/prefix
  stability suites pass.

### Acceptance notes

- The issue's blanket criterion "100% of message injections are mediated
  through lifecycle extension hooks" is met for the discovery hints; the
  context status line stays core for the accounting reason above. Flag in
  the PR body rather than silently claiming 100%.
