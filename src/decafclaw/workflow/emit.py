"""Server-side emission of workflow-driven conversation messages.

Stub for Phase 1. Phase 2 implements `emit_workflow_message(ctx, ...)`
following the pattern from `skills/background/tools.py:142-165` (append
to archive + emit to WebSocket subscribers, no agent turn / LLM call).
"""

from __future__ import annotations
