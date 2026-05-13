# Agent.py Split — Extract tool_execution + tool_definitions Spec

**Goal:** Finish the `agent.py` decomposition started by PRs #382 / #407 by relocating tool-invocation and tool-registry machinery into dedicated modules, dropping `agent.py` from ~1451 lines to ~750.

**Source:** [Issue #438](https://github.com/lmorchard/decafclaw/issues/438)

## Current state

`src/decafclaw/agent.py` (1451 lines) still hosts ~12 module-level helpers below the `class TurnRunner` boundary that are tool-execution machinery, not orchestration:

**Tool-invocation cluster** (~250 lines):
- `_execute_tool_calls` (`agent.py:741`)
- `_execute_single_tool` (`agent.py:580`)
- `_process_tool_media` (`agent.py:529`)
- `_resolve_widget` (`agent.py:652`)
- `_media_placeholder_pattern` (`agent.py:522`)

**Tool-registry cluster** (~150 lines):
- `_collect_all_tool_defs` (`agent.py:381`)
- `_build_tool_list` (`agent.py:422`)
- `_refresh_dynamic_tools` (`agent.py:334`)
- `_skill_def_cache` + `invalidate_skill_cache` (top of file)

The registry cluster is also imported by `context_composer.py` — moving it gives composer a non-private import target (consumed by issue #439).

## Desired end state

1. New `src/decafclaw/tool_execution.py` (~250 lines) — "given a tool call, run it and produce a normalized result with media + widget integration."
2. New `src/decafclaw/tool_definitions.py` (~150 lines) — "given a ctx, gather/classify/refresh the tool list."
3. Underscores dropped on the public functions in their new homes; module-internal helpers stay private.
4. Imports updated in `agent.py`, `context_composer.py`, and any other callers (do not change `context_composer.py` *import targets* beyond the necessary rename — full relocation per issue #439 lands separately).
5. `agent.py` lands at ~750 lines.
6. `run_agent_turn` shim at `agent.py:1438` preserved verbatim — three external callers depend on it (`compaction.py`, `eval/runner.py`, `conversation_manager.py`).
7. `IterationOutcome` / `_Continue` / `_Final` / `ReflectionOutcome` tagged unions unchanged.

## Design decisions

- **Decision:** Split into two modules along the invocation/definition boundary rather than one big `tool_runtime.py`.
  - **Why:** They have different consumers — invocation is internal to the agent loop; definitions are consumed by both the loop and `context_composer.py`. Separation prevents composer from accidentally importing execution internals.
  - **Rejected:** Single combined module — would re-create the same coupling problem from a different angle.

- **Decision:** Drop underscores from the relocated public functions.
  - **Why:** Cross-module imports of underscore-prefixed names are a code smell (the underscore lies about visibility). The whole point of relocation is to make a real public surface.
  - **Rejected:** Keep underscores — fails the spirit of the refactor.

- **Decision:** Land before #439 so the new `tool_definitions.py` is available for composer to import from.

## Patterns to follow

- Module shape mirrors existing peers (`reflection.py`, `compaction.py`) — top-level functions, dataclasses where helpful, no class wrappers unless state demands it.
- Keep the `_skill_def_cache` mutable state co-located with `invalidate_skill_cache` and any cache-touching helpers — they form one logical unit.

## What we're NOT doing

- **NOT changing call sites in `context_composer.py` beyond the import-target rename for `_collect_all_tool_defs` → `collect_all_tool_defs`.** The wiki/page-injection and `_resolve_attachments` relocations are issue #439's scope.
- **NOT touching `reflection.py`** — it's already cleanly read-only relative to `agent.py`.
- **NOT renaming or modifying `run_agent_turn`** — it's the stable public surface.
- **NOT modifying the tagged-union types** (`IterationOutcome`, `_Continue`, `_Final`, `ReflectionOutcome`).
- **NOT adding new functionality.** Pure relocation.
- **NOT changing `TOOL_TIMEOUT_SEC`, per-tool timeout machinery, or shell-approval helpers.**

## Validation

- `make check` (lint + typecheck) green.
- `make test` green.
- Spot-check: `compaction.py` child-agent invocation still imports cleanly; `eval/runner.py` still imports `run_agent_turn`.
- Grep audit: no `from .agent import _execute_*` / `_collect_*` / `_build_*` / `_refresh_*` from outside `agent.py` after the move.

## Open questions

- None. Issue body fully specifies the split; no decisions deferred to plan/execute.
