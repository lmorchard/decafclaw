# Research — Token accounting in ContextComposer

Documentarian-mode findings (Explore subagent). All `file:line` refs against this worktree's tree.

## 1. Token-budget calculation in `ContextComposer.compose()`

### `SourceEntry`

- Defined at `src/decafclaw/context_composer.py:46-53` — dataclass with `source`, `tokens_estimated`, `items_included`, `items_truncated`, `details`.
- Instances appended in `compose()` for every component:
  - System prompt: `:280-281`
  - Wiki references: `:285-293` (only when present)
  - Conversation notes: `:298-303`
  - Preemptive tool matches: `:308-312`
  - Preemptive skill matches: `:317-321`
  - Tools: `:324-325`
  - Memory context: `:368-376`
  - History: `:423-429`

### `injected_ids`

- Built at `:338-339` — `set` of Python `id()` values for current-turn `wiki_msgs` + `notes_msgs`.
- Consulted only at `:340-344` (the `existing_history_tokens` filter).

### "Existing history" token total used for budget

- Computed at `:340-344`:
  ```python
  existing_history_tokens = sum(
      estimate_tokens(str(m.get("content", "")))
      for m in history
      if id(m) not in injected_ids
  )
  ```
- Filter is by Python object identity, so messages **loaded from the archive on prior turns** are NOT in `injected_ids` and therefore ARE counted.
- Accumulated into `fixed_tokens` at `:345`.

### `history_entry` (diagnostics-side accounting)

- `history_only` filter at `:414` excludes messages whose role is in the remap dict (`vault_retrieval`, `vault_references`, `conversation_notes`).
- `history_tokens` summed at `:415-419` over `history_only` (role in `LLM_ROLES`).
- `history_entry = SourceEntry(source="history", tokens_estimated=history_tokens, ...)` at `:423-428`.
- This entry is what the diagnostics sidecar reports and what `_build_context_status` totals.

### Dynamic memory budget

- `fixed_tokens` accumulates: system, wiki (if present), notes (if present), `existing_history_tokens`, user message, tools, preempt-skill matches (`:329-351`).
- `window_size = self._get_context_window_size(config)` at `:357`; resolved at `:1047-1058` (prefers `config.llm.context_window_size`, else `config.compaction.max_tokens`).
- `response_reserve = 4096` (hard-coded, `:354`).
- `remaining_budget = max(0, window_size - fixed_tokens - response_reserve)` at `:358`.
- Memory budget = `remaining_budget` if > 0 else `None` (`:362-365`); `None` falls back to `config.vault_retrieval.max_tokens` inside `_compose_vault_retrieval` (`:620`).

## 2. Role remapping

### Current `role_remap`

`context_composer.py:383-387`:

```python
role_remap = {
    "vault_retrieval": "user",
    "vault_references": "user",
    "conversation_notes": "user",
}
```

### `vault_retrieval`

- Created in `_compose_vault_retrieval()` at `:647`: `msg = {"role": "vault_retrieval", "content": formatted}`
- Appended to `history` and to archive list at `:372-374`.
- Reloaded from archive on subsequent turns into `history`.
- Remapped to `"user"` at the LLM-history filter `:388-399`.

### `vault_references`

- Created in `_compose_vault_references()` at `:732-736` (carries extra `wiki_page` field).
- Appended to history at `:289`, archive at `:290`.
- Remapped to `"user"` at `:388-399`.

### `conversation_notes`

- Created in `_compose_notes()` at `:780`: `msg = {"role": "conversation_notes", "content": text}`
- Appended to history at `:300`, archive at `:301`.
- Remapped to `"user"` at `:388-399`.

### Where remapping happens

- Single loop at `:388-399`. Iterates `history`, includes messages whose role is in `LLM_ROLES` as-is, includes messages whose role is in `role_remap` with the remapped role, and expands `background_event` into a synthetic tool-call/tool-result pair (`:391-395`).

## 3. Test coverage

### Direct `compose()` tests in `tests/test_context_composer.py`

- `TestCompose.test_produces_valid_composed_context` (`:817-834`) — basic shape: messages, token estimate > 0, `sources` populated.
- `TestCompose.test_message_ordering` (`:836-858`) — system / deferred / user ordering and budget-pressure deferral.
- `TestCompose.test_truncates_long_user_message` (`:860-875`) — `agent.max_message_length` truncation.
- `TestCompose.test_stores_sources_on_state` (`:877-890`) — `composer.state.last_sources` and `last_total_tokens_estimated` updates.
- `TestCompose.test_includes_retrieved_context_text` (`:892-909`) — memory text formatting.

### Sub-component tests

- `TestGetContextWindowSize` (`:134-145`) — window size resolution.
- `TestComposeSystemPrompt` (`:111-131`) — system prompt token estimation.
- `TestComposeMemoryContext` (`:151-270`) — memory retrieval / suppression / token budgeting.
- `TestRetrievalModes` (`:275-366`) — dynamic budget per `vault_retrieval.mode`.
- `TestComposeTools` (`:441-500`) — tool deferral under budget pressure.
- `TestComposePreemptMatches` (`:506-631`) — preempt token addition.
- `TestComposePreemptSkillMatches` (`:650-810`) — preempt skill token addition.

### Adjacent

- `TestScoreCandidates` (`:915-966`) — composite scoring.
- `TestBuildDiagnostics` (`:973-1028`) — diagnostics dict shape.
- `TestContextSidecar` (`:1031-1050`) — sidecar write/read, fail-open.
- `TestBuildContextStatus` (`:1073-1096`) — status-line formatting.
- `TestContextStatusInCompose` (`:1099-1135`) — context-status injection on/off.
- `TestExpandBackgroundEvent` (`:1141-1263`) and `TestComposeExpandsBackgroundEvent` (`:1266-1356`) — background-event expansion.

No test currently exercises the role-based `history_only` filter at `:414` against archived remap-role messages.

### Helpers

- `_make_tool_def()` at `:430-438`.
- `_make_skill_info()` at `:637-647`.

## 4. Diagnostics surface

### `show_context_status`

- Config: `config_types.py:178` — `show_context_status: bool = True`.
- Consulted at `context_composer.py:449-455`. When on, appends a status line as a final `system` message inside `messages`.
- Status string built by `_build_context_status` at `:1061-1077`:
  ```
  [Context: ~{total_tokens:,} / {context_window:,} tokens ({pct:.0f}%), {message_count} messages{hint}]
  ```
  Adds ` — consider being concise` above 70% utilization.

### Sidecar

- Path: `workspace/conversations/{conv_id}.context.json` resolved at `:98-107`.
- Writer `write_context_sidecar` at `:110-117` — creates parent dir, JSON-dumps with `indent=2, default=str`, fail-open on exception.
- Schema returned by `build_diagnostics` at `:1084-1126`:
  - `timestamp`, `total_tokens_estimated`, `total_tokens_actual`, `context_window_size`, `compaction_threshold`
  - `sources: [SourceEntry-shaped dicts]`
  - `memory_candidates: [...]`
  - `cleanup: {cleared_count, cleared_bytes}`
- Call site for sidecar write: `agent.py:1410-1424` (`_write_diagnostics`).
- REST endpoint `/api/conversations/{id}/context` at `http_server.py:1778`; handler `get_context_diagnostics` at `:557-570` reads via `read_context_sidecar`.

## Summary of relevant divergence

The bug surface has two filters that count history differently:

- **Budget filter** (`:340-344`) — by `id()`. Archived remap-role messages from prior turns ARE counted here.
- **Diagnostics filter** (`:414-419`) — by role. Archived remap-role messages from prior turns are NOT counted here. `history_entry.tokens_estimated` and the status-line totals therefore underreport.

Issue #393's framing implies the budget side is also affected; the agent's reading of `:340-344` says the budget side already uses an id-based filter that does count archived messages. **This needs verification by direct read before designing a fix.**
