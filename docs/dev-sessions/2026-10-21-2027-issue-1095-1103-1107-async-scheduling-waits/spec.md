# Spec: Async Scheduling, Waits, and Polling Optimization (#1095, #1103, #1107)

## Background & Motivation

Agents handling long-running or asynchronous operations (builds, test suites, training runs, CI jobs, background services) face three interlocking challenges:

1. **Conversational Fiction & Lack of Honest Lifecycle Boundaries (#1095):** Agents frequently tell users "I will check back in 5 minutes" without initiating any background watcher, timer, or polling mechanism. The agent turn ends, the agent goes dormant, and the promise is never fulfilled. Additionally, agents fail to reach for `wait` or `shell_background_start` when appropriate.
2. **Loop-Breaker False Positives on Polling (#1107):** The loop-breaker (`loop_breaker.py`) trips after 3 identical tool calls in a turn (`repeat_threshold: 3`). While beneficial for detecting stuck mutating tools, this prematurely terminates legitimate polling loops such as `wait(seconds=60)` or `shell_background_status(job_id=...)`.
3. **Absence of First-Class Asynchronous Turn Scheduling (#1103):** When a task will take several minutes or hours, blocking the active interactive turn with `wait()` is impractical (burning connection keep-alives and blocking user input). Agents have no way to schedule an autonomous wake turn (`TurnKind.WAKE`) in the future while concluding the current turn cleanly.

Addressing all three together provides a complete, robust lifecycle for both in-turn and cross-turn asynchronous operations.

---

## Technical Specifications

### Phase 1: Polling Tool Exemption in Loop Breaker (#1107)

1. **Configuration (`src/decafclaw/config_types.py` & `src/decafclaw/config.py`):**
   - Add `exempt_tools: tuple[str, ...] = ("wait", "shell_background_status")` to `LoopBreakerConfig`.
   - Update `_coerce` in `config.py` to handle `origin is tuple` by parsing comma-delimited strings into tuples.
   - Update `load_sub_config` in `config.py` to convert lists from JSON into tuples for tuple-annotated fields.

2. **Loop Breaker (`src/decafclaw/loop_breaker.py`):**
   - In `LoopBreaker.record(self, calls)`:
     - Check if `sig.tool_name in getattr(self._cfg, "exempt_tools", ())`.
     - If exempt, skip incrementing repeat counts in `self._counts` (preventing repeat threshold trips).
     - Keep tracking errors: `sig.is_error` continues to append to `self._recent_errors` and increment `self._total_errors` to catch error surges even on exempt tools.

3. **Verification:**
   - Unit tests in `tests/test_loop_breaker.py`:
     - 5+ consecutive calls to `wait` or `shell_background_status` do not trip `LoopVerdict.NUDGE` or `LoopVerdict.HARD_STOP`.
     - Non-exempt tools continue tripping at `repeat_threshold`.
     - Errors from `wait` or `shell_background_status` still trigger error surge trips at `error_threshold`.
   - Agent loop test in `tests/test_agent_loop_breaker.py`.

---

### Phase 2: First-Class Asynchronous Turn Scheduling (#1103)

1. **Data Model & Sidecar (`src/decafclaw/scheduled_wakes.py` & `src/decafclaw/conversation_paths.py`):**
   - Add `(".scheduled_wakes.json", "scheduled_wakes.json")` to `SIDECAR_FILENAMES`.
   - Sidecar path: `{workspace}/conversations/{conv_id}/scheduled_wakes.json`.
   - Schema:
     ```json
     {
       "schedules": [
         {
           "schedule_id": "wake_abc123",
           "conv_id": "conv_xyz",
           "delay_seconds": 120,
           "reason": "Check CI build status",
           "scheduled_at": "2026-10-21T20:30:00Z",
           "fire_at": "2026-10-21T20:32:00Z",
           "fire_at_ts": 1792614720.0,
           "metadata": {}
         }
       ]
     }
     ```

2. **Scheduler Service (`src/decafclaw/scheduled_wakes.py`):**
   - `ScheduledWakeManager`:
     - Maintains active `asyncio.Task` timer handles mapped by `schedule_id`.
     - `schedule(conv_id, delay_seconds, reason, metadata=None) -> ScheduledWake`:
       - Enforces `delay_seconds >= 1` (or reasonable bounds, e.g., max 86400s / 24h).
       - Persists to sidecar.
       - Starts background timer task.
     - `cancel(conv_id, schedule_id) -> bool`:
       - Cancels background timer task, removes from sidecar, returns boolean success.
     - `list_schedules(conv_id) -> list[ScheduledWake]`:
       - Returns pending schedules from sidecar.
     - `fire(schedule: ScheduledWake)`:
       - Removes from sidecar.
       - Enqueues wake turn:
         `manager.enqueue_turn(conv_id=conv_id, kind=TurnKind.WAKE, prompt=..., metadata=...)`.
       - Prompt:
         `"Scheduled wake triggered: {reason}. Review history and current status, then respond to the user or reply with BACKGROUND_WAKE_OK to remain silent if nothing requires attention."`
     - Startup Recovery (`startup_scan()`):
       - Scans conversation sidecars for `scheduled_wakes.json`.
       - For each entry: if `fire_at_ts <= now`, schedules immediate trigger; otherwise calculates remaining delay and schedules timer.
     - Shutdown (`shutdown()`):
       - Cancels in-flight timer tasks cleanly without losing sidecar state.

3. **Tool Definitions (`src/decafclaw/tools/schedule_tools.py`):**
   - `schedule_turn(ctx: "Context", delay_seconds: int, reason: str, metadata: dict | None = None) -> ToolResult`
   - `schedule_cancel(ctx: "Context", schedule_id: str) -> ToolResult`
   - `schedule_list(ctx: "Context") -> ToolResult`
   - Priority: `normal`.
   - Registered in `src/decafclaw/tools/__init__.py` in `TOOLS` and `TOOL_DEFINITIONS`.

4. **Integration with ConversationManager & Runner:**
   - ConversationManager instantiates `self.scheduled_wakes = ScheduledWakeManager(self)`.
   - `startup_scan()` recovers pending scheduled wakes.
   - `shutdown()` cleanly cancels active timers.

---

### Phase 3: Prompt Guardrails, Tool Steering & Preemptive Matchers (#1095)

1. **Prompt Guardrails (`src/decafclaw/prompts/AGENT.md`):**
   - Add new section `## Background Operations & Lifecycle`:
     - Explicit rule: NEVER promise or imply future autonomous check-ins without an active background process (`shell_background_start`) or scheduled wake (`schedule_turn`).
     - Clarify turn boundaries: if no automated watcher or schedule is set, inform the user the process has started and ask them to prompt when they want status checked.
     - Clarify in-turn vs cross-turn: use `wait` for short verification within the turn; use `schedule_turn` or `shell_background_start` for longer tasks.

2. **Sharpened Tool Descriptions:**
   - `src/decafclaw/tools/core.py` (`wait`): Emphasize in-turn waiting before checking status; distinguish from `schedule_turn`.
   - `src/decafclaw/skills/background/SKILL.md` & `src/decafclaw/skills/background/tools.py` (`shell_background_start`): Emphasize long-running commands (servers, watchers, builds), output monitoring, and pairing with `wait` or `schedule_turn`.
   - `src/decafclaw/tools/schedule_tools.py` (`schedule_turn`): Emphasize scheduling future turns without blocking the active turn.

3. **Preemptive Search Matchers (`src/decafclaw/preempt_search.py`):**
   - Ensure terms like "check back", "monitor", "background", "watcher", "schedule", "progress" align with `shell_background_start`, `shell_background_status`, `wait`, and `schedule_turn`.

4. **Evals & Prompt Tests:**
   - Tool choice eval in `evals/tool_choice/core_overlaps.yaml`.
   - Unit tests verifying prompt tags and preemptive tool matching.
