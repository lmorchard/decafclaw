# Scoped Reminders Before Matching Tool Calls Execute — Experiment Design

**Status:** drafted for delivery (burndown run 2026-11-13)
**Date:** 2026-11-13
**Owner:** Les (via agent delivery)
**Audience:** human + agent implementer
**Issue:** [#1127](https://github.com/lmorchard/decafclaw/issues/1127)

## Problem

Standing instructions consume context even when they are irrelevant. Reflection can also notice a known mistake only after a tool changes external state.

## Goals

- Test trusted, admin-authored rules against finalized tool arguments before execution.
- A matching rule can add a reminder or reject the call with correction guidance.
- Scope rules by tool and relevant argument fields.
- Bound repeated injections and correction attempts.
- Record matched rule identifiers for inspection and evaluation.
- Keep capability grants and approval decisions in existing permission mechanisms; workspace-authored rules must not grant authority or replace trusted rules.

## Non-goals

- Mid-stream interruption, AST matching, and model-based rule judges in the first experiment; defer until the smaller experiment proves useful.

## Current state (as of base `origin/main@579bdb0`)

- Standing instruction / memory paths consume context regardless of relevance.
- Reflection surfaces known mistakes post-hoc.

## Design proposal

### 1. Deterministic first check

- Match finalized tool arguments against trusted admin-authored rules (deterministic checks first).
- Matching rule → reminder or rejection with correction guidance.

### 2. Bounds

- Bounded repeated injections and correction attempts; matched-rule recording.

### 3. Safety

- Rules cannot bypass shell approval, skill permissions, or child tool restrictions.

## Acceptance criteria mapping

- Matching rejected call → no side effect + correction guidance.
- Unrelated call → no reminder, normal result.
- Repeated matches stop within budget.
- Rules cannot bypass approvals/permissions.
- Direct calls and enabled tool bridges apply same boundary or are explicitly excluded.
- Trusted reminders distinguishable from untrusted tool output.

## First implementation slice and proposed tests

- Slice: deterministic checks against finalized tool arguments; small eval set (genuine mistakes, harmless lookalikes, correction attempts); report false positives and added calls/tokens.

## Open design decisions

- None blocking; defer mid-stream interruption, AST matching, model-based judges.

## Evidence

- `src/decafclaw/loop_breaker.py`, `src/decafclaw/tool_execution.py`, oh-my-pi TSR injection lifecycle docs.
- Coordination: #530 pre-write judge.
