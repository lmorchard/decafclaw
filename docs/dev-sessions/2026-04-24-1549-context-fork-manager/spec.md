# context: fork command manager fix — spec

Tracks: [#361](https://github.com/lmorchard/decafclaw/issues/361)

## Goal

Make `!command` invocation work for bundled skills that have `context: fork` in their SKILL.md frontmatter (today: `dream`, `garden`; latent for any future skill with the same pattern). Currently these commands fail with:

> `[error: delegate_task requires a ConversationManager; no manager on parent ctx]`

This was surfaced during smoke-testing of PR #356 (newsletter). Newsletter worked around it by flipping its own skill to `context: inline`. Dream and garden still have the latent bug.

## Why fork matters (vs flipping everything to inline)

`context: fork` is worth preserving for user-invocable background-style skills. Dream and garden do multi-turn work with many tool calls (reading journal, writing pages, pruning). A forked child conversation gives the user a clean summary reply in their chat, with the scaffolding isolated. Flipping those skills to `context: inline` would dump all the tool-call noise into the user's main chat — a regression in UX.

So the direction is: **fix the propagation**, not eliminate fork.

## Root cause

Both transport entry points that dispatch `!commands` construct a fresh, minimal `Context` for the dispatcher but don't attach a `ConversationManager`:

- `src/decafclaw/web/websocket.py:218-220` — `cmd_ctx = Context(config=..., event_bus=...)` + `cmd_ctx.user_id`, `cmd_ctx.conv_id`. No `cmd_ctx.manager`.
- `src/decafclaw/mattermost.py:342-344` — same pattern.

`dispatch_command` routes `context: fork` skills through `commands.py:419-429` → `tools/delegate.py::_run_child_turn`, which reads `parent_ctx.manager` at line 101. If `None`, it returns the error above and never attempts the fork.

The normal (non-command) user-message flow attaches the manager in `conversation_manager.py::_start_turn` (line ~619) via `ctx.manager = self`. Command dispatch bypasses that path, which is why the manager never gets attached for the dispatcher ctx.

Both transports already have `manager` in scope at the construction site (web: `state["manager"]`; Mattermost: function parameter). So the fix is literally two lines — one per transport.

## Design

### Scope

Two one-line additions to two files, plus a regression test. No new subsystems, no signature changes, no refactor of `Context`.

### Files touched

- `src/decafclaw/web/websocket.py` — attach `cmd_ctx.manager = manager` after the existing `cmd_ctx.user_id`/`cmd_ctx.conv_id` assignments.
- `src/decafclaw/mattermost.py` — attach `cmd_ctx.manager = manager` after the existing `cmd_ctx.user_id` assignment.
- `tests/test_commands.py` (extend or create if absent) — regression test at the `commands.py` level, mock-based.

### Context dataclass

`Context` at `src/decafclaw/context.py` is NOT a `@dataclass` — it's a plain class. `self.manager: Any = None` is already declared in `Context.__init__` line 94. No change needed there; no convention smell (the "declare on the dataclass" rule in CLAUDE.md doesn't apply to plain classes with inline-annotated init attributes).

### Data flow after fix

1. User types `!dream` in web (or Mattermost).
2. Transport handler parses message, constructs `cmd_ctx = Context(config=..., event_bus=...)`.
3. Transport attaches identifiers **and now the manager**: `cmd_ctx.manager = manager`.
4. `dispatch_command(cmd_ctx, "!dream")` parses trigger, finds `dream` skill with `context: fork`.
5. `execute_command` (commands.py:419-429) calls `_run_child_turn(cmd_ctx, body, ...)`.
6. `_run_child_turn` reads `parent_ctx.manager` — populated — and calls `manager.enqueue_turn(kind=CHILD_AGENT, ...)` for the forked child conv.
7. Child conv runs dream's SKILL.md body, produces a final narrative string.
8. `cmd_result.mode == "fork"`; transport sends `cmd_result.text` as an assistant message in the user's chat.
9. User sees the dream summary with none of the intermediate tool-call scaffolding.

### Error handling

No new error cases. The existing `[error: delegate_task requires a ConversationManager; no manager on parent ctx]` stays as defense-in-depth: if a future transport forgets to attach the manager, the error message points the reader back to the attachment sites.

## Testing

Two test layers — the first pins commands.py behavior, the second actually catches the bug we're fixing.

### Contract tests at commands.py (pin existing behavior)

Mock-based, so no ConversationManager scaffolding needed:

- **Happy path:** monkeypatch `decafclaw.tools.delegate._run_child_turn` to record its `parent_ctx` argument. Build a fake `SkillInfo(context="fork")`, set `ctx.manager = sentinel`, call `execute_command`. Assert the recorded ctx has `manager is sentinel`.
- **Negative (defense-in-depth):** same setup but `ctx.manager = None`. Do NOT mock `_run_child_turn` — let the real function hit its bail-out at `delegate.py:101-106`. Assert `cmd_result.mode == "fork"` and `"ConversationManager" in cmd_result.text` (substring match, stable against minor wording changes).

Note: both tests **pass before the fix** — commands.py already propagates `ctx.manager` through correctly. These are contract tests pinning the invariant, not regression tests for the specific bug.

### Regression test at web transport (catches the actual bug)

This is the test that fails before the fix and passes after. It exercises the real code path we're modifying:

- Call `websocket._handle_send` with a minimal fake `state` containing a sentinel manager.
- Monkeypatch `decafclaw.commands.dispatch_command` to capture its first positional argument.
- Assert the captured `ctx.manager` is the sentinel.

Before the fix, `ctx.manager` is `None` because `_handle_send` never attaches it. After the fix, it matches `state["manager"]`. This is the test that would flag a regression if someone reverts the websocket.py line — or if a new transport is added that forgets the same attachment.

### Mattermost transport

Skipping a dedicated transport-level test for Mattermost. The command-dispatch lives inside `_process_msgs`, which needs substantial scaffolding (MM config, message shapes) to isolate for a mock test. The one-line fix is symmetric to web and visible in the diff; the commands.py contract tests cover the invariant across transports. If the Mattermost code path is later restructured to be more test-friendly, adding the symmetric test is a small follow-up.

### Existing tests

All existing `pytest` tests must still pass. No behavior changes for `context: inline` commands or for `dispatch_command`'s help/unknown/error/inline paths.

## Out of scope

- Making `Context` a `@dataclass`. Context has never been one; the conversion would ripple through many call sites unrelated to this fix.
- Changing `dispatch_command`'s signature to require `manager` as a parameter. Only two callers today — the enforcement gain doesn't justify the churn, and the post-construction assignment idiom is consistent with how `cmd_ctx.user_id` etc. are set.
- Upgrading the `ctx.manager` type annotation from `Any` to `ConversationManager | None`. Introduces an import-cycle risk between `context.py` and `conversation_manager.py`. A separate cleanup if/when the two modules get restructured.
- Addressing the paired latent issue at #362 (scheduled skills ending with `HEARTBEAT_OK` instead of narrative summaries). Independent concern, tracked separately.

## Success criteria

- `!dream` and `!garden` invoked from the web UI AND Mattermost produce a forked reply showing the skill's summary output — no `ctx.manager` error.
- Existing `context: inline` commands (`!health`, `!ingest`, `!postmortem`, `!newsletter`) continue to work unchanged.
- Regression test covers both happy path (manager attached → fork proceeds) and defense-in-depth (manager missing → clear error).
- `make lint && make typecheck && make test` clean.
