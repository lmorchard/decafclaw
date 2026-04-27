# delegate_task: structured return schema

Tracking issue: #395 (split out of #300).

## Problem

`delegate_task` returns unstructured text. Callers then parse prose
to extract specific fields ("how many bugs were found", "what's the
recommended approach"). That's brittle and bloats parent context.

Anthropic's [Effective Context Engineering for AI Agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
calls out structured returns as one of the primary benefits of the
sub-agent pattern: ask the child to return a known shape, parse
once, hand the structured result to the parent.

## Goal

Optional `return_schema: dict | None` parameter on
`tool_delegate_task`. When supplied:

- The child's system prompt gets an addendum instructing it to emit
  a fenced JSON block matching the schema shape **after** any
  prose explanation.
- After the child run, parse the JSON block out of the response.
- Return `ToolResult(text=prose, data=parsed)` so the parent's tool
  loop renders both: human-readable prose explanation **plus** the
  structured object the parent needs (auto-rendered as a fenced
  JSON block in the tool result content).

Parse failures fall through silently with a debug log: the parent
gets the raw response as text, no `data`. No retry on failure —
the child has already done the work; a retry burns budget for
marginal gain.

## Decisions (autonomous)

1. **Schema is a hint, not enforced.** The schema is rendered into
   the prompt as a JSON example. We don't validate the parsed JSON
   against the schema (`jsonschema` library not added). If a
   caller needs strict validation, they can do it themselves on
   `ToolResult.data`. Keeps the dependency surface small.
2. **Strip the JSON block from the prose.** Mirroring
   `compaction_decisions.strip_json_block`, the prose half of the
   tool result is the response with the fenced block removed —
   keeps the rendered tool output tidy when the agent loop
   auto-appends `data` as JSON below the text.
3. **No retry on parse failure.** Issue body explicitly leaves this
   open; my call: silent prose-only fallback with debug log. The
   child's response *is* still useful as prose even when the
   structured part failed.
4. **Schema parameter type is `dict | None`.** JSON-schema-shaped
   dicts pass through opaquely. The OpenAI-style tool parameter
   declaration uses `"type": "object"` with no further constraints
   — callers can supply any shape they want.

## Architecture

### Prompt addendum

```python
STRUCTURED_OUTPUT_INSTRUCTION = """\

You MUST return your output in the following form:

1. Any prose explanation, analysis, or context first.
2. Then a fenced JSON block matching this exact schema:

```json
{schema}
```

Replace placeholder values with actual data; keep the field shape
exactly. Use `null` for missing values rather than omitting fields."""
```

When `return_schema` is supplied, render the schema as JSON via
`json.dumps(schema, indent=2)` and append the formatted addendum
to the child system prompt.

### Parse step

```python
_FENCED_JSON_RE = re.compile(r"```json\s*\n(?P<body>.+?)\n```", re.DOTALL)

def _parse_structured_output(text: str) -> tuple[Any | None, str]:
    """Return (parsed_or_None, prose_with_json_stripped). Lenient."""
```

Returns `(None, text)` when no JSON block, malformed JSON, or
non-object root — caller treats that as the silent fallback.

### Tool wrapper

```python
async def tool_delegate_task(
    ctx, task: str, model: str = "",
    return_schema: dict | None = None,
) -> ToolResult:
    raw = await _run_child_turn(ctx, task, model=model, return_schema=return_schema)
    # _run_child_turn returns ToolResult on error paths; pass through
    if isinstance(raw, ToolResult):
        return raw
    raw_text = raw or ""
    if return_schema is None:
        return ToolResult(text=raw_text)
    parsed, prose = _parse_structured_output(raw_text)
    if parsed is None:
        log.debug("delegate_task: child response had no parseable JSON; "
                  "falling back to prose-only")
        return ToolResult(text=raw_text)
    return ToolResult(text=prose or raw_text, data=parsed)
```

### `_run_child_turn` change

Adds `return_schema: dict | None = None` parameter. When set,
appends the rendered structured-output addendum to
`child_system_prompt` (after the existing skill bodies).

## Out of scope

- Schema validation against the parsed JSON.
- Retry on parse failure.
- Read-only vault access (#396).
- Parallel dispatch (#397).

## Acceptance criteria

- Calling `delegate_task` without `return_schema` is byte-identical
  to current behavior.
- Calling with `return_schema={...}` makes the child emit a JSON
  block matching the shape; parent gets `ToolResult(text=prose,
  data=parsed)`.
- Bad JSON (or no block) → `ToolResult(text=raw_response)`, debug
  log fires.
- Tool definition includes the new parameter with a clear
  description.

## Testing

- `_parse_structured_output` unit tests: valid object, no block,
  malformed JSON, non-object root, JSON that's a string/list (treat
  as parsed since the schema is opaque), prose stripping.
- Tool wrapper test (with mocked `_run_child_turn`): no schema →
  text-only result; schema + valid JSON → text + data; schema +
  bad JSON → text-only with debug log.
- No real-LLM CI test. Manual smoke after merge.

## Files touched

- `src/decafclaw/tools/delegate.py` — addendum, parser, wrapper, tool def.
- `tests/test_delegate.py` (new or extend) — unit + wrapper tests.
- `docs/delegation.md` — describe `return_schema`.
- `CLAUDE.md` — no change (no new convention; tools section already
  covers `ToolResult.data`).
