# Plan: Bound shell collection and UI payloads before transcript truncation (#922)

Spec: `spec.md` (same dir). One commit per phase; gates (`make fmt`, lint,
typecheck) + targeted tests before each commit; full `make test` at the end.

## Phase 1 — Shared truncation module (pure)

**Files:** `src/decafclaw/output_truncation.py` (new), `tests/test_output_truncation.py` (new)

- `TruncationResult` dataclass: `content`, `truncated`, `truncated_by`
  (`"lines"` | `"bytes"` | None), `total_lines`, `total_bytes`,
  `output_lines`, `output_bytes`.
- `_cut_to_bytes(s, max_bytes) -> str` — longest prefix of `s` whose UTF-8
  encoding is ≤ max_bytes (linear scan over chars; never splits a character).
- `truncate_head(text, *, max_lines, max_bytes) -> TruncationResult` — first N
  lines; if the kept block exceeds max_bytes, drop lines from the end; a single
  line over the limit is cut via `_cut_to_bytes`. Never returns partial lines
  except that one cut line.
- `truncate_tail(text, *, max_lines, max_bytes) -> TruncationResult` — mirror
  image (last N lines; drop from the front; cut one line if needed). This is
  what shell diagnostics need.
- `retention_for_tool(name) -> "head" | "tail"` — registry, default `"head"`,
  `{"shell": "tail"}`. (Default head = today's byte-prefix behavior for every
  tool we're not changing; no regression.)
- `format_size(n) -> str` — `512B` / `6.0KB` / `1.2MB`.
- Line counting = `splitlines()` semantics (trailing newline adds no line).

**Tests:** head/tail × {line limit, byte limit, both}; single 100KB line
bounded with no partial char; multibyte text (é/emoji) never produces U+FFFD
and byte counts are UTF-8 bytes; exact-fit → `truncated=False`; empty string;
metadata fields correct.

## Phase 2 — Async streaming shell collector + config knobs

**Files:** `src/decafclaw/config.py` (2 new top-level fields),
`src/decafclaw/tools/shell_tools.py` (`_execute_command` rewrite),
`tests/test_shell_output_collection.py` (new)

- Config (next to `max_tool_output_bytes`, config.py:241):
  - `max_tool_output_lines: int = 2000` (env `MAX_TOOL_OUTPUT_LINES`)
  - `max_tool_output_disk_bytes: int = 10 * 1024 * 1024` (env
    `MAX_TOOL_OUTPUT_DISK_BYTES`) — cap on spilled artifact size.
- `_execute_command` becomes `async def _execute_command(ctx, command, timeout=30)`
  (timeout param is a test seam; default stays the hardcoded 30s — #1136 makes
  it configurable and is out of scope). `tool_shell` awaits it.
- Spawn: `asyncio.create_subprocess_shell(command, cwd=workspace,
  stdout=PIPE, stderr=PIPE, start_new_session=True, env=env)` (same env with
  `DECAFCLAW_WORKSPACE` as today).
- Per-stream collector (small class in shell_tools.py): incremental UTF-8
  decoder (`codecs.getincrementaldecoder("utf-8")("replace")` — same as the
  background skill's `_read_stream`), rolling tail bounded to max_bytes,
  running total_lines/total_bytes. Two concurrent reader tasks (one per pipe)
  — sequential draining deadlocks on full pipes.
- Real-time spill: when a stream's total first exceeds max_bytes, open
  `conversation_dir(config, conv_id, create=True) / "tool_outputs" /
  {call_id}.stdout.txt` (resp. `.stderr.txt`) and write the buffered tail, then
  every subsequent decoded piece. Per-stream files preserve stdout/stderr
  identity. Writes stop (file stays valid) once the disk cap is hit.
- Artifact naming: `ctx.tools.current_call_id` (set per forked tool call in
  production); fallback to a short uuid when empty (tests call the function
  directly).
- Wait: `await asyncio.wait_for(process.wait(), timeout=timeout)`. On normal
  exit, drain both readers with a bounded wait (5s — a grandchild can hold the
  pipes open past exit; proceed with what was captured, mirroring #1136's
  drain bound). On `asyncio.TimeoutError`: kill the process group via the
  background skill's `_kill_process` (function-level import — module-level
  would cycle), cancel readers, close spill files + transport, return bounded
  preview of what was collected + `[error: command timed out after N seconds]`.
- Cancellation (`asyncio.CancelledError`): same kill + close, then re-raise
  (execute_single_tool produces the `[cancelled: shell]` result).
- Transport close on every exit path: `getattr(process, "_transport", None)` →
  `.close()` (the background skill's `_close_transport` pattern — prevents
  PytestUnraisableExceptionWarning from GC on a dead loop).
- Result shape (small output = byte-for-byte today's shape):
  `{stdout}` + `\n[stderr]\n{stderr}` if stderr + `\n[exit code: N]` if
  nonzero, or `(no output)`. For truncated streams the tail is shaped by
  `truncate_tail(tail, max_lines, max_bytes)` and each spilled stream appends
  `[Output truncated. Full stdout saved to {path}. Use the 'read' tool to
  inspect it.]` (existing wording).

**Tests** (local `ctx` fixture like test_shell_guidance_modes.py; small config
limits — e.g. max_tool_output_bytes=1024, disk cap 8KB — to trigger spill with
moderate output; await process exit/reader tasks, no fixed sleeps):

- Small output → exact legacy shape, no artifact files.
- Large stdout (`seq`/python loop) → preview ≤ limit, `.stdout.txt` holds the
  full output; stdout/stderr identity (both streams spill to their own files).
- Single enormous line (`print('x'*100000)`) → bounded, no partial char.
- Multibyte (`'é'*N` / emoji) → preview decodes cleanly, no U+FFFD.
- stderr + nonzero exit (`echo err >&2; exit 3`) → preview shows `[stderr]`
  tail + `[exit code: 3]`.
- Timeout (`sleep 5`, timeout=1) → error text, process dead (killpg worked),
  spill file closed, no warnings.
- Cancellation mid-command → process dead, files closed, CancelledError
  propagates.
- Disk cap (max_tool_output_disk_bytes=2048, larger output) → spill file ≤
  cap + one chunk, preview still bounded.

## Phase 3 — Early `tool_end` bound + UTF-8-safe transcript cap

**Files:** `src/decafclaw/tool_execution.py`, `tests/test_tool_output_bound.py`
(new — or extend the existing tool-execution test file if one fits better)

- In `execute_single_tool`, right after `process_tool_media` (inside the try,
  before the finally publishes `tool_end`): if
  `len(result.text.encode("utf-8")) > max_tool_output_bytes`, spill the full
  text to `tool_outputs/{tool_call_id}.txt` and **mutate** `result.text` to
  bounded preview + reference (retention via `retention_for_tool(fn_name)`).
  Because the mutation precedes both the event publish and the archive write,
  Web UI payload, Mattermost post, archived transcript, and LLM history all
  carry the same bounded preview.
- The existing transcript cap (text + JSON data) stays as a safety net for
  large structured `data`, switched from byte-prefix `errors="replace"` to the
  shared truncation (UTF-8-safe, per-tool retention). Same artifact path — a
  second spill overwrites with the text+JSON superset; reference stays valid.

**Tests:** large ToolResult.text → `tool_end` event payload bounded + artifact
written; small text untouched (no file); multibyte at the boundary → no U+FFFD
in archived content; `shell` keeps tail (end marker survives), default tool
keeps head (start marker survives); bounded text + huge JSON data → safety net
still caps, UTF-8-safe.

## Phase 4 — Docs + notes

**Files:** `docs/tools.md` (shell section: bounded collection, per-stream
artifacts, disk cap), `docs/config.md` (two new knobs + where
max_tool_output_bytes is documented), `CLAUDE.md` (key-files entry for
`output_truncation.py`; short Tools bullet on the early bound — only if it
changes conventions), `docs/context-composer.md` (if it documents the
transcript cap — check first), dev session `notes.md`.

## Finish

- `make fmt`, lint, typecheck; full `make test`; check `--durations=25` for the
  new tests.
- Evals: no tool-description change expected → verify, then skip (record why).
- Push + PR (`Closes #922`); board → In review.
