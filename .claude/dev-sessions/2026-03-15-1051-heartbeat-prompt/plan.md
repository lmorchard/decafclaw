# Heartbeat Prompt — Implementation Plan

## Overview

6 phases, each ending with lint + test + commit. Phases build sequentially.

---

## Phase 1: Config, Interval Parsing, and HEARTBEAT.md Parsing

**Goal:** Add heartbeat config to the Config dataclass, parse time intervals, read and split HEARTBEAT.md files into sections. Pure functions, no async, easy to test.

**Prompt:**

Create `src/decafclaw/heartbeat.py` with config, interval parsing, and HEARTBEAT.md parsing.

Requirements:
1. Add heartbeat fields to `Config` in `config.py`:
   - `heartbeat_interval: str = "30m"`
   - `heartbeat_user: str = ""`
   - `heartbeat_channel: str = ""`
   - `heartbeat_suppress_ok: bool = True`
   - Wire them in `load_config()` from `HEARTBEAT_INTERVAL`, `HEARTBEAT_USER`, `HEARTBEAT_CHANNEL`, `HEARTBEAT_SUPPRESS_OK`

2. In `src/decafclaw/heartbeat.py`, write `parse_interval(value: str) -> int | None`:
   - Returns seconds, or None if disabled
   - Supports: `"30m"` → 1800, `"1h"` → 3600, `"1h30m"` → 5400, `"90"` → 90 (plain seconds)
   - `""` or `"0"` → None (disabled)
   - Invalid format → log warning, return None
   - Simple regex, no external dependency

3. Write `load_heartbeat_sections(config) -> list[dict]`:
   - Reads `config.agent_path / "HEARTBEAT.md"` and `config.workspace_path / "HEARTBEAT.md"`
   - Merges content (admin first, then workspace)
   - Splits on `## ` headers
   - Content before the first `##` is treated as its own section (title: "General")
   - Returns list of `{"title": str, "body": str}` dicts
   - Empty/missing files return empty list

4. Write `is_heartbeat_ok(response: str) -> bool`:
   - Returns True if `HEARTBEAT_OK` appears (case-insensitive) within the first 300 characters

5. Write `build_section_prompt(section: dict) -> str`:
   - Wraps a section in the heartbeat prompt template:
     ```
     You are running a scheduled heartbeat check. Execute the following task and report your findings.
     If there is nothing to report, respond with HEARTBEAT_OK.

     ## {title}

     {body}
     ```
   - For the "General" section (no `##` header), omit the `## General` line and just include the body.

6. Create `tests/test_heartbeat.py` with tests:
   - `parse_interval`: "30m" → 1800, "1h" → 3600, "1h30m" → 5400, "90" → 90, "" → None, "0" → None, "garbage" → None
   - `load_heartbeat_sections`: admin only, workspace only, both merged, missing files, content before first header, multiple sections
   - `is_heartbeat_ok`: matches, case insensitive, beyond 300 chars doesn't match, not present
   - `build_section_prompt`: correct format for titled and untitled sections

Lint and test after.

---

## Phase 2: Heartbeat Runner (Core Logic)

**Goal:** The async function that executes one heartbeat cycle — reads sections, runs agent turns, collects results. No timer, no reporting yet — just the execution loop.

**Prompt:**

Add the heartbeat cycle runner to `src/decafclaw/heartbeat.py`.

Requirements:
1. Write `async def run_heartbeat_cycle(config, event_bus) -> list[dict]`:
   - Load sections via `load_heartbeat_sections(config)`
   - If no sections, return empty list
   - For each section:
     - Create a fresh `Context(config=config, event_bus=event_bus)`
     - Set context fields: `user_id="heartbeat"`, `channel_id="heartbeat"`, `conv_id=f"heartbeat-{timestamp}-{i}"`
     - Build the prompt via `build_section_prompt(section)`
     - Call `run_agent_turn(ctx, prompt, history=[])` with empty history
     - Collect the result: `{"title": section["title"], "response": response, "is_ok": is_heartbeat_ok(response)}`
   - Return the list of results

2. The function should handle exceptions per section — if one section fails, log the error and continue with the next. Don't let one broken section kill the cycle.

3. Tests:
   - Mock `run_agent_turn` to return canned responses
   - Verify correct number of sections executed
   - Verify each section gets a fresh empty history
   - Verify failed section doesn't stop subsequent sections
   - Verify `is_ok` detection in results

Lint and test after.

---

## Phase 3: Async Timer with Overlap Protection

**Goal:** The timer that fires `run_heartbeat_cycle` periodically, with overlap protection and graceful shutdown.

**Prompt:**

Add the heartbeat timer to `src/decafclaw/heartbeat.py`.

Requirements:
1. Write `async def run_heartbeat_timer(config, event_bus, shutdown_event: asyncio.Event, on_results=None)`:
   - Parse interval via `parse_interval(config.heartbeat_interval)`
   - If None (disabled), return immediately
   - Loop:
     - `await asyncio.sleep(interval)` (first heartbeat after one full interval)
     - Check `shutdown_event` — if set, break
     - Check overlap flag — if cycle is running, log warning and skip
     - Set overlap flag
     - Run `run_heartbeat_cycle(config, event_bus)`
     - Call `on_results(results)` callback if provided (for reporting)
     - Clear overlap flag
   - On shutdown: if a cycle is in progress, wait for it to complete (with timeout)

2. The `on_results` callback is how reporting hooks in. Mattermost and interactive modes will provide different callbacks. This keeps the timer generic.

3. Use `asyncio.wait_for` on the sleep to respect shutdown without waiting the full interval.

4. Tests:
   - Timer with short interval (0.1s) fires callback
   - Timer respects shutdown event
   - Overlap skips when cycle is slow (mock a slow cycle)
   - Disabled interval (None) returns immediately

These tests need careful async handling — use `asyncio.Event` and short intervals.

Lint and test after.

---

## Phase 4: Mattermost Reporting

**Goal:** Wire heartbeat into Mattermost — DM channel resolution, top-level post, threaded replies, suppression.

**Prompt:**

Add Mattermost heartbeat reporting and wire the timer into `MattermostClient.run`.

Requirements:
1. Add `async def _get_dm_channel(self, user_id: str) -> str` to `MattermostClient`:
   - Calls `POST /api/v4/channels/direct` with `[self.bot_user_id, user_id]`
   - Returns the channel ID

2. Add `async def _resolve_heartbeat_channel(self, config) -> str | None` to `MattermostClient`:
   - If `config.heartbeat_channel` is set, return it
   - If `config.heartbeat_user` is set, call `_get_dm_channel` and return the result
   - Otherwise return None (heartbeat disabled)

3. Add `_make_heartbeat_reporter(self, channel_id, event_bus)` to `MattermostClient`:
   - Returns an `async def on_results(results)` callback
   - The callback:
     - Posts a top-level marker: `🫀 Heartbeat — {datetime}`
     - For each result:
       - If `is_ok` and `suppress_ok` is True, skip
       - Otherwise post as threaded reply to the marker: `**{title}**\n\n{response}`
     - If ALL results were suppressed (all OK), delete the top-level marker
   - For each section, subscribe a progress handler (reuse `_subscribe_progress`) with the threaded reply post as the placeholder

4. In `MattermostClient.run`:
   - After MCP init, resolve the heartbeat channel
   - If channel resolved, start the heartbeat timer as a background task:
     ```python
     heartbeat_task = asyncio.create_task(
         run_heartbeat_timer(config, event_bus, shutdown_event,
                            on_results=reporter)
     )
     ```
   - In the `finally` block, cancel and await the heartbeat task

5. Tests:
   - `_resolve_heartbeat_channel`: returns channel when set, resolves DM when user set, returns None when neither
   - Reporter callback: posts marker + threaded replies, suppresses OK, deletes marker when all OK
   - These will need mocked HTTP calls

Lint and test after.

---

## Phase 5: Interactive Mode Reporting

**Goal:** Wire heartbeat into interactive terminal mode.

**Prompt:**

Add heartbeat reporting to `run_interactive` in `agent.py`.

Requirements:
1. Create an interactive heartbeat reporter:
   - Prints a header: `--- Heartbeat — {datetime} ---`
   - For each result:
     - If `is_ok` and `suppress_ok` is True, skip
     - Otherwise print: `[{title}] {response}`
   - If all suppressed, print nothing (or a single `[heartbeat: all OK]` at debug level)

2. In `run_interactive`:
   - After MCP init, start the heartbeat timer as a background task
   - Pass the interactive reporter as `on_results`
   - On shutdown (in `finally`), cancel and await the heartbeat task
   - The timer runs concurrently with the input loop via `asyncio.create_task`

3. Note: in interactive mode, heartbeat output will interleave with the user prompt. This is acceptable — the heartbeat fires every 30m, unlikely to collide with typing. If it does, the output just appears above the next `you>` prompt.

4. Tests: minimal — interactive reporting is best verified manually. The core logic (timer, cycle, sections) is already tested.

Lint and test after.

---

## Phase 6: Integration, Documentation, and Cleanup

**Goal:** End-to-end verification, documentation, backlog cleanup.

**Prompt:**

Final integration and documentation pass.

Requirements:
1. **Manual verification** (Mattermost):
   - Set `HEARTBEAT_INTERVAL=1m` for testing (short interval)
   - Create a `data/decafclaw/HEARTBEAT.md` with two sections
   - One section that does something (e.g., "check the weather")
   - One section that returns nothing interesting (should produce HEARTBEAT_OK)
   - Verify: top-level marker posted, threaded replies for non-OK sections, OK section suppressed, marker deleted if all OK
   - Verify: overlap skip logged when interval < cycle duration
   - Verify: graceful shutdown cancels timer and waits for in-flight cycle

2. **Manual verification** (interactive):
   - Same HEARTBEAT.md, `HEARTBEAT_INTERVAL=1m`
   - Verify heartbeat output appears between prompts

3. **Documentation**:
   - Create `docs/heartbeat.md` — config, HEARTBEAT.md format, reporting, suppression, examples
   - Update `docs/index.md` with heartbeat link
   - Update `CLAUDE.md` key files and conventions
   - Update `docs/backlog/devinfra.md` — remove heartbeat prompt item

4. Run full test suite, lint. Commit.

---

## Summary of Phases

| Phase | What | Key Files | Tests |
|-------|------|-----------|-------|
| 1 | Config + interval/section parsing | `config.py`, `heartbeat.py` | ~12 tests |
| 2 | Heartbeat cycle runner | `heartbeat.py` | ~4 tests |
| 3 | Async timer with overlap protection | `heartbeat.py` | ~4 tests |
| 4 | Mattermost reporting | `mattermost.py` | ~3 tests |
| 5 | Interactive mode reporting | `agent.py` | manual |
| 6 | Integration + docs | docs, CLAUDE.md | manual |

## Implementation Notes

- **Heartbeat runs concurrently with the listen loop** — both Mattermost and interactive mode use `asyncio.create_task` to start the timer alongside their main loops. The `shutdown_event` coordinates cleanup.
- **Each section is a full agent turn** — it goes through `run_agent_turn` with the system prompt, tools, and all. This means heartbeat sections can use skills (if activated globally) and MCP tools.
- **Progress events work naturally** — each section's `context_id` is unique, so the progress subscriber filters correctly. Heartbeat sections get their own progress handling separate from user conversations.
- **The `on_results` callback pattern** keeps reporting decoupled from the timer/runner. Mattermost posts threads, interactive prints to stdout, tests assert on results — same core logic.
- **DM channel creation** — Mattermost's `POST /channels/direct` creates or returns an existing DM channel. We call it once during setup, not on every heartbeat.
