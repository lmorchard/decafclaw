# Research — #485 request_confirmation overwrite

Documentarian read of the confirmation lifecycle in `src/decafclaw/conversation_manager.py` and surrounding code.

## 1. `request_confirmation` contract

**Signature** (`conversation_manager.py:763`):
```python
async def request_confirmation(self, conv_id: str, request: ConfirmationRequest) -> ConfirmationResponse
```

Flow:
1. **Archive** (L786) — `append_message` persists request synchronously, **outside** the lock.
2. **State setup under `state.lock`** (L790–794):
   - `state.pending_confirmation = request`
   - `state.confirmation_event = asyncio.Event()` (fresh event)
   - `state.confirmation_response = None`
   - Captures `event` locally for the release-and-wait pattern.
3. **Emit** `confirmation_request` event outside the lock (L798–807).
4. **Wait** on `event.wait()` with `request.timeout` (L812). Timeout may be `None` (block indefinitely — widget inputs).
5. **Post-wait claim** under lock (L837–881):
   - Responder claimed → honor it.
   - Timeout → synthesize `ConfirmationResponse(approved=False)`, archive, `needs_timeout_emit=True`.
   - Cancel raced → return denial without archiving (cancel already wrote it).
6. **Timeout emit** outside lock (L882–887) if needed.

Returns `ConfirmationResponse`.

## 2. `respond_to_confirmation` flow

`conversation_manager.py:413–559`.

Lock-held sequence (L446–505):
- Pending check (L447–449), ID match (L450–455), duplicate-response guard (L459–464).
- Build `ConfirmationResponse` (L466–472).
- Archive under lock (L483–491) — on failure, raise without partial claim.
- Capture & claim (L501–505): `claimed_request = state.pending_confirmation`, `waiter_event = state.confirmation_event`, set `state.confirmation_response = response`, clear `state.pending_confirmation = None`, clear `state.confirmation_event = None`.

Emit `confirmation_response` outside lock (L512–525).

Recovery dispatch (L527–558):
- If `waiter_event is not None` → `waiter_event.set()`; waiter post-block re-acquires lock and consumes claim.
- If `waiter_event is None` → no running loop. Re-acquire lock, clear `state.confirmation_response = None`, call `_dispatch_recovery(conv_id, claimed_request, response)`. On handler failure (L550–558), restore `state.pending_confirmation = claimed_request` for retry.

L527 identity check pattern (described in #486): the check that `state.confirmation_response is not response` in the recovery branch was the path documented in #486; #485 is the upstream design question.

## 3. `cancel_pending_confirmation` flow

`conversation_manager.py:560–634`.

Lock-held checks (L580–627):
1. State exists (L580–582) — else return False.
2. Pending exists (L584–585) — else return False.
3. **Response already claimed (L586–593)** — if `state.confirmation_response is not None`, defer to responder and return False. This is the asymmetry from `request_confirmation`: cancel respects a live claim; request_confirmation does not.
4. Capture pending (L594), build denial (L595–598), capture `waiter_event` (L604).
5. Archive under lock (L614–621) — raise on failure with pending state untouched.
6. Claim & clear (L625–627): set `state.confirmation_response = response`, clear `state.pending_confirmation` and `state.confirmation_event`.

Signal waiter outside lock (L632–633).

## 4. `execute_tool_calls` concurrency

`src/decafclaw/tool_execution.py:282–364`.

- `asyncio.Semaphore(ctx.config.agent.max_concurrent_tools)` at L292 (default 5).
- Per-tool ctx fork at L297: `call_ctx = ctx.fork_for_tool_call(tc["id"])`.
- `asyncio.create_task(execute_single_tool(call_ctx, tc, semaphore))` per tool (L298–302).
- `asyncio.gather(*tasks, return_exceptions=True)` at L316.

Call path for confirmation from a tool:
- `execute_single_tool` (L210) → `execute_tool` (L232) → tool handler → `request_confirmation(ctx, ...)` (in `tools/confirmation.py:106`) → `ctx.request_confirmation(request)` → `ConversationManager.request_confirmation` (L763).

**Concurrency conclusion:** Two tools running concurrently in the same turn can each invoke `request_confirmation` on the same `conv_id`. The second's state setup (L790–794) will overwrite the first's `pending_confirmation` / `confirmation_event` / `confirmation_response`, orphaning the first waiter on a now-dead event.

## 5. Existing tests

In `tests/test_conversation_manager.py` and `tests/test_confirmation.py`.

**Lifecycle** (`test_conversation_manager.py`):
- `test_request_confirmation_approved` (L104)
- `test_request_confirmation_denied` (L132)
- `test_request_confirmation_timeout` (L155)
- `test_confirmation_emits_request_event` (L170)
- `test_confirmation_persisted_to_archive` (L196)
- `test_request_confirmation_no_timeout` (L296)
- `test_always_field_in_confirmation_response` (L388)

**Startup recovery** (L414–481):
- `test_startup_scan_finds_pending_confirmation`
- `test_startup_scan_ignores_resolved_confirmations`
- `test_startup_scan_ignores_stale_confirmations`

**Race / durability** (L1283–1799):
- `test_concurrent_confirmation_responses_dont_double_dispatch` (L1283) — two `respond_to_confirmation` for same id → handler runs once (#440).
- `test_cancel_pending_confirmation_after_response_claimed_is_noop` (L1340)
- `test_request_confirmation_timeout_loses_race_to_late_responder` (L1378)
- `test_respond_to_confirmation_rolls_back_claim_on_archive_failure` (L1465)
- `test_cancel_pending_confirmation_rolls_back_on_archive_failure` (L1518)
- `test_recover_confirmation_restores_on_dispatch_failure` (L1553)
- `test_request_confirmation_timeout_archive_failure_preserves_state` (L1647)
- `test_recovered_confirmation_dispatch_uses_captured_request` (L1699) — covers a related concurrent-overwrite case at the recovery-branch level.
- `test_recovery_dispatch_proceeds_when_new_request_lands_mid_flight` (L1802) — related to #486.
- `test_cancel_pending_confirmation_wakes_live_waiter` (L1878)

**Gap:** no test exercising **two concurrent `request_confirmation` calls** on the same `conv_id` (the #485 reproduction).

`test_confirmation.py` covers archive round-trips only (L15–112).

## 6. Types and state fields

**`ConfirmationRequest`** (`confirmations.py:23–60`):
- `action_type: ConfirmationAction` (enum: RUN_SHELL_COMMAND, ACTIVATE_SKILL, CONTINUE_TURN, ADVANCE_PROJECT_PHASE, WIDGET_RESPONSE)
- `action_data: dict`, `message: str`, `approve_label: str`, `deny_label: str`
- `tool_call_id: str` (empty for non-tool contexts)
- `timeout: float | None` (default 300.0; None for widgets)
- `confirmation_id: str` — `uuid4().hex[:12]` (L37). 48 bits of entropy.
- `timestamp: str`

`to_archive_message()` / `from_archive_message()` at L40 / L50.

**`ConfirmationResponse`** (`confirmations.py:63–97`):
- `confirmation_id: str` (matches the request)
- `approved: bool`, `always: bool`, `add_pattern: bool`
- `data: dict`, `timestamp: str`

`to_archive_message()` (L78) drops falsy optional flags; `from_archive_message()` (L93) tolerant of missing keys.

**`ConversationState` fields** (`conversation_manager.py:188–191`):
- `pending_confirmation: ConfirmationRequest | None`
- `confirmation_event: asyncio.Event | None`
- `confirmation_response: ConfirmationResponse | None`

All three mutated atomically under `state.lock`.

## Summary

| Aspect | Status |
|---|---|
| `request_confirmation` overwrite | unconditional — sets `pending_confirmation` / `confirmation_event` / `confirmation_response = None` without checking for an existing live claim (L790–794) |
| `cancel_pending_confirmation` overwrite | guarded — defers to claimed responses (L586–593) |
| Concurrent tool path | real: per-tool `asyncio.create_task` under `max_concurrent_tools` semaphore (default 5) |
| Test coverage of concurrent `request_confirmation` | none |
