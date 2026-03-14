# Portable Skills Backlog

Composable tools + prompts that could be packaged and used in any agent
(DecafClaw, OpenClaw, Nanobot, Picoclaw, etc.) that provides a context
object, workspace, and event bus.

## Episodic memory (enhancements)

Current implementation is basic. Improvements:

**Done:**
- ~~Entry-aware search~~ — returns whole entries
- ~~Per-agent memory~~ — dropped per-user directory
- ~~Semantic search~~ — embeddings via text-embedding-004

**Future:**
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

## ~~Per-conversation to-do list~~ (DONE)

Implemented: markdown checkboxes on disk, per-conversation, crash-recoverable.

## ~~Chain-of-thought / scratchpad~~ (DONE)

Implemented: `think` tool, hidden from user, logged for debugging.

## ~~Conversation history search~~ (DONE)

Implemented: semantic search over archived messages via `source_type`
column in shared embeddings DB. Messages indexed as they're archived.

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

## Tool confirmation / approval flow

Before executing dangerous tools, ask the user for confirmation.
"I'm about to run `rm -rf /tmp/data`. React with :+1: to confirm."

- Channel-specific approval mechanism (Mattermost reactions, terminal y/n)
- Configurable per-tool risk levels
- Reusable interaction pattern

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

## Scheduled / recurring tasks

"Check this URL every hour and tell me if it changes."

- Event bus for notifications
- Scheduler + task definitions (SQLite or workspace files)
- Pairs with proactive outreach

## Proactive outreach

The agent messages the user first, not just in response.

- "That URL you asked me to monitor changed"
- Daily summary of memories or completed tasks
- Requires scheduled tasks + channel write capability

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
