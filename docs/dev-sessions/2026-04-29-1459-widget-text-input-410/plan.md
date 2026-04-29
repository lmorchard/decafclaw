# Widget: text_input Implementation Plan

**Goal:** Ship the `text_input` widget + `ask_user_text` tool, and rename `ask_user` → `ask_user_multiple_choice` as part of the same PR.

**Approach:** Do the rename first (mechanical, isolates churn). Then add the widget files (front-end + CSS + registry tests). Then add the new tool and its tests. Finish with docs and eval fixtures so the LLM can disambiguate the two `ask_user_*` tools.

**Tech stack:** Python (tools, widget registry), Lit + plain CSS (front-end), `jsonschema` for widget data validation, pytest for tests.

---

## Phase 1: Rename `ask_user` → `ask_user_multiple_choice`

Hard rename of the existing tool, helpers, registry key, tool definition, tests, and docs. No alias. Establishes the `ask_user_*` family naming before the new tool lands.

**Files:**

- Modify: `src/decafclaw/tools/core.py`
  - Module docstring (line 1) — update reference to `ask_user`.
  - Rename `_normalize_ask_user_options` → `_normalize_multiple_choice_options` (line 138).
  - Rename `_ask_user_default_on_response` → `_default_multiple_choice_callback` (line 168). Update docstring on line 170.
  - Rename `tool_ask_user` → `tool_ask_user_multiple_choice` (line 195). Update log message (line 198), error strings (lines 201, 205–206).
  - In `CORE_TOOLS` dict (line 313), key `"ask_user": tool_ask_user` → `"ask_user_multiple_choice": tool_ask_user_multiple_choice`.
  - In `CORE_TOOL_DEFINITIONS` (line 403), update `"name": "ask_user"` → `"ask_user_multiple_choice"`. Tweak first sentence of description to land cleanly: `"Pause the turn and ask the user to pick from a fixed list of options."` (rest unchanged).

- Rename: `tests/test_ask_user.py` → `tests/test_ask_user_multiple_choice.py`. Update imports (lines 6–10) and inline test names where the function is called.

- Modify: `tests/test_reflection.py` — string literals `"ask_user"` → `"ask_user_multiple_choice"` at lines 57, 69, 82, 97, 98 (test fixtures and assertions). Update the comment on line 97.

- Modify: `tests/test_web_widget_response_handler.py` — string literal `"ask_user"` → `"ask_user_multiple_choice"` at line 123.

- Modify: `src/decafclaw/mattermost.py` — comment on line 520: `description for ask_user already discourages` → `description for ask_user_multiple_choice already discourages`.

- Modify: `docs/widgets.md` — section heading `### The ask_user core tool` (line 150) → `### The ask_user_multiple_choice core tool`. Body references at lines 152, 153, 157 updated. Key files reference at line 434 updated.

**Key changes:**

```python
# tools/core.py — rename signatures only; behavior unchanged.
def _normalize_multiple_choice_options(options: list) -> list[dict] | None: ...
def _default_multiple_choice_callback(options: list[dict],
                                       allow_multiple: bool): ...
async def tool_ask_user_multiple_choice(ctx, prompt: str, options: list,
                                        allow_multiple: bool = False) -> ToolResult: ...

CORE_TOOLS = {
    # ...
    "ask_user_multiple_choice": tool_ask_user_multiple_choice,
}
```

**TDD note:** This is mechanical refactoring (no behavior change). Procedure: rename the test file first, run pytest to watch the import fail, then rename in `core.py`. The `make test` run after is the regression check.

**Verification — automated:**
- [x] `make lint` passes
- [x] `make test` passes
- [x] `make check` passes
- [x] `grep` for stale `ask_user` references returns nothing.

**Verification — manual:**
- [ ] In the running app, the LLM still receives a tool named `ask_user_multiple_choice` and calling it pauses the turn for a user choice (test by triggering it through the web UI). _(deferred to Phase 4 manual block)_

---

## Phase 2: Add `text_input` widget files (front-end + CSS + registry tests)

Build the widget itself: `widget.json` (schema for the agent's request), `widget.js` (Lit component), CSS. No tool yet — the registry alone gates rendering, and tests verify the schema accepts/rejects well-formed payloads.

**Files:**

- Create: `src/decafclaw/web/static/widgets/text_input/widget.json`
- Create: `src/decafclaw/web/static/widgets/text_input/widget.js`
- Modify: `src/decafclaw/web/static/styles/widgets.css` — append a `/* ---- text_input ---- */` block following the `multiple_choice` pattern at lines 138–215.
- Create: `tests/test_text_input_widget.py` — registry-level validation tests (no tool yet).

**Key changes:**

`widget.json`:

```json
{
  "name": "text_input",
  "description": "Ask the user a free-form text question — single-line, multiline, or a small multi-field form. Pauses the agent turn until the user submits.",
  "modes": ["inline"],
  "accepts_input": true,
  "data_schema": {
    "type": "object",
    "required": ["prompt", "fields"],
    "properties": {
      "prompt": { "type": "string", "minLength": 1 },
      "fields": {
        "type": "array",
        "minItems": 1,
        "items": {
          "type": "object",
          "required": ["key", "label"],
          "properties": {
            "key": { "type": "string", "minLength": 1 },
            "label": { "type": "string", "minLength": 1 },
            "placeholder": { "type": "string" },
            "default": { "type": "string" },
            "multiline": { "type": "boolean" },
            "required": { "type": "boolean" },
            "max_length": { "type": "integer", "minimum": 1 }
          }
        }
      },
      "submit_label": { "type": "string", "minLength": 1 }
    }
  }
}
```

`widget.js` — Lit component, light DOM (mirrors `multiple_choice/widget.js:24` pattern):

```js
import { LitElement, html, nothing } from 'lit';

export class TextInputWidget extends LitElement {
  static properties = {
    data: { type: Object },
    submitted: { type: Boolean },
    response: { type: Object, attribute: false },
    _values: { type: Object, state: true },
  };

  createRenderRoot() { return this; }

  constructor() {
    super();
    this.data = null;
    this.submitted = false;
    this.response = null;
    this._values = {};
  }

  updated(changed) {
    // On reload, seed values from the archived response so the
    // submitted UI shows what the user actually entered.
    if ((changed.has('response') || changed.has('submitted'))
        && this.submitted && this.response) {
      this._values = { ...this.response };
    }
    // On first data, seed defaults.
    if (changed.has('data') && this.data && !this.submitted) {
      const seeded = {};
      for (const f of (this.data.fields || [])) {
        seeded[f.key] = (f.default ?? '');
      }
      this._values = seeded;
    }
  }

  _onInput(key, e) {
    this._values = { ...this._values, [key]: e.target.value };
  }

  _isSingleLineSingleField() {
    const fields = this.data?.fields || [];
    return fields.length === 1 && !fields[0].multiline;
  }

  _canSubmit() {
    if (this.submitted) return false;
    for (const f of (this.data?.fields || [])) {
      const required = f.required !== false; // default true
      if (required && !(this._values[f.key] || '').trim()) return false;
    }
    return true;
  }

  _onSubmit() {
    if (!this._canSubmit()) return;
    this.dispatchEvent(new CustomEvent('widget-response', {
      detail: { ...this._values },
      bubbles: true,
      composed: true,
    }));
  }

  _onKeyDown(e) {
    // Enter submits ONLY when there's exactly one non-multiline field.
    // Cmd/Ctrl+Enter submits in any shape.
    if (e.key !== 'Enter') return;
    if ((e.metaKey || e.ctrlKey) || this._isSingleLineSingleField()) {
      e.preventDefault();
      this._onSubmit();
    }
  }

  _renderField(f) {
    const value = this._values[f.key] ?? '';
    const required = f.required !== false;
    const common = {
      id: `tf-${f.key}`,
      placeholder: f.placeholder || '',
      maxlength: f.max_length || undefined,
      disabled: this.submitted,
    };
    const input = f.multiline
      ? html`
          <textarea
            id=${common.id}
            placeholder=${common.placeholder}
            maxlength=${common.maxlength ?? nothing}
            ?required=${required}
            ?disabled=${common.disabled}
            .value=${value}
            @input=${(e) => this._onInput(f.key, e)}
            @keydown=${this._onKeyDown}
            rows="3"
          ></textarea>`
      : html`
          <input
            type="text"
            id=${common.id}
            placeholder=${common.placeholder}
            maxlength=${common.maxlength ?? nothing}
            ?required=${required}
            ?disabled=${common.disabled}
            .value=${value}
            @input=${(e) => this._onInput(f.key, e)}
            @keydown=${this._onKeyDown}
          />`;
    return html`
      <label class="widget-text-input__field" for=${common.id}>
        <span class="widget-text-input__label">${f.label}</span>
        ${input}
      </label>`;
  }

  render() {
    const d = this.data;
    if (!d || !Array.isArray(d.fields) || d.fields.length === 0) {
      return html`<div class="widget-text-input widget-text-input--empty"><em>no fields</em></div>`;
    }
    const submitLabel = this.submitted
      ? 'Submitted'
      : (d.submit_label || 'Submit');
    return html`
      <div class="widget-text-input">
        ${d.prompt ? html`<p class="widget-text-input__prompt">${d.prompt}</p>` : nothing}
        <div class="widget-text-input__fields">
          ${d.fields.map((f) => this._renderField(f))}
        </div>
        <div class="widget-text-input__actions">
          <button
            type="button"
            class="widget-text-input__submit"
            ?disabled=${!this._canSubmit()}
            @click=${this._onSubmit}
          >${submitLabel}</button>
        </div>
      </div>`;
  }
}

customElements.define('dc-widget-text-input', TextInputWidget);
```

`widgets.css` (appended block):

```css
/* ---- text_input ---- */

.widget-text-input {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.widget-text-input__prompt {
  margin: 0 0 0.25rem 0;
}

.widget-text-input__fields {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.widget-text-input__field {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

.widget-text-input__label {
  font-size: 0.85rem;
  opacity: 0.85;
}

.widget-text-input__actions {
  margin-top: 0.25rem;
}

.widget-text-input__submit {
  padding: 0.35rem 0.9rem;
  font-size: 0.85rem;
}
```

`tests/test_text_input_widget.py`:

```python
"""Tests for the text_input widget's data_schema validation."""

from decafclaw.widgets import load_widget_registry


class _Cfg:
    def __init__(self, agent_path):
        self.agent_path = agent_path


def _registry(tmp_path):
    return load_widget_registry(_Cfg(tmp_path / "agent"))


def test_text_input_registered(tmp_path):
    reg = _registry(tmp_path)
    desc = reg.get("text_input")
    assert desc is not None
    assert desc.accepts_input is True
    assert desc.modes == ["inline"]


def test_validate_single_field(tmp_path):
    reg = _registry(tmp_path)
    ok, err = reg.validate("text_input", {
        "prompt": "What's your name?",
        "fields": [{"key": "value", "label": "Name"}],
    })
    assert ok, err


def test_validate_multi_field_with_optionals(tmp_path):
    reg = _registry(tmp_path)
    ok, err = reg.validate("text_input", {
        "prompt": "Contact info",
        "fields": [
            {"key": "name", "label": "Name", "placeholder": "Les"},
            {"key": "email", "label": "Email", "default": "x@y",
             "max_length": 200, "required": False},
            {"key": "bio", "label": "Bio", "multiline": True},
        ],
        "submit_label": "Send",
    })
    assert ok, err


def test_validate_rejects_empty_fields(tmp_path):
    reg = _registry(tmp_path)
    ok, _ = reg.validate("text_input", {
        "prompt": "x", "fields": [],
    })
    assert not ok


def test_validate_rejects_missing_key(tmp_path):
    reg = _registry(tmp_path)
    ok, _ = reg.validate("text_input", {
        "prompt": "x",
        "fields": [{"label": "no key"}],
    })
    assert not ok


def test_validate_rejects_missing_prompt(tmp_path):
    reg = _registry(tmp_path)
    ok, _ = reg.validate("text_input", {
        "fields": [{"key": "v", "label": "V"}],
    })
    assert not ok
```

**TDD note:** Write `test_text_input_widget.py` first, watch the registry tests fail (`reg.get("text_input")` returns `None`), then add `widget.json` + `widget.js`. JS not unit-tested (no JS framework here); rely on registry tests + manual UI verification at the end of execute.

**Verification — automated:**
- [x] `make lint` passes
- [x] `make test` passes (new tests included)
- [x] `make check` passes (Python + `make check-js` over the new `widget.js`)

**Verification — manual:**
- [ ] After Phase 3 lands, UI test in `make dev` confirms the widget renders correctly. (No standalone manual verification mid-phase — widget can't render without a tool to emit it.)

---

## Phase 3: Add `ask_user_text` tool

The tool that emits the `text_input` widget. Mirrors the structure of `tool_ask_user_multiple_choice` (Phase 1's renamed tool): normalization helper, default-callback builder, async tool function, registry entries.

**Files:**

- Modify: `src/decafclaw/tools/core.py`
  - Add `_normalize_text_input_fields(fields)` — accepts bare strings, `{key, label, ...}` dicts. Returns `None` on bad shape (mirrors `_normalize_multiple_choice_options`).
  - Add `_default_text_input_callback(field_keys: list[str])` — returns a callback. Single-key: bare value. Multi-key: JSON object. Empty/missing: `"User did not respond."`.
  - Add `tool_ask_user_text(ctx, prompt, fields=None, submit_label="Submit") -> ToolResult`.
  - Register in `CORE_TOOLS`: `"ask_user_text": tool_ask_user_text`.
  - Register in `CORE_TOOL_DEFINITIONS` with a description that explicitly contrasts with `ask_user_multiple_choice`.
- Create: `tests/test_ask_user_text.py` — happy paths, normalization edge cases, callback formatting, integration with `WidgetRequest`/`ToolResult`.

**Key changes:**

```python
# tools/core.py — appended below existing helpers and tool

import json  # already imported at top

def _normalize_text_input_fields(fields: list | None) -> list[dict] | None:
    """Normalize the fields argument into the widget's data_schema shape.

    Accepts None / [] (single-field default added by caller), bare
    strings (used as both key and title-cased label), or dicts. Dict
    form requires both ``key`` and ``label``. Returns None on any bad
    entry; returns [] for an explicit empty list (caller decides).
    """
    if fields is None:
        return None
    out: list[dict] = []
    seen_keys: set[str] = set()
    for f in fields:
        if isinstance(f, str):
            key = f.strip()
            if not key or key in seen_keys:
                return None
            entry = {"key": key, "label": key.replace("_", " ").title()}
        elif isinstance(f, dict):
            key = f.get("key")
            label = f.get("label")
            if not key or not label or key in seen_keys:
                return None
            entry = {"key": str(key), "label": str(label)}
            for opt in ("placeholder", "default"):
                v = f.get(opt)
                if isinstance(v, str):
                    entry[opt] = v
            for opt in ("multiline", "required"):
                v = f.get(opt)
                if isinstance(v, bool):
                    entry[opt] = v
            ml = f.get("max_length")
            if isinstance(ml, int) and ml > 0:
                entry["max_length"] = ml
        else:
            return None
        seen_keys.add(entry["key"])
        out.append(entry)
    return out


def _default_text_input_callback(field_keys: list[str]):
    """Build the default ``on_response`` callback for ask_user_text.

    Single field: returns ``"User responded: <value>"``. Multi-field:
    returns ``"User responded: {json}"``. Empty / no recognised data:
    ``"User did not respond."``.
    """
    def _cb(data: dict) -> str:
        if not isinstance(data, dict) or not data:
            return "User did not respond."
        if len(field_keys) == 1:
            value = data.get(field_keys[0], "")
            text = str(value).strip() if value is not None else ""
            if not text:
                return "User did not respond."
            return f"User responded: {text}"
        # Multi-field: emit a JSON object preserving field order.
        ordered = {k: str(data.get(k, "")) for k in field_keys}
        # Treat all-empty as "no response."
        if not any(v.strip() for v in ordered.values()):
            return "User did not respond."
        return "User responded: " + json.dumps(ordered, ensure_ascii=False)
    return _cb


async def tool_ask_user_text(ctx, prompt: str, fields: list | None = None,
                              submit_label: str = "Submit") -> ToolResult:
    """Pause the turn and ask the user for free-form text input."""
    log.info(f"[tool:ask_user_text] prompt={prompt!r} "
             f"fields={len(fields) if fields else 0}")
    if not prompt or not prompt.strip():
        return ToolResult(
            text="[error: ask_user_text requires a non-empty prompt]")
    if not fields:
        # Single-field default.
        normalized = [{"key": "value", "label": prompt.strip()}]
    else:
        normalized = _normalize_text_input_fields(fields)
        if normalized is None or not normalized:
            return ToolResult(
                text="[error: ask_user_text fields must each be a non-empty "
                     "string or a {key, label, ...} dict with unique keys]")
    widget_data: dict = {"prompt": prompt, "fields": normalized}
    if submit_label and submit_label != "Submit":
        widget_data["submit_label"] = submit_label
    widget = WidgetRequest(
        widget_type="text_input",
        data=widget_data,
        on_response=_default_text_input_callback(
            [f["key"] for f in normalized]),
    )
    short = (f"ask: {len(normalized)} field"
             + ("s" if len(normalized) != 1 else ""))
    return ToolResult(
        text=f"[awaiting user response: {prompt}]",
        display_short_text=short,
        widget=widget,
        end_turn=True,
    )
```

`CORE_TOOLS` addition:

```python
CORE_TOOLS = {
    # ... existing entries ...
    "ask_user_multiple_choice": tool_ask_user_multiple_choice,
    "ask_user_text": tool_ask_user_text,
}
```

`CORE_TOOL_DEFINITIONS` addition (paste below the existing `ask_user_multiple_choice` entry):

```python
{
    "type": "function",
    "priority": "low",
    "function": {
        "name": "ask_user_text",
        "description": (
            "Pause the turn and ask the user a free-form text question — "
            "a single-line answer, a multiline blob, or a small multi-field "
            "form. Use this when the answer is open-ended (a name, a URL, "
            "a paragraph). For picking from a fixed list of options use "
            "ask_user_multiple_choice instead. Use ONLY when the right "
            "answer is genuinely ambiguous from context and you cannot make "
            "a reasonable choice on your own. "
            "Only works in the web UI; Mattermost / terminal render the "
            "prompt as text and the turn ends without a response."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "Question presented to the user.",
                },
                "fields": {
                    "type": "array",
                    "description": (
                        "Optional. Each field is either a bare string "
                        "(used as both key and title-cased label) or a "
                        "{key, label, placeholder?, default?, multiline?, "
                        "required?, max_length?} dict. Keys must be "
                        "unique. Omit for a single-field text question "
                        "keyed 'value'."
                    ),
                    "items": {
                        "anyOf": [
                            {"type": "string"},
                            {
                                "type": "object",
                                "properties": {
                                    "key": {"type": "string"},
                                    "label": {"type": "string"},
                                    "placeholder": {"type": "string"},
                                    "default": {"type": "string"},
                                    "multiline": {"type": "boolean"},
                                    "required": {"type": "boolean"},
                                    "max_length": {"type": "integer"},
                                },
                                "required": ["key", "label"],
                            },
                        ],
                    },
                },
                "submit_label": {
                    "type": "string",
                    "description": "Optional submit button label (default 'Submit').",
                },
            },
            "required": ["prompt"],
        },
    },
},
```

`tests/test_ask_user_text.py`:

```python
"""Tests for the ask_user_text core tool."""

import json

import pytest

from decafclaw.media import ToolResult, WidgetRequest
from decafclaw.tools.core import (
    _default_text_input_callback,
    _normalize_text_input_fields,
    tool_ask_user_text,
)


# ------------- field normalization -------------

def test_normalize_bare_strings_title_cases_label():
    out = _normalize_text_input_fields(["name", "email_address"])
    assert out == [
        {"key": "name", "label": "Name"},
        {"key": "email_address", "label": "Email Address"},
    ]


def test_normalize_dicts_with_optionals():
    out = _normalize_text_input_fields([
        {"key": "bio", "label": "Bio", "multiline": True,
         "max_length": 500, "required": False, "placeholder": "tell me",
         "default": "x"},
    ])
    assert out == [{
        "key": "bio", "label": "Bio", "multiline": True,
        "max_length": 500, "required": False,
        "placeholder": "tell me", "default": "x",
    }]


def test_normalize_dict_missing_key_or_label_is_rejected():
    assert _normalize_text_input_fields([{"label": "no key"}]) is None
    assert _normalize_text_input_fields([{"key": "v"}]) is None


def test_normalize_duplicate_keys_rejected():
    assert _normalize_text_input_fields([
        {"key": "v", "label": "A"}, {"key": "v", "label": "B"},
    ]) is None


def test_normalize_bad_max_length_dropped():
    out = _normalize_text_input_fields([
        {"key": "v", "label": "V", "max_length": 0},
    ])
    assert out == [{"key": "v", "label": "V"}]


def test_normalize_None_returns_None():
    assert _normalize_text_input_fields(None) is None


def test_normalize_bad_entry_returns_None():
    assert _normalize_text_input_fields([123]) is None


# ------------- default on_response -------------

def test_default_callback_single_returns_bare_value():
    cb = _default_text_input_callback(["value"])
    assert cb({"value": "Hello"}) == "User responded: Hello"


def test_default_callback_single_strips_whitespace():
    cb = _default_text_input_callback(["value"])
    assert cb({"value": "  Hi  "}) == "User responded: Hi"


def test_default_callback_single_empty_says_no_response():
    cb = _default_text_input_callback(["value"])
    assert cb({"value": ""}) == "User did not respond."
    assert cb({}) == "User did not respond."


def test_default_callback_multi_returns_json():
    cb = _default_text_input_callback(["name", "email"])
    out = cb({"name": "Les", "email": "x@y"})
    assert out.startswith("User responded: ")
    assert json.loads(out[len("User responded: "):]) == {
        "name": "Les", "email": "x@y"}


def test_default_callback_multi_preserves_field_order():
    cb = _default_text_input_callback(["b", "a"])
    out = cb({"a": "first", "b": "second"})
    # Field-key order, not insertion order from the response.
    body = out[len("User responded: "):]
    assert body.index('"b"') < body.index('"a"')


def test_default_callback_multi_all_empty_says_no_response():
    cb = _default_text_input_callback(["a", "b"])
    assert cb({"a": "", "b": "  "}) == "User did not respond."


# ------------- tool integration -------------

@pytest.mark.asyncio
async def test_tool_happy_single_field_default():
    ctx = object()
    result = await tool_ask_user_text(ctx, prompt="Your name?")
    assert isinstance(result, ToolResult)
    assert result.end_turn is True
    assert isinstance(result.widget, WidgetRequest)
    assert result.widget.widget_type == "text_input"
    fields = result.widget.data["fields"]
    assert len(fields) == 1
    assert fields[0] == {"key": "value", "label": "Your name?"}
    assert "awaiting user response" in result.text


@pytest.mark.asyncio
async def test_tool_multi_field():
    ctx = object()
    result = await tool_ask_user_text(
        ctx, prompt="Contact info?",
        fields=[
            {"key": "name", "label": "Name"},
            {"key": "email", "label": "Email", "required": False},
        ],
        submit_label="Send",
    )
    assert result.widget.data["submit_label"] == "Send"
    assert len(result.widget.data["fields"]) == 2
    inject = result.widget.on_response({"name": "Les", "email": "x@y"})
    assert json.loads(inject[len("User responded: "):]) == {
        "name": "Les", "email": "x@y"}


@pytest.mark.asyncio
async def test_tool_blank_prompt_returns_error():
    ctx = object()
    result = await tool_ask_user_text(ctx, prompt="   ")
    assert result.widget is None
    assert "error" in result.text.lower()


@pytest.mark.asyncio
async def test_tool_bad_fields_returns_error():
    ctx = object()
    result = await tool_ask_user_text(
        ctx, prompt="?", fields=[{"label": "no key"}])
    assert result.widget is None
    assert "error" in result.text.lower()


@pytest.mark.asyncio
async def test_tool_default_callback_wired():
    ctx = object()
    result = await tool_ask_user_text(
        ctx, prompt="?", fields=["color"])
    inject = result.widget.on_response({"color": "blue"})
    assert inject == "User responded: blue"
```

**TDD note:** Write `test_ask_user_text.py` first, watch ImportError on `_default_text_input_callback`/`_normalize_text_input_fields`/`tool_ask_user_text`. Then add the helpers and tool. Then re-run.

**Verification — automated:**
- [x] `make lint` passes
- [x] `make test` passes
- [x] `make check` passes

**Verification — manual:** (deferred to Phase 4 manual block)

---

## Phase 4: Docs + tool-disambiguation eval + UI verification

Final slice: documentation update, eval fixture for the new tool pair, and live UI verification of single + multi-field flows.

**Files:**

- Modify: `docs/widgets.md` — add a new sub-section under "Input widgets" describing `text_input`. Update the example block at line 156–162 to also cover `ask_user_text`. Update the key-files list at line 434 if needed.
- Modify: `evals/tool_choice/core_overlaps.yaml` — add cases that disambiguate `ask_user_multiple_choice` vs `ask_user_text`.
  - `"Should I deploy to production or staging?"` → `ask_user_multiple_choice` (fixed list).
  - `"What name would you like for the new project?"` → `ask_user_text` (free-form).
  - `"Tell me your name, email, and a short bio."` → `ask_user_text` (multi-field).
- TDD opt-out: docs are pure prose; eval is an evaluation, not a test. Both are verification surfaces, not implementation.

**Key changes (docs/widgets.md, around line 150):**

```markdown
### Asking the user — `ask_user_multiple_choice` vs `ask_user_text`

Two core tools wrap the input-widget infrastructure:

- **`ask_user_multiple_choice(prompt, options, allow_multiple=False)`** —
  pick one (or several) from a fixed list. Renders the `multiple_choice`
  widget. Inject string: `"User selected: <label>"`.

- **`ask_user_text(prompt, fields=None, submit_label="Submit")`** —
  free-form text answer. Single-field by default (`fields` omitted), or
  pass `fields=[...]` for a small multi-field form. Renders the
  `text_input` widget. Inject string: bare value for single-field,
  `{...}` JSON for multi-field.

Both pause the turn until the user submits and only work in the web UI.
```

**Verification — automated:**
- [x] `make lint` passes
- [x] `make test` passes
- [x] `make check` passes
- [x] `make eval-tools` runs — all 3 new ask_user disambiguation cases pass; one pre-existing canvas/workspace_write failure unrelated to this PR.

**Verification — manual:** (Playwright walkthrough against local `uv run decafclaw` on port 18881)
- [x] Single-field default → single text input rendered with prompt as label, Enter submits, agent received `"User responded: Indigo"` and replied "Got it — your favorite color is indigo."
- [x] Multi-field (`name` + multiline `bio`) → two fields rendered (input + textarea), Enter on the name field does NOT submit, button click does, agent received `User responded: {"name": "Les", "bio": ""}` JSON and correctly extracted both.
- [ ] Multiline single-field Enter→newline path not separately exercised (covered indirectly: multi-field test confirmed Enter doesn't submit when multiline is present).
- [x] Renamed `ask_user_multiple_choice` rendered three radios, picked "green", agent replied "Got it — you picked green."
- [x] Page refresh after single-field submit → widget restored with `submitted: true`, `response: {value: "Indigo"}`, button "Submitted", input disabled, value preserved.

**Found during walkthrough and fixed in this PR:** Live-tab `submitted` state didn't flip after submission — `tool-status-store.js` `respondToWidget` removed the confirm from `pendingConfirms` before the broadcast `CONFIRMATION_RESPONSE` arrived, so `markToolWidgetSubmitted` never fired on the submitting tab. Affected both `multiple_choice` and `text_input` (pre-existing, landed with #366). Fix: have `respondToWidget` call `markToolWidgetSubmitted` directly. Verified end-to-end via Playwright after the fix — both widgets now flip immediately on submit.

---

## Plan self-review

- **Spec coverage:**
  - Widget `text_input` files (json + js + css) → Phase 2.
  - Tool `ask_user_text` with single-field default + multi-field signature → Phase 3.
  - Smart-default inject string (bare for single, JSON for multi) → Phase 3 (`_default_text_input_callback`).
  - Hard rename `ask_user` → `ask_user_multiple_choice` → Phase 1.
  - Field schema `{key, label, placeholder?, default?, multiline?, required?, max_length?}` → Phase 2 (widget.json) + Phase 3 (normalize helper).
  - Client-side `required` + `maxlength` only → Phase 2 (widget.js).
  - Enter-submit only for single non-multiline field → Phase 2 (`_isSingleLineSingleField` + `_onKeyDown`).
  - No alias for `ask_user` → Phase 1 (no shim added; `grep` check in verification).
  - Inline-only mode → Phase 2 (widget.json `modes: ["inline"]`).
  - Tool description disambiguation via `make eval-tools` → Phase 4.
- **Placeholder scan:** no TBDs / TODOs / "implement later" / vague phrases.
- **Type consistency:** `tool_ask_user_multiple_choice` (Phase 1) and `tool_ask_user_text` (Phase 3) match across all later references; `_default_multiple_choice_callback` and `_default_text_input_callback` symmetric and used consistently; widget name `text_input` consistent across `widget.json` `name`, `customElements.define('dc-widget-text-input', ...)`, CSS class prefix `.widget-text-input__`, registry tests.

No gaps found.
