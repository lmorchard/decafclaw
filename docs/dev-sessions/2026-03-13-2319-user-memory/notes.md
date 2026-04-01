# Session Notes — User Memory

## Session Info

- **Date:** 2026-03-13, started ~23:19
- **Branch:** `user-memory`
- **Commits:** 8
- **Files changed:** 13 (+810 / -6 lines)
- **New files:** `memory.py`, `tools/memory_tools.py`
- **Conversation turns:** ~30 (brainstorm + plan + execute + live testing)

## Recap

Built a file-based memory system: per-user directories with daily
markdown files, searchable via substring grep. Three tools: save,
search, and recent.

### What we built

1. **Config additions** — `DATA_HOME`, `AGENT_ID`, `AGENT_USER_ID`, `workspace_path`
2. **Context enrichment** — Mattermost and terminal layers populate user/channel/thread on forked context
3. **memory.py** — core operations: `save_entry`, `search_entries`, `recent_entries`
4. **memory_tools.py** — tool wrappers pulling context from ctx
5. **Tool registry + system prompt** — wired in, prompt encourages memory use
6. **Iterative search prompt engineering** — multiple rounds of refinement based on live agent feedback

### The prompt engineering cycle

The most interesting part of this session was the live feedback loop:

1. Deployed memory tools, tested with the agent in Mattermost
2. Agent failed to find "cocktails" (saved as "cocktail") on first search
3. Asked the agent what would improve the tools
4. Agent suggested query expansion guidance in tool description
5. Implemented its suggestion, tested again
6. Asked again — agent suggested system-level reinforcement
7. Added iterative search guidance to system prompt
8. Asked again — agent suggested a "checklist" approach
9. Restructured tool description as a numbered checklist
10. Agent successfully found the memory on next test

Key insight: **the agent can diagnose its own tool limitations and propose
actionable fixes**. We just needed to ask the right questions and translate
its suggestions into concrete changes.

## Divergence from Plan

The 6-step plan was executed as written. All divergence was post-plan:

- Four additional commits for prompt engineering, driven by live testing
- The checklist-style tool description was not planned — emerged from
  the agent's own feedback
- System prompt update for iterative search was the agent's idea

## Key Insights

1. **Tool descriptions are a real control surface.** The difference between
   "here are some things you could try" and "work through this numbered
   checklist" is significant for LLM behavior. Prescriptive > suggestive.

2. **The agent can improve itself.** Asking "what would make this tool
   better?" and "what changes to your prompt would help?" yielded
   actionable, correct suggestions. The agent understood its own failure
   mode (substring vs semantic) and proposed the right mitigation.

3. **File-based memory is surprisingly usable.** Simple append-only
   markdown files with grep search. No database, no embeddings. The
   limitations are real (substring matching) but manageable with good
   prompt engineering.

4. **Context propagation matters.** The tools don't know about Mattermost —
   they just read `ctx.user_id`, `ctx.channel_id`, etc. This clean
   separation means the memory system works identically in terminal
   and Mattermost modes.

5. **Agents don't like admitting they need help.** When asked for
   improvements, the agent initially said "no changes needed, I just
   need to follow instructions better." The actual fix was structural
   (better tool descriptions), not motivational.

## Efficiency Notes

- Implementation was fast — 6 steps in about 15 minutes of execution
- The prompt engineering cycle took longer than the implementation
- Live testing with the real agent was essential — would not have
  discovered the substring matching issues from code review alone

## Process Improvements

- **Live testing should be part of the plan.** For tool-centric features,
  the "smoke test" step should include real agent interaction, not just
  import checks.
- **Agent self-feedback is a technique worth systematizing.** "What would
  improve this tool?" should be a standard post-deployment question.

## Backlog Items Added

- Proper linting and testing (ruff/pytest)
- Memory future enhancements in existing backlog entry (linking, RAG, pruning)
