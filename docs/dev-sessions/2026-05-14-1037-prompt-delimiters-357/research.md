# Prompt Delimiter Systems Research

## 1. REFLECTION.md Call Site

**Load point:** `src/decafclaw/reflection.py:24` — bundled default and `src/decafclaw/reflection.py:43-49` (override path check)

**Function:** `load_reflection_prompt(config) -> str` (lines 36-49)
- Priority: `data/{agent_id}/REFLECTION.md` (override) → bundled `src/decafclaw/prompts/REFLECTION.md`
- Returns raw markdown stripped of leading/trailing whitespace

**Template usage:** `src/decafclaw/reflection.py:260-287` in `evaluate_response()`
- Line 281: template is used with `.format()` to inject placeholders:
  - `{user_message}` — current user input
  - `{agent_response}` — the agent's text response
  - `{tool_results_summary}` — built via `build_tool_summary()` (lines 156-165) or "(no tools used)" stub
  - `{prior_turn_tools}` — built via `build_prior_turn_summary()` (lines 168-205)
  - `{retrieved_context}` — formatted vault retrieval block (line 274-280), wrapped as "Retrieved context (automatically injected...)\n" when present, empty string when missing

**Dynamic data concatenation (exact order in prompt):**
```python
prompt = prompt_template.format(
    user_message=user_message,                    # direct param
    tool_results_summary=tool_summary or "(no tools used)",  # fallback stub if empty
    agent_response=agent_response,                # direct param
    retrieved_context=context_block,              # see above
    prior_turn_tools=prior_turn_summary,          # built separately
)
```

**LLM call:** Line 289 — single-turn invocation with no system prompt:
```python
messages = [{"role": "user", "content": prompt}]
```
The filled template becomes the entire user message. No wrapping, no XML delimiters in the current system.

**Separators:** Template placeholders are inline, no explicit join/separator — the template markdown itself controls flow (lines 35-43 in REFLECTION.md show headers and blank lines).

---

## 2. MEMORY_SWEEP.md Call Site

**Load point:** `src/decafclaw/compaction.py:21` — bundled path; `src/decafclaw/compaction.py:24-29` loads with override support

**Function:** `_load_sweep_prompt(config) -> str` (lines 24-29)
- Override: `config.agent_path / "MEMORY_SWEEP.md"` (check line 26)
- Default: `Path(__file__).parent / "prompts" / "MEMORY_SWEEP.md"`
- Returns raw text via `.read_text()`

**Integration:** `_run_memory_sweep(ctx, old_messages: list[dict]) -> None` (lines 32-82)
- Called asynchronously in background during compaction (line 523 in `compact_history()`)
- Line 49: `sweep_prompt = _load_sweep_prompt(config)`
- Line 50-51: conversation messages are flattened to text via `flatten_messages()` (lines 208-233)
- Line 51: task prompt built as:
  ```python
  task_prompt = f"Conversation history to review:\n\n{flattened}"
  ```
  — simple `\n\n` join between header and flattened text

**LLM call:** Line 78
```python
result = await run_agent_turn(child_ctx, task_prompt, [])
```
- `task_prompt` becomes the user message in a child agent turn
- `system_prompt=sweep_prompt` (line 59) is set on a forked context
- No parent system prompt; sweep_prompt is the complete system message
- Vault tools only (lines 73-76), no standard tools

**Separators:** `\n\n` between "Conversation history to review:" header and flattened message list. Flattening itself uses `\n` (line 233).

---

## 3. Compaction Prompt Construction

**Location:** `src/decafclaw/compaction.py` — inline string definitions and assembly

**Default prompt (no decisions):** Lines 83-92
```python
DEFAULT_COMPACTION_PROMPT = """\
Summarize the following conversation, preserving:
- Key facts and decisions made
- User preferences and corrections
- Important tool results and findings
- Approaches that were tried but didn't work, and why — this prevents re-exploration
- The current topic and any open questions

Be concise but don't lose critical details. Err on the side of including information
that would prevent duplicate work or repeated mistakes. Format as a brief narrative."""
```
Inline plain-text string, no XML wrapping at definition.

**Incremental variant:** Lines 94-100
```python
INCREMENTAL_COMPACTION_PROMPT = """\
You have an existing conversation summary and new turns that need to be incorporated.
Update the summary to include the new information while preserving all important details
from the original summary. ...
```

**Decisions addendum (when enabled):** Lines 106-133
```python
DECISIONS_PROMPT_ADDENDUM = """\

After your prose summary, append a JSON block in this exact shape:

```json
{
  "decisions": ["..."],
  "open_questions": ["..."],
  "artifacts": ["..."]
}
```
[instructions for reusing existing entries verbatim, adding new, dropping obsolete]
...
```

**Assembly pipeline:**

1. Line 138-143: `_load_compaction_prompt(config)` — loads custom override from `config.agent_path / "COMPACTION.md"` or returns `DEFAULT_COMPACTION_PROMPT`

2. Line 146-151: `_with_decisions_addendum(prompt: str, config)` — appends addendum if `config.compaction.decisions_enabled` is true (direct string concatenation with no separator — the addendum starts with `\n\n`)

3. **Full compaction flow** (lines 561-573):
   ```python
   prompt = _with_decisions_addendum(_load_compaction_prompt(config), config)
   flattened = (slice_prefix_input + flatten_messages(mode.old_messages)
                if slice_prefix_input
                else flatten_messages(mode.old_messages))
   ```
   - `slice_prefix_input` (lines 537-542) is `"Current state slice (preserve verbatim...)\n{format_slice(old_slice)}\n\n"` when non-empty, empty string otherwise
   - `flatten_messages()` produces text with `\n` joins (line 233)
   - Order: prompt + optional slice prefix + flattened messages

4. **Incremental flow** (lines 548-560):
   ```python
   newly_old_flat = flatten_messages([msg for turn in mode.newly_old_turns for msg in turn])
   combined_input = (
       f"{slice_prefix_input}"
       f"Existing summary:\n{mode.prev_summary}\n\n"
       f"New conversation turns to incorporate:\n{newly_old_flat}"
   )
   summary = await _single_summarize(ctx, config, combined_input, 
       _with_decisions_addendum(INCREMENTAL_COMPACTION_PROMPT, config))
   ```
   Order: slice_prefix + "Existing summary:\n" + summary_text + "\n\n" + "New conversation turns to incorporate:\n" + flat_text

5. **LLM call:** Lines 238-254 in `_single_summarize()`
   ```python
   summary_messages = [
       {"role": "system", "content": prompt},
       {"role": "user", "content": flattened_text},
   ]
   ```
   — Two-message structure: system (the prompt template) + user (the input text)

**Exact separators:**
- Addendum: starts with `\n\n`, prepended to prompt string
- Slice prefix: `"Current state slice...\n{slice}\n\n"` (line 540-541)
- Section headers in combined_input: literal strings like `"Existing summary:\n"`, `"New conversation turns to incorporate:\n"`
- Message flatten: `\n` joins (line 233)
- Messages flattened: "User: ...", "Assistant: ...", "Tool result: ..." prefixes, joined by newline (line 233)

---

## 4. System Prompt XML Delimiter Pattern (#304)

**Assembly location:** `src/decafclaw/prompts/__init__.py` — function `load_system_prompt(config)` (lines 36-115)

**Wrapping function:** `_wrap(tag: str, body: str) -> str` (lines 25-33)
```python
def _wrap(tag: str, body: str) -> str:
    """Wrap body in <tag>\n…\n</tag>; return "" if body is empty."""
    if not body:
        return ""
    return f"<{tag}>\n{body}\n</{tag}>"
```
Pattern: `<tag>\n{body}\n</tag>` with literal newlines, empty string for empty body (gating applies).

**Exact tags and order** (from `_PROMPT_FILES` and subsequent appends, lines 19-115):

| Order | Tag | Source | Condition |
|-------|-----|--------|-----------|
| 1 | `<soul>` | `SOUL.md` (bundled or override) | Always (paired line 20) |
| 2 | `<agent_role>` | `AGENT.md` (bundled or override) | Always (paired line 21) |
| 3 | `<user_context>` | `USER.md` (workspace only) | Only when file exists + non-empty (lines 73-78) |
| 4 | `<skill_catalog>` | `build_catalog_text(skills)` output | Only when non-empty (lines 82-86) |
| 5 | `<loaded_skills>` | Always-loaded bundled skill bodies | Only when at least one present (lines 110-113) |
| - | `<deferred_tools>` | Separate system message (not system prompt) | See `tool_registry.build_deferred_list_text()` |

**Assembly mechanics** (lines 54-115):
1. Iterate `_PROMPT_FILES` pairs (SOUL→soul, AGENT→agent_role); load, wrap, append (lines 56-70)
2. Check USER.md, wrap if present and non-empty (lines 73-78)
3. Call `discover_skills()`, build catalog, wrap if non-empty (lines 82-86)
4. Collect always-loaded skill bodies, wrap each as `<skill name="{safe_name}">\n{body}\n</skill>` inside `<loaded_skills>` wrapper (lines 92-113)
5. Join all sections with `\n\n` (line 115)

**Skill name escaping:** Line 106 — `html.escape(skill.name, quote=True)` for XML attribute safety (defends against quotes, `<`, `>`, `&` in skill names).

**Empty section gating:** `_wrap()` returns `""` for empty body, so dangling empty wrappers never appear (lines 25-33).

**Inner content:** Plain markdown preserved as-is within tags; no escaping or rewriting (lines 79-89 of test_prompts.py confirm this).

**Deferred tools variant:** Built separately in `build_deferred_list_text()` (tool_registry.py, referenced by docs/context-composer.md:34). Sent as a second system message if deferred pool exists (context_composer.py:443-445):
```python
if deferred_text:
    messages.append({"role": "system", "content": deferred_text})
```

---

## 5. Tests for Prompt Assembly

**System prompt tests:** `tests/test_prompts.py` (all lines 1-200)

- **TestDefaultLoad** (lines 13-36): Validates bundled load with expected tags in order (soul → agent_role → skill_catalog → loaded_skills). Confirms tags are closed properly.
- **TestUserContext** (lines 42-72): USER.md wrapping, positioning between agent_role and skill_catalog, empty-file gating.
- **TestInnerContent** (lines 78-99): Content inside tags preserved intact, markdown not transformed.
- **TestSkillSections** (lines 105-165): Catalog/loaded_skills gating on discovery results, per-skill `<skill name="…">` blocks, XML attribute escaping for skill names containing special characters, trust-boundary checks (non-bundled skills excluded from bodies).

**Reflection tests:** `tests/test_reflection.py` (lines 1-120 shown, extends further)

- **TestBuildToolSummary** (lines 24-48): Tool call/result extraction and formatting. No prompt-assembly tests.
- **TestBuildToolSummary.test_with_tools()** (lines 32-47): Verifies tool names and arguments appear in summary.
- **TestBuildToolSummary.test_includes_widget_response_user_messages()** (lines 49-71): Widget synthetic messages included.
- Widget response turn-boundary handling verified (lines 73-102).
- Tool result truncation tested (lines 104-118).

No direct tests of reflection prompt assembly or template substitution (template filling happens in `evaluate_response()`, not tested in isolation).

**Compaction tests:** `tests/test_compaction.py` (lines 1-190+ shown)

- **TestSplitIntoTurns** (lines 20-66): Turn splitting logic for partitioning archive into old/protected/recent.
- **TestFlattenMessages** (lines 69-100): Message flattening to text (tool call names, tool results truncation, role prefixes).
- **TestEstimateTokens** (lines 104-108): Token estimation utility.
- **TestCompactHistory** (lines 111-214+): Full compaction workflow. Mock LLM responses, verify summary message structure, incremental vs full modes, decision slice handling.

No direct tests of compaction prompt assembly or template substitution. Tests mock `call_llm()` to avoid dependency on actual LLM.

**No existing tests snapshot or assert against assembled prompt text** (neither system prompt, reflection, memory sweep, nor compaction). All tests validate behavior (tags present, structure, gating logic) but not the exact wording or formatting of assembled prompts.

**Fixture:** All tests use a shared `config` fixture (conftest.py) with bundled skills and minimal workspace.

---

## Summary of Conventions

### System Prompt (#304)
- **Delimiter pattern:** `<tag>\n{body}\n</tag>`
- **Separator:** `\n\n` between sections
- **Gating:** Empty sections emit nothing (prevent dangling tags)
- **Attribute escaping:** `html.escape(value, quote=True)` for XML attributes

### Reflection Prompt
- **Template:** Markdown with placeholders `{user_message}`, `{agent_response}`, `{tool_results_summary}`, `{retrieved_context}`, `{prior_turn_tools}`
- **Placeholder injection:** `.format()` substitution
- **LLM context:** Single user message, no system prompt
- **Separators:** Template controls via markdown headers/blanks (no programmatic join)

### Memory Sweep Prompt
- **Template:** Plain markdown, loaded as-is
- **LLM context:** System prompt, separate user message with `\n\n` join ("Conversation history to review:\n\n" + flattened)
- **Child turn:** Isolated agent loop with vault tools only
- **Separator:** `\n\n` before message list

### Compaction Prompt
- **Template variants:** Default + Incremental + optional Decisions addendum
- **Assembly:** `_with_decisions_addendum()` prepends addendum (starts with `\n\n`)
- **Input structure (full):** `slice_prefix + flattened_messages` or just `flattened_messages`
- **Input structure (incremental):** `slice_prefix + "Existing summary:\n" + summary + "\n\n" + "New conversation turns to incorporate:\n" + newly_old_flat`
- **LLM context:** Two-message (system prompt + user text)
- **Separators:** Explicit header strings ("Existing summary:\n", etc.) + `\n\n` between major sections + `\n` within flattened messages
- **Decision slice format:** Rendered inline before prose via `format_slice()` (compaction_decisions.py, not detailed here)

