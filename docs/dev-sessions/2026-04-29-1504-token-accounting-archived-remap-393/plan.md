# History/Injection Separation Implementation Plan

**Goal:** Refactor `ContextComposer.compose()` so `history` represents only archived past content during all calculation, eliminating the dual filter that produces #393's archived-remap-role under-counting.

**Approach:** Move history-mutation to the end of `compose()`. Replace both filters (id-based at `:340-344`, role-based at `:413-414`) with a single role-allowlist sum over the input `history`. Build the LLM message list from an explicit `combined = [*history, *wiki_msgs, *notes_msgs, *memory_msgs, user_msg]`.

**Tech stack:** Python (stdlib only — no new deps), pytest, existing `make` targets.

---

## Phase 1: Failing regression test for #393

Add a test that constructs an input `history` containing one archived message of each remap role plus a user/assistant pair, calls `compose()` with no fresh injections, and asserts that `history_entry.tokens_estimated` reflects the archived remap-role content. The test will fail on the current code (role-based filter excludes them) and pass after Phase 2.

**Files:**
- Modify: `tests/test_context_composer.py` — add a new test class `TestHistoryArchivedRemap` (or single test method on `TestCompose`; choose the location adjacent to existing `TestCompose.test_includes_retrieved_context_text` for cohesion).

**Key changes:**

```python
class TestHistoryArchivedRemap:
    """Regression coverage for #393 — archived remap-role messages
    must count toward history_entry.tokens_estimated."""

    @pytest.mark.asyncio
    async def test_archived_remap_messages_counted_in_history_entry(
        self, ctx, config,
    ):
        """An archived vault_retrieval / vault_references / conversation_notes
        message in history (loaded from the archive on a later turn) must
        contribute to history_entry.tokens_estimated."""
        composer = ContextComposer()
        archived_user = {"role": "user", "content": "earlier user message"}
        archived_assistant = {"role": "assistant", "content": "earlier reply"}
        archived_vault_retrieval = {
            "role": "vault_retrieval",
            "content": "AAAA " * 100,  # ~ measurable token weight
        }
        archived_vault_references = {
            "role": "vault_references",
            "content": "BBBB " * 100,
            "wiki_page": "Foo",
        }
        archived_notes = {
            "role": "conversation_notes",
            "content": "CCCC " * 100,
        }
        history = [
            archived_user,
            archived_assistant,
            archived_vault_retrieval,
            archived_vault_references,
            archived_notes,
        ]

        composed = await composer.compose(
            ctx, "current user message", history,
            mode=ComposerMode.INTERACTIVE,
        )

        history_entries = [s for s in composed.sources if s.source == "history"]
        assert len(history_entries) == 1
        history_entry = history_entries[0]

        # Sum of all five archived messages — naive estimate the same way
        # compose() does it, to match the assertion to the implementation.
        expected_min = sum(
            estimate_tokens(str(m.get("content", "")))
            for m in (
                archived_user,
                archived_assistant,
                archived_vault_retrieval,
                archived_vault_references,
                archived_notes,
            )
        )
        assert history_entry.tokens_estimated >= expected_min, (
            f"history_entry.tokens_estimated={history_entry.tokens_estimated} "
            f"expected >= {expected_min} (sum of all five archived messages)"
        )
```

Imports to add at the top of the test file (if not already present): `estimate_tokens` from the same module the production code imports it from (verify with a grep on the existing test file). The test depends on the existing `ctx` and `config` fixtures from `tests/conftest.py`.

**Verification — automated:**
- [x] `make test PYTEST_ARGS='-x tests/test_context_composer.py::TestHistoryArchivedRemap'` runs the new test.
- [x] The new test FAILS on this commit (assertion: `history_entry.tokens_estimated` is too low because the role-based filter at `context_composer.py:413-414` excludes the three archived remap messages). Verified output: `history_entry.tokens_estimated=13 expected >= 383`.
- [x] All other tests in `tests/test_context_composer.py` still pass: `make test PYTEST_ARGS='tests/test_context_composer.py'`. Verified: 1 failed (the new test), 91 passed.

**Verification — manual:**
- [x] Eyeball the test — does the assertion clearly express what should be true after the fix? Does it fail for the right reason on the unmodified code? (Confirmed by Les.)

---

## Phase 2: Refactor `compose()` — defer history-mutation, single token calc

The actual fix. Replace the dual filter with one role-allowlist sum over the input `history` (which now contains only archived past content during calculation). Build LLM message list from an explicit `combined`. Append fresh injections to `history` only at the end. The Phase 1 regression test passes after this commit. Existing tests that pass fresh-this-turn-shaped messages in the input `history` are audited and updated to the new shape.

**Files:**
- Modify: `src/decafclaw/context_composer.py` — see "Key changes" below for specific edits. No new files.
- Modify: `tests/test_context_composer.py` — fixture updates as discovered by running the suite. Likely candidates: `TestCompose.test_message_ordering`, tests in `TestComposeMemoryContext`, tests in `TestRetrievalModes`. Document each adjustment with a one-line code comment if non-obvious.

**Key changes:**

### 2a. Lift `role_remap` to a module-level constant

Currently defined as a local at `:383-387`. Promote it to module-level so it's reachable from the budget-side calculation and from any future caller. Place it just below the `LLM_ROLES` import at `:258` (or near the other module-level constants near the top of the file — pick the location adjacent to `_TASK_MODE_TO_COMPOSER` if it exists; otherwise at the top of the module).

```python
# Roles that are remapped to "user" before sending to the LLM.
# These messages are auto-injected by the composer per-turn, then
# archived under their internal role for accounting and dedupe.
ROLE_REMAP: dict[str, str] = {
    "vault_retrieval": "user",
    "vault_references": "user",
    "conversation_notes": "user",
}
```

Drop the local `role_remap = {...}` in `compose()` and reference `ROLE_REMAP` instead.

### 2b. Stop appending fresh injections to `history` mid-`compose()`

At the wiki injection (`:288-290`):

```python
for wm in wiki_msgs:
    to_archive.append(wm)
    await ctx.publish("vault_references", text=wm["content"], page=wm.get("wiki_page"))
```

Drop the `history.append(wm)` line.

At the notes injection (`:299-301`):

```python
for nm in notes_msgs:
    to_archive.append(nm)
```

Drop the `history.append(nm)` line.

At the memory injection (`:372-374`):

```python
for mm in memory_msgs:
    to_archive.append(mm)
```

Drop the `history.append(mm)` line.

At the user-message append (`:379-380`):

```python
to_archive.append(user_msg)
```

Drop the `history.append(user_msg)` line.

### 2c. Replace the budget-side filter with a single role-allowlist sum

Currently `:338-345`:

```python
injected_ids = {id(wm) for wm in wiki_msgs}
injected_ids |= {id(nm) for nm in notes_msgs}
existing_history_tokens = sum(
    estimate_tokens(str(m.get("content", "")))
    for m in history
    if id(m) not in injected_ids
)
fixed_tokens += existing_history_tokens
```

Replace with:

```python
# History contains only archived prior-turn content at this point —
# fresh wiki / notes / memory / user injections are appended after
# all token accounting, at the end of compose(). Count messages whose
# role is sent to the LLM either directly (LLM_ROLES) or after remap
# (ROLE_REMAP keys). Other roles (e.g. confirmation_request, archive-only
# bookkeeping) are not sent and don't count.
countable_roles = LLM_ROLES | ROLE_REMAP.keys()
history_tokens = sum(
    estimate_tokens(str(m.get("content", "")))
    for m in history
    if m.get("role") in countable_roles
)
fixed_tokens += history_tokens
```

The variable name changes from `existing_history_tokens` to `history_tokens`. Both filters now collapse to this one value — no separate diagnostics-side calc.

### 2d. Replace the diagnostics-side `history_entry` with the same value

Currently `:410-429`:

```python
remapped_roles = set(role_remap.keys())
history_only = [m for m in history if m.get("role") not in remapped_roles]
history_tokens = sum(
    estimate_tokens(str(m.get("content", "")))
    for m in history_only
    if m.get("role") in LLM_ROLES
)
history_msg_count = sum(
    1 for m in history_only if m.get("role") in LLM_ROLES
)
history_entry = SourceEntry(
    source="history",
    tokens_estimated=history_tokens,
    items_included=history_msg_count,
    details={"total_llm_messages": len(llm_history)},
)
sources.append(history_entry)
```

Replace with:

```python
history_msg_count = sum(
    1 for m in history if m.get("role") in countable_roles
)
history_entry = SourceEntry(
    source="history",
    tokens_estimated=history_tokens,  # computed earlier, single source of truth
    items_included=history_msg_count,
    details={"total_llm_messages": len(llm_history)},
)
sources.append(history_entry)
```

### 2e. Build LLM message list from explicit `combined`

Currently `:388-399`:

```python
llm_history = []
for m in history:
    role = m.get("role")
    if role == "background_event":
        llm_history.extend(_expand_background_event(m))
    elif role in LLM_ROLES:
        llm_history.append(m)
    elif role in role_remap:
        llm_history.append({**m, "role": role_remap[role]})
```

Replace with:

```python
# Combine archived history with this turn's injections in the same
# order they'll be archived: prior history → wiki → notes → memory →
# user message. The combined list is what the LLM sees.
combined = [*history, *wiki_msgs, *notes_msgs, *memory_msgs, user_msg]
llm_history = []
for m in combined:
    role = m.get("role")
    if role == "background_event":
        llm_history.extend(_expand_background_event(m))
    elif role in LLM_ROLES:
        llm_history.append(m)
    elif role in ROLE_REMAP:
        llm_history.append({**m, "role": ROLE_REMAP[role]})
```

### 2f. Mutate `history` once, at the end, before returning

After the sources list, llm_history, diagnostics, and `messages` are all built (after `:437` `messages.extend(llm_history)`), append the fresh injections to `history` so the caller's continued use of `self.history` is preserved. Place this immediately before the `return ComposedContext(...)` statement:

```python
# Preserve the load-bearing side effect: caller (agent.py) continues
# to read self.history after compose() returns. Append this turn's
# injections in the same order as `combined` above.
history.extend(wiki_msgs)
history.extend(notes_msgs)
history.extend(memory_msgs)
history.append(user_msg)
```

### 2g. Update the docstring at `:252`

Currently:

> Orchestrates all context sources, mutates history in place (appending …)

Replace with (preserving the rest of the docstring; only the relevant sentence changes):

> Orchestrates all context sources. Token accounting treats `history`
> as read-only (archived prior turns); this turn's wiki / notes / memory
> / user-message injections are appended to `history` once, at the end,
> before returning. The caller's continued reads of `history` after
> compose() returns see the post-turn state.

### 2h. Audit and update existing tests

After the production-code changes are in place, run `make test PYTEST_ARGS='tests/test_context_composer.py'`. For each failure:

1. Read the test to determine if it was relying on `compose()`'s in-progress mutation of `history` (e.g., asserting on `history` membership before `compose()` finishes, or constructing an input `history` that includes objects shaped like fresh-this-turn injections).
2. Adjust the test to either (a) construct injections through the production paths (memory/wiki fixtures) instead of by hand, or (b) accept that injections appear in `history` after `compose()` returns (matching the new contract).
3. Do NOT broaden test changes beyond the minimum needed to express the same intent under the new contract.
4. The Phase 1 regression test must still pass throughout.

Likely candidates (audit before assuming):
- `TestCompose.test_message_ordering` (`tests/test_context_composer.py:836-858`)
- Tests in `TestComposeMemoryContext` (`:151-270`) — these construct memory candidates and may verify mutation.
- Tests in `TestRetrievalModes` (`:275-366`) — same.

**Verification — automated:**
- [x] `make test PYTEST_ARGS='tests/test_context_composer.py::TestHistoryArchivedRemap'` — Phase 1 regression test now PASSES. Verified: 1 passed.
- [x] `make test` — full suite passes (2244 passed, 0 failed; was 2243 + 1 failing in Phase 1).
- [x] `make check` — lint + typecheck clean. Verified: 0 errors / 0 warnings / 0 informations.

**Verification — manual:**
- [ ] Read the diff for `compose()` end-to-end. Does `history` become read-only between the start of the function and the final `history.extend(...)` block?
- [ ] Confirm no other call site or sub-method depends on mid-compose mutation of `history` (re-grep `history.append` / `history.extend` inside `context_composer.py` after edits — only the final block should remain).
- [x] Run the worktree against a real conversation. CLI smoke test (substituted for live web-UI run because the agent's Playwright MCP held the Chrome lock when `make dev` was up). Loaded archived conversation `web-lmorchard-e5a17d78.jsonl` (46 messages, 8 of them auto-injected remap roles totaling 6946 tokens) as `history`, called `compose()`, inspected `history_entry.tokens_estimated`. Pre-fix would report 2623 tokens (LLM_ROLES only). Post-fix reports 9569 — the archived remap-role tokens are now correctly included.

---

## Phase 3: Structural test — no double-counting across `SourceEntry` totals

Add a guard rail that catches future regressions where a fresh injection is double-counted (in `history_entry` AND its own SourceEntry) or dropped (in neither). This protects the architecture against drift if someone later re-introduces a filter or accidentally appends to `history` mid-compose.

**Files:**
- Modify: `tests/test_context_composer.py` — add a new test method to `TestHistoryArchivedRemap` (or a separate `TestSourceEntryNoDoubleCount` class).

**Key changes:**

```python
@pytest.mark.asyncio
async def test_source_entries_account_for_all_llm_content(
    self, ctx, config, monkeypatch,
):
    """The sum of SourceEntry.tokens_estimated across all sources
    should equal the sum of token estimates over llm_history (the
    messages the model actually sees). Catches double-counting and
    silent drops."""
    composer = ContextComposer()
    history = [
        {"role": "user", "content": "prior user " * 50},
        {"role": "assistant", "content": "prior assistant " * 50},
        {"role": "vault_references", "content": "archived ref " * 50, "wiki_page": "Old"},
    ]

    composed = await composer.compose(
        ctx, "fresh user message " * 20, history,
        mode=ComposerMode.INTERACTIVE,
    )

    # Sum of all SourceEntry.tokens_estimated.
    source_total = sum(s.tokens_estimated for s in composed.sources)

    # Sum of token estimates over the actual LLM-bound message list.
    # composed.messages includes the system prompt as the first entry;
    # SourceEntry breakdown also includes "system_prompt" as a source,
    # so this comparison is apples-to-apples.
    llm_total = sum(
        estimate_tokens(str(m.get("content", "")))
        for m in composed.messages
    )

    # Allow small rounding drift from per-message estimate boundaries
    # (estimate_tokens may use ceiling division). Tighten if the gap
    # is larger than a few percent — that signals a real divergence.
    assert abs(source_total - llm_total) <= max(50, llm_total * 0.05), (
        f"SourceEntry sum {source_total} vs llm_history sum {llm_total} "
        f"diverge by more than 5% / 50 tokens — token accounting is drifting."
    )
```

The 5%/50-token tolerance is a guard against `estimate_tokens` ceiling/floor drift across many small messages. If the test reveals a tighter possible bound after Phase 2 lands, narrow it.

**Verification — automated:**
- [x] `make test PYTEST_ARGS='-x tests/test_context_composer.py::TestHistoryArchivedRemap::test_source_entries_account_for_all_llm_content'` passes. (After the Phase 2 fixup that added the `user_message` SourceEntry — see notes.md.)
- [x] `make test` full suite green: 2245 passed (was 2244 before the structural test).
- [x] `make check` clean.

**Verification — manual:**
- [ ] Confirm the 5%/50-token tolerance is the right ballpark — if Phase 2's actual implementation produces a tighter match, narrow the bound. Observed gap: ~12 tokens (self-referential `[Context: ...]` status line, by design). Floor of 50 absorbs it cleanly. Tightening the bound would couple the guard rail to the exact size of the status string — fragile. Recommend leaving as planned.

---

## Phase 4: Update `docs/context-composer.md`

Per CLAUDE.md: "When changing a feature: update its `docs/` page **as part of the same PR**." The relevant section is `### Token budget` (line 84) and `### compose()` (line 97). Add a one-paragraph note clarifying the post-#393 contract: `history` is read-only during calculation, archived auto-injected role messages are counted in `history_entry`, fresh-this-turn injections are counted only in their per-source `SourceEntry`.

**Files:**
- Modify: `docs/context-composer.md` — extend the "Token budget" section with a "History accounting" sub-section. Suggested placement: after the existing bullets at `:84-89`.

**Key changes:**

Add a new sub-section after the existing "Token budget" bullets:

```markdown
#### History accounting

`compose()` treats the input `history` list as read-only during all token-budget and diagnostics calculation. Fresh-this-turn injections (`vault_references`, `conversation_notes`, `vault_retrieval`, the user message) are accounted for via their own per-source `SourceEntry` — `wiki_entry`, `notes_entry`, `memory_entry`. The `history` source entry counts everything archived from prior turns, including messages whose role is in `ROLE_REMAP` (auto-injected role messages archived from earlier turns). After all token accounting and message-list assembly, `compose()` appends this turn's injections to `history` so the caller (agent loop) continues to see the post-turn state on subsequent turns.

This contract closes a class of underreporting bugs (#393) where the diagnostics-side filter excluded archived auto-injected messages by role, even though the LLM still sees them after remap.
```

**Verification — automated:**
- [x] `make lint` clean.
- [x] No broken cross-links — implementer audited the rest of `docs/context-composer.md` for passages that contradict the new sub-section; none found. The "compose() steps" list at lines 99-109 describes step semantics without implying mid-compose mutation.

**Verification — manual:**
- [ ] Read the new sub-section in context. Does it explain enough to a future reader why `history` becoming read-only is the load-bearing property?
- [x] Skim the full `docs/context-composer.md` page for any other passages that imply mid-compose history mutation; if found, reconcile. (Implementer audited; nothing else needs editing.)

---

## Plan self-review

**Spec coverage:**
- Spec Decision 1 (defer history-append to end) → Phase 2, items 2b + 2f.
- Spec Decision 2 (one history-token calc, no filters) → Phase 2, items 2c + 2d.
- Spec Decision 3 (explicit `combined` assembly) → Phase 2, item 2e.
- Spec Decision 4 (`_get_already_injected_pages` unchanged) → no phase needed (no code change).
- Spec Decision 5 (`compose()` still mutates at end, docstring updated) → Phase 2, items 2f + 2g.
- Spec test plan: regression test → Phase 1; structural test → Phase 3; existing-test audit → Phase 2 item 2h.
- Spec verification (`make test`, `make check`) → covered in each phase's automated checks.
- Spec "What we're NOT doing" → no phase touches the listed exclusions (no schema bump, no role-remap removal, no formula change, etc.).

**Placeholder scan:** No "TBD", "TODO", or "implement later" markers. Each phase has concrete file paths, code snippets, and verification commands. The two flexible spots — the precise location of `ROLE_REMAP` (Phase 2a) and which existing tests need fixture updates (Phase 2h) — are explicitly bounded and require runtime evidence to resolve, which is appropriate for `execute`.

**Type/name consistency:**
- `ROLE_REMAP` (module-level constant) is the only name introduced. It replaces the local `role_remap`. Both phases that reference it (2c, 2d, 2e) use the same name.
- `countable_roles = LLM_ROLES | ROLE_REMAP.keys()` is reused as the role allowlist in 2c and 2d. Spelled consistently.
- `combined` is the name of the assembled message list in 2e; consumed by the role-remap loop.
- `history_tokens` (Phase 2c, single source of truth) is the name used in 2c, 2d. The previous `existing_history_tokens` is removed; nothing else in the codebase references it (verify with `grep existing_history_tokens` during execute).
- `wiki_msgs`, `notes_msgs`, `memory_msgs`, `user_msg` are existing local names in `compose()` — no new names introduced for these.

**Scope discipline check:** No drive-by refactors. The plan touches `compose()` mechanics, two test files, and one docs page. No adjacent code (other composer methods, agent loop, archive layer) is modified.

Plan is ready for `execute`.
