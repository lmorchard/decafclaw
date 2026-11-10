# Spec: Bound shell collection and UI payloads before transcript truncation (#922)

## Problem

Two stages of tool-output handling are unbounded, ahead of the existing
transcript cap (which only bounds what is *archived*):

1. **Collection.** `_execute_command()` (shell) uses
   `subprocess.run(capture_output=True, text=True)` — the *complete* stdout and
   stderr are assembled in memory before anything is bounded. A command emitting a
   huge log consumes unbounded RAM.
2. **Transport payload.** `execute_single_tool()` publishes the full
   `result.text` in `tool_end` *before* the transcript cap runs. The Web UI
   receives the whole thing over WebSocket (unbounded); Mattermost shell results
   are display-bounded by `_shell_preview` (8000 chars) but the full text still
   flows through the bus and is embedded (then truncated) in the post.

The existing transcript cap also has quality problems: it truncates by
**byte-prefix** with `errors="replace"` (splits multibyte UTF-8 into U+FFFD) and
keeps only the **head** — but shell failures put the useful diagnostics at the
**tail**.

## Intended result

- Shell stdout/stderr are collected **incrementally** with bounded in-memory
  previews; full output is spilled to the conversation artifact layout as it
  streams, so memory stays bounded regardless of output size.
- Bounded previews are applied **before** `tool_end` reaches transports, so the
  Web UI payload is bounded for *all* tools (not just shell).
- Truncation preserves **UTF-8 boundaries**, supports **head/tail retention by
  tool** (tails for command diagnostics, heads for file reads), reports
  **truncation counts**, and keeps a **usable artifact reference**.
- stdout/stderr identity and exit status remain observable.
- Timeout/cancellation close readers/files and terminate owned command processes
  without traceback or resource-warning noise. Disk spill is capped so memory
  limiting does not create unbounded disk growth.

## Design decisions

### Shared truncation module (`src/decafclaw/output_truncation.py`)

New pure-function module (no I/O), borrowing pi's `truncate.ts` shape:

- `TruncationResult` dataclass: `content`, `truncated`, `truncated_by`
  (`"lines"` | `"bytes"` | None), `total_lines`, `total_bytes`,
  `output_lines`, `output_bytes`.
- `truncate_head(text, *, max_lines, max_bytes) -> TruncationResult` — keep the
  first N lines/bytes (whichever limit is hit first). Never returns partial
  lines; if the first line alone exceeds `max_bytes`, returns empty content with
  a flag. For file reads (see retention policy).
- `truncate_tail(text, *, max_lines, max_bytes) -> TruncationResult` — keep the
  last N lines/bytes. For command diagnostics (errors/final results at the end).
- `format_size(n) -> str` — human-readable byte size (`512B`, `6.0KB`, `1.2MB`).

Byte limits are measured as UTF-8 bytes of the (already-decoded) text; because
we operate on `str`, character boundaries are never split. Line limits count
`\n`-delimited lines. These are the single source of truth for preview shaping,
used by both shell collection and the generic tool-output path.

### Shell streaming collector (rewrites `_execute_command`)

`_execute_command` becomes `async` and streams via
`asyncio.create_subprocess_shell(..., stdout=PIPE, stderr=PIPE,
start_new_session=True)`:

- **Two concurrent readers** (stdout, stderr), each feeding a bounded
  accumulator. Concurrent reads avoid the pipe-buffer deadlock that sequential
  draining would cause.
- **Bounded accumulator** (per stream): a streaming UTF-8 incremental decoder
  (`codecs.getincrementaldecoder("utf-8")("replace")` — same as the background
  skill's `_read_stream`, so multibyte chars split across chunks stay valid), a
  rolling tail bounded to `max_bytes`, and running `total_lines`/`total_bytes`
  counters. Memory is O(max_bytes) per stream, never the full output.
- **Real-time spill** to the conversation artifact layout: once a stream's total
  exceeds `max_bytes`, open `{conversation_dir}/tool_outputs/{tool_call_id}.stdout.txt`
  (resp. `.stderr.txt`) and write every subsequent decoded chunk to it (plus the
  head already buffered, which is itself bounded by `max_bytes`). Per-stream files
  preserve stdout/stderr identity in the full output. Spill is capped at a disk
  limit (config); beyond it, writing stops but the rolling tail keeps collecting.
- **Result shape** (preserves today's identity + exit status):
  ```
  {stdout tail}
  [stderr]
  {stderr tail}
  [exit code: N]

  [Output truncated. Full stdout saved to <path>. Use the 'read' tool to inspect it.]
  ```
  The truncation note is appended only for streams that actually spilled, and
  reports the truncated line/byte counts. Small output (under the limit) returns
  exactly today's shape with no note and no file.

- **Timeout** (`shell` has no per-tool watchdog; the 30s limit is enforced here):
  a timer kills the process group (SIGTERM → grace → SIGKILL, reusing the
  background skill's `_kill_process` semantics), readers/files close cleanly, and
  the result is a bounded preview of what was collected plus
  `[error: command timed out after N seconds]`. No traceback, no resource warnings.
- **Cancellation** (turn cancelled mid-command): same clean kill + close, then
  re-raise `CancelledError` (the existing `[cancelled: shell]` result is produced
  by `execute_single_tool`).

The total-runtime limit stays the existing hardcoded 30s in this branch
(#1136, which makes it configurable via `shell.max_runtime_sec`, is in review and
out of scope here — this PR must not depend on it).

### Generic `tool_end` bounding (`execute_single_tool`)

Bound the payload **once, early** — right after `result` is produced and before
`publish("tool_end", ...)`: if `len(result.text.encode("utf-8")) >
max_tool_output_bytes`, spill the full text to `tool_outputs/{tool_call_id}.txt`
(the existing artifact path) and **mutate** `result.text` to a bounded preview +
reference. Because the mutation happens before both the event publish and the
archive write, the Web UI payload, the Mattermost post, the archived transcript,
and what the LLM sees in history all carry the *same* bounded preview — no more
divergence between "what the UI got" (full) and "what was archived" (capped).

This is the fix for the unbounded Web UI payload and reuses the existing artifact
layout. Shell's preview is already small (from its own collector), so this path
rarely triggers for shell — it matters for any tool that returns a large string in
one shot.

The existing **transcript cap** (archive) is kept as a safety net for the case
where *(bounded text + JSON data)* still exceeds `max_tool_output_bytes` (large
structured data), and is switched from byte-prefix `errors="replace"` to the
shared truncation (UTF-8-safe) so it no longer mangles multibyte text. If it
spills, it writes the combined content to the same `{tool_call_id}.txt` — an
acceptable superset of the text-only spill, and the reference stays valid.

### Per-tool retention policy (required to avoid regression)

The current transcript cap is **byte-prefix = head retention for every tool**.
Switching to tail globally would regress file reads (you'd lose the start of a
document). So retention is per-tool, and the **default is head** (preserving
today's behavior for every tool I'm not changing); only `shell` moves to **tail**
(command diagnostics — errors, final status — live at the end):

- `retention_for_tool(name) -> "head" | "tail"` — a small registry, default
  `"head"`, with `{"shell": "tail"}`. Extensible for future per-tool tuning
  (e.g., explicit head for specific read tools if ever needed).

The policy selects `truncate_tail` vs `truncate_head` when shaping previews in
both the shell collector and the generic early-bound path. This satisfies the
issue's "tails for command diagnostics, heads for file reads" without risking any
other tool.

### Reused / out-of-scope pieces (verified in origin/main)

- **Process kill on timeout/cancellation** reuses the background skill's
  `_kill_process` (SIGTERM → grace → SIGKILL on the process group) via a
  function-level import — it is pre-existing in origin/main (not from #1136), and
  the function-level import is the established pattern for shell_tools ↔ background.
- **`shell_background_start`** already streams through the bounded background
  runner (500-line deques) — out of scope; #922 targets the *foreground* shell
  path and the generic tool-output path.
- **Mattermost display** is already bounded by `_shell_preview` (8000 chars); it
  will now receive the already-bounded `result.text` (harmless double-bound). Left
  as-is.

### Config knobs (top-level `Config`, next to `max_tool_output_bytes`)

- `max_tool_output_bytes` (existing, 51200) — the preview byte limit for both
  shell collection and `tool_end` bounding, and the transcript cap. One knob for
  "how much tool output do we carry around."
- `max_tool_output_lines` (new, default 2000) — the preview line limit.
- `max_tool_output_disk_bytes` (new, default e.g. 10MB) — cap on the spilled
  artifact file size, so a runaway command cannot fill the disk.

## Acceptance mapping

| Criterion | Where |
|---|---|
| Large output → bounded collection memory + transport payload; full output readable from artifact | shell collector test (large `yes`/`seq` → preview bounded, artifact file has full output); tool_end bounding test (large ToolResult.text → event payload bounded + artifact) |
| Single enormous line bounded as well as many short lines | truncation module tests (one 100KB line → tail-bounded, no partial char); shell collector test |
| Multibyte chars split across chunks remain valid in previews | collector test feeding a multibyte char split across two pipe reads; truncation module tests (no U+FFFD) |
| Tail retention includes final error/exit diagnostics; stdout/stderr + nonzero exit observable | collector test (command writes to stderr then exits 1 → preview shows `[stderr]` tail + `[exit code: 1]`) |
| Timeout/cancellation closes readers/files, terminates process, no noise | collector tests (timeout on `sleep`; cancellation) — assert process dead, files closed, no warnings (pytest promotes unraisable to error) |
| Existing tool-output retrieval continues to work | transcript cap test (large archive content → `tool_outputs/{id}.txt` + read-tool reference, now UTF-8-safe) |

## Out of scope

- #1136 (configurable foreground budget / auto-backgrounding) — in review; this
  PR keeps the hardcoded 30s and must merge independently. If #1136 lands first,
  rebase adapts `_execute_command` to `shell.max_runtime_sec`; the two are
  complementary (budgeted commands already stream through the bounded background
  runner).
- Reimplementing saved-output files — we reuse the existing `tool_outputs/` layout.
- Bounding `tool_status` progress events beyond what exists (only delegate.py
  publishes one, already bounded).
- Changing Mattermost `_shell_preview` display shaping (already bounded).
