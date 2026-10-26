# Specification: Workspace Structural Outlines (v2 Radical Simplification)

## Context
Issue: #1135
Supersedes: PR #1176 (closed due to accidental complexity: AST unparsing, thread-safe LRU cache, mutation invalidation hooks, and regex CommonMark engine).

## Problem
Large workspace files require repeated line reads or send irrelevant bodies into context. We need a deterministic outline that lets the agent inspect structure before reading specific sections, matching the interaction pattern in `vault_show_sections`.

## Scope & Constraints
- Support Python (`.py`) classes and functions (sync and async), plus Markdown (`.md`, `.markdown`) headings.
- **Boring Design**:
  - **No AST string unparsing**: Slice declaration lines directly from file text at `node.lineno`. Preserves Python 3.12+ `[T]` generic type parameters, metaclasses, decorators, kwargs, and formatting natively with zero custom syntax reconstruction.
  - **No cache**: Parsing local files under 10,000 lines takes 1-2 ms. Eliminates `threading.Lock`, LRU eviction, and 8 mutation-invalidation hooks across `workspace_tools.py`.
  - **Simple Section Lookup**: Case-insensitive exact name and unique suffix matching. Section retrieval delegates directly to the existing line-range reader.
  - **Bounded Output**: Outline capped at 200 items; file read capped at 200 lines with explicit continuation hints.
  - **Explicit Fallback**: Unsupported extensions or malformed files (e.g. `SyntaxError`) produce explicit fallback messages, not fabricated structure.

## Acceptance Criteria
- [ ] `workspace_outline(path)` returns line ranges and declaration lines for Python files without fabricating or unparsing code.
- [ ] `workspace_outline(path)` returns line ranges and heading levels for Markdown files.
- [ ] `workspace_read(path, section=...)` reads the exact line range for a matched section.
- [ ] Malformed Python files produce a helpful syntax error fallback.
- [ ] Unsupported file types produce an explicit unsupported message.
- [ ] Disambiguation eval in `evals/tool_choice/core_overlaps.yaml` passes.
- [ ] Full `make check` and test suite pass cleanly with zero warnings.
