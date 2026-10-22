# Implementation Plan: Async Scheduling, Waits, and Polling Optimization (#1095, #1103, #1107)

## Phase 1: Polling Tool Exemption in Loop Breaker (#1107)
- [ ] Step 1.1: Add `exempt_tools` to `LoopBreakerConfig` in `src/decafclaw/config_types.py` and tuple support in `src/decafclaw/config.py`.
- [ ] Step 1.2: Update `LoopBreaker.record()` in `src/decafclaw/loop_breaker.py` to bypass repeat counts for exempt tools while preserving error tracking.
- [ ] Step 1.3: Add tests in `tests/test_loop_breaker.py` and `tests/test_agent_loop_breaker.py`.
- [ ] Step 1.4: Verify tests pass with `uv run pytest tests/test_loop_breaker.py tests/test_agent_loop_breaker.py`.

## Phase 2: First-Class Asynchronous Turn Scheduling (#1103)
- [ ] Step 2.1: Register `.scheduled_wakes.json` in `src/decafclaw/conversation_paths.py`.
- [ ] Step 2.2: Implement `ScheduledWake` and `ScheduledWakeManager` in `src/decafclaw/scheduled_wakes.py` (schedule, cancel, list, fire, startup_scan, shutdown).
- [ ] Step 2.3: Wire `ScheduledWakeManager` into `src/decafclaw/conversation_manager.py` (instantiation, startup scan, shutdown).
- [ ] Step 2.4: Implement tools `schedule_turn`, `schedule_cancel`, `schedule_list` in `src/decafclaw/tools/schedule_tools.py` and register in `src/decafclaw/tools/__init__.py`.
- [ ] Step 2.5: Write unit tests in `tests/test_scheduled_wakes.py` and integration tests in `tests/test_conversation_manager.py`.
- [ ] Step 2.6: Verify tests pass with `uv run pytest tests/test_scheduled_wakes.py tests/test_conversation_manager.py`.

## Phase 3: Prompt Guardrails, Tool Steering & Preemptive Matchers (#1095)
- [ ] Step 3.1: Add `## Background Operations & Lifecycle` section to `src/decafclaw/prompts/AGENT.md`.
- [ ] Step 3.2: Update tool descriptions for `wait` in `src/decafclaw/tools/core.py`, and `shell_background_start` in `src/decafclaw/skills/background/SKILL.md` and `tools.py`.
- [ ] Step 3.3: Verify preemptive keyword matching for background/wait/schedule in `tests/test_preempt_search.py` and sharpen keyword overlap if needed.
- [ ] Step 3.4: Add tool choice eval cases in `evals/tool_choice/core_overlaps.yaml`.
- [ ] Step 3.5: Run `make eval-tools` and unit tests.

## Phase 4: Documentation and Verification Gate
- [ ] Step 4.1: Update relevant documentation in `docs/` (`docs/tools.md`, `docs/background-wake.md` / `docs/schedules.md`).
- [ ] Step 4.2: Update `CLAUDE.md` key files list if new modules (`scheduled_wakes.py`, `schedule_tools.py`) warrant navigation entry.
- [ ] Step 4.3: Run full gate: `make fmt`, `make check`, `make test`.
