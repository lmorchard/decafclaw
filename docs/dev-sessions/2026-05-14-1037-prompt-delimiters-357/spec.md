# Prompt Delimiters: Reflection / Memory Sweep / Compaction Spec

**Goal:** Apply explicit XML delimiters to the dynamic inputs of the three remaining LLM prompt surfaces (reflection judge, pre-compaction memory sweep, compaction summarizer) so each segment's role is structurally clear to the model. Mirrors the convention #304 established for the main system prompt.

**Source:** https://github.com/lmorchard/decafclaw/issues/357

## Current state

Three prompt surfaces today concatenate dynamic content with minimal or no delimiters:

- **Reflection judge** (`src/decafclaw/reflection.py:281`): `REFLECTION.md` is loaded as a markdown template (`src/decafclaw/prompts/REFLECTION.md`), filled via `.format()` with five placeholders (`{retrieved_context}`, `{prior_turn_tools}`, `{user_message}`, `{tool_results_summary}`, `{agent_response}`), and sent as a single `user` message — no system prompt, no XML wrappers. The template uses ad-hoc headers like `User: {user_message}` and `Assistant response: {agent_response}` as inline labels.

- **Memory sweep** (`src/decafclaw/compaction.py:32-78`): `MEMORY_SWEEP.md` becomes the system prompt of a child agent; the user message is built as `f"Conversation history to review:\n\n{flattened}"` where `flattened` comes from `flatten_messages()` (`compaction.py:208-233`). No XML wrapping anywhere.

- **Compaction** (`compaction.py:548-573`): Two inline prompt strings (`DEFAULT_COMPACTION_PROMPT`, `INCREMENTAL_COMPACTION_PROMPT`) plus optional `DECISIONS_PROMPT_ADDENDUM` form the system message. User message is assembled in Python with literal section headers (`Existing summary:\n`, `New conversation turns to incorporate:\n`) and an optional `Current state slice (preserve verbatim if still applicable):\n` prefix containing a `<decision_slice>` block (already XML-wrapped via `format_slice()` in `compaction_decisions.py:264`). Otherwise plain text.

The #304 system-prompt pattern (`src/decafclaw/prompts/__init__.py:25-33`) uses `_wrap(tag, body)` to produce `<tag>\n{body}\n</tag>` with empty-body gating (returns `""`), sections joined by `\n\n`. Tags so far: `<soul>`, `<agent_role>`, `<user_context>`, `<skill_catalog>`, `<loaded_skills>`, plus nested `<skill name="…">` and `<deferred_tools>`. Snake_case, lean.

Existing tests in `tests/test_prompts.py`, `tests/test_reflection.py`, `tests/test_compaction.py` assert structural facts (tags present, order, gating) but don't snapshot full prompt text. They mock `call_llm` to avoid live LLM dependencies.

## Desired end state

Each of the three call sites produces an LLM input where every dynamic chunk lives inside a snake_case XML tag whose name conveys its semantic role.

**Reflection** (`REFLECTION.md` template; single user message):
- Static instruction text remains role-less plain markdown (imperative voice is unambiguous on its own — no `<task>` wrapper needed).
- Placeholders are wrapped inside the template itself:
  ```
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
- Python (`reflection.py:281`) keeps using `.format()` with the same placeholder names; the wrapping moves into the markdown. Empty-string placeholders produce empty tags — acceptable here (every section is semantically relevant and the model can read "empty" as "none"). Existing prefix line "Retrieved context (automatically injected before the user's message):" is dropped — the tag name conveys it.

**Memory sweep** (`MEMORY_SWEEP.md` system prompt; user message in `_run_memory_sweep`):
- System prompt unchanged (the prompt is itself the instruction block — owns the system slot).
- User message becomes:
  ```
  <messages_to_compact>
  {flattened}
  </messages_to_compact>
  ```
- Drop the `f"Conversation history to review:\n\n"` prefix — the tag name carries the same meaning.

**Compaction** (system prompt + user message; two modes):
- System prompts (`DEFAULT_COMPACTION_PROMPT`, `INCREMENTAL_COMPACTION_PROMPT`, `DECISIONS_PROMPT_ADDENDUM`) stay inline strings. No wrapping of the static body (system slot conveys role).
- `DECISIONS_PROMPT_ADDENDUM` text update: replace the literal string `"Current state slice"` with `<decision_slice>` (the actual tag name the model sees). One-line wording change inside the addendum.
- **Full mode** user message:
  ```
  <decision_slice>
  ...
  </decision_slice>

  <messages_to_compact>
  {flatten_messages(old_messages)}
  </messages_to_compact>
  ```
  When `slice_prefix_input` is empty, the `<decision_slice>` block is omitted.
- **Incremental mode** user message:
  ```
  <decision_slice>
  ...
  </decision_slice>

  <previous_summary>
  {mode.prev_summary}
  </previous_summary>

  <new_messages>
  {newly_old_flat}
  </new_messages>
  ```
- The "Current state slice (preserve verbatim if still applicable):" prefix line is dropped — `<decision_slice>` is self-describing, and the addendum already instructs the model to reuse entries verbatim.
- Sections joined by `\n\n`.

**Shared helper:** `src/decafclaw/prompts/__init__.py` `_wrap` renamed to `wrap_xml` (public). Internal callers in `prompts/__init__.py` updated. Imported by `compaction.py`. `reflection.py` does not need it (wrapping lives in the template).

**Tests:** new structural assertions for each of the three sites — tag presence, snake_case match, expected order, slice-gated behavior in compaction. Following `test_prompts.py` style: build minimal inputs, call the assembly function, assert substring presence and ordering. No full-text snapshots.

## Design decisions

- **Decision:** Tag dynamic data only; do not wrap static instruction bodies in `<task>` or `<instructions>`.
  - **Why:** The static text is already imperative-voice prose; the model role (system message for sweep/compaction, user message for reflection) provides framing. Adding an outer wrapper buys little, and the issue scope is "wrap dynamic inputs."
  - **Rejected:** Wrapping the reflection static body in `<task>` for symmetry. Not load-bearing — the data tags do the structural work.

- **Decision:** Tag names — `<user_request>`, `<assistant_response>`, `<tool_results>` (plural), `<prior_turn_tools>`, `<retrieved_context>`, `<messages_to_compact>`, `<previous_summary>`, `<new_messages>`, `<decision_slice>` (existing, unchanged).
  - **Why:** Snake_case + descriptive, matching #304 (`<soul>`, `<agent_role>`, `<user_context>`, `<skill_catalog>`). `<tool_results>` plural reflects that it's a summary across multiple tool calls. `<messages_to_compact>` carries semantic intent (these are slated for summarization) and is the issue text's literal suggestion.
  - **Rejected:** `<conversation_history>` (less specific about why it's here), singular `<tool_result>` (inaccurate when summary covers multiple calls).

- **Decision:** Promote `_wrap` → public `wrap_xml` in `src/decafclaw/prompts/__init__.py`.
  - **Why:** Empty-body gating logic is non-trivial enough to share. Single source of truth for the wrap convention. Cross-module use (`compaction.py`) is the third call site (after #304's internal use), so the helper has earned extraction (per the three-callsite rule).
  - **Rejected:** Inlining `f"<{tag}>\n{body}\n</{tag}>"` at each new site — duplicates the empty-gating concern. Moving to `util.py` — `prompts/__init__.py` is already the home of `_PROMPT_FILES` and `load_system_prompt`; adding the helper there keeps prompt-assembly utilities colocated.

- **Decision:** Reflection wrapping lives inside `REFLECTION.md`, not in `reflection.py`.
  - **Why:** `REFLECTION.md` already owns the template structure via `.format()` placeholders. Putting tags in the markdown keeps the template self-contained and lets operators view the full prompt shape by reading one file. The only Python-side change is dropping the "Retrieved context (automatically injected…)" string-prefix logic in `evaluate_response()`.
  - **Rejected:** Wrapping in `evaluate_response()` (Python) — would split the template definition across two files.

- **Decision:** Update `DECISIONS_PROMPT_ADDENDUM` to reference `<decision_slice>` instead of `"Current state slice"`.
  - **Why:** The addendum instructs the LLM to look for a particular section by name. When we replace the literal heading with a tag, the addendum must point at the new name or instructions and rendered input drift apart.
  - **Rejected:** Keeping the legacy heading wording — would create a documentation/reality mismatch (model is told to look for X, sees Y).

- **Decision:** Drop the leading prose lines (`"Conversation history to review:\n\n"`, `"Current state slice (preserve verbatim if still applicable):\n"`, `"Existing summary:\n"`, `"New conversation turns to incorporate:\n"`) when adding the matching XML wrapper.
  - **Why:** The tag name conveys the same semantic, with less token bloat and no risk of the literal heading drifting from the tag name. The "preserve verbatim" instruction relocates to the decisions addendum where it already lives.
  - **Rejected:** Keeping headings *and* tags — token cost without payoff; two synonyms confuse rather than clarify.

- **Decision:** Empty-body sections in compaction (e.g., no decision slice) emit no tag at all (via `wrap_xml`'s empty gating). Reflection's `.format()` style intentionally always emits the tag — empty tags acceptable there because the placeholder names enumerate a fixed, always-relevant set.
  - **Why:** `wrap_xml` already implements gating for compaction. For reflection, the template uses positional `.format()`; making sections conditional would require Python-side assembly and re-tooling that's out of scope. Empty tag = "no data this time."
  - **Rejected:** Wrapping reflection sections in Python so empty ones can be omitted — too much refactoring for the stated benefit.

## Patterns to follow

- **Wrap helper signature and contract:** mirror `_wrap` at `src/decafclaw/prompts/__init__.py:25-33` (empty body → empty string return; produces `<tag>\n{body}\n</tag>`). Rename to `wrap_xml` and remove underscore prefix.
- **Section join:** `"\n\n".join(sections)` per `prompts/__init__.py:115`.
- **Test style:** assertion-based structural tests (tags present, order correct) per `tests/test_prompts.py:13-99`. Don't snapshot full text — too brittle.
- **Skill-name attribute escaping** (`prompts/__init__.py:106`, `html.escape(value, quote=True)`) — not applicable here since no user-provided strings flow into tag names or attributes. All tags are static literals.
- **Test fixtures**: existing `tests/conftest.py` `config` fixture for any test that needs a Config instance.

## What we're NOT doing

- **Not extracting `DEFAULT_COMPACTION_PROMPT` / `INCREMENTAL_COMPACTION_PROMPT` / `DECISIONS_PROMPT_ADDENDUM` to markdown files** for parity with `REFLECTION.md` / `MEMORY_SWEEP.md`. Override path exists (`data/{agent_id}/COMPACTION.md`); a bundled markdown sibling is a reasonable future change but scope-creep for this issue.
- **Not changing `flatten_messages()` output format** to wrap each message in `<message role="user">…</message>` style. The flattened block gets a single outer tag; internal `User: / Assistant: / Tool result: ` prefixes are unchanged. Per-message wrapping is a bigger change with separate trade-offs.
- **Not adding an `<instructions>` or `<task>` outer wrapper** around the static prompt bodies. (See design decision above.)
- **Not modifying the `<decision_slice>` tag itself** or `format_slice()` / `compaction_decisions.py`. Already shaped correctly. (One exception: the trailing `\n` that `format_slice` adds is consumed naturally by `\n\n` section joining — no change needed there.)
- **Not running an end-to-end eval pass** against real reflection/sweep/compaction outputs to measure delta. The issue references #303's eval harness as a dependency; we'll rely on existing unit-test coverage + manual smoke for this change.
- **Not touching workspace overrides** at `data/{agent_id}/REFLECTION.md` / `MEMORY_SWEEP.md` / `COMPACTION.md`. Operators with overrides keep their current files unless they choose to adopt the new pattern. No migration helper.
- **Not adjusting tag-naming or wrap conventions in `<deferred_tools>` or any already-wrapped section.** Out of scope.

## Open questions

None — design decisions resolved during brainstorm.
