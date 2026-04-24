# Background Job Agent Wake — Session Notes

## Summary

Closes #241. Delivers agent-facing wake-on-completion for background jobs, plus a prerequisite unification of all agent turn orchestration through `ConversationManager`. Heartbeat / scheduled tasks / child-agent delegations used to bypass CM and call `run_agent_turn` directly — now everything routes through `enqueue_turn(kind=TurnKind.X)` and shares the per-conv busy flag. Wakes fire safely alongside in-flight turns, heartbeat-originated jobs trigger wakes on their own conv_ids, and the web UI renders completions as system cards live (and on reload).

## Phases shipped

1. **CM refactor** — `TurnKind` enum, `enqueue_turn` public API, per-kind policy matrix in `_start_turn`, mixed-kind `_drain_pending`.
2. **Migrate heartbeat** through CM (adds `ctx.manager` plumbing).
3. **Migrate scheduled tasks** through CM (extends `context_setup` to support async callables for skill pre-activation).
4. **Migrate delegate** through CM with a `context_setup` callback for child-specific wiring.
5. **Archive record + history rendering** — `background_event` JSONL record with tail clamping + 4KB ceiling; shared `format_status_text`; composer expands records into synthetic `shell_background_status` tool-call pairs at LLM-input time (tool-role framing for untrusted process output).
6. **Wake dispatch** — `completion_tail_lines` parameter, `_finalize_job` helper (idempotent via `job.finalized` CAS), per-conv wake rate limiter, `_enqueue_wake`, `BACKGROUND_WAKE_OK` sentinel, transport suppression across Mattermost / web UI / terminal.
7. **Docs** — new `docs/background-wake.md`, cross-links, CLAUDE.md update describing unified turn orchestration.
8. **Integration test + live smoke** — end-to-end tests (user, heartbeat-originated, BACKGROUND_WAKE_OK path), plus live web UI smoke that caught the `fork_for_tool_call` bug.

## What went well

- **Subagent-driven execution held up** across 20+ implementer dispatches. Two-stage (spec + code-quality) review caught real issues early — the stopgap-None future resolution in Task 1.2 would have been a latent hang otherwise.
- **Phases 1–4 came in clean** with minimal rework. The `context_setup` callback pattern turned out to be flexible enough to absorb the elaborate per-kind setup in delegate.
- **`_finalize_job` idempotency** (guard via `job.finalized` CAS) caught the clean-exit-then-stop race before it shipped.
- **Copilot review was genuinely useful.** Flagged real bugs three rounds in a row: futures discarded in `_drain_pending`, Mattermost transport ignoring `message_complete.text` when streaming was off, `_fanout` not guarding head-future exceptions, WAKE turns missing transport context, `is_background_wake_ok` substring match being too loose. Addressed all of them.

## Surprises

- **`fork_for_tool_call` was a silent bug.** It hand-copied 18 fields from the parent ctx but missed `manager`. Tools that read `ctx.manager` (including `shell_background_start`) saw `None`, so `_enqueue_wake` silently skipped. Extended the existing `test_fork_for_tool_call_copies_all_fields` test to cover `manager` + `request_confirmation` so this category of bug can't slip through again. The test's `fields_to_check` list is a fragile pattern — any new Context field needs to be added there manually; probably worth a follow-up to auto-enumerate fields from the dataclass.
- **Wake nudge stored as `role: user` in archive** was a UX regression visible only on reload. Fix: archive under `role: "wake_trigger"` and filter it from UI rendering. The LLM still sees it as a user-role prompt at compose time — runtime and archive semantics now cleanly decoupled.
- **Streaming × suppression interaction.** Disabling streaming for WAKE turns (so `BACKGROUND_WAKE_OK` suppression isn't defeated by streamed chunk prefixes) broke non-WAKE Mattermost behavior too, because Mattermost relied on `on_text_complete` posting before `message_complete`. Fix: `message_complete.text` is now authoritative — buffer gets overwritten at finalize time regardless of streaming mode.
- **Main moved during the PR** three times (vault section refactor, email feature, vault-page notification channel). Rebased each time cleanly; conflicts were all in the notification/config areas that both branches edited.

## Key design decisions

- **Shared conv_id for wake turns.** Wake fires on the originating conversation, not an isolated task conv, so the agent has full context for its own prior tool call. Applies uniformly to user convs, heartbeat convs, scheduled-task convs, and child-agent convs.
- **Tool-result framing for untrusted process output.** `background_event` records expand into a synthetic `shell_background_status` tool-call/tool-result pair at LLM-input time. Process output stays in tool-role messages (where the model expects externally-sourced data), not system messages.
- **`BACKGROUND_WAKE_OK` sentinel** with transport-level suppression rather than agent-loop-level gating. Agent still emits the `message_complete` event (archive, event stream); transports just skip the user-visible post.
- **Per-conv wake rate limiter** separate from the user circuit breaker. A buggy script spamming restarts can saturate its own conv without affecting user turns there; inbox and archive still record the events.
- **`_finalize_job` idempotency** via `job.finalized` flag — every terminal-status path (clean exit, expired, stopped, exception) funnels through the same three-step sequence exactly once.

## Numbers

- Tests: 1472 baseline → **1680** passing. Net +208 tests across the feature, migrations, and co-evolving merges from main.
- Files touched: ConversationManager, Context, agent, heartbeat, schedules, delegate, background tools, context composer, 3 transport modules, 4 docs, web UI client (message-store, conversation-store, chat-view, chat-message, chat.css), + tests.

## Follow-ups (not blocking merge)

- **Reflection-retry prompts stored as `role: user`** — pre-existing behavior unrelated to this PR, but now looks weird alongside the new `wake_trigger` handling. Consider giving them their own role too for consistent UI treatment.
- **Transport `context_setup` closures may grow stale.** When a WAKE turn fires hours after the last user turn on the same conv, the captured Mattermost post-IDs / thread-root-IDs may no longer be live. Worked for v1; watch for edge cases.
- **Auto-enumerate Context fields** in `test_fork_for_tool_call_copies_all_fields` so future additions can't silently skip the field-list bump. Use `dataclasses.fields` or introspection.
- **Live smoke found the `fork_for_tool_call` bug** where unit tests hadn't. Integration tests that exercise a real agent turn through the tool-call dispatch path would have caught it earlier — worth adding one for the full wake flow that actually executes `tool_shell_background_start` under the real forked-ctx path.
