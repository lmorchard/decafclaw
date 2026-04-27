# Thread structured decisions through compaction

Tracking issue: #302

## Problem

Compaction today produces one free-form prose summary. That blob is
lossy for high-signal facts — architectural decisions, unresolved
questions, artifacts produced — and the loss compounds across iterated
compactions: the second compaction summarizes a paragraph that already
dropped detail from the first, and the third compounds the loss further.

Anthropic's [Effective Context Engineering for AI Agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
specifically calls this out: preserve decisions, open questions, and
key artifacts through compaction; "maximize recall first, then iterate
for precision."

## Goal

Alongside the prose summary, compaction emits and threads forward a
**structured slice**:

- `decisions` — choices made and still in effect.
- `open_questions` — unresolved threads the agent should remember to
  follow up on.
- `artifacts` — files produced, vault pages written, tools that
  actually shipped output, etc.

The slice is **threaded forward into every subsequent compaction**, so
once an entry lands it's not re-derived from prose each cycle. The
slice renders compactly at the top of context alongside the prose
summary.

## Decisions (autonomous brainstorm)

Six open questions resolved without back-and-forth:

1. **Schema per entry: `{text, created_at}`.** No `category` (the
   list it lives on IS its category). No `confidence` (these are
   extracted as canonical, not probabilities). No `turn_ref`
   (interesting in theory, useless without a stable cross-cycle ID
   for turns). Add fields later if a real use case emerges.

2. **Dedup: exact text match within each list.** No semantic
   similarity, no LLM dedup call. The compaction prompt explicitly
   instructs the model to reuse existing entries verbatim if they
   still apply, so exact match catches almost all duplicates. If
   we observe drift in practice, revisit; for v1 the cheap rule
   wins.

3. **Overflow: per-list FIFO cap of 30 entries.** Soft drop —
   oldest entries fall off when a new entry pushes over the cap.
   Configurable via `compaction.decisions_max_per_category`. The
   prose summary still gets the dropped fact represented in
   summary form, so nothing's truly lost; we just stop carrying
   it as a structured entry.

4. **No edit/retract tool.** v2. The model has no way to retract
   structured entries today; the compaction prompt is asked to
   drop entries that have been obsoleted, but no agent-callable
   API for that. Out of scope for #302.

5. **Storage: per-conversation JSON sidecar.** Path:
   `{workspace}/conversations/{conv_id}.decisions.json`. Same
   pattern as the context sidecar from #298. Survives restarts;
   easy to inspect.

6. **Rendering: prepended block to the prose summary message.**
   The summary message that compaction injects into history is one
   user-role message starting with `[Conversation summary]: …`.
   We prepend the slice as a markdown block before the prose, in
   that same single message. No new role, no new injection point —
   the slice rides the existing summary path.

## Architecture

### Module layout

New module `src/decafclaw/compaction_decisions.py`:

```python
@dataclass(frozen=True)
class DecisionEntry:
    text: str
    created_at: str  # ISO-8601 UTC


@dataclass
class DecisionSlice:
    decisions: list[DecisionEntry] = field(default_factory=list)
    open_questions: list[DecisionEntry] = field(default_factory=list)
    artifacts: list[DecisionEntry] = field(default_factory=list)

    def is_empty(self) -> bool: ...
    def to_dict(self) -> dict: ...
    @classmethod
    def from_dict(cls, raw: dict) -> "DecisionSlice": ...


def load_slice(config, conv_id: str) -> DecisionSlice: ...
def save_slice(config, conv_id: str, slice_: DecisionSlice) -> None: ...

def merge_slice(
    old: DecisionSlice,
    new_lists: dict[str, list[str]],  # raw LLM output: list-of-strings per category
    *,
    max_per_category: int,
    now: str | None = None,
) -> DecisionSlice: ...

def format_slice(slice_: DecisionSlice) -> str: ...
def parse_slice_from_response(text: str) -> dict[str, list[str]] | None: ...
```

### Compaction prompt addendum

Both `DEFAULT_COMPACTION_PROMPT` and `INCREMENTAL_COMPACTION_PROMPT`
get a structured-output section appended:

```
After your prose summary, append a JSON block in this exact shape:

```json
{
  "decisions": ["..."],
  "open_questions": ["..."],
  "artifacts": ["..."]
}
```

Each list contains short strings (≤ 200 chars) covering:
- decisions: choices made and still in effect (architectural,
  product, conventions, preferences locked in)
- open_questions: unresolved questions the agent should remember
- artifacts: concrete things produced (files written, pages
  created, PRs opened, etc.)

If a current-state slice was provided, **reuse existing entries
verbatim** when they still apply. Add new entries for new info.
Drop entries that have been obsoleted (e.g. a decision was
reversed, a question was answered).
```

When a slice already exists for the conversation, the LLM also
receives `Current state slice:\n<formatted>` as part of the input.
The model is instructed to carry over still-applicable entries
verbatim.

### Parse logic

`parse_slice_from_response(text)` tries:

1. Find a fenced ```json block via regex.
2. Parse it as JSON.
3. Validate that it's an object with `decisions`, `open_questions`,
   and `artifacts` keys, each mapping to a list of strings.
4. Return that dict on success; `None` on any failure (silently —
   prose-only fall-through is intentional).

If parse returns None, the prose summary is used unchanged with no
slice update — the existing slice persists, no harm done.

### Merge logic

```python
def merge_slice(old, new_lists, *, max_per_category, now):
    """Merge LLM-parsed lists into the existing slice.

    For each category:
    - Keep existing entries that still appear in `new_lists[cat]`
      (matched by exact text).
    - Drop existing entries that are absent from `new_lists[cat]`
      (the LLM signaled they're obsolete).
    - Add new entries (text not in old slice) with `created_at=now`.
    - Cap at `max_per_category` (FIFO: drop oldest by `created_at`).
    """
```

This means the LLM's output is authoritative for "what's currently
in the slice" — it can both add and remove entries. The merge layer
handles deduping by text and timestamp accounting.

### Wiring into compaction

In `compact_history`:

1. Before summarization, load the existing slice via `load_slice(config, conv_id)`.
2. If the slice is non-empty, prepend `Current state slice:\n<formatted>\n\n` to the prompt input.
3. After getting the summary, call `parse_slice_from_response(summary)`.
4. If parse succeeds, `merge_slice(old, parsed, max_per_category=...)`, then `save_slice`.
5. Strip the JSON block out of the summary text before persisting.
6. Prepend `format_slice(new_slice)` to the cleaned prose for the rebuilt history's summary message.

### Configuration

New `CompactionConfig` fields:

```python
decisions_enabled: bool = True
decisions_max_per_category: int = 30
```

`decisions_enabled: false` short-circuits the entire feature: no
prompt change, no parse, no slice persist. Useful for A/B and
debugging.

## Out of scope

- **Agent-callable retract/edit API** — v2. Out of scope.
- **Semantic dedup.** Exact match for v1.
- **Per-category caps via separate fields** — single global cap is
  simpler. Revisit if categories drift wildly in volume.
- **UI exposure.** The sidecar JSON is available; a context
  inspector tab is a separate frontend touch-up.
- **Multi-conversation rollups** (vault pages built from the slice
  across conversations). Different feature.

## Acceptance criteria

- After compaction, the rebuilt history's summary message starts
  with the formatted slice (when non-empty), then the prose.
- The sidecar JSON file at `{workspace}/conversations/{conv_id}.decisions.json`
  contains the merged slice.
- A second compaction reads the existing slice, threads it into
  the prompt, and merges the LLM's new output without
  re-introducing dropped entries.
- A failed JSON parse falls through silently — the prose summary
  is used; the existing slice persists.
- `compaction.decisions_enabled = false` disables the feature
  entirely (no slice file written, no prompt change).
- Cap enforcement: per-category FIFO when over limit.

## Testing

- **Unit tests** for the new module:
  - `parse_slice_from_response` on valid / partial / malformed
    inputs.
  - `merge_slice` for: new entries appended, existing entries
    preserved verbatim, dropped entries removed, cap enforced
    (FIFO by `created_at`).
  - `load_slice` / `save_slice` round-trip; missing-file fallback.
  - `format_slice` handles empty / partial / full slices.
  - `DecisionSlice.is_empty`.
- **Integration test** at the compaction layer:
  - Faked LLM that returns a prose+JSON response — assert the
    summary message has the slice prepended and the sidecar file
    was written.
  - Re-run compaction with an existing slice and a new LLM
    response — assert the merge happened and the carry-over
    entries kept their original `created_at`.
  - `decisions_enabled=False` — sidecar untouched.
- **No real-LLM CI test.** Manual smoke after merge: hold a
  several-turn conversation that triggers compaction, then run a
  follow-up turn — verify the summary message in
  `workspace/conversations/{conv_id}.compacted.jsonl` has the slice
  block above the prose.

## Files touched

- `src/decafclaw/compaction_decisions.py` (new) — schema, persist,
  merge, parse, format.
- `src/decafclaw/compaction.py` — extend prompts; thread slice
  load/save through `compact_history`; update `_rebuild_history`
  to take an optional formatted-slice prefix.
- `src/decafclaw/config_types.py` — `CompactionConfig` gains
  `decisions_enabled` + `decisions_max_per_category`.
- `tests/test_compaction_decisions.py` (new) — unit tests.
- `tests/test_compaction.py` — extend with the integration cases.
- `docs/context-composer.md` — describe the slice tier alongside
  the existing prose summary.
- `docs/config.md` — `compaction.decisions_*` reference.
- `CLAUDE.md` — context-engineering bullet update.
