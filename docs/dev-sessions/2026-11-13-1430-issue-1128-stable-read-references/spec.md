# Stable Read References for Tool Outputs, Child Results, and History — Design

**Status:** drafted for delivery (burndown run 2026-11-13)
**Date:** 2026-11-13
**Owner:** Les (via agent delivery)
**Audience:** human + agent implementer
**Issue:** [#1128](https://github.com/lmorchard/decafclaw/issues/1128)

## Problem

Saved tool outputs and child results need references that stay useful after compaction or reload.

- `ContextComposer` assembles LLM history from the in-memory message list; compaction summarizes old content and `context_cleanup` replaces old, large tool bodies with `[tool output cleared: N bytes]` stubs. Full bodies survive durably only where they were materialized (`tool_outputs/{tool_call_id}.txt` on truncation).
- After compaction or reload, the model has no stable handle to the saved content — only ordering/position and stubs.
- Child agent results are likewise nested inside parent history rather than addressed by content handle.

## Goals

- Trace a saved output from tool completion through compaction, reload, paged retrieval, and deletion.
- Define ownership and authorization for references (references cannot expose another conversation's content without permission).
- Show two conversations with identical local identifiers resolving to their respective content.
- Distinguish complete output, capped output, failed capture, missing content, and denied access.
- Specify compatibility with existing workspace paths and identify a small implementation slice with observable tests.

## Non-goals

- Cross-conversation / user-global terminals and sharing in this slice (terminal carve-out mirrors the #1021 design treatment).
- Universal artifact registry / cross-user sharing.

## Current state (as of base `origin/main@579bdb0`)

- `tool_execution.py`: on `max_tool_output_bytes` overflow, body is written to `{conv_dir}/tool_outputs/{tool_call_id}.txt`; in-memory history keeps the truncated body plus `[Output truncated. Full output saved to …]` pointer.
- `context_cleanup.py`: in-memory-only stub pass (`[tool output cleared: …]`, `[tool error superseded …]`) ahead of the compaction-threshold check; archive retains originals.
- `context_composer.py`: `_reorder_tool_results` re-anchors tool results to the assistant message that issued them after compaction / concurrent archiving.
- Compaction: summarize old content; surviving history is rebuilt.

## Design proposal

### 1. Resource reference vs. content

- Introduce a stable resource reference (handle) for a saved tool output; the handle is content-addressed or handle-addressed and persists across compaction/reload.
- Tab/tool handle stays view handle; artifact/resource handle is the durable identity (mirrors the #1021 artifact-handle pattern).

### 2. Lifecycle

1. **Capture**: tool completes → body persisted (on truncation) under tool-call-scoped store; handle issued.
2. **Reference**: handle enters conversation history as a reference (not a body).
3. **Clear**: old tool bodies may be stubbed in-memory; handle + pointer remain.
4. **Retrieve**: paged retrieval by handle.
5. **Delete**: conversation cleanup or explicit deletion removes the stored content; a deleted reference reports missing on retrieval, never stale or foreign content.

### 2.1 Retrieval result states

Retrieval by handle distinguishes five outcomes, each reported distinctly (and rendered distinctly in model-facing text):

- **Complete** — full body captured; paging serves the whole content.
- **Capped** — capture hit `max_tool_output_bytes`; only the stored prefix exists; retrieval reports the cap and serves pages of what was persisted.
- **Failed capture** — persistence errored at tool completion; the reference records the failure and retrieval reports it (no silent empty body).
- **Missing** — handle is known but the content file is absent (deleted, or never materialized because the output fit inline).
- **Denied** — the handle resolves to another conversation's content without permission; retrieval refuses and never falls back to foreign content.

### 3. Ownership and authorization

- References cannot expose another conversation's content without permission; per-conversation scoping preserved.

### 4. Child results and retained history

- Extend design to child results and retained history where storage supports it; first slice covers saved tool output.

### 5. Compatibility with existing workspace paths

- The first slice reuses the existing materialization: `{conv_dir}/tool_outputs/{tool_call_id}.txt` (session-aware conversation dir per `conversation_paths.py`). No new top-level workspace area is introduced.
- Handles are conversation-scoped: two conversations with identical local identifiers resolve within their own `{conv_dir}` and cannot cross (mirrors §3).
- The in-memory pointer (`[Output truncated. Full output saved to …]`) is the existing reference form; the design's handle syntax (open decision below) replaces or wraps it without moving where files live.
- The JSONL archive retains original tool messages; handles address the materialized file and do not duplicate archived content.

## Acceptance criteria mapping

- Trace saved output through completion → compaction → reload → paged retrieval → deletion — covered by §2.
- Ownership and authorization; two conversations with identical local identifiers resolve to their respective content — §3.
- Distinguish complete/capped/failed-capture/missing/denied — §2.1.
- Compatibility with existing workspace paths — §5; small slice with observable tests — "First implementation slice and proposed tests" below.

## First implementation slice and proposed tests

- Slice: introduce stable resource references for saved tool output first (design + handle), with observable tests for capture/truncation, compaction, reload, paged retrieval, deletion.
- Tests: tool-output store tests for truncation pointer, context-cleanup stub behavior with handle, composer reordering after compaction.

## Open design decisions

- Reference syntax (bare handle vs. typed envelope) and cross-conversation access model remain decisions for this design; the implementation slice must not hard-code either.

## Evidence

- `src/decafclaw/tool_execution.py` (truncation + pointer), `src/decafclaw/context_cleanup.py` (stub), `src/decafclaw/context_composer.py` (`_reorder_tool_results`).
- `src/decafclaw/conversation_paths.py` (tool_outputs materialization).
- `docs/context-composer.md`, #298, #306, #1146.
- Prior art: oh-my-pi resource schemes (`artifact://`, `agent://`), #1021 durable artifact identity design.
