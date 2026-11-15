# Shared instruction budget

User request: fit AGENTS.md within the 32,768-byte project-instruction budget.
Base: eb242060 (origin/main). Worktree: docs-trim-agent-instructions.

AGENTS.md remains a symlink to CLAUDE.md. Condensed repeated feature descriptions, issue anecdotes, file lists, and command descriptions. Retained operational rules and subsystem documentation links. Corrected stale context/cache wording to match #1259. Added a 28 KiB maintenance target for headroom.

Validation: 42,280 -> 22,674 bytes (10,094 bytes below the limit). Symlink/content checks, local-link checks, `git diff --check`, `make fmt`, and `make lint` passed. No source files changed. The documentation-only baseline, application suite, and runtime-model evals were skipped.

Next: publish a PR. No merge authorization. Same-session documentation review; no independent model review.
