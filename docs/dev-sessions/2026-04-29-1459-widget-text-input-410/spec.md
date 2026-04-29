# Widget: text_input Spec

**Goal:** Give the agent a way to ask the user for free-form text — a single-line answer, a multiline blob, or a small multi-field form — pausing the turn until the user submits.

**Source:** [#410](https://github.com/lmorchard/decafclaw/issues/410)

## Current state

Input widgets today: only `multiple_choice` (radios/checkboxes). Tool surface: `ask_user(prompt, options, allow_multiple)` at `tools/core.py:195–225`. The widget infra (registry, schema validation, agent-loop pause via `WidgetInputPause`, persistence/recovery) is fully built and shared across input widgets — see `research.md` §1, §4. New input widgets only need: a `widget.json` + `widget.js` pair under `web/static/widgets/{name}/`, plus a tool that emits a `WidgetRequest` with `end_turn=True` and an `on_response` callback.

## Desired end state

### New widget — `text_input`

Bundled at `src/decafclaw/web/static/widgets/text_input/{widget.json,widget.js}`.

- `modes`: `["inline"]`
- `accepts_input`: `true`
- `data_schema` (validates the agent's request):
  - `prompt` (string, required)
  - `fields` (array, required, min 1): each `{key (string, required), label (string, required), placeholder? (string), default? (string), multiline? (boolean, default false), required? (boolean, default true), max_length? (integer)}`
  - `submit_label` (string, optional, default `"Submit"`)
- Response payload from JS to agent: object keyed by `key` — `{<key>: <string>, ...}`. Empty/unfilled non-required fields submit as empty string.

### Render behavior

- One `<input>` (or `<textarea>` if `multiline`) per field, labeled, with `placeholder` and `default` applied. `required` and `max_length` enforced via HTML attributes only — no client-side validation framework.
- Submit button labeled `submit_label`, disabled until all `required` fields are non-empty.
- **Keyboard:** Enter submits when the form has exactly one non-multiline field (single-field common case). In all other shapes (multiline field present, or ≥2 fields), Enter is normal newline/tab and submit requires a click or Cmd/Ctrl+Enter.
- After submit: read-only display of submitted values (mirrors `multiple_choice` "winner" pattern).

### New tool — `ask_user_text`

In `tools/core.py`. Signature:

```python
async def tool_ask_user_text(
    ctx,
    prompt: str,
    fields: list | None = None,
    submit_label: str = "Submit",
) -> ToolResult: ...
```

- If `fields` is `None` or empty, default to a single field `[{"key": "value", "label": prompt}]`. Fields can be passed as bare strings (`"name"` → `{key: "name", label: "Name"}` via title-casing) or full dicts.
- Returns `ToolResult(text="[awaiting user response: ...]", widget=WidgetRequest(...), end_turn=True)`.
- Default `on_response` callback shapes the inject string:
  - **Single field:** `User responded: <value>` (matches `"User selected: <label>"` style of `multiple_choice`).
  - **Multi-field:** `User responded: {"name": "Les", "email": "..."}` — JSON, compact.
  - Empty / cancelled: `"User did not respond."` (matches the `multiple_choice` fallback shape).

### Rename — `ask_user` → `ask_user_multiple_choice`

Hard rename, no alias. Touches: tool definition (`tools/core.py`), tool registry (`tools/tool_registry.py`), system prompt / tool descriptions, eval fixtures (`evals/`), any bundled skill that calls it by name (`src/decafclaw/skills/*/`), docs (`docs/tools.md`, `docs/widgets*.md`), and tests.

The two tools must be obviously different to the LLM: descriptions explicitly contrast "pick from a fixed list of options" vs. "free-form text answer." Validate via `make eval-tools`.

## Design decisions

- **Decision:** New widget `text_input`, separate from `multiple_choice`.
  - **Why:** Different schemas, different render shapes, different keyboard behavior. The widget catalog already separates by widget type, not by tool.
  - **Rejected:** Overloading `multiple_choice` with a "free-text" mode — it would balloon the schema and make tool descriptions harder.

- **Decision:** Smart-default inject string — single-field unwraps to bare value, multi-field is JSON.
  - **Why:** The single-field case is the dominant one and a bare string reads more naturally to the LLM. Mirrors `multiple_choice`'s `"User selected: <label>"` natural-language style. JSON is unambiguous when there are multiple keys.
  - **Rejected:** Always JSON — uniform but verbose for the common case.

- **Decision:** Two sibling tools (`ask_user_text`, `ask_user_multiple_choice`) instead of one polymorphic `ask_user`.
  - **Why:** Tool descriptions are a control surface (CLAUDE.md). Two clean schemas with sharp descriptions disambiguate better than one tool with branching parameters. Sets up the `ask_user_*` family for future input widgets (date picker, slider, etc.).
  - **Rejected:** Extending `ask_user` with `fields` (mutually exclusive with `options`); convenience tool `ask_user_input` with both flat and `fields=[...]` shapes.

- **Decision:** Hard rename `ask_user` → `ask_user_multiple_choice` in this PR. No alias.
  - **Why:** CLAUDE.md "no deprecated code for test compatibility." Symmetric naming pays off forever; the churn is contained to one PR.
  - **Rejected:** Keeping `ask_user` as a deprecated alias.

- **Decision:** v1 field schema = `{key, label, placeholder?, default?, multiline?, required?, max_length?}`.
  - **Why:** All cheaply supported by HTML `<input>`/`<textarea>` with no extra code. Adding them in v1 costs nothing and avoids a follow-up.
  - **Rejected:** Trimming to `{key, label, required}` for "minimal v1."

- **Decision:** Client-side `required`/`maxlength` only; no server-side response validation.
  - **Why:** Matches the trust model of `multiple_choice`, which doesn't validate response payloads server-side either. The widget `data_schema` validates the *agent's* request shape (so the LLM can't pass malformed `fields`).
  - **Rejected:** Server-side enforcement on response data — adds plumbing without a clear threat model in v1.

- **Decision:** Enter submits only for single non-multiline field. Multi-field or multiline: button only (Cmd/Ctrl+Enter shortcut).
  - **Why:** Real-form convention. Tabbing between fields with Enter would feel wrong; accidental submit in field 1 of 3 is annoying.
  - **Rejected:** Always-button (loses muscle memory for the common one-shot question); always-Enter-with-Shift-newline (chat-input style — wrong for forms).

## Patterns to follow

- Widget pair structure: `widget.json` + `widget.js` per `web/static/widgets/multiple_choice/` (`research.md` §3).
- Tool emit pattern: `tools/core.py:195–225` (`tool_ask_user`) — option normalization helper, default `on_response` builder, `ToolResult(..., widget=WidgetRequest(...), end_turn=True)`.
- Default callback signature: `widget_input.py:46` `default_inject_message(data)`.
- Light-DOM Lit component with `createRenderRoot() { return this; }`: `multiple_choice/widget.js:24`.
- BEM-ish class naming: `.widget-text-input__field--required`, `.widget-text-input__submit`.
- Pico-var fallbacks for borders/colors: `widgets.css:64` style.
- Submitted state styling pattern: `multiple_choice` "winner" CSS at `widgets.css:190–206`.
- Pico v2 button gotcha: tag-qualify (`button.foo`) for any custom button rules — see memory `reference_pico_cascade_gotchas.md`.
- Eval-driven tool disambiguation: run `make eval-tools` after the rename + new tool description (CLAUDE.md "Tools" section).
- Per-conversation persistence/recovery is automatic via the existing `WidgetInputPause` infra — no new code path needed (`research.md` §4).

## What we're NOT doing

- **No richer validation in v1.** No regex, min_length, type=email/url/number, or cross-field rules. Punt to a follow-up.
- **No server-side response validation.** Trust the WS payload like `multiple_choice` does.
- **No canvas mode for `text_input`.** Inline only.
- **No deprecated `ask_user` alias.** Hard rename only.
- **No new input primitive library / shared form component.** Each widget keeps its own markup.
- **No changes to `WidgetInputPause` / agent loop / confirmation infra.** Reuse as-is.
- **No changes to other tools / skills beyond mechanical updates required by the rename.**
- **No mobile-specific keyboard tweaks** (e.g., setting `inputmode`, `autocapitalize`). Default browser behavior is fine in v1.
- **No accessibility audit beyond using semantic `<label for=...>` + native `<input>`/`<textarea>`.**

## Open questions

None blocking. Two minor defaults I'm proceeding with:

- **Field key for the single-field default:** `"value"` (matches the issue's example).
- **Default `required` per field:** `true`. Most asks expect an answer; opt out with `required: false`.
