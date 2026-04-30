# Spec — Separate `history` from current-turn injections in `ContextComposer.compose()`

Tracks: [#393](https://github.com/lmorchard/decafclaw/issues/393)

## Goal

Eliminate the design tension that produced #393's bug — `compose()` using `history` as both input (archived prior turns) and scratchpad (fresh-this-turn injections appended into it). Result: token accounting becomes structurally correct rather than maintained-by-convention via two filters that have to stay in sync.

## Current state

`ContextComposer.compose()` in `src/decafclaw/context_composer.py`:

- Mutates the input `history` list in place by appending fresh-this-turn `vault_references`, `conversation_notes`, `vault_retrieval`, and the user message.
- Has **two** filters that count "history tokens" with different semantics:
  - **Budget filter** (`:338-344`) — id-based; excludes current-turn wiki+notes by Python object identity. Used to compute `existing_history_tokens` → `fixed_tokens` → `remaining_budget` → `memory_budget`. Correct today.
  - **Diagnostics filter** (`:413-414`) — role-based; excludes ALL messages whose role is in `role_remap` (`vault_retrieval`, `vault_references`, `conversation_notes`), including those archived from prior turns. Feeds `history_entry.tokens_estimated`, the sidecar `total_tokens_estimated`, and the `[Context: ...]` status line. Underreports.
- Sub-methods that read `history` (`_get_already_injected_pages` in `_compose_vault_references`, `extract_last_assistant_text` in `_compose_notes` / `_compose_preempt_*`) work correctly with prior-turn-only `history`; they don't depend on seeing this turn's injections.
- After `compose()` returns, the caller (`agent.py:1050+`) continues to mutate `self.history` (tool results, assistant messages, etc.) and passes it back into `compose()` on the next turn — so the post-turn state of `history` MUST include this turn's injections.

## Desired end state

- `compose()` treats the input `history` list as **read-only during all calculation**. It still appends this turn's injections, but only **after** all token / budget / diagnostics work is finished, so the caller-visible side-effect is preserved.
- The two history-token filters are gone. There is one calculation: `sum tokens in history` (with no exclusions) feeds both budget and diagnostics. Per-source `SourceEntry` instances (`wiki_entry`, `notes_entry`, `memory_entry`) account for fresh injections; `history_entry` accounts for everything archived (including archived remap-role messages).
- Archived `vault_retrieval` / `vault_references` / `conversation_notes` messages from prior turns count toward `history_entry.tokens_estimated`, `total_tokens_estimated`, and the `[Context: ...]` status line — closing #393.
- Final assembly builds `combined = [*history, *wiki_msgs, *notes_msgs, *memory_msgs, user_msg]` explicitly, runs role-remap / `_reorder_tool_results` / attachment resolution over `combined`, and returns it as `composed.messages`.
- Tests cover the regression: archived remap-role messages in `history` produce the expected `history_entry.tokens_estimated`.

## Design decisions

### Decision 1 — Defer history-append to end of `compose()`

**Chosen:** Append `wiki_msgs`, `notes_msgs`, `memory_msgs`, and `user_msg` to `history` at the **end** of `compose()`, after diagnostics are computed.

**Reasoning:** `self.history` is load-bearing for the agent loop after `compose()` returns (tool-result appends, future turns). We can't drop the side-effect; we can move it past the calculation so during calculation `history` represents only archived past content. Single mutation point, clearly placed, easy to reason about.

**Alternative considered:** Stop mutating `history` entirely and have the agent loop append injections itself from `composed.messages_to_archive`. Rejected — pushes a compose-internal concern into the caller.

### Decision 2 — One history-token calculation, no filters

**Chosen:** A single `history_tokens = sum(estimate_tokens(str(m.get("content", ""))) for m in history if m.get("role") in LLM_ROLES_OR_REMAP)` calculation (where `LLM_ROLES_OR_REMAP` is `LLM_ROLES | role_remap.keys()`). Use this value for both `existing_history_tokens` (budget) and `history_entry.tokens_estimated` (diagnostics).

**Reasoning:** With (1) in place, `history` only contains archived prior-turn content during calculation. No injection-vs-archive disambiguation is needed. Remap-role messages should count (they're sent to the LLM after remap). Background-event records and other non-LLM-role internal messages should not. The role check enforces this without an id-based exclusion set.

**Alternative considered:** Drop the role check entirely and count everything in `history`. Rejected — `history` may contain `confirmation_request` / `confirmation_response` records and similar archive-only entries that aren't sent to the LLM.

### Decision 3 — Explicit `combined` assembly for LLM message list

**Chosen:** Build `combined = [*history, *wiki_msgs, *notes_msgs, *memory_msgs, user_msg]` immediately before the role-remap loop, and iterate `combined` instead of `history` in the loop at `:388-399`.

**Reasoning:** Replicates the existing LLM message ordering (archived → wiki → notes → memory → user) without relying on prior in-place appends. Explicit, single source of truth for ordering, easier to read.

### Decision 4 — `_compose_vault_references` already-injected check still scans `history`

**Chosen:** `_get_already_injected_pages(history)` continues to scan only `history`. `_compose_vault_references` is called once per turn before any current-turn injections happen, so within-turn dedupe is unnecessary; cross-turn dedupe works because prior turns' `vault_references` messages live in archived `history`.

**Reasoning:** No code change needed. The function's premise — `history` contains archived prior-turn injections — is now structurally true throughout `compose()` rather than incidentally true.

### Decision 5 — Compose still mutates `history` at the end (don't break the API)

**Chosen:** `compose()` continues to mutate the caller's `history` list as the final step. Update the docstring at `:252` to reflect the new ordering ("calculates token accounting on the input history; appends this turn's injections at the end").

**Reasoning:** The agent loop and tests rely on the post-compose state of `history` containing this turn's injections. Breaking that API would require touching every call site. Keep the side effect; just move it past the calculation.

## Patterns to follow

- **`SourceEntry`** for per-source token accounting, defined at `src/decafclaw/context_composer.py:46-53`. Each fresh injection (wiki, notes, memory) already produces one. `history_entry` continues to account for archived content. The sum of all `SourceEntry.tokens_estimated` should equal the total prompt-token estimate.
- **Single mutation point** — match the existing pattern of `to_archive` (built incrementally, appended to once at the end). Apply the same to `history`.
- **Role-remap dict** stays at `:383-387`. The dict's purpose narrows to "remap role names before sending to LLM"; it's no longer load-bearing for token-accounting filtering.
- **`LLM_ROLES`** constant defined at `src/decafclaw/archive.py:13` as `{"system", "user", "assistant", "tool"}`. For the new history-token calculation, count messages whose role is in `LLM_ROLES | role_remap.keys()` so archived remap-role messages count.

## What we're NOT doing

- Not changing how `vault_retrieval` / `vault_references` / `conversation_notes` messages are produced, archived, or remapped to `user` role for the LLM. Only how their tokens are counted.
- Not changing the `to_archive` flow or `composed.messages_to_archive`.
- Not changing the dynamic memory-budget formula (`window_size - fixed_tokens - response_reserve`). Only the `existing_history_tokens` term that feeds it (which becomes structurally correct via this refactor).
- Not changing the response-reserve constant (4096).
- Not changing sidecar JSON schema. The numbers in `total_tokens_estimated` and `history` source entry change (become accurate), but field names stay.
- Not changing context-window-size resolution.
- Not changing the docs/architecture page beyond the relevant `docs/context-composer.md` section about history accounting (per CLAUDE.md "update its `docs/` page as part of the same PR").
- Not adding new config knobs.
- Not removing `role_remap` — it still drives LLM role remapping at `:398-399`. We just stop using its keys as a filter for token accounting.

## Test plan

### Regression test (the bug from #393)

In `tests/test_context_composer.py`, add a test in `TestCompose` that:

1. Constructs a `history` containing one archived message of each remap role (`vault_retrieval`, `vault_references`, `conversation_notes`) plus a normal `user` + `assistant` pair. None of these have `id()` matches with this turn's freshly-built injections (they're prior-turn loaded-from-archive).
2. Calls `compose()` with no fresh wiki references, no fresh memory hits, notes empty (or otherwise verifies the test isn't accidentally re-injecting).
3. Asserts `history_entry.tokens_estimated` is approximately the sum of token estimates for ALL five archived messages — not just the user/assistant pair.
4. Asserts `composed.total_tokens_estimated` (or the equivalent that build_diagnostics returns) reflects the same total.

### Structural test (no double-counting)

Same test class, separate test:

1. Constructs a `history` (some archived content), and a `compose()` call that triggers fresh wiki + notes + memory injection.
2. Asserts that the sum of `SourceEntry.tokens_estimated` across `wiki_entry`, `notes_entry`, `memory_entry`, and `history_entry` equals the post-compose history-content token total **plus** the user message tokens (i.e., no message's tokens are counted twice and none are dropped).

### Existing test impact

Run the existing `tests/test_context_composer.py` suite. Tests that construct an input `history` containing fresh-this-turn-shaped injections (with `id()`s that won't be in `injected_ids` after the refactor since `injected_ids` is gone) — those tests will likely need adjustment to either:
- Pass injections via the new path (let `compose()` produce them via its sub-methods, with appropriate fixtures), or
- Pass them as archived-history-shaped (already remap-roled) entries the calculation should count.

Audit candidates: `TestCompose.test_message_ordering`, `TestCompose.test_includes_retrieved_context_text`, `TestComposeMemoryContext.*`, `TestRetrievalModes.*`. Likely small edits, not rewrites.

## Verification

- `make test` — full suite passes (2243 baseline → still 2243+).
- `make check` — lint + typecheck clean.
- The sidecar diagnostics for a real multi-turn conversation now report a `total_tokens_estimated` that matches what compaction sees (no archived-remap drift). Manual smoke test in the web UI after the change is a nice-to-have but not blocking.

## Open questions

(None remaining at spec time. Spec is ready for `plan`.)

## Related

- #392 — added `conversation_notes` to `role_remap`.
- `src/decafclaw/context_composer.py::compose` — primary edit site.
- `src/decafclaw/context_composer.py::build_diagnostics` (`:1084-1126`) — consumer of `history_entry`.
- `src/decafclaw/agent.py:1050` — caller of `compose()`.
- `src/decafclaw/agent.py:944 _get_already_injected_pages` — sub-consumer of `history` mid-compose; unchanged.
- `docs/context-composer.md` — to update as part of the same PR.
