# Frozen acceptance checks

**Source:** https://github.com/lmorchard/decafclaw/issues/82
**Frozen at:** 93d7d8ad
**Check files — read-only from Phase 1 onward:**
- `tests/test_friction.py`

## C1
CRITERION: WHEN the friction analysis scanner runs over conversation archives containing user messages with repeated correction patterns across multiple sessions, THE SYSTEM SHALL group them by theme and emit proposed AGENT.md additions or memory entries.
CHECK: `pytest tests/test_friction.py::test_friction_analysis_groups_corrections` passes.
AT FREEZE: fails — `AssertionError: No themes extracted`

## C2
CRITERION: WHEN triggered via the `!friction` command, THE SYSTEM SHALL scan recent conversation archives for user correction messages and output a summary of proposed persistent instruction updates.
CHECK: `pytest tests/test_friction.py::test_friction_command_execution` passes.
AT FREEZE: fails — `AssertionError: Command !friction not recognized`

## Guards
- G1: `make test` — existing test suite passes without regressions.
  Passed at freeze.

## Adjudication
- C1: accepted — the check provides specific input across multiple sessions and asserts that the analysis tool returns expected structure and extracts specific concepts. Hardcoding could bypass, but as a unit test, it validates the structure.
- C2: accepted — validates that the command is registered and responds with text containing expected keywords ("proposed", "theme").
- G1: accepted — the existing test suite ensures no regressions.

## Amendments
