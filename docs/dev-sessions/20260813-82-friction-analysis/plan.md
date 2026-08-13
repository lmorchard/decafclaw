# Friction Analysis Implementation Plan

**Goal:** Surface repeated user corrections and propose AGENT.md additions.

**Source issue:** https://github.com/lmorchard/decafclaw/issues/82 — **Tier:** `auto-ok` (User approved criteria)

**Approach:** Implement `analyze_friction` in `friction.py` using LLM to extract themes from user correction messages. Create a user-invocable skill `friction` that calls a tool to run this analysis and output the summary.

**Criteria:** C1 [Scanner groups user corrections by theme] · C2 [!friction command outputs summary]

---

## Phase 0: Freeze the acceptance checks

Write `checks.md` and author the tests the checks name.
**Files:**
- Create: `docs/dev-sessions/20260813-82-friction-analysis/checks.md`
- Create: `tests/test_friction.py`

**Verification — automated:**
- [x] Every criterion's check runs and fails for expected reason
- [x] Every guard runs and passes
- [x] Check-reviewer dispatched
- [x] Freeze commit made; sha recorded

---

## Phase 1: Implement Friction Analyzer

Implement core logic to scan archives and extract friction themes.

**Advances:** C1

**Micro-tasks:**
- [x] Implement `analyze_friction` in `src/decafclaw/friction.py`
- [x] Scan archives using `iter_conversation_archives` and `_read_jsonl` to collect recent user messages
- [x] Filter messages using a basic keyword heuristic to avoid sending all history to LLM
- [x] Call LLM using a structured output or simple prompt to extract `FrictionTheme` items

**Files:**
- Modify: `src/decafclaw/friction.py`
- Modify: `tests/test_friction.py` (add mock for LLM if necessary)

**Verification — automated:**
- [x] C1's check passes: `pytest tests/test_friction.py::test_friction_analysis_groups_corrections`
- [x] Guards still pass: `make test`

---

## Phase 2: User Command !friction

Create a user-invokable skill to trigger the analysis and output it.

**Advances:** C2

**Micro-tasks:**
- [x] Create `src/decafclaw/skills/friction/SKILL.md` with `user-invocable: true`
- [x] Create `src/decafclaw/skills/friction/tools.py` with `friction_analyze` tool
- [x] Implement tool to call `analyze_friction` and format the output

**Files:**
- Create: `src/decafclaw/skills/friction/SKILL.md`
- Create: `src/decafclaw/skills/friction/tools.py`

**Verification — automated:**
- [x] C2's check passes: `pytest tests/test_friction.py::test_friction_command_execution`
- [x] Guards still pass: `make test`
