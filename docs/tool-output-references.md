# Tool Output References

A saved tool output gets a stable **handle** — a short, conversation-scoped address the model can read back later — so truncation, context clearing, compaction, and reload stop losing the body (#1128).

Design rationale and open questions: [the dev-session spec](dev-sessions/2026-11-13-1430-issue-1128-stable-read-references/spec.md).

## Problem

When a tool returns more text than the preview limit, the body is written to `{conv_dir}/tool_outputs/` and the model keeps a short pointer. Context cleanup then replaces old tool results with `[tool output cleared: N bytes]` stubs, and compaction rewrites the history. After that the model has no address for the saved content: only ordering and a filesystem path that no longer appears anywhere in context.

## Handle

```
tool_output://{conv_id}/{filename}
```

The handle wraps the existing materialization: files stay at `{conv_dir}/tool_outputs/`, so nothing moves and no new top-level workspace area appears. The `conv_id` makes the address conversation-scoped, and the filename may name any artifact in that directory — `{tool_call_id}.txt` for a truncated tool result, `{tool_call_id}.stdout.txt` / `.stderr.txt` for a streamed shell stream.

A malformed handle (wrong scheme, a path separator, `..`, an empty segment) is rejected rather than resolved. A handle from another conversation is refused, not followed.

## States

Each artifact carries a metadata sidecar, `{filename}.meta.json`, recording what the file holds. A reader cannot tell "complete" from "capped" from the file alone.

| State | Meaning |
|---|---|
| `complete` | The full body was captured. Paging serves all of it. |
| `capped` | Capture stopped at a cap (or a shell stream hit the disk cap); only the stored prefix exists. Reads report both stored and original size. |
| `failed` | Persistence errored at capture. The reference records the error, so a later read says so instead of showing an empty body. |
| `missing` | The handle is valid but the file is gone: deleted, or never materialized because the result fit the preview limit. |
| `denied` | The handle names another conversation's content. |

An artifact written before this feature (no metadata sidecar) reads as `complete`: legacy files hold what they hold, and a missing sidecar is not evidence of truncation.

## Reading

`tool_output_read` takes the handle plus optional `offset` and `limit` bytes and returns one page. Pages decode on a UTF-8 boundary, so a multi-byte character is never split across two reads. The result carries the state plus `next_offset`, `total_bytes`, and `stored_bytes` in `ToolResult.data`.

A truncated tool result now carries the handle in its note:

```
[Output truncated. Full output saved as tool_output://abc123/call_456.txt. Use tool_output_read with that handle to page through it.]
```

A truncated shell stream keeps its established note and appends the handle:

```
[Output truncated. Full output saved to /…/call_456.stdout.txt. Use the 'read' tool to inspect it. Stable handle: tool_output://abc123/call_456.stdout.txt (pass it to tool_output_read to page through the saved output).]
```

## Deletion

`tool_outputs.delete()` removes the artifact and its metadata. Conversation deletion removes the whole conversation directory, so every handle under it reports `missing` afterwards. Nothing else deletes artifacts today.

## Boundaries

References are read-only addresses. This feature adds no write path: no tool writes or rewrites a saved output, and no handle can reach outside its conversation's `tool_outputs/` directory. Child results and retained history are not addressable yet; that is the next slice in the design.

## Implementation

- `src/decafclaw/tool_outputs.py` — handle format/parse, capture, paged read, delete, state reporting.
- `src/decafclaw/tools/tool_output_tools.py` — the `tool_output_read` tool.
- `src/decafclaw/tool_execution.py` — capture on truncation, handle in the note.
- `src/decafclaw/tools/shell_tools.py` — streamed artifacts record their state and expose a handle.

Tests: `tests/test_tool_outputs.py` (states, paging, scoping, reload, deletion, the truncation flow) and `tests/test_shell_output_collection.py` (shell stream handle).
