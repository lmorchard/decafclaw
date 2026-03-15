# Portable Skills Backlog

Composable tools + prompts that could be packaged and used in any agent
(DecafClaw, OpenClaw, Nanobot, Picoclaw, etc.) that provides a context
object, workspace, and event bus.

## Episodic memory (enhancements)

Current implementation is basic. Improvements:

- `related_to` / `supersedes` entry linking — lightweight knowledge graph
- Memory pruning / archival — summarize old entries

## Knowledge base (Obsidian-style wiki)

Augment episodic memory with a structured knowledge base of wiki-linked
topics that can be added to and refined over time.

- Lives in workspace: `workspace/{agent_id}/wiki/`
- Each topic is a markdown file with `[[wiki-links]]`
- Agent can create, update, and link pages — not append-only
- **Memory** = "Les said on March 13 he likes Boulevardiers" (episodic)
- **Wiki** = "Les's drink preferences: Boulevardier, Old Fashioned" (curated truth)
- Agent consolidates memories into wiki pages over time

Tools: `wiki_read`, `wiki_write`, `wiki_append`, `wiki_search`, `wiki_links`

## Self-reflection / retry

Post-response evaluation: "Did I actually answer the question?"
Retry with a different approach if not.

- Separate LLM call (cheap model) for evaluation
- Could be a `reflect` tool or built into the agent loop
- Limit retries to prevent loops

## Spec / plan / execute loop

Autonomous project workflow: gather info → spec → plan → to-do → execute.

- `start_project(description)` meta-tool or skill
- Each phase produces artifacts (spec.md, plan.md, to-do list)
- User reviews/approves at phase boundaries or lets it run
- Pairs with: to-do list, scratchpad, sub-agent delegation, tool confirmation

## Sub-agent delegation

Fork a child agent to handle a subtask concurrently.

- `delegate(task, tools)` spawns a child `run_agent_turn`
- Child gets forked context, subset of tools, different system prompt
- Results flow back via event bus or return value
- Async architecture already supports concurrent agent turns

## Claude Code as a sub-agent tool

Delegate software engineering tasks to Claude Code (or similar).

- `code_agent(task, working_dir)` spawns `claude -p "..."`
- Streams progress back as `tool_status` events
- Results: diff, test output, commit hash — posted to chat
- Could also work with Codex CLI, aider, etc.
- The spec/plan/execute loop could drive it: DecafClaw plans, Claude Code codes

## Document ingestion

Chunk and store long documents for future reference.

- `ingest_document(url_or_path)` tool
- Chunks stored as markdown files, searchable like memories
- Tabstack extract for initial conversion
- Future: vector embeddings for chunk retrieval

## Proactive outreach (heartbeat enhancements)

The heartbeat system enables basic proactive outreach. Enhancements:

- Active hours (restrict heartbeat to time windows)
- Per-section interval overrides (some tasks hourly, others daily)
- Agent-initiated DMs based on triggers (not just periodic)

## Multi-turn structured input

Walk the user through collecting structured data step by step.

- Form wizard pattern: ask fields one by one
- To-do list tracks which fields are collected
- Final output: structured JSON, ticket, etc.

## Conversation handoff

Summarize a conversation and hand it to a different agent or channel.

- Escalation, cross-channel moves, shift handoffs
- Preserves context via summary

## System prompt as a living document

Agent suggests edits to its own system prompt, user approves.

- `suggest_prompt_edit(section, old_text, new_text, rationale)` tool
- Suggestions logged for human review
- We're already doing this manually — formalize it
