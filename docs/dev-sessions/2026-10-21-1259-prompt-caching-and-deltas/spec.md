# Spec: Prompt Caching Prefix Stability and Delta Context Injections

- Issues: [#1111](https://github.com/lmorchard/decafclaw/issues/1111), [#1116](https://github.com/lmorchard/decafclaw/issues/1116)
- Date: 2026-10-21
- Status: Draft / In Progress

## Context and Goals

LLM providers (OpenAI automatic, Gemini 2.5+ implicit, Anthropic prompt caching) rely on exact prefix matching to provide prompt cache discounts. Any change or rewrites to the prompt prefix between turns busts the prompt cache from that byte offset onward.

Between compactions, a conversation's prompt must remain **append-only**:
1. All static prefix components (tools, system prompt, vault guide, deferred tools, tool guidance) must remain stable across turns.
2. Injected context items (conversation notes, recent journal entries) must be injected once as a baseline and then as deltas, rather than repeated in full every turn.
3. Injections must be archived at the point they were introduced so the prompt history remains identical on subsequent turns.
4. Pre-emptive skill hints and dynamic context status notes that depend on current turn input/tokens must not sit in the system prefix; they must be appended as messages at the tail (or omitted/relocated) so the prefix up to the prior turn is completely preserved.
5. In-place tool result clearing (`clear_old_tool_results`) must not rewrite past tool message bodies in-memory between compactions, as doing so breaks the cached prefix.
6. When compaction runs, high-water marks and injected paths reset so the post-compaction turn injects a fresh full baseline into the new session.

## Detailed Requirements

### 1. High-Water Marks & Delta Injections (#1111)
- **Persisted State in `conversation_manager.py`:**
  - Add `last_recent_journal_ts: datetime | None = None` to `PersistedTurnState` and `_PERSISTED_BINDINGS`.
  - Add `last_notes_count: int = 0` to `PersistedTurnState` and `_PERSISTED_BINDINGS`.
  - Bindings read/write to `ctx.composer.last_recent_journal_ts` and `ctx.composer.last_notes_count`.
  - Add both field names to `_CTX_DRIVEN_FIELDS`.
- **`ComposerState` in `context_composer.py`:**
  - `last_recent_journal_ts: datetime | None = None` (already exists, now persisted across turns).
  - Add `last_notes_count: int = 0`.
- **Delta Behavior in `_compose_notes`:**
  - Read all notes up to limits.
  - If `last_notes_count > 0`: only take items `items[last_notes_count:]`.
  - If new items exist, format and return them with header `"[Conversation notes update — new entries added to scratchpad]"` (or similar delta header if needed, or format delta entries).
  - Update `ctx.composer.last_notes_count = len(items)`.
- **Delta Behavior in `_compose_recent_journal`:**
  - Filter by `timestamp > last_recent_journal_ts`.
  - If new entries exist, advance `last_recent_journal_ts` to newest timestamp.
- **Archive Invariant:**
  - Continue archiving `notes_msgs` and `recent_journal_msgs` in `to_archive`. Since only deltas are generated on subsequent turns, no duplicates are injected or archived!
- **Compaction Reset:**
  - In `compaction.py:compact_history` and `agent.py`: reset `last_recent_journal_ts = None` and `last_notes_count = 0` (via `ctx.composer.reset_for_compaction()` or clearing state).
  - Next turn after compaction re-injects full baseline.

### 2. Prefix Stability for Prompt Caching (#1116)
- **Context Status Note:**
  - Currently appended as `{"role": "system", "content": status_line}` at the end of `messages`.
  - On Vertex (Gemini), provider adapter extracts all `system` role messages and puts them in `systemInstruction`. This changes `systemInstruction` every turn!
  - Fix: emit `status_line` with `role: "user"` (e.g. `{"role": "user", "content": status_line}`) or ensure Vertex adapter only puts true system messages in `systemInstruction`. Using a user note (or non-system role) at the end ensures `systemInstruction` remains constant, and on OpenAI/Anthropic it is at the very tail after user messages.
- **Pre-emptive Skill Hint:**
  - Currently placed before `llm_history` as a `system` message (`messages.append({"role": "system", "content": preempt_skill_text})`).
  - It depends on the current user message, so putting it in system messages or before history breaks caching of the entire history!
  - Fix: convert `preempt_skill_text` to an injected tail message (e.g. role `preempt_skill` remapped to `user` in `ROLE_REMAP` and archived, or injected right alongside user turn injections before the user message).
- **Tool Result Clearing (`clear_old_tool_results`):**
  - Disable in-place clearing between compactions during interactive turns so the prefix remains byte-identical.
  - Run tool result clearing only during/before compaction, or gate behind a config toggle that defaults to preserving the cache.
- **Prefix Invariant Verification:**
  - Test asserting that between compactions across turns 1..N, request N's prompt prefix (system + tools + prior history) is a strict prefix of request N+1's prompt.
