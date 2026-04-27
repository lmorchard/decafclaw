# Plan: Threaded structured compaction

See `spec.md` for architecture + decisions.

## Phase 1 — schema + persistence module

- `src/decafclaw/compaction_decisions.py` (new):
  - `@dataclass(frozen=True) DecisionEntry(text, created_at)`.
  - `@dataclass DecisionSlice(decisions, open_questions, artifacts)`
    with `is_empty()`, `to_dict()`, `from_dict()`.
  - `load_slice(config, conv_id) -> DecisionSlice` — read JSON, return
    empty slice on missing/invalid.
  - `save_slice(config, conv_id, slice_)` — atomic write (tmp +
    `os.replace`) under `{workspace}/conversations/{conv_id}.decisions.json`.
  - `format_slice(slice_) -> str` — markdown rendering wrapped in a
    `<decision_slice>` XML envelope (matching the system-prompt
    section convention from #304) with `### Decisions`,
    `### Open Questions`, `### Artifacts` sub-sections inside;
    emits "" if empty.
  - `parse_slice_from_response(text) -> dict | None` — finds a fenced
    ```json block, validates the three-key structure with list-of-string
    values; returns None on any failure.
  - `merge_slice(old, new_lists, *, max_per_category, now) -> DecisionSlice` —
    keep existing entries that still appear in new_lists by exact-text
    match (preserve their `created_at`), add new entries (with `now`
    timestamp), drop entries absent from new_lists; FIFO cap.
- `tests/test_compaction_decisions.py`:
  - Round-trip persistence (save → load → equal); missing file gives
    empty slice.
  - `parse_slice_from_response` valid, malformed JSON, missing keys,
    wrong types, no fenced block — all covered.
  - `merge_slice`: new only, drop on absence, preserve verbatim, cap
    FIFO order, `now` is used only for new entries.
  - `format_slice` for empty / partial / full slices.

## Phase 2 — prompt + parse + persist in compact_history

- `src/decafclaw/compaction.py`:
  - Extend `DEFAULT_COMPACTION_PROMPT` and `INCREMENTAL_COMPACTION_PROMPT`
    with the structured-output addendum (gated on
    `config.compaction.decisions_enabled`).
  - In `compact_history`:
    - Load `old_slice = load_slice(config, conv_id)` if enabled.
    - If non-empty, prepend `Current state slice:\n{format_slice(old_slice)}\n\n`
      to the prompt input.
    - After getting the summary, run `parse_slice_from_response(summary)`.
    - If parse succeeds, merge + save sidecar; strip the JSON block from
      the summary text before passing to `_rebuild_history`.
    - If parse fails or feature disabled, no slice operations.
- `src/decafclaw/config_types.py`: add `decisions_enabled` +
  `decisions_max_per_category` to `CompactionConfig`.
- `tests/test_compaction.py` (extend): integration cases asserting the
  end-to-end behavior with a faked LLM.

## Phase 3 — render the slice with the prose summary

- `compaction._rebuild_history` accepts an optional `slice_prefix: str = ""`
  that gets prepended to the summary text inside the `summary_msg`'s
  `content`. The single user-role summary message carries both.
- `compact_history` builds the prefix via `format_slice(merged_slice)` if
  the merged slice is non-empty.
- One assertion in the integration test that the rebuilt history's first
  message starts with the slice block.

## Phase 4 — docs

- `docs/context-composer.md`: new "Decision slice through compaction"
  subsection alongside the existing compaction discussion.
- `docs/config.md`: `compaction.decisions_*` entries in the existing
  `compaction` table.
- `CLAUDE.md`: context-engineering bullet — mention the slice as the
  third tier alongside the clear pass and full compaction.

## Phase 5 — PR

Squash phase commits, rebase if main moved, push, open PR
(`Closes #302`), request Copilot, move #302 to In review.

## Risk register

- **Prose still contains the JSON block.** If the LLM follows the
  prompt literally, the prose summary will end with a fenced
  ```json block. We strip it before persisting so the prose half
  is clean.
- **LLM ignores the structured-output instruction.** Parse returns
  None, prose-only fallback runs, existing slice persists. Quiet
  failure.
- **LLM hallucinates artifacts that didn't actually happen.** No
  cross-check today; the slice is fuzzy by construction. Document
  this as a known limitation; validation tools can come later.
- **Slice grows beyond cap.** FIFO drop is cheapest; the prose
  summary still represents the dropped facts indirectly. If a
  user notices losses, raise the cap or add per-category tuning.
- **Existing compaction tests use prose-only fakes.** They'll
  break iff the new feature changes the summary-message content
  even when no slice is parsed. We ensure the prose-only path is
  byte-identical to before by stripping nothing and prepending
  nothing when the slice is empty.
