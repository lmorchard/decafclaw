# Research — Workflow status=running auto-resume (#581)

Dispatched a documentarian subagent. Findings verbatim, condensed to load-bearing points.

## 1. Existing startup scan (confirmations)

- **Method:** `ConversationManager.startup_scan()` at `conversation_manager.py:1794-1830`.
- **Iteration:** `iter_conversation_archives(self.config)` from `conversation_paths.py:24` walks per-conversation archive JSONL files.
- **Per-archive helper:** `_scan_archive_for_pending()` at `conversation_manager.py:1832-1893`. Reads only the last 64 KB of each archive; reverse-scans for a `confirmation_request` without a matching `confirmation_response`; drops stale (>24 h) entries.
- **Recovery dispatch:** does NOT call `enqueue_turn`. Installs the recovered request directly into `state.pending_confirmation` with `state.confirmation_event = None`. The `None`-event branch routes through `_dispatch_recovery()` when the user later approves.
- **Startup order:** called from `runner.py:59`, after `ConversationManager` construction, before any transport connects (HTTP line 64, Mattermost line 82).

**Implication:** Sibling scan for `status="running"` journals. Unlike the confirmation scan, this one re-enqueues immediately (no user in the loop) — via `enqueue_turn(kind=TurnKind.WORKFLOW, metadata={"workflow_name": ..., "resume": True})`.

## 2. Journal structure

- **File:** `workflow/journal.py:42-48`. Dataclass fields: `workflow_name: str`, `status: str = "running"`, `entries: dict[tuple[int, ...], JournalEntry]`.
- **Serialization:** `to_dict()` at 62-76 (hand-rolled), `from_dict()` at 78-89 (`d.get(k, default)` — forgiving of missing keys).
- **Status write sites** (all four):
  - `"running"`: `resume.py:45` (new journal), `resume.py:118` (on user-input approve).
  - `"suspended"`: `engine.py:43`.
  - `"error"`: `engine.py:46-51`, `resume.py:56` (skill activation failure).
  - `"done"`: `engine.py:54`.
- **Adding `attempts: int = 0`:** field on dataclass, one line in `to_dict`, `from_dict` gets it via `d.get("attempts", 0)`. No migration; existing on-disk journals load with default.

## 3. `tool_status` publish flow

- **Existing publish:** `workflow/resume.py:62-63` and `67-68`: `await ctx.publish("tool_status", tool="workflow", message=f"[workflow: {workflow_name}] running")`.
- **`ctx.publish`:** `context.py:191-201`. Auto-fills `context_id` and `tool_call_id`. Fans out via `EventBus.publish` (`events.py:27-36`), exception-safe.
- **WebSocket relay:** `web/websocket.py:512-591`. `_subscribe_to_conv` installs an `on_conv_event` callback via `manager.subscribe(conv_id, on_conv_event)` (line 734). Lines 585-591 route `tool_status` → `{"type": WSMessageType.TOOL_STATUS, "conv_id", "tool", "message", "tool_call_id"}`.

**Timing wrinkle:** startup scan runs before transports connect, so a scan-level publish would fan out to zero subscribers. The workflow's own subsequent publishes (once the resumed turn runs) will reach live clients when they subscribe. Cleanest UX: publish `[workflow: X] resuming after restart` from the resumed worker task (via `run_workflow_turn`'s existing path), not from the scan itself.

## 4. `enqueue_turn` API contract

- **Signature:** `conversation_manager.py:412-427`: `enqueue_turn(conv_id, *, kind, prompt, history=None, task_mode=None, context_setup=None, user_id="", archive_text="", attachments=None, command_ctx=None, wiki_page=None, metadata=None) -> asyncio.Future`.
- **WORKFLOW dispatch:** `conversation_manager.py:1507-1513`: reads `metadata.get("workflow_name", "")` + `metadata.get("resume", False)`, calls `run_workflow_turn(ctx, self, workflow_name=..., resume=...)`.
- **Busy flag / lock:** `state.lock` at `enqueue_turn:440`. If `state.busy`, queues into `state.pending_messages`. `_start_turn` sets `busy=True` (1366); reset in finally (1597-1612).
- **Startup timing:** transports not yet connected → `busy` always `False` at scan time. Concurrent turns from different conversations run fine.

## 5. Test patterns to mirror

- `tests/test_conversation_manager.py:866-935` — four confirmation-recovery-scan tests.
- Fixture pattern: `manager` fixture from conftest + `append_message(manager.config, conv_id, msg_dict)` for arranging state.
- Sibling tests to model after:
  - `test_startup_scan_finds_pending_confirmation` (869-889): arrange archive → call scan → assert count + state.
  - `test_startup_scan_empty_archive` (932-935): baseline zero.
- E2E to mirror: `test_workflow_turn_integration.py:test_durable_resume_after_simulated_restart` (108-173): suspend workflow → reload journal from disk → simulate recovery → assert completion.

## Key patterns for the new feature

1. Sibling method `startup_scan_workflows()` on `ConversationManager` — called from `runner.py` right after `startup_scan()`, or extend `startup_scan()` with a second pass.
2. Iterate conversation dirs (per-conv layout post-#576: `conversations/{conv_id}/workflow.json`), load each journal, filter `status="running"` with `attempts < N`, increment `attempts`, persist, `enqueue_turn(kind=WORKFLOW, metadata={workflow_name, resume: True})`.
3. `Journal` gains `attempts: int = 0` — dataclass + `to_dict`/`from_dict`.
4. When `attempts >= N`, mark `status="error"` and skip. Emit a log line (no live subscriber to receive `tool_status` at scan time).
5. Resume "ping" `tool_status` fires from the worker task itself once transports are up (existing publish in `resume.py:62-63` already runs on every resumed turn — probably sufficient without additions).
