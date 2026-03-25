# Proactive Memory Retrieval — Plan

## Step 1: Config dataclass and wiring

**Context:** We need a `MemoryContextConfig` dataclass and it needs to be wired into the main `Config`.

**What to do:**
- Add `MemoryContextConfig` dataclass to `config_types.py` with fields: `enabled` (bool, default True), `similarity_threshold` (float, default 0.3), `max_results` (int, default 5), `max_tokens` (int, default 500), `show_in_ui` (bool, default True)
- Add `memory_context: MemoryContextConfig` field to the main `Config` dataclass in `config.py`
- Add `skip_memory_context: bool = False` to `Context.__init__` in `context.py`
- Verify: `make check` passes

## Step 2: Set skip flags in non-interactive callers

**Context:** After step 1, the flag exists but nothing sets it. Mirror the `skip_reflection` pattern.

**What to do:**
- In `heartbeat.py`: set `ctx.skip_memory_context = True` (same place as `skip_reflection`)
- In `schedules.py`: set `ctx.skip_memory_context = True` (same place as `skip_reflection`)
- In `tools/delegate.py`: set `child_ctx.skip_memory_context = True` (same place as `skip_reflection`)
- Verify: `make check` passes

## Step 3: Core retrieval function

**Context:** Steps 1-2 gave us config and skip flags. Now build the retrieval logic as a standalone async function.

**What to do:**
- Create `src/decafclaw/memory_context.py` with an async function `retrieve_memory_context(config, user_message) -> list[dict]` that:
  - Returns empty list if `config.memory_context.enabled` is False
  - Returns empty list if `config.embedding.model` is not set
  - Calls `embeddings.search_similar(config, user_message, top_k=max_results * 2)` with `source_type=None` to search all types (wiki, memory, conversation). Note: wiki results already get a 1.2x similarity boost in `search_similar_sync`, so no additional source-type sorting needed — just use results as-returned by similarity
  - Filters by `similarity_threshold`
  - Trims to `max_results` and `max_tokens` budget (using `len(text) // 4`)
  - Returns list of result dicts with `entry_text`, `source_type`, `similarity`
  - **Fail-open:** Entire function wrapped in try/except — any error (embedding API down, DB issue, etc.) logs a warning and returns empty list. Must never crash the agent turn.
  - **No reindex trigger:** If the embedding index is empty, return empty results. Do not call `reindex_all` — avoid adding latency to the hot path. The agent's explicit `memory_search` tool handles reindexing. This means we should call `embeddings.embed_text` + `embeddings.search_similar_sync` directly instead of `search_similar` (which auto-reindexes).
- Add a `format_memory_context(results) -> str` function that formats the results into a single string with the framing prefix and source labels
- Write a test in `tests/test_memory_context.py` covering: empty results, threshold filtering, token budget trimming, disabled config, no embedding model, error handling (fail-open)
- Verify: `make test` and `make check` pass

## Step 4: Inject into agent turn

**Context:** Step 3 gives us a retrieval function. Now wire it into `run_agent_turn` in `agent.py`.

**What to do:**
- In `agent.py`, after the user message is appended to history (line ~451) and before building the messages array (line ~456):
  - Check `ctx.skip_memory_context` — if True, skip
  - Call `retrieve_memory_context(config, user_message)`
  - If results are non-empty, call `format_memory_context(results)` to get the text
  - Create a message dict with `role: "memory_context"` and the formatted content
  - Append to `history` (before the user message — insert at `len(history) - 1`)
  - Archive the message via `_archive(ctx, memory_context_msg)`
- Do **not** add `"memory_context"` to `LLM_ROLES` — it needs role remapping, not passthrough
- Update the `llm_history` builder in `agent.py` to remap `"memory_context"` → `"user"`:
  ```python
  # Role remapping for non-standard roles that should appear in LLM context
  ROLE_REMAP = {"memory_context": "user"}

  for m in history:
      role = m.get("role")
      if role in LLM_ROLES:
          llm_history.append(m)
      elif role in ROLE_REMAP:
          llm_history.append({**m, "role": ROLE_REMAP[role]})
  ```
- Verify: `make check` passes

## Step 5: UI event emission

**Context:** Step 4 handles retrieval and injection. Now add visibility.

**What to do:**
- In `agent.py`, right after injecting the memory context message, emit an event:
  ```python
  if config.memory_context.show_in_ui:
      await ctx.publish("memory_context", results=results, text=formatted_text)
  ```
- In `mattermost.py` `_subscribe_progress`, add a handler for `"memory_context"` events that calls `conv_display.on_tool_status("memory_context", text)` — format as an expandable block showing source types and snippets
- In `web/websocket.py`, handle the `"memory_context"` event similarly if needed (check if tool_status events already flow through to the web UI generically)
- Verify: `make check` passes

## Step 6: Docs and cleanup

**Context:** Feature is functional. Update docs.

**What to do:**
- Create `docs/memory-context.md` documenting the feature: what it does, config options, how to disable, silent skip when no embedding model
- Update `CLAUDE.md`: add `memory_context.py` to key files, add convention note about `skip_memory_context` flag
- Update `README.md`: add config options to the config table if one exists
- Update `docs/index.md` with link to new doc
- Verify: `make check` passes
- Commit everything
