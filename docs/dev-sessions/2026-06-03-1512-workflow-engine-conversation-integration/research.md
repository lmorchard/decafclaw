# Codebase research — integration surfaces

Documentarian survey from a read-only Explore subagent, focused on existing decafclaw infrastructure relevant to making workflows a peer to agent turns. Carry-forward `src/decafclaw/workflow/*.py` modules are deliberately out of scope.

## Q1 — Slash-command dispatch (current state)

**Parsing:** `commands.py:26-41` — `parse_command_trigger(text, prefix)` detects `!` or `/` prefixes and returns `(command_name, arguments)` or `None`. Called from `web/websocket.py:309` and equivalent in `mattermost.py`.

**Central dispatch:** `commands.py:279-355` — `dispatch_command(ctx, text, prefixes)` is the single entry point for both transports:
1. Parses the trigger
2. Handles `help` specially (returns formatted text, no LLM call)
3. Looks up skill via `find_command` from the skill catalog
4. Calls `execute_command` which either runs `_run_child_turn` (fork) or returns a substituted body string (inline)

**Modes that bypass the LLM today:**
- `"help"` — `commands.py:321-324` — formatted help, emitted as `MESSAGE_COMPLETE` without invoking the agent at `websocket.py:311-316`
- `"unknown"` / `"error"` — error text, same path
- `"fork"` — `commands.py:419-429` — calls `_run_child_turn` internally; web UI at `websocket.py:318-323` emits result as `MESSAGE_COMPLETE` **without** calling `manager.send_message`, so the outer agent loop never fires
- `"inline"` — substituted body passed to `manager.send_message`; outer agent loop DOES fire (this is what `/interview` currently uses)

**Manager contract:** for `help`/`unknown`/`error`/`fork`, web emits directly; for `inline`, goes through `manager.send_message` → `enqueue_turn(kind=TurnKind.USER)`. The `command_ctx` carries pre-approved tools/skills/shell-patterns onto the turn (`commands.py:12-23`).

**Implication:** A non-LLM dispatch path for commands already exists (the `fork` mode). Extending the command system to dispatch directly to the workflow engine is precedented.

## Q2 — `ConversationManager` dispatch and state

**`TurnKind` values** (`conversation_manager.py:73-79`): `USER`, `HEARTBEAT_SECTION`, `SCHEDULED_TASK`, `CHILD_AGENT`, `WAKE`. All five enter through `enqueue_turn` (`conversation_manager.py:402-506`) → `_start_turn` → `run_agent_turn`. **There is no non-agent-loop dispatch path in the manager.** The differences are only in context construction (`for_task` flags vs persisted state).

**`ConversationState` fields** (`conversation_manager.py:223-335`):
- `busy: bool`
- `pending_confirmation: ConfirmationRequest | None`
- `confirmation_event: asyncio.Event | None`
- `confirmation_response: ConfirmationResponse | None`
- `confirmation_queue: list[_QueuedConfirmation]`
- `agent_task: asyncio.Task | None`
- `cancel_event: asyncio.Event | None`
- `partial_assistant_chunks`
- `persisted: PersistedTurnState`
- `last_user_id` / `last_context_setup`

**No "active workflow" field exists.** All multi-step state today is per-turn or via the confirmation queue.

**Scheduled / heartbeat:** `schedules.py:504` and `heartbeat.py:172` both call `manager.enqueue_turn(...)`. They serialize behind user turns via the `busy` flag. Same dispatch as user turns.

## Q3 — Message roles & rendering

**Archive `role` values found:**

LLM-facing (`archive.py:13`): `system`, `user`, `assistant`, `tool`.

Internal/metadata, hidden or remapped before LLM/UI:
- `cancel_marker` (`conversation_manager.py:67`), `turn_aborted` (`conversation_manager.py:45`) — remapped to `user` via `ROLE_REMAP` (`context_composer.py:28-29`)
- `vault_retrieval`, `vault_references`, `conversation_notes` (`context_composer.py:25-27`) — remapped to `user`
- `confirmation_request` / `confirmation_response` (`confirmations.py:44-86`) — hidden from LLM and web UI
- `model` (`websocket.py:405`), `effort` (`websocket.py:214`) — hidden from web UI history
- `wake_trigger` (`agent.py:560`) — not in `ROLE_REMAP`, not in `LLM_ROLES`; archive-only (no UI hiding either — possible gap)
- `reflection` (`agent.py:773`) — not in LLM_ROLES; archive metadata
- `background_event` (`skills/background/tools.py:209`) — expanded in `context_composer.py:201-236` into a synthetic assistant tool_call + tool result pair for the LLM

**Web UI `_HIDDEN_ROLES`** (`websocket.py:214`): `{"effort", "model", "confirmation_request", "confirmation_response", "wake_trigger"}` — stripped from history returned to client.

**Server-side emission without LLM:** YES. `skills/background/tools.py:160-165` writes to the archive via `append_message` and emits to subscribers via `job.manager.emit(...)` directly. This is the precedent for "server adds a conversation message without an agent turn."

**Widget role binding:** `tool_execution.py:270-278` — widgets attach **exclusively** to `role: "tool"` messages. No path for widget on a non-tool message exists today.

## Q4 — Widget emission and `ConfirmationRequest` lifecycle

**Wire path:** `tool_execution.py:248-260` — after `resolve_widget`, the widget is emitted via `await call_ctx.publish("tool_end", widget=widget_payload, ...)`. WebSocket forwarder at `websocket.py:557-558` (`_project_tool_end`) includes `widget` if present.

**Always inside ToolResult.widget:** `tool_execution.py:121-207` — `resolve_widget` only runs against `result.widget`. **No server path for standalone widget emission outside a tool call.**

**`ConfirmationRequest` lifecycle:**
1. Created in tool code (e.g. `tools/confirmation.py:46-54`, `agent.py:217-222` for widget pauses, `tools/skill_tools.py:168` for skill confirmation)
2. Persisted: `conversation_manager.py:1136-1137` — `append_message(... request.to_archive_message())` BEFORE the lock
3. Emitted to UI: `conversation_manager.py:1160-1161` (active) / `:1194-1195` (queue-promoted) — `await self.emit(conv_id, _confirmation_request_payload(request))`
4. Responded: `conversation_manager.py:533-703` — `respond_to_confirmation` archives response, sets `state.confirmation_response`, signals `waiter_event`
5. Cleared: `conversation_manager.py:1085-1101` — `_clear_active_and_promote_unlocked` nulls pending/event/response, promotes queued

**Scheduled task creating a confirmation without a running agent turn:** **Not supported.** `ctx.request_confirmation` is wired by `_start_turn` at `conversation_manager.py:1410-1412` — only available when a turn is running. There is no API for "create a confirmation outside any turn."

## Q5 — Scheduled / heartbeat / other non-LLM-driven flows

**Scheduled skill execution:** `schedules.py:429-539` — `run_schedule_task` calls `manager.enqueue_turn(conv_id=..., kind=TurnKind.SCHEDULED_TASK, prompt=..., history=[], ...)`. Always invokes `run_agent_turn`. No non-LLM path.

**Heartbeat:** `heartbeat.py:155-197` — `run_section_turn` calls `manager.enqueue_turn(..., kind=TurnKind.HEARTBEAT_SECTION, ...)`. Same.

**Scheduled / heartbeat conv_ids:** ephemeral — `schedule-{name}-{timestamp}`, `heartbeat-{timestamp}-{index}`. Their archive messages use normal LLM roles (`user`/`assistant`/`tool`). The `user_id` differentiates (`f"schedule-{task.source}"` or `f"heartbeat-{section.get('source')}"`), but the role does not.

**Server adding messages without an LLM call:** YES — `skills/background/tools.py:142-165` — `_finalize_job` writes `role: "background_event"` to the JSONL via `append_message` directly, then `job.manager.emit(job.conv_id, {"type": "background_event", "record": rec})` to subscribers. No agent turn, no LLM. This is the cleanest existing precedent for what workflow-as-peer would need.

## Q6 — Class-of-bug analogues

Other features where the LLM-as-control-flow problem could surface:

1. **Checklist tool** (`tools/checklist_tools.py:1-7`) — module docstring explicitly describes expected LLM control-flow: `do step → call step_done → get next step → do next step`. If the LLM summarizes without calling `step_done`, or hallucinates the next step, the checklist stays stuck. Same "LLM must call the right next tool" reliability problem.

2. **Project skill** (`skills/project/tools.py:162-259`) — `tool_project_task_done` returns `EndTurnConfirm` at phase boundaries. The LLM must call `project_task_done` at the right phase. Phase advancement is gated through confirmation infra, but the LLM still drives WHEN to call.

3. **Skill activation confirmation** (`tools/skill_tools.py:168`, `tools/confirmation.py:32-64`) — `tool_activate_skill` suspends the agent loop while awaiting confirmation. Tied to confirmation infra, structurally similar but the LLM-calls-right-tool path is just `activate_skill`, less open-ended than checklist.

4. **Widget input pause** (`agent.py:189-288`, `tool_execution.py:181-206`) — input widgets (`ask_user_choice`, `ask_user_text`) suspend the agent loop inside a `WidgetInputPause` (`WIDGET_RESPONSE`). Agent resumes with synthetic user message. **This is the closest existing precedent to PR #572's pattern** — suspended-inside-tool, resume-on-external-input.

5. **`delegate_task` / child agents** (`tools/delegate.py`) — child agents are `TurnKind.CHILD_AGENT` invoked via tool call. Parent loop suspends while child runs. The "LLM decides to delegate" boundary has the same problem; the inside-child loop is naturally LLM-driven (it IS an agent), so less of a concern there.

## Things not found

- `ConfirmationAction.ADVANCE_PROJECT_PHASE` (`confirmations.py:19`) is declared but has zero usage. Reserved/planned.
- Web UI client-side role → component mapping was not examined (static JS excluded from scope).
- `wake_trigger` role is archived (`agent.py:560`) but NOT in `ROLE_REMAP` or `LLM_ROLES` or `_HIDDEN_ROLES` — passed through to client as visible. Possible gap.
- No existing mechanism for a workflow step to run as a peer to agent turns. All current multi-step interactive patterns (checklist, project, widget input) nest inside tool calls within an agent turn.
