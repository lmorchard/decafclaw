# Prompt Delimiters Implementation Plan

**Goal:** Wrap dynamic inputs of the reflection, memory sweep, and compaction LLM surfaces in snake_case XML tags, matching #304's pattern.

**Approach:** Promote the existing `_wrap` helper to public `wrap_xml`, then apply it (or template-literal XML for the reflection markdown file) at each call site. Extract small input-builder helpers so the assembled prompts are unit-testable without mocking `call_llm`. Update the decisions addendum so its instructions reference the new `<decision_slice>` tag.

**Tech stack:** Python stdlib only (no new deps); pytest for tests.

---

## Phase 1: Promote `_wrap` → public `wrap_xml`

Refactor the existing helper in `src/decafclaw/prompts/__init__.py` to be a public, importable utility. No behavior change. Establishes the shared building block Phase 2 and Phase 3 rely on.

**Files:**
- Modify: `src/decafclaw/prompts/__init__.py` — rename `_wrap` → `wrap_xml`; update 3 internal callers (lines 68, 76, 84). Keep the docstring (drop the "Callers rely on…" sentence's leading underscore reference).
- Modify: `tests/test_prompts.py` — add a small `TestWrapXml` class with the three contract cases below; do not delete existing tests (they exercise `wrap_xml` indirectly through `load_system_prompt`).

**Key changes:**

```python
# src/decafclaw/prompts/__init__.py
def wrap_xml(tag: str, body: str) -> str:
    """Wrap body in <tag>\n…\n</tag>; return "" if body is empty.

    Callers rely on the empty-case returning "" so they can skip the
    section entirely — no dangling `<tag></tag>` wrappers.
    """
    if not body:
        return ""
    return f"<{tag}>\n{body}\n</{tag}>"
```

Three internal call sites in `load_system_prompt` (current lines 68, 76, 84) become `wrap_xml(...)`. The literal `f'<skill name="{safe_name}">\n{skill.body}\n</skill>'` at line 107 and the manual outer `<loaded_skills>` wrapper at lines 110-112 stay as-is — those use an attribute (`name="…"`) which the helper does not support, so inlining is appropriate. Out of scope to refactor further.

**Test additions** (`tests/test_prompts.py`, new class):

```python
from decafclaw.prompts import wrap_xml


class TestWrapXml:
    def test_wraps_body_in_tag(self):
        assert wrap_xml("foo", "bar") == "<foo>\nbar\n</foo>"

    def test_empty_body_returns_empty_string(self):
        assert wrap_xml("foo", "") == ""

    def test_preserves_internal_newlines(self):
        assert wrap_xml("foo", "line1\nline2") == "<foo>\nline1\nline2\n</foo>"
```

**Verification — automated:**
- [x] `make lint` passes
- [x] `make test` passes (full suite — refactor must not break #304's existing tag tests)
- [x] `make check` passes
- [x] `uv run pytest tests/test_prompts.py -v` — confirm `TestWrapXml` passes and existing system-prompt tests still pass

**Verification — manual:**
- [x] `grep -n "_wrap\b" src/` returns no remaining references (rename is complete; vendor JS bundle matches are unrelated)
- [x] `grep -n "wrap_xml" src/decafclaw/prompts/__init__.py` shows 4 occurrences (1 def + 3 callers)

---

## Phase 2: Compaction — extract input builder + wrap dynamic data

Wrap the compaction user-message dynamic data in `<messages_to_compact>` (full) / `<previous_summary>` + `<new_messages>` (incremental). Extract the assembly to a helper so it's directly testable. Update `DECISIONS_PROMPT_ADDENDUM` so its instructions reference the new tag name. Update one existing test that asserts on now-removed string literals.

**Files:**
- Modify: `src/decafclaw/compaction.py`
  - Update `DECISIONS_PROMPT_ADDENDUM` (lines 106-133): change `"Current state slice"` (line 126) → `` `<decision_slice>` `` (backticks for inline-code emphasis in the prompt).
  - Add: `_build_compaction_user_input(mode, old_slice_block: str) -> str` near the other helpers (after `_with_decisions_addendum` at line 151).
  - Modify `compact_history` (lines 532-573): replace inline assembly with `_build_compaction_user_input`. The `slice_prefix_input` literal-string prefix (lines 537-542) becomes a bare `format_slice(old_slice)` block; the prose prefix is dropped.
- Modify: `tests/test_compaction.py`
  - Update `test_incremental_compaction_includes_existing_summary` (around line 239-241): replace `assert "Existing summary:" in user_content` and `assert "New conversation turns to incorporate:" in user_content` with `assert "<previous_summary>" in user_content` and `assert "<new_messages>" in user_content`. Keep `"Summary of turns 0-4." in user_content` (the summary text is preserved inside the new tag).
  - Add: new `TestBuildCompactionUserInput` class exercising the helper directly for both modes and the slice-gated branches.
  - Add: standalone `test_decisions_addendum_references_decision_slice_tag` function.

**Key changes:**

```python
# src/decafclaw/compaction.py — new import near top
from .prompts import wrap_xml


# After _with_decisions_addendum (current line ~151):
def _build_compaction_user_input(mode, old_slice_block: str = "") -> str:
    """Assemble the user-message text fed to the compaction LLM.

    Sections, in order, joined by "\n\n":
      - <decision_slice>...</decision_slice>          (optional; pre-wrapped via format_slice)
      - Incremental mode:
          <previous_summary>{prev_summary}</previous_summary>
          <new_messages>{newly_old_flat}</new_messages>
      - Full mode:
          <messages_to_compact>{flattened}</messages_to_compact>

    ``old_slice_block`` is either the output of ``format_slice(slice_)``
    (a full XML block, possibly with a trailing newline) or "" when no
    slice is active.
    """
    sections: list[str] = []
    slice_section = old_slice_block.strip()
    if slice_section:
        sections.append(slice_section)

    if mode.incremental:
        newly_old_flat = flatten_messages(
            [msg for turn in mode.newly_old_turns for msg in turn]
        )
        sections.append(wrap_xml("previous_summary", mode.prev_summary))
        sections.append(wrap_xml("new_messages", newly_old_flat))
    else:
        flattened = flatten_messages(mode.old_messages)
        sections.append(wrap_xml("messages_to_compact", flattened))

    return "\n\n".join(s for s in sections if s)
```

Then replace the inline assembly in `compact_history`:

```python
# Replace lines 537-542 (slice_prefix_input construction) with:
old_slice_block = (
    format_slice(old_slice)
    if old_slice and not old_slice.is_empty()
    else ""
)

# Replace lines 548-573 (incremental + full branches) with:
if mode.incremental:
    combined_input = _build_compaction_user_input(mode, old_slice_block)
    estimated = estimate_tokens(combined_input)
    log.info(f"Incremental summarization: ~{estimated} est. tokens")
    summary = await _single_summarize(
        ctx, config, combined_input,
        _with_decisions_addendum(INCREMENTAL_COMPACTION_PROMPT, config))
else:
    prompt = _with_decisions_addendum(_load_compaction_prompt(config), config)
    flattened_input = _build_compaction_user_input(mode, old_slice_block)
    estimated = estimate_tokens(flattened_input)
    if estimated > budget:
        log.info(f"Flattened text ({estimated} est. tokens) exceeds "
                 f"budget ({budget}), using chunked compaction")
        # Note: chunked path flattens per-chunk and does not wrap in
        # <messages_to_compact> — it's a rare fallback for oversized
        # inputs. The <decision_slice> guidance still lives in the
        # system prompt via the addendum.
        summary = await _chunked_summarize(
            ctx, config, mode.old_turns, prompt, budget)
    else:
        summary = await _single_summarize(ctx, config, flattened_input, prompt)
```

```python
# src/decafclaw/compaction.py — DECISIONS_PROMPT_ADDENDUM update at line ~126
# Old:
#   If a "Current state slice" is provided in the input, **reuse existing
# New:
#   If a `<decision_slice>` block is provided in the input, **reuse existing
```

**Test additions** (`tests/test_compaction.py`):

```python
from decafclaw.compaction import (
    DECISIONS_PROMPT_ADDENDUM,
    _build_compaction_user_input,
)


class TestBuildCompactionUserInput:
    def _mock_mode(self, incremental: bool, **kwargs):
        from types import SimpleNamespace
        return SimpleNamespace(incremental=incremental, **kwargs)

    def test_full_mode_wraps_in_messages_to_compact(self):
        mode = self._mock_mode(
            incremental=False,
            old_messages=[
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hello"},
            ],
        )
        out = _build_compaction_user_input(mode, old_slice_block="")
        assert "<messages_to_compact>" in out
        assert "</messages_to_compact>" in out
        assert "User: hi" in out
        assert "Assistant: hello" in out

    def test_incremental_mode_wraps_previous_summary_and_new_messages(self):
        mode = self._mock_mode(
            incremental=True,
            prev_summary="prior summary text",
            newly_old_turns=[
                [{"role": "user", "content": "next message"}],
            ],
        )
        out = _build_compaction_user_input(mode, old_slice_block="")
        assert "<previous_summary>\nprior summary text\n</previous_summary>" in out
        assert "<new_messages>" in out
        assert "User: next message" in out

    def test_decision_slice_block_prepended_when_provided(self):
        mode = self._mock_mode(
            incremental=False,
            old_messages=[{"role": "user", "content": "hi"}],
        )
        slice_block = "<decision_slice>\nDecisions:\n- foo\n</decision_slice>"
        out = _build_compaction_user_input(mode, old_slice_block=slice_block)
        assert out.startswith("<decision_slice>")
        assert "<messages_to_compact>" in out
        assert "</decision_slice>\n\n<messages_to_compact>" in out

    def test_empty_slice_block_omits_section(self):
        mode = self._mock_mode(
            incremental=False,
            old_messages=[{"role": "user", "content": "hi"}],
        )
        out = _build_compaction_user_input(mode, old_slice_block="")
        assert "<decision_slice>" not in out
        assert out.startswith("<messages_to_compact>")


def test_decisions_addendum_references_decision_slice_tag():
    """The addendum must instruct the LLM about the actual tag name it sees."""
    assert "<decision_slice>" in DECISIONS_PROMPT_ADDENDUM
    assert "Current state slice" not in DECISIONS_PROMPT_ADDENDUM
```

**Verification — automated:**
- [x] `make lint` passes
- [x] `make test` passes (entire suite — confirms updated `test_incremental_compaction_includes_existing_summary` passes against new tag names)
- [x] `make check` passes
- [x] `uv run pytest tests/test_compaction.py -v` — confirm new `TestBuildCompactionUserInput` + addendum test + updated incremental test all pass

**Verification — manual:**
- [x] `grep -n "Existing summary:\|New conversation turns to incorporate\|Current state slice" src/decafclaw/compaction.py` returns no matches
- [x] Read the chunked-compaction comment to confirm the no-slice-on-chunked behavior is documented

---

## Phase 3: Memory sweep — wrap flattened conversation in `<messages_to_compact>`

Wrap the sweep child-agent's user message body in `<messages_to_compact>`. Drop the `"Conversation history to review:\n\n"` prefix. Extract a tiny helper for testability.

**Files:**
- Modify: `src/decafclaw/compaction.py`
  - Add: `_build_sweep_user_input(flattened: str) -> str` near `_run_memory_sweep` (around line 30). One-line body using `wrap_xml`.
  - Modify `_run_memory_sweep` (line 51): replace `task_prompt = f"Conversation history to review:\n\n{flattened}"` with `task_prompt = _build_sweep_user_input(flattened)`.
- Modify: `tests/test_compaction.py`
  - Add: `TestBuildSweepUserInput` class with two tests (tag presence, legacy prefix gone).

**Key changes:**

```python
# src/decafclaw/compaction.py — near _load_sweep_prompt (~line 30):
def _build_sweep_user_input(flattened: str) -> str:
    """Wrap flattened messages in <messages_to_compact> for the memory sweep child agent."""
    return wrap_xml("messages_to_compact", flattened)
```

Then in `_run_memory_sweep`:

```python
# Replace line 51:
task_prompt = _build_sweep_user_input(flattened)
```

Note: `wrap_xml` returns `""` for an empty body, but the sweep is only invoked when `mode.old_messages` is non-empty (guard at `compact_history:521-523`), so empty-flatten is not a real edge case. No defensive code needed.

**Test additions** (`tests/test_compaction.py`):

```python
from decafclaw.compaction import _build_sweep_user_input


class TestBuildSweepUserInput:
    def test_wraps_flattened_in_messages_to_compact(self):
        out = _build_sweep_user_input("User: hi\nAssistant: hello")
        assert out == "<messages_to_compact>\nUser: hi\nAssistant: hello\n</messages_to_compact>"

    def test_no_legacy_prefix_present(self):
        out = _build_sweep_user_input("any text")
        assert "Conversation history to review:" not in out
```

**Verification — automated:**
- [x] `make lint` passes
- [x] `make test` passes
- [x] `make check` passes
- [x] `uv run pytest tests/test_compaction.py::TestBuildSweepUserInput -v` — confirm new tests pass

**Verification — manual:**
- [x] `grep -n "Conversation history to review" src/` returns no matches

---

## Phase 4: Reflection — wrap template placeholders in `REFLECTION.md`; drop prose prefix in `evaluate_response`

Edit `REFLECTION.md` so each `.format()` placeholder lives inside its own XML tag. Remove the Python-side "Retrieved context (automatically injected…)" prefix (the tag name now carries that meaning). Add a test that fills the template with sample values and asserts the tags appear in the expected order.

**Files:**
- Modify: `src/decafclaw/prompts/REFLECTION.md` — replace lines 33-43 (the `---` divider + placeholder block) with the tagged structure below.
- Modify: `src/decafclaw/reflection.py` — in `evaluate_response` (lines 274-280), simplify `context_block` to just `retrieved_context` (no prefix line).
- Modify: `tests/test_reflection.py` — add a `TestReflectionPromptStructure` class that loads `_BUNDLED_PROMPT`, fills it with sample values, and asserts every tag appears.

**Key changes:**

`src/decafclaw/prompts/REFLECTION.md`, replace lines 33-43 with:

```markdown
---

<retrieved_context>
{retrieved_context}
</retrieved_context>

<prior_turn_tools>
{prior_turn_tools}
</prior_turn_tools>

<user_request>
{user_message}
</user_request>

<tool_results>
{tool_results_summary}
</tool_results>

<assistant_response>
{agent_response}
</assistant_response>
```

`src/decafclaw/reflection.py`, replace lines 274-280:

```python
context_block = retrieved_context  # tag in REFLECTION.md conveys "auto-injected" semantic
```

The full `.format()` call (lines 281-287) is otherwise unchanged.

**Test additions** (`tests/test_reflection.py`):

```python
from decafclaw.reflection import _BUNDLED_PROMPT


class TestReflectionPromptStructure:
    """Assert the bundled REFLECTION.md wraps each dynamic input in its tag."""

    def _filled(self) -> str:
        return _BUNDLED_PROMPT.read_text().format(
            user_message="what is 2+2?",
            agent_response="4",
            tool_results_summary="(no tools used)",
            prior_turn_tools="(none)",
            retrieved_context="page X is relevant",
        )

    def test_all_expected_tags_present(self):
        out = self._filled()
        for tag in (
            "<user_request>", "</user_request>",
            "<assistant_response>", "</assistant_response>",
            "<tool_results>", "</tool_results>",
            "<prior_turn_tools>", "</prior_turn_tools>",
            "<retrieved_context>", "</retrieved_context>",
        ):
            assert tag in out, f"missing {tag}"

    def test_placeholder_values_inside_tags(self):
        out = self._filled()
        assert out.index("<user_request>") < out.index("what is 2+2?") < out.index("</user_request>")
        assert out.index("<assistant_response>") < out.index("4") < out.index("</assistant_response>")
        assert out.index("<retrieved_context>") < out.index("page X is relevant") < out.index("</retrieved_context>")

    def test_legacy_prefix_removed(self):
        text = _BUNDLED_PROMPT.read_text()
        assert "Retrieved context (automatically injected" not in text
        assert "User: {user_message}" not in text
        assert "Assistant response: {agent_response}" not in text
```

**Verification — automated:**
- [x] `make lint` passes
- [x] `make test` passes
- [x] `make check` passes
- [x] `uv run pytest tests/test_reflection.py -v` — confirm new structure tests pass alongside existing reflection tests

**Verification — manual:**
- [x] Read `src/decafclaw/prompts/REFLECTION.md` end-to-end — confirm static instruction body is unchanged, all five tags appear, no stray legacy headers (`User:`, `Assistant response:`)
- [x] `grep -n "Retrieved context (automatically" src/` returns no matches

---

## Phase 5: Update docs

Update `docs/context-composer.md` with a brief note that the delimiter convention now applies to reflection / memory sweep / compaction dynamic inputs.

**Files:**
- Modify: `docs/context-composer.md` — add a short subsection (or extend the existing #304 section) listing the additional tags and noting `wrap_xml` is the shared helper.

**Key changes:** documentation only; no code changes. TDD opt-out: docs phase.

**Verification — automated:**
- [x] `make lint` passes
- [x] `make test` passes (no test changes — sanity check that docs edits don't break anything)
- [x] `make check` passes

**Verification — manual:**
- [x] Read the updated `docs/context-composer.md` section — confirms the new tags are listed (`<user_request>`, `<assistant_response>`, `<tool_results>`, `<prior_turn_tools>`, `<retrieved_context>`, `<messages_to_compact>`, `<previous_summary>`, `<new_messages>`) and the `wrap_xml` rename is mentioned.
