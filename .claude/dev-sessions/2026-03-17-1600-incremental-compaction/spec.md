# Incremental Compaction

## Problem

`compact_history` reads the full original archive and re-summarizes all old turns from scratch on every compaction run. Cost grows linearly with conversation length.

## Goal

Use the previous compaction summary as a base and only summarize newly-old turns (turns that have fallen off the recent-preserve window since the last compaction).

## Scope

- Modify `compact_history` in `compaction.py` to work incrementally
- Preserve the existing sidecar `.compacted.jsonl` format
- No changes to archive format or tool interfaces

## References

- Issue: #57
- Prior session: `docs/dev-sessions/2026-03-14-1024-conversation-compaction/`
- Key files: `src/decafclaw/compaction.py`, `src/decafclaw/archive.py`
