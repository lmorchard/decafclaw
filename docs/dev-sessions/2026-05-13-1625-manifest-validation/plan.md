# Manifest Validation via Typed Payloads — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `message_types.json` enforced by pyright at every `ws_send` call site (Python side) and tsc at every TUI consumer (TS side) — closing #492 and finishing the TUI spec's "Option A → Option B" promotion path.

**Architecture:** Manifest stays canonical. `scripts/gen_message_types.py` gains a field-type parser plus two new renderers: one extends `src/decafclaw/web/message_types.py` with TypedDicts + discriminated unions + a `WSSendCallable` type alias; the other produces `tui/src/types.generated.ts`. Python emit sites are retyped through pyright-driven cleanup. The TUI's hand-typed `types.ts` is deleted and consumers re-import from the generated file. Wire shape unchanged byte-for-byte.

**Tech Stack:** Python 3.13 (TypedDict, NotRequired, Literal narrowing), pyright, TypeScript 5 + tsc, vitest, GNU make.

**Spec:** [`spec.md`](spec.md). Read it first if you haven't.

**File map (final state):**

```
src/decafclaw/web/message_types.json     # canonical, unchanged structure
src/decafclaw/web/message_types.py       # GENERATED — enum + TypedDicts + unions + WSSendCallable
src/decafclaw/web/static/lib/message-types.js  # GENERATED — unchanged
docs/websocket-messages.md               # GENERATED — unchanged
tui/src/types.generated.ts               # NEW, GENERATED — replaces types.ts
tui/src/types.ts                         # DELETED

scripts/gen_message_types.py             # extended with parse_field_type + render_python_typed + render_typescript
Makefile                                 # check-message-types target extended
src/decafclaw/web/websocket.py           # ws_send + handlers retyped to WSSendCallable
tests/test_message_types.py              # extended with parser tests + TypedDict coverage tests

tui/src/wsClient.ts                      # import path updated
tui/src/dispatcher.ts                    # import path updated
tui/src/dispatcher.test.ts               # import path updated
tui/src/App.tsx                          # import path updated
```

**Three commits on the branch:**

1. **Generator extension** (Task 1–4): parser, two renderers, Makefile + coverage tests. Pure addition; no existing behavior changes.
2. **Python emit-site retype** (Task 5): `ws_send` chain typed; pyright errors resolved with documented patterns.
3. **TUI codegen swap** (Task 6): hand-typed `types.ts` deleted; consumers re-imported.

Task 7 is a regression check before the PR opens.

---

### Task 1: TDD `parse_field_type`

Pure function that maps a manifest field-type string to `(python_base_type, ts_base_type, required)`. Test-drive it before adding renderers, since the renderers consume it.

**Files:**
- Modify: `tests/test_message_types.py`
- Modify: `scripts/gen_message_types.py`

- [ ] **Step 1: Write failing test cases**

Append to `tests/test_message_types.py`:

```python
import importlib.util
from pathlib import Path

import pytest

_GEN_PATH = Path(__file__).resolve().parent.parent / "scripts/gen_message_types.py"


def _gen_module():
    spec = importlib.util.spec_from_file_location("gen_message_types", _GEN_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("inp,expected_py,expected_ts,expected_required", [
    ("string", "str", "string", True),
    ("number", "int", "number", True),
    ("boolean", "bool", "boolean", True),
    ("object", "dict[str, object]", "Record<string, unknown>", True),
    ("array of string", "list[str]", "string[]", True),
    ("array of object", "list[dict[str, object]]", "Array<Record<string, unknown>>", True),
    ("string | null", "str | None", "string | null", True),
    ("string | object", "str | dict[str, object]", "string | Record<string, unknown>", True),
    ("string?", "str", "string", False),
    ("object?", "dict[str, object]", "Record<string, unknown>", False),
])
def test_parse_field_type(inp, expected_py, expected_ts, expected_required):
    py, ts, req = _gen_module().parse_field_type(inp)
    assert py == expected_py
    assert ts == expected_ts
    assert req == expected_required


def test_parse_field_type_unknown_scalar_raises():
    with pytest.raises(ValueError, match="unknown"):
        _gen_module().parse_field_type("frobnicator")


def test_parse_field_type_unknown_array_element_raises():
    with pytest.raises(ValueError, match="unknown array"):
        _gen_module().parse_field_type("array of frobnicator")
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
uv run pytest tests/test_message_types.py -v
```

Expected: 12 new test cases ERROR with `AttributeError: module 'gen_message_types' has no attribute 'parse_field_type'`. Existing 2 tests still pass.

- [ ] **Step 3: Implement `parse_field_type` + `_scalar_pair` in `scripts/gen_message_types.py`**

Add these two helper functions near the top of `scripts/gen_message_types.py`, after the `sorted_messages` function:

```python
_SCALAR_TYPES = {
    "string":  ("str", "string"),
    "number":  ("int", "number"),
    "boolean": ("bool", "boolean"),
    "object":  ("dict[str, object]", "Record<string, unknown>"),
    "null":    ("None", "null"),
}


def _scalar_pair(s: str) -> tuple[str, str]:
    """Map a single scalar type name to (python, typescript) pair."""
    if s not in _SCALAR_TYPES:
        raise ValueError(f"unknown scalar type: {s!r}")
    return _SCALAR_TYPES[s]


def parse_field_type(s: str) -> tuple[str, str, bool]:
    """Parse a manifest field-type string.

    Returns ``(python_base_type, ts_base_type, required)``. Renderers
    wrap the base type with ``NotRequired[]`` (Python) or a ``?:`` field
    suffix (TS) when ``required`` is False.

    Grammar:
      * scalar: "string" | "number" | "boolean" | "object" | "null"
      * array:  "array of <scalar>"
      * union:  "<scalar> | <scalar>" (two members; e.g. "string | null")
      * optional: any of the above with a trailing "?"
    """
    optional = s.endswith("?")
    if optional:
        s = s[:-1]

    if s.startswith("array of "):
        elem = s[len("array of "):]
        if elem == "string":
            return ("list[str]", "string[]", not optional)
        if elem == "object":
            return ("list[dict[str, object]]", "Array<Record<string, unknown>>", not optional)
        raise ValueError(f"unknown array element type: {elem!r}")

    if " | " in s:
        py_parts: list[str] = []
        ts_parts: list[str] = []
        for part in s.split(" | "):
            py, ts = _scalar_pair(part)
            py_parts.append(py)
            ts_parts.append(ts)
        return (" | ".join(py_parts), " | ".join(ts_parts), not optional)

    py, ts = _scalar_pair(s)
    return (py, ts, not optional)
```

- [ ] **Step 4: Run tests, verify they pass**

```bash
uv run pytest tests/test_message_types.py -v
```

Expected: all 14 tests pass (12 parser + 2 existing).

No commit yet — Task 4 commits all of Commit 1 together.

---

### Task 2: `render_python_typed`

Extends the existing Python output with TypedDicts, the two discriminated unions, and the `WSSendCallable` alias.

**Files:**
- Modify: `scripts/gen_message_types.py`

- [ ] **Step 1: Update `render_python` to add the TypedDict imports**

Find this block (lines 60–62 in the current file):

```python
    out.append("from __future__ import annotations")
    out.append("")
    out.append("from enum import StrEnum")
    out.append("")
```

Replace with:

```python
    out.append("from __future__ import annotations")
    out.append("")
    out.append("from collections.abc import Awaitable, Callable")
    out.append("from enum import StrEnum")
    out.append("from typing import Literal, NotRequired, TypedDict")
    out.append("")
```

This adds the imports needed by the TypedDicts section that `render_python_typed` will append. Existing enum content remains intact.

- [ ] **Step 2: Add `_interface_name` helper**

Add this helper after `_scalar_pair` (or anywhere in module scope):

```python
def _interface_name(message_name: str, direction: str) -> str:
    """tool_call_start, server_to_client -> SrvToolCallStart."""
    parts = [w.capitalize() for w in message_name.split("_")]
    prefix = "Srv" if direction == "server_to_client" else "Cli"
    return prefix + "".join(parts)
```

- [ ] **Step 3: Implement `render_python_typed`**

Add this function in `scripts/gen_message_types.py` (after `render_python`):

```python
def render_python_typed(data: dict) -> str:
    """Render TypedDicts, discriminated unions, and WSSendCallable.

    Appended after `render_python`'s output in the same file.
    """
    items = sorted_messages(data)
    out: list[str] = ["", "", "# -- TypedDicts (one per wire message) --", ""]

    s2c_ifaces: list[str] = []
    c2s_ifaces: list[str] = []

    for name, entry in items:
        iface = _interface_name(name, entry["direction"])
        if entry["direction"] == "server_to_client":
            s2c_ifaces.append(iface)
        elif entry["direction"] == "client_to_server":
            c2s_ifaces.append(iface)
        # bidirectional (none currently) would need a separate bucket

        out.append(f"class {iface}(TypedDict):")
        # Use Literal[WSMessageType.X] (not Literal["x"]) so existing emit
        # sites that pass WSMessageType.X enum values continue to narrow
        # correctly. Pyright treats StrEnum members as enum-member literals,
        # not as `str` literals, so the latter would force every call site
        # to be rewritten with bare strings.
        out.append(f'    type: Literal[WSMessageType.{name.upper()}]')
        for fname, ftype in entry["fields"].items():
            py_base, _, required = parse_field_type(ftype)
            if required:
                out.append(f"    {fname}: {py_base}")
            else:
                out.append(f"    {fname}: NotRequired[{py_base}]")
        out.append("")

    out.append("")
    out.append("# -- Discriminated unions --")
    out.append("")
    if s2c_ifaces:
        out.append("ServerMessage = " + " | ".join(s2c_ifaces))
        out.append("")
    if c2s_ifaces:
        out.append("ClientMessage = " + " | ".join(c2s_ifaces))
        out.append("")

    out.append("")
    out.append("# -- Callable alias for ws_send and friends --")
    out.append("")
    out.append("WSSendCallable = Callable[[ServerMessage], Awaitable[None]]")
    out.append("")

    return "\n".join(out)
```

- [ ] **Step 4: Update `main()` to combine renderings into one file**

Find `main()` (currently around line 158). Replace:

```python
    PY_OUT.write_text(render_python(data), encoding="utf-8")
```

With:

```python
    PY_OUT.write_text(render_python(data) + render_python_typed(data), encoding="utf-8")
```

- [ ] **Step 5: Regenerate and verify**

```bash
uv run python scripts/gen_message_types.py
```

Expected: prints `wrote src/decafclaw/web/message_types.py` etc.

Inspect the output:

```bash
head -30 src/decafclaw/web/message_types.py
tail -50 src/decafclaw/web/message_types.py
```

Expected: file starts with the existing enum + frozensets, then has TypedDict definitions, then `ServerMessage` / `ClientMessage` unions, then `WSSendCallable`.

- [ ] **Step 6: Verify the regenerated file imports correctly**

```bash
uv run python -c "from decafclaw.web.message_types import WSMessageType, ServerMessage, ClientMessage, WSSendCallable, SrvChunk; print('OK')"
```

Expected: prints `OK`. (If import errors, the generated file has a problem — likely an import-order issue.)

- [ ] **Step 7: Run existing tests**

```bash
uv run pytest tests/test_message_types.py -v
```

Expected: all 14 tests still pass.

No commit yet — continues into Task 3.

---

### Task 3: `render_typescript`

Adds the TypeScript output: `tui/src/types.generated.ts`.

**Files:**
- Modify: `scripts/gen_message_types.py`

- [ ] **Step 1: Add the output path constant**

Near the top of `scripts/gen_message_types.py`, add:

```python
TS_OUT = REPO_ROOT / "tui/src/types.generated.ts"
```

(Below the existing `DOC_OUT = REPO_ROOT / "docs/websocket-messages.md"` line.)

- [ ] **Step 2: Implement `render_typescript`**

Add this function after `render_python_typed`:

```python
def render_typescript(data: dict) -> str:
    """Render TS interfaces + discriminated unions for the TUI."""
    items = sorted_messages(data)
    out: list[str] = []
    out.append(f"// {GEN_HEADER}")
    out.append(f"// Source: {SOURCE_REL}")
    out.append("")

    s2c_ifaces: list[str] = []
    c2s_ifaces: list[str] = []

    for name, entry in items:
        iface = _interface_name(name, entry["direction"])
        if entry["direction"] == "server_to_client":
            s2c_ifaces.append(iface)
        elif entry["direction"] == "client_to_server":
            c2s_ifaces.append(iface)

        out.append(f"export interface {iface} {{")
        out.append(f'  type: "{name}";')
        for fname, ftype in entry["fields"].items():
            _, ts_base, required = parse_field_type(ftype)
            suffix = "" if required else "?"
            out.append(f"  {fname}{suffix}: {ts_base};")
        out.append("}")
        out.append("")

    def _emit_union(union_name: str, ifaces: list[str]) -> None:
        if not ifaces:
            return
        out.append(f"export type {union_name} =")
        for iface in ifaces:
            out.append(f"  | {iface}")
        out[-1] = out[-1] + ";"
        out.append("")

    _emit_union("ServerMessage", s2c_ifaces)
    _emit_union("ClientMessage", c2s_ifaces)

    return "\n".join(out)
```

- [ ] **Step 3: Update `main()` to write the TS output**

Find the body of `main()`. Add the TS write alongside the others:

```python
def main() -> int:
    data = load_manifest()
    PY_OUT.write_text(render_python(data) + render_python_typed(data), encoding="utf-8")
    JS_OUT.write_text(render_js(data), encoding="utf-8")
    DOC_OUT.write_text(render_doc(data), encoding="utf-8")
    TS_OUT.write_text(render_typescript(data), encoding="utf-8")
    for p in (PY_OUT, JS_OUT, DOC_OUT, TS_OUT):
        print(f"wrote {p.relative_to(REPO_ROOT)}")
    return 0
```

- [ ] **Step 4: Regenerate**

```bash
make gen-message-types
```

Expected: prints `wrote` for all four output paths, no errors.

- [ ] **Step 5: Spot-check the TS output**

```bash
head -20 tui/src/types.generated.ts
diff <(grep -E '^export (interface|type) ' tui/src/types.ts | sort) \
     <(grep -E '^export (interface|type) ' tui/src/types.generated.ts | sort)
```

Expected: the `diff` shows no missing/extra `export interface` / `export type` declarations. The two files should declare the same set of public surfaces. (Bodies will differ: the hand-typed file has `// NOTE:` comments; the generated file doesn't. That's fine.)

- [ ] **Step 6: Verify the TUI still typechecks**

```bash
cd tui && npm run typecheck
```

Expected: clean. The TUI is still importing from `./types.js` (the hand-typed file), so the new generated file isn't consumed yet — but it must still be syntactically valid TS.

```bash
cd tui && npm test
```

Expected: 12/12 pass.

No commit yet — continues into Task 4.

---

### Task 4: Makefile + coverage tests + Commit 1

Wire the new TS file into the drift check, add property tests for the TypedDict coverage, then commit the full generator extension.

**Files:**
- Modify: `Makefile`
- Modify: `tests/test_message_types.py`

- [ ] **Step 1: Extend `check-message-types` in `Makefile`**

Find this line in `Makefile`:

```makefile
	git diff --exit-code -- src/decafclaw/web/message_types.py src/decafclaw/web/static/lib/message-types.js docs/websocket-messages.md
```

Append `tui/src/types.generated.ts`:

```makefile
	git diff --exit-code -- src/decafclaw/web/message_types.py src/decafclaw/web/static/lib/message-types.js docs/websocket-messages.md tui/src/types.generated.ts
```

- [ ] **Step 2: Add TypedDict coverage tests**

Append to `tests/test_message_types.py`:

```python
from typing import get_args, get_type_hints


def test_typeddict_exists_for_every_message_type() -> None:
    """Every WSMessageType must have a TypedDict in message_types module."""
    import decafclaw.web.message_types as mt

    for member in WSMessageType:
        # Determine direction from the existing frozensets.
        if member in mt.S2C_MESSAGE_TYPES:
            iface_name = "Srv" + "".join(w.capitalize() for w in member.value.split("_"))
        elif member in mt.C2S_MESSAGE_TYPES:
            iface_name = "Cli" + "".join(w.capitalize() for w in member.value.split("_"))
        else:
            continue  # bidirectional (none currently)
        assert hasattr(mt, iface_name), f"missing TypedDict {iface_name} for {member.value}"


def test_server_message_union_covers_all_s2c() -> None:
    import decafclaw.web.message_types as mt

    union_members = set(get_args(mt.ServerMessage))
    expected = set()
    for member in mt.S2C_MESSAGE_TYPES:
        iface_name = "Srv" + "".join(w.capitalize() for w in member.value.split("_"))
        expected.add(getattr(mt, iface_name))
    assert union_members == expected


def test_client_message_union_covers_all_c2s() -> None:
    import decafclaw.web.message_types as mt

    union_members = set(get_args(mt.ClientMessage))
    expected = set()
    for member in mt.C2S_MESSAGE_TYPES:
        iface_name = "Cli" + "".join(w.capitalize() for w in member.value.split("_"))
        expected.add(getattr(mt, iface_name))
    assert union_members == expected


def test_every_typeddict_has_correct_type_literal() -> None:
    """Each TypedDict's `type` field must be Literal['<message_name>']."""
    import decafclaw.web.message_types as mt
    from typing import get_args, get_origin, Literal

    for member in WSMessageType:
        if member in mt.S2C_MESSAGE_TYPES:
            iface_name = "Srv" + "".join(w.capitalize() for w in member.value.split("_"))
        elif member in mt.C2S_MESSAGE_TYPES:
            iface_name = "Cli" + "".join(w.capitalize() for w in member.value.split("_"))
        else:
            continue
        td = getattr(mt, iface_name)
        hints = get_type_hints(td)
        type_hint = hints["type"]
        assert get_origin(type_hint) is Literal
        assert get_args(type_hint) == (member.value,)
```

- [ ] **Step 3: Run tests**

```bash
make test
```

Expected: existing 2431 tests still pass + 16 new tests (12 parser + 4 coverage) all pass.

- [ ] **Step 4: Run make check**

```bash
make check
```

Expected: clean. The drift check (`check-message-types`) passes because we've regenerated all four files and they're all in sync.

- [ ] **Step 5: Stage and commit Commit 1**

```bash
git add scripts/gen_message_types.py \
        src/decafclaw/web/message_types.py \
        tui/src/types.generated.ts \
        Makefile \
        tests/test_message_types.py
git commit -m "$(cat <<'EOF'
feat(web): typed message_types codegen + TUI types.generated.ts

Extends scripts/gen_message_types.py with a field-type parser and two
renderers: render_python_typed appends TypedDicts + ServerMessage /
ClientMessage discriminated unions + WSSendCallable to the existing
message_types.py output; render_typescript emits a new
tui/src/types.generated.ts mirroring the hand-typed file's naming
convention (Srv*/Cli* + PascalCase).

No consumers wired up yet — the new typed surface exists alongside
hand-typed and dict-based callers. Next commit retypes ws_send;
commit after that swaps the TUI to the generated file.

Adds:
- parse_field_type unit tests (10 grammar inputs + error cases)
- TypedDict coverage tests (every WSMessageType has a TypedDict,
  unions are complete, type-discriminants match)
- check-message-types Makefile target now tracks the new TS file

Part of #492.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Python emit-site retype + Commit 2

Type the `ws_send` chain in `websocket.py`. Pyright surfaces every emit site that doesn't conform; fix each via documented patterns.

**Files:**
- Modify: `src/decafclaw/web/websocket.py`
- Possibly modify: `src/decafclaw/conversation_manager.py` and any other file pyright flags through the ws_send chain.

- [ ] **Step 1: Find the `ws_send` definition site**

```bash
grep -nB2 -A8 'async def ws_send\|ws_send = \|def websocket_chat' src/decafclaw/web/websocket.py | head -40
```

Note where `ws_send` is defined as a closure inside `websocket_chat()` and what its current parameter type is (likely untyped or `dict`).

- [ ] **Step 2: Import the typed surface**

At the top of `src/decafclaw/web/websocket.py`, add to the existing `from decafclaw.web.message_types import ...` line (or add a new import if there isn't one):

```python
from decafclaw.web.message_types import WSMessageType, WSSendCallable, ServerMessage
```

(The exact existing import line may differ; add `WSSendCallable` and `ServerMessage` to what's already there.)

- [ ] **Step 3: Retype the closure**

Find the `ws_send` definition inside `websocket_chat()` (around line 770ish). Change its parameter type from whatever it is (likely `dict` or `Any`) to `ServerMessage`. Preserve the existing function body verbatim — only the signature changes:

```python
# Before — body details may differ from this sketch:
async def ws_send(payload):
    ...  # existing body

# After:
async def ws_send(payload: ServerMessage) -> None:
    ...  # SAME existing body, untouched
```

- [ ] **Step 4: Thread `WSSendCallable` through handler signatures**

Find the `_HANDLERS` dict around line 695 and the handler function definitions above it. Each handler has a signature like:

```python
async def _handle_select_conv(ws_send, index, username, msg, state):
```

Update to:

```python
async def _handle_select_conv(
    ws_send: WSSendCallable, index, username, msg, state,
) -> None:
```

Repeat for every handler (`_handle_select_conv`, `_handle_send`, `_handle_load_history`, `_handle_set_model`, `_handle_confirm_response`, `_handle_widget_response`, etc. — grep for `async def _handle_` to enumerate).

Also update the forwarder factories (`_make_notification_forwarder`, `_make_vault_forwarder`, etc.) — change their `ws_send` parameter to `WSSendCallable`.

- [ ] **Step 5: Run pyright, capture errors**

```bash
make check 2>&1 | grep -E 'error|warning' | head -50
```

Expected: pyright complains about some number of emit sites. The errors will be one of these documented patterns:

| Pyright error pattern | Fix |
|---|---|
| "Argument of type `dict[str, X]` is not assignable to parameter of type `ServerMessage`" | Variable was built without type annotation. Add `out: SrvFoo = {...}` at the build site. |
| "Type of return expression cannot be assigned to declared return type `SrvFoo`" | Helper returns a payload but isn't annotated. Add `-> SrvFoo` to the helper's signature. |
| "Could not assign item in TypedDict... 'foo' is not defined in TypedDict 'SrvBar'" | Typo in key name OR the field genuinely shouldn't exist. Verify the manifest, fix the typo, OR mark the field `NotRequired[]` in the manifest if it should exist. |
| "Required key 'foo' is missing from TypedDict 'SrvBar'" | The payload conditionally omits a required field. Either always-include the field, OR mark the field `NotRequired[]` in the manifest. |

- [ ] **Step 6: Resolve errors iteratively**

For each pyright error, apply the matching pattern. The fix is local — no need to refactor surrounding code.

**Example: variable-built dict (typical of forwarders):**

Original:

```python
out = {"type": WSMessageType.SOMETHING, "conv_id": conv_id}
if event.get("extra"):
    out["extra"] = event["extra"]
await ws_send(out)
```

If `extra` is a known optional field on the manifest:

```python
from decafclaw.web.message_types import SrvSomething

out: SrvSomething = {"type": WSMessageType.SOMETHING, "conv_id": conv_id}
if event.get("extra"):
    out["extra"] = event["extra"]
await ws_send(out)
```

The `WSMessageType.SOMETHING` enum value continues to work because the TypedDict's `type` field is `Literal[WSMessageType.SOMETHING]`, not `Literal["something"]` — pyright matches the enum member, not a bare string.

**Example: helper returning payload (e.g. `_project_tool_end`):**

Original:

```python
def _project_tool_end(event, conv_id):
    payload = {"type": WSMessageType.TOOL_END, "conv_id": conv_id, ...}
    return payload
```

Fixed:

```python
from decafclaw.web.message_types import SrvToolEnd

def _project_tool_end(event: dict, conv_id: str) -> SrvToolEnd:
    payload: SrvToolEnd = {"type": WSMessageType.TOOL_END, "conv_id": conv_id, ...}
    return payload
```

**Example: conditional key inclusion:**

Original (in a forwarder):

```python
out = {"type": WSMessageType.TOOL_END, ...}
if widget:
    out["widget"] = widget
```

If `widget` is `NotRequired[]` in the manifest (which it is — we marked it `object?` during PR #489), the above works once `out` is annotated `SrvToolEnd`. No further change needed.

- [ ] **Step 7: Re-run pyright until clean**

```bash
make check
```

Repeat Step 6 until pyright passes with zero errors.

If pyright reports an error that doesn't fit any documented pattern, STOP and report it. Don't add `# pyright: ignore[...]` blanket suppressions. If a single site genuinely needs a suppression, use `# pyright: ignore[<specific-rule>]` with an inline comment explaining why.

- [ ] **Step 8: Run all tests**

```bash
make test
```

Expected: 2431 + 16 (Task 4 added 16) = 2447 tests pass.

- [ ] **Step 9: Confirm wire is byte-for-byte unchanged**

This is the load-bearing check that the retype is purely type-level. Diff the actual emit sites' field sets before and after by inspecting `git diff` for `websocket.py`. There should be:

- Many added `: WSSendCallable` parameter annotations
- Many added `: SrvFoo = {...}` variable annotations
- Possibly some `WSMessageType.X` → `"x"` literal swaps inside dict literals
- Helper function return type annotations

There should NOT be:

- Any new key added to a payload
- Any key removed from a payload
- Any value type changed
- Any `WSMessageType.X` swapped for a bare literal string (the TypedDict uses `Literal[WSMessageType.X]` so the enum keeps working)
- Any `await ws_send(...)` call site removed or restructured beyond what's needed for the type annotation

```bash
git diff src/decafclaw/web/websocket.py | grep -E '^\+.*"type":|^-.*"type":' | head -30
```

If you see deletions of `"type":` lines that aren't paired with additions of the same `"type":` line, something has changed behavior — investigate.

- [ ] **Step 10: Commit Commit 2**

```bash
git add src/decafclaw/web/websocket.py
# If conversation_manager.py or others were touched, add them too:
# git status --short  # check what's modified
git commit -m "$(cat <<'EOF'
refactor(web): type ws_send chain end-to-end against manifest

Retypes ws_send and the handler / forwarder chain to use
WSSendCallable + ServerMessage from the generated typed surface.
Pyright now enforces the manifest's shape at every WS emit site:
a typo'd field name, missing required field, or wrong-typed value
fails check at type-check time instead of at runtime.

Wire is byte-for-byte unchanged. The diff is purely:
- ws_send / handler / forwarder signatures gain WSSendCallable
- variable-built payloads gain explicit SrvFoo annotations
- helper returns gain typed annotations

The TypedDict's `type` field is `Literal[WSMessageType.X]` (not
`Literal["x"]`), so existing emit sites that pass `WSMessageType.X`
enum values continue to typecheck without rewriting to bare strings.

Tests: existing 2431 + 16 from prior commit, all pass.

Part of #492.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: TUI codegen swap + Commit 3

Replace the hand-typed `tui/src/types.ts` with `tui/src/types.generated.ts` at import time. The names and shapes are identical, so consumer code doesn't change beyond the import path.

**Files:**
- Delete: `tui/src/types.ts`
- Modify: `tui/src/wsClient.ts`
- Modify: `tui/src/dispatcher.ts`
- Modify: `tui/src/dispatcher.test.ts`
- Modify: `tui/src/App.tsx`

- [ ] **Step 1: Delete the hand-typed file**

```bash
git rm tui/src/types.ts
```

- [ ] **Step 2: Update imports in the 4 consumer files**

Run this sed (verify on macOS BSD sed; on Linux it may need `-i ''` minor variation):

```bash
sed -i '' 's|"./types\.js"|"./types.generated.js"|g' \
    tui/src/wsClient.ts \
    tui/src/dispatcher.ts \
    tui/src/dispatcher.test.ts \
    tui/src/App.tsx
```

(macOS / BSD `sed` requires `-i ''`. Linux `sed` would use `-i`.)

Verify:

```bash
grep -n 'from "\./types' tui/src/*.ts tui/src/*.tsx
```

Expected: all matches are `from "./types.generated.js"` — zero `from "./types.js"` left.

- [ ] **Step 3: Run TUI typecheck**

```bash
cd tui && npm run typecheck
```

Expected: clean. (If "Cannot find module './types.js'" — the import update missed a site; re-grep and fix.)

- [ ] **Step 4: Run TUI tests**

```bash
cd tui && npm test
```

Expected: 12/12 pass. The dispatcher test's `import type { ServerMessage } from "./types.generated.js"` resolves to the generated file with identical shape.

- [ ] **Step 5: Run full make check**

From the repo root:

```bash
make check
```

Expected: clean. Drift check passes (types.generated.ts is committed and matches regen output); pyright still passes; ruff passes; tsc on web/static passes.

- [ ] **Step 6: Commit Commit 3**

```bash
git add tui/src/wsClient.ts tui/src/dispatcher.ts tui/src/dispatcher.test.ts tui/src/App.tsx
# types.ts deletion already staged by git rm
git commit -m "$(cat <<'EOF'
refactor(tui): swap hand-typed types.ts for generated types.generated.ts

Completes the TUI spec's "Option A -> Option B" promotion path.
The hand-typed types.ts (written in deliberately codegen-shaped form
during #489) is replaced by tui/src/types.generated.ts, produced by
scripts/gen_message_types.py from the same message_types.json
manifest that drives the Python and JS sides.

Consumer changes are import-path only: `./types.js` -> `./types.generated.js`.
Type names and shapes are identical; the 12 vitest tests pass unchanged.
The generator file's drift check is part of `make check-message-types`,
so the TUI's wire types can't silently rot.

Closes the wire-shape side of #492 for the TUI.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: Regression check

Manually verify that pyright catches a typo. This is a one-off check, not committed.

**Files:**
- Temporary edit + revert: `src/decafclaw/web/websocket.py`

- [ ] **Step 1: Pick a small emit site and inject a typo**

Find a simple `await ws_send({...})` site — e.g., a `chunk` emit. Change the `"text"` key to `"texxt"`:

```python
# was:
await ws_send({"type": "chunk", "conv_id": conv_id, "text": data})
# becomes (temporarily):
await ws_send({"type": "chunk", "conv_id": conv_id, "texxt": data})
```

- [ ] **Step 2: Run pyright, confirm it errors**

```bash
make check 2>&1 | grep -E 'error|texxt'
```

Expected: pyright reports something like:

```
.../websocket.py:NNN:NN - error: Could not assign item in TypedDict
    "texxt" is not defined in "SrvChunk"
```

This confirms the contract enforcement is real.

- [ ] **Step 3: Revert the typo**

```bash
git checkout -- src/decafclaw/web/websocket.py
```

- [ ] **Step 4: Final clean check**

```bash
make check && make test
cd tui && npm test && npm run typecheck
```

Expected: all clean.

- [ ] **Step 5: Verify three commits exist and branch is clean**

```bash
git log --oneline main..HEAD
git status --short
```

Expected: exactly 3 commits ahead of main (the three Commits above), working tree clean.

---

## After implementation

Open a PR per `superpowers:finishing-a-development-branch`. Request Copilot reviewer. The PR body should reference #492 and call out:

- 8 manifest drifts that motivated the work (link to #492)
- The three-commit shape
- The validation guarantee (pyright + tsc enforce manifest from now on)
- That the wire shape is byte-for-byte unchanged

## Self-review checklist (run before each commit)

- [ ] No `pyright: ignore` added without an inline reason comment.
- [ ] No fields added/removed/retyped in the manifest (the prior PR #489 fixed all known drift; this PR is purely type-discipline).
- [ ] No new wire message types added (out of scope).
- [ ] All existing test counts preserved (or grown by the explicit additions).
- [ ] `make check && make test` clean.
